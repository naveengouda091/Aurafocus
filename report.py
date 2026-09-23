"""AuraFocus Automated Decision Summary Reporter.

Analyzes decisions.jsonl and generates a comprehensive Markdown audit report
covering mode distribution, notification suppression rates, confidence calibration,
and focus state transition histories.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import CONFIG, AuraConfig


@dataclass
class ReportMetrics:
    """Aggregated analytical metrics computed from the decision audit trail."""

    total_records: int
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]
    duration_str: str

    # Mode distributions
    mode_counts: Dict[str, int]
    mode_percentages: Dict[str, float]

    # Silence & Bypass stats
    suppressed_count: int
    bypassed_count: int
    suppression_rate: float
    bypass_rate: float

    # Debounce / Anti-flapping stats
    debounce_interventions: int
    debounce_rate: float

    # Confidence & Urgency
    avg_confidence_overall: float
    avg_confidence_by_mode: Dict[str, float]
    avg_urgency_by_mode: Dict[str, float]
    confidence_tiers: Dict[str, int]

    # Top processes
    top_processes: List[Tuple[str, int]]
    top_apps_by_mode: Dict[str, List[Tuple[str, int]]]

    # OS State Transitions
    transitions: List[Dict[str, Any]]


def format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def parse_audit_log(
    log_path: Path, tail: Optional[int] = None
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse JSONL audit log into a list of telemetry dictionaries."""
    if not log_path.exists():
        return [], f"Audit log file not found at: {log_path}"

    records = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                records.append(record)
            except json.JSONDecodeError:
                continue

    if not records:
        return [], "No valid decision records found in audit log."

    if tail is not None and tail > 0:
        records = records[-tail:]

    return records, None


def compute_metrics(records: List[Dict[str, Any]]) -> ReportMetrics:
    """Compute comprehensive analytical metrics from parsed decision records."""
    total = len(records)
    first_ts = records[0].get("timestamp")
    last_ts = records[-1].get("timestamp")

    # Duration calculation
    try:
        t0 = datetime.datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        t1 = datetime.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        duration_sec = max(0.0, (t1 - t0).total_seconds())
        duration_str = format_duration(duration_sec)
    except Exception:
        duration_sec = float(total)
        duration_str = format_duration(duration_sec)

    # Counts
    mode_counts: Dict[str, int] = Counter()
    process_counts: Dict[str, int] = Counter()
    apps_by_mode: Dict[str, Counter] = defaultdict(Counter)

    suppressed_count = 0
    bypassed_count = 0
    debounce_interventions = 0

    confidences_by_mode: Dict[str, List[float]] = defaultdict(list)
    urgencies_by_mode: Dict[str, List[int]] = defaultdict(list)
    all_confidences: List[float] = []

    conf_tiers = {"High (>=85%)": 0, "Medium (70-84%)": 0, "Low (<70%)": 0}

    transitions = []
    prev_os_state = None
    prev_mode = None

    for rec in records:
        mode = rec.get("laya_mode", "unknown")
        proc = rec.get("process_name", "unknown")
        silence = bool(rec.get("should_silence", False))
        debounce = bool(rec.get("debounce_action_applied", False))
        conf = float(rec.get("confidence", 0.0))
        urgency = int(rec.get("urgency_score", 0))
        os_state = rec.get("os_dnd_state", "normal")

        mode_counts[mode] += 1
        process_counts[proc] += 1
        apps_by_mode[mode][proc] += 1

        if silence:
            suppressed_count += 1
        else:
            bypassed_count += 1

        if debounce:
            debounce_interventions += 1

        confidences_by_mode[mode].append(conf)
        urgencies_by_mode[mode].append(urgency)
        all_confidences.append(conf)

        if conf >= 0.85:
            conf_tiers["High (>=85%)"] += 1
        elif conf >= 0.70:
            conf_tiers["Medium (70-84%)"] += 1
        else:
            conf_tiers["Low (<70%)"] += 1

        # Track transitions
        if prev_os_state is not None and (os_state != prev_os_state or mode != prev_mode):
            transitions.append(
                {
                    "timestamp": rec.get("timestamp", ""),
                    "process_name": proc,
                    "window_title": rec.get("window_title", ""),
                    "prev_mode": prev_mode,
                    "new_mode": mode,
                    "prev_os_state": prev_os_state,
                    "new_os_state": os_state,
                    "is_fullscreen": rec.get("is_fullscreen", False),
                }
            )

        prev_os_state = os_state
        prev_mode = mode

    # Percentages
    mode_percentages = {m: (c / total) * 100.0 for m, c in mode_counts.items()}
    suppression_rate = (suppressed_count / total) * 100.0 if total > 0 else 0.0
    bypass_rate = (bypassed_count / total) * 100.0 if total > 0 else 0.0
    debounce_rate = (debounce_interventions / total) * 100.0 if total > 0 else 0.0
    avg_conf_overall = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0

    avg_conf_by_mode = {
        m: sum(lst) / len(lst) if lst else 0.0 for m, lst in confidences_by_mode.items()
    }
    avg_urgency_by_mode = {
        m: sum(lst) / len(lst) if lst else 0.0 for m, lst in urgencies_by_mode.items()
    }

    top_processes = process_counts.most_common(5)
    top_apps_by_mode_dict = {
        m: counter.most_common(3) for m, counter in apps_by_mode.items()
    }

    return ReportMetrics(
        total_records=total,
        first_timestamp=first_ts,
        last_timestamp=last_ts,
        duration_str=duration_str,
        mode_counts=dict(mode_counts),
        mode_percentages=mode_percentages,
        suppressed_count=suppressed_count,
        bypassed_count=bypassed_count,
        suppression_rate=suppression_rate,
        bypass_rate=bypass_rate,
        debounce_interventions=debounce_interventions,
        debounce_rate=debounce_rate,
        avg_confidence_overall=avg_conf_overall,
        avg_confidence_by_mode=avg_conf_by_mode,
        avg_urgency_by_mode=avg_urgency_by_mode,
        confidence_tiers=conf_tiers,
        top_processes=top_processes,
        top_apps_by_mode=top_apps_by_mode_dict,
        transitions=transitions,
    )


