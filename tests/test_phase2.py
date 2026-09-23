"""Phase 2 Unit & Integration Tests.

Validates:
1. Configuration defaults and immutability.
2. Context extraction, sanitization, and center-truncation.
3. Strict 512-token context envelope guarantee.
4. Laya decision evaluation across all 4 modes (deep_work, communication_meeting, casual_browsing, media_or_gaming).
5. Sub-40ms decision latency benchmark.
"""

from __future__ import annotations

import time
import pytest

from config import AuraConfig, CONFIG
from monitor import (
    MockWindowMonitor,
    WindowContext,
    get_monitor,
    sanitize_string,
)
from brain import LayaBrain, DecisionOutput


class TestPhase2CoreSensingAndBrain:
    """Test suite covering Phase 2 deliverables."""

    def test_config_defaults_and_immutability(self):
        """Verify configuration invariants and immutability."""
        assert CONFIG.POLL_INTERVAL_SECONDS == 1.0
        assert CONFIG.HYSTERESIS_WINDOW_SECONDS == 8
        assert CONFIG.HYSTERESIS_ACTIVATION_TICKS == 5
        assert CONFIG.MAX_CONTEXT_TOKENS == 512
        assert "deep_work" in CONFIG.FOCUS_MODES
        assert "communication_meeting" in CONFIG.FOCUS_MODES
        assert "casual_browsing" in CONFIG.FOCUS_MODES
        assert "media_or_gaming" in CONFIG.FOCUS_MODES

        # Verify frozen immutability
        with pytest.raises(Exception):
            CONFIG.POLL_INTERVAL_SECONDS = 2.0  # type: ignore

    def test_string_sanitizer(self):
        """Verify control characters and extra whitespace are removed."""
        raw = "Visual Studio Code\x00\x08\x1f - \t\n  my_project   "
        clean = sanitize_string(raw)
        assert clean == "Visual Studio Code - my_project"
        assert "\x00" not in clean
        assert "\t" not in clean

    def test_context_truncation_guarantee(self):
        """Verify strict character and token limits on extreme context titles."""
        long_title = "A" * 2000  # Massive title
        context = WindowContext(
            timestamp=time.time(),
            iso_timestamp="2026-09-23T15:00:00Z",
            hwnd=123,
            process_id=456,
            process_name="Code.exe",
            window_title=long_title,
            is_fullscreen=False,
            is_valid=True,
        )

        brain = LayaBrain(force_fallback=True)
        sanitized_envelope = brain.sanitize_and_truncate_envelope(context)

        # Title must be <= WINDOW_TITLE_MAX_CHARS (384 chars)
        assert len(str(sanitized_envelope["title"])) <= CONFIG.WINDOW_TITLE_MAX_CHARS
        # Total serialized length must be < 600 chars (~170 tokens, strictly < 512 tokens)
        serialized_len = len(str(sanitized_envelope))
        assert serialized_len < 600

    def test_monitor_mock_and_factory(self):
        """Verify monitor factory and mock monitor interface."""
        mock_mon = get_monitor(mock=True)
        ctx = mock_mon.get_active_window()
        assert isinstance(ctx, WindowContext)
        assert ctx.process_name == "Code.exe"
        assert ctx.is_valid is True

        # Test live monitor factory
        live_mon = get_monitor(mock=False)
        assert live_mon is not None
        live_ctx = live_mon.get_active_window()
        assert isinstance(live_ctx, WindowContext)

    def test_brain_evaluates_deep_work(self):
        """Verify IDE / development context evaluates to deep_work with notification silencing."""
        brain = LayaBrain(force_fallback=True)
        ctx = WindowContext(
            timestamp=time.time(),
            iso_timestamp="2026-09-23T15:00:00Z",
            hwnd=100,
            process_id=200,
            process_name="Code.exe",
            window_title="AuraFocus - brain.py [Workspace] - Visual Studio Code",
            is_fullscreen=False,
            is_valid=True,
        )
        decision = brain.evaluate(ctx)

        assert decision.mode == "deep_work"
        assert decision.confidence >= 0.70
        assert decision.urgency_score >= 7
        assert decision.should_silence is True

    def test_brain_evaluates_communication_meeting(self):
        """Verify video meeting context evaluates to communication_meeting with silencing."""
        brain = LayaBrain(force_fallback=True)
        ctx = WindowContext(
            timestamp=time.time(),
            iso_timestamp="2026-09-23T15:00:00Z",
            hwnd=101,
            process_id=201,
            process_name="Zoom.exe",
            window_title="Zoom Meeting - Sprint 42 Planning Call",
            is_fullscreen=False,
            is_valid=True,
        )
        decision = brain.evaluate(ctx)

        assert decision.mode == "communication_meeting"
        assert decision.confidence >= 0.80
        assert decision.should_silence is True

    def test_brain_evaluates_casual_browsing(self):
        """Verify social media browsing evaluates to casual_browsing without silencing."""
        brain = LayaBrain(force_fallback=True)
        ctx = WindowContext(
            timestamp=time.time(),
            iso_timestamp="2026-09-23T15:00:00Z",
            hwnd=102,
            process_id=202,
            process_name="msedge.exe",
            window_title="Reddit - Dive into anything - Personal Profile",
            is_fullscreen=False,
            is_valid=True,
        )
        decision = brain.evaluate(ctx)

        assert decision.mode == "casual_browsing"
        assert decision.confidence >= 0.70
        assert decision.urgency_score <= 4
        assert decision.should_silence is False

    def test_brain_evaluates_fullscreen_media(self):
        """Verify fullscreen video stream evaluates to media_or_gaming with silencing."""
        brain = LayaBrain(force_fallback=True)
        ctx = WindowContext(
            timestamp=time.time(),
            iso_timestamp="2026-09-23T15:00:00Z",
            hwnd=103,
            process_id=203,
            process_name="vlc.exe",
            window_title="Documentary 4K - VLC Media Player",
            is_fullscreen=True,
            is_valid=True,
        )
        decision = brain.evaluate(ctx)

        assert decision.mode == "media_or_gaming"
        assert decision.confidence >= 0.85
        assert decision.should_silence is True

    def test_brain_latency_benchmark(self):
        """Benchmark 100 consecutive decision evaluations to verify sub-40ms execution."""
        brain = LayaBrain(force_fallback=True)
        ctx = WindowContext(
            timestamp=time.time(),
            iso_timestamp="2026-09-23T15:00:00Z",
            hwnd=100,
            process_id=200,
            process_name="Code.exe",
            window_title="AuraFocus - brain.py - Visual Studio Code",
            is_fullscreen=False,
            is_valid=True,
        )

        latencies = []
        for _ in range(100):
            t0 = time.perf_counter()
            d = brain.evaluate(ctx)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(dt_ms)

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)

        # Average must be well below 40ms (typically < 0.5ms for local evaluator)
        assert avg_latency < 40.0, f"Average latency too high: {avg_latency:.2f}ms"
        assert max_latency < 50.0, f"Max latency spike too high: {max_latency:.2f}ms"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
