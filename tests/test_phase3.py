"""Phase 3 Unit & Integration Tests.

Validates:
1. HysteresisStabilizer anti-flapping dampening during brief (<5s) window switches.
2. Sustained transition threshold activation (>=5 consecutive/sliding ticks).
3. Fullscreen fast-path override for media and meetings.
4. Asynchronous JSONL DecisionLogger write throughput, durability, and exact schema fidelity.
5. Report CLI metrics aggregation and formatted Markdown summary generation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
import pytest

from brain import DecisionOutput
from config import AuraConfig, CONFIG
from logger import DecisionLogger, TelemetryRecord
from monitor import WindowContext
from report import compute_metrics, generate_markdown_report, parse_audit_log
from stabilizer import HysteresisStabilizer, StabilizedDecision


def make_context(
    process: str = "Code.exe",
    title: str = "AuraFocus - stabilizer.py",
    fullscreen: bool = False,
) -> WindowContext:
    """Helper to produce test WindowContext snapshots."""
    now = time.time()
    return WindowContext(
        timestamp=now,
        iso_timestamp="2026-09-23T15:30:00.123456Z",
        hwnd=100,
        process_id=500,
        process_name=process,
        window_title=title,
        is_fullscreen=fullscreen,
        is_valid=True,
    )


def make_decision(
    mode: str,
    confidence: float = 0.90,
    urgency: int = 8,
    silence: bool = True,
) -> DecisionOutput:
    """Helper to produce test DecisionOutput structures."""
    return DecisionOutput(
        mode=mode,
        confidence=confidence,
        urgency_score=urgency,
        should_silence=silence,
        latency_ms=0.5,
        raw_answers={},
        used_fallback=True,
    )


class TestPhase3StabilizerAndAuditEngine:
    """Test suite covering Phase 3 deliverables."""

    def test_stabilizer_anti_flapping_transient_suppression(self):
        """Verify that a brief 3-second switch to chat does NOT toggle OS focus state."""
        stabilizer = HysteresisStabilizer()
        code_ctx = make_context("Code.exe", "editor.py")
        code_dec = make_decision("deep_work", confidence=0.92, urgency=8, silence=True)

        # 1. Establish initial deep work state across 5 ticks
        for _ in range(5):
            res = stabilizer.process(code_ctx, code_dec)

        assert res.mode == "deep_work"
        assert res.should_silence is True
        assert res.os_dnd_state == "focus_assist_priority"
        assert res.debounce_action_applied is False

        # 2. Brief 3-second Alt-Tab to Slack (communication_meeting)
        slack_ctx = make_context("Slack.exe", "team-sync")
        slack_dec = make_decision(
            "communication_meeting", confidence=0.88, urgency=4, silence=False
        )

        for tick in range(3):
            res = stabilizer.process(slack_ctx, slack_dec)
            # Must remain dampened! Mode and OS state must NOT flap to Slack
            assert res.mode == "deep_work", f"Flapped on tick {tick + 1}!"
            assert res.os_dnd_state == "focus_assist_priority"
            assert res.debounce_action_applied is True

        # 3. Return to Code.exe
        res = stabilizer.process(code_ctx, code_dec)
        assert res.mode == "deep_work"
        assert res.os_dnd_state == "focus_assist_priority"
        assert res.debounce_action_applied is False

    def test_stabilizer_sustained_transition(self):
        """Verify that a candidate mode persisting for >=5 ticks commits a state transition."""
        stabilizer = HysteresisStabilizer()
        browser_ctx = make_context("msedge.exe", "Wikipedia")
        browser_dec = make_decision("casual_browsing", confidence=0.80, urgency=3, silence=False)

        # Initialize to casual browsing
        for _ in range(5):
            stabilizer.process(browser_ctx, browser_dec)

        # Switch to Code.exe (deep work)
        code_ctx = make_context("Code.exe", "main.rs")
        code_dec = make_decision("deep_work", confidence=0.95, urgency=9, silence=True)

        # Ticks 1 to 4: Should be debounced
        for _ in range(4):
            res = stabilizer.process(code_ctx, code_dec)
            assert res.debounce_action_applied is True
            assert res.mode == "casual_browsing"

        # Tick 5: Activation threshold reached! Transition committed
        res = stabilizer.process(code_ctx, code_dec)
        assert res.debounce_action_applied is False
        assert res.mode == "deep_work"
        assert res.should_silence is True
        assert res.os_dnd_state == "focus_assist_priority"

    def test_stabilizer_fullscreen_fast_path(self):
        """Verify that entering fullscreen gaming or media bypasses the 5-second wait."""
        stabilizer = HysteresisStabilizer()

        # Start in casual browsing
        browser_ctx = make_context("msedge.exe", "Browsing", fullscreen=False)
        browser_dec = make_decision("casual_browsing", confidence=0.80, silence=False)
        for _ in range(5):
            stabilizer.process(browser_ctx, browser_dec)

        # Enter fullscreen VLC video immediately
        vlc_ctx = make_context("vlc.exe", "Movie - Fullscreen", fullscreen=True)
        vlc_dec = make_decision("media_or_gaming", confidence=0.95, urgency=8, silence=True)

        res = stabilizer.process(vlc_ctx, vlc_dec)

        # Must switch IMMEDIATELY without debounce delay
        assert res.debounce_action_applied is False
        assert res.mode == "media_or_gaming"
        assert res.should_silence is True
        assert res.os_dnd_state == "focus_assist_alarms_only"

    def test_stabilizer_manual_override(self):
        """Verify manual override locks state regardless of active application."""
        stabilizer = HysteresisStabilizer()
        stabilizer.set_override("force_dnd")

        browser_ctx = make_context("msedge.exe", "Casual Web")
        browser_dec = make_decision("casual_browsing", confidence=0.90, silence=False)

        res = stabilizer.process(browser_ctx, browser_dec)
        assert res.should_silence is True
        assert res.os_dnd_state == "focus_assist_priority"

        # Switch to force_normal
        stabilizer.set_override("force_normal")
        res = stabilizer.process(browser_ctx, browser_dec)
        assert res.should_silence is False
        assert res.os_dnd_state == "normal"

    def test_decision_logger_async_writes_and_flush(self, tmp_path: Path):
        """Verify asynchronous JSONL logger writes records with exact schema fidelity."""
        test_log_file = tmp_path / "test_decisions.jsonl"
        logger = DecisionLogger(log_path=test_log_file)

        ctx = make_context("Code.exe", "test.py")
        stab = StabilizedDecision(
            mode="deep_work",
            should_silence=True,
            os_dnd_state="focus_assist_priority",
            debounce_action_applied=False,
            raw_mode="deep_work",
            raw_confidence=0.9412,
            urgency_score=8,
            is_fullscreen=False,
        )

        # Write 25 records
        for i in range(25):
            logger.log_decision(ctx, stab)

        logger.flush()
        logger.close()

        # Read back records and assert structure
        records = logger.read_records()
        assert len(records) == 25

        rec = records[0]
        assert rec["process_name"] == "Code.exe"
        assert rec["laya_mode"] == "deep_work"
        assert rec["confidence"] == 0.9412
        assert rec["urgency_score"] == 8
        assert rec["should_silence"] is True
        assert rec["debounce_action_applied"] is False
        assert rec["os_dnd_state"] == "focus_assist_priority"

    def test_report_generation_from_jsonl(self, tmp_path: Path):
        """Verify report.py parses decisions.jsonl and produces formatted Markdown summary."""
        test_log_file = tmp_path / "decisions_for_report.jsonl"
        with DecisionLogger(log_path=test_log_file) as logger:
            # Generate diverse records: 10 deep work, 5 meetings, 3 casual, 2 media
            ctx_code = make_context("Code.exe", "model.py")
            ctx_zoom = make_context("Zoom.exe", "Standup Call")
            ctx_web = make_context("msedge.exe", "News")

            # 10 deep work
            d_code = StabilizedDecision(
                "deep_work", True, "focus_assist_priority", False, "deep_work", 0.92, 8, False
            )
            for _ in range(10):
                logger.log_decision(ctx_code, d_code)

            # 2 transient dampened ticks to web
            d_damped = StabilizedDecision(
                "deep_work", True, "focus_assist_priority", True, "casual_browsing", 0.75, 2, False
            )
            for _ in range(2):
                logger.log_decision(ctx_web, d_damped)

            # 5 meetings
            d_zoom = StabilizedDecision(
                "communication_meeting",
                True,
                "focus_assist_alarms_only",
                False,
                "communication_meeting",
                0.90,
                7,
                False,
            )
            for _ in range(5):
                logger.log_decision(ctx_zoom, d_zoom)

        # Parse log and compute metrics
        records, err = parse_audit_log(test_log_file)
        assert err is None
        assert len(records) == 17

        metrics = compute_metrics(records)
        assert metrics.total_records == 17
        assert metrics.mode_counts["deep_work"] == 12  # 10 steady + 2 dampened
        assert metrics.mode_counts["communication_meeting"] == 5
        assert metrics.debounce_interventions == 2
        assert metrics.suppressed_count == 17  # all were silenced in this test
        assert metrics.suppression_rate == 100.0

        # Generate markdown and verify sections
        md = generate_markdown_report(metrics)
        assert "# AuraFocus — Decision Taken Audit Summary Report" in md
        assert "## 1. Executive Summary" in md
        assert "## 2. Focus Mode Distribution" in md
        assert "## 3. Notification Suppression & Bypass Rate" in md
        assert "## 4. Confidence Calibration Breakdown" in md
        assert "## 5. Top Active Applications" in md
        assert "## 6. Focus State Transition Timeline" in md
        assert "`Code.exe`" in md
        assert "`Zoom.exe`" in md


if __name__ == "__main__":
    pytest.main(["-v", __file__])