def generate_markdown_report(metrics: ReportMetrics) -> str:
    """Format computed metrics into a clean, executive Markdown report."""
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = []
    lines.append("# AuraFocus — Decision Taken Audit Summary Report")
    lines.append(f"**Report Generated:** {now_str}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 1: Executive Overview
    lines.append("## 1. Executive Summary")
    lines.append("| Metric | Value | Description |")
    lines.append("| :--- | :--- | :--- |")
    lines.append(f"| **Total Cycles Evaluated** | `{metrics.total_records:,}` | Discrete 1.0s observation ticks |")
    lines.append(f"| **Monitoring Duration** | `{metrics.duration_str}` | Active window monitoring window |")
    lines.append(f"| **Earliest Observation** | `{metrics.first_timestamp or 'N/A'}` | First recorded cycle |")
    lines.append(f"| **Latest Observation** | `{metrics.last_timestamp or 'N/A'}` | Most recent recorded cycle |")
    lines.append(f"| **Average Model Confidence** | `{metrics.avg_confidence_overall * 100:.1f}%` | Mean classification certainty |")
    lines.append(f"| **Anti-Flapping Damped Cycles** | `{metrics.debounce_interventions:,} ({metrics.debounce_rate:.1f}%)` | Transient context switches suppressed |")
    lines.append("")

    # Section 2: Focus Mode Distribution
    lines.append("## 2. Focus Mode Distribution")
    lines.append("| Focus Mode Category | Cycles | Share (%) | Estimated Time | Avg Confidence | Avg Urgency |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for mode in ("deep_work", "communication_meeting", "casual_browsing", "media_or_gaming"):
        cnt = metrics.mode_counts.get(mode, 0)
        pct = metrics.mode_percentages.get(mode, 0.0)
        dur = format_duration(cnt)
        conf = metrics.avg_confidence_by_mode.get(mode, 0.0) * 100.0
        urg = metrics.avg_urgency_by_mode.get(mode, 0.0)
        lines.append(f"| **`{mode}`** | {cnt:,} | {pct:.1f}% | {dur} | {conf:.1f}% | {urg:.1f}/10 |")
    lines.append("")

    # Section 3: Notification Suppression & Bypass Metrics
    lines.append("## 3. Notification Suppression & Bypass Rate")
    lines.append("| Decision Outcome | Count | Percentage | System Behavior |")
    lines.append("| :--- | :--- | :--- | :--- |")
    lines.append(
        f"| **Notifications Suppressed (DND)** | `{metrics.suppressed_count:,}` | **{metrics.suppression_rate:.1f}%** | OS Focus Assist engaged (Priority/Alarms Only) |"
    )
    lines.append(
        f"| **Notifications Allowed (Bypass)** | `{metrics.bypassed_count:,}` | **{metrics.bypass_rate:.1f}%** | Normal desktop toasts permitted |"
    )
    lines.append(
        f"| **Hysteresis Anti-Flapping Interventions** | `{metrics.debounce_interventions:,}` | **{metrics.debounce_rate:.1f}%** | Rapid toggles blocked during brief window switches |"
    )
    lines.append("")

    # Section 4: Confidence Calibration
    lines.append("## 4. Confidence Calibration Breakdown")
    lines.append("| Certainty Tier | Count | Share (%) |")
    lines.append("| :--- | :--- | :--- |")
    for tier, cnt in metrics.confidence_tiers.items():
        pct = (cnt / metrics.total_records) * 100.0 if metrics.total_records > 0 else 0.0
        lines.append(f"| `{tier}` | {cnt:,} | {pct:.1f}% |")
    lines.append("")

    # Section 5: Top Applications
    lines.append("## 5. Top Active Applications")
    lines.append("| Rank | Process Name | Cycles | Estimated Time |")
    lines.append("| :--- | :--- | :--- | :--- |")
    for idx, (proc, cnt) in enumerate(metrics.top_processes, 1):
        dur = format_duration(cnt)
        lines.append(f"| {idx} | `{proc}` | {cnt:,} | {dur} |")
    lines.append("")

    # Section 6: Focus State Transitions Timeline
    lines.append("## 6. Focus State Transition Timeline")
    if not metrics.transitions:
        lines.append("*No OS focus state transitions recorded (stable session).*")
    else:
        lines.append("| Timestamp (UTC) | Application | Previous Mode -> New Mode | OS Focus Assist Action |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for t in metrics.transitions[:25]:  # Show up to 25 transitions
            ts = t["timestamp"].split("T")[-1].replace("Z", "")[:8]
            app = t["process_name"]
            mode_trans = f"`{t['prev_mode']}` -> `{t['new_mode']}`"
            os_trans = f"`{t['prev_os_state']}` -> `{t['new_os_state']}`"
            lines.append(f"| `{ts}` | `{app}` | {mode_trans} | {os_trans} |")

        if len(metrics.transitions) > 25:
            lines.append(f"| ... | *({len(metrics.transitions) - 25} more transitions omitted)* | | |")

    lines.append("")
    lines.append("---")
    lines.append("*AuraFocus Audit Engine — Immutable Local Decision Trail.*")
    return "\n".join(lines)


def main():
    """CLI entrypoint for report generation."""
    parser = argparse.ArgumentParser(
        description="AuraFocus Automated Decision Summary Reporter"
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=CONFIG.DECISIONS_LOG_PATH,
        help="Path to decisions.jsonl audit file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for output Markdown report file",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the report directly to stdout",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=None,
        help="Analyze only the last N decision records",
    )

    args = parser.parse_args()

    records, err = parse_audit_log(args.log, tail=args.tail)
    if err:
        print(f"[AuraFocus Report] Error: {err}", file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(records)
    report_md = generate_markdown_report(metrics)

    if args.stdout:
        print(report_md)
        return

    # Determine output file path
    if args.output:
        out_path = args.output
    else:
        CONFIG.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = CONFIG.REPORTS_DIR / f"decision_summary_{timestamp_str}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"[AuraFocus Report] Decision Summary successfully generated at:")
    print(f"  -> {out_path.resolve()}")


if __name__ == "__main__":
    main()
