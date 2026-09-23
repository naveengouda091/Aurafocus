# Product Requirements Document (PRD)

## Project: AuraFocus — Autonomous Context-Aware OS Focus Daemon
**Document Version:** 1.0.0  
**Status:** Approved / Architecture Milestone  
**Authors:** Staff Technical Product Manager (TPM) & Lead Systems Architect  
**Target Release:** v1.0.0  

---

## 1. Executive Summary

Modern knowledge workers lose up to **2.5 hours per day** to notification fragmentation. Research indicates that recovering from a single disruption takes an average of **23 minutes and 15 seconds**. Existing operating system solutions (e.g., Windows Focus Assist, macOS Focus) rely almost exclusively on crude, static schedules (e.g., "Silence from 9:00 to 17:00") or manual toggles that users frequently forget to engage.

**AuraFocus** is an ultra-lightweight, 100% offline, privacy-first background desktop daemon. It continuously monitors the active user window context, evaluates user intent in sub-40ms using **Laya** (a non-autoregressive encoder decision engine), dampens transient switching jitter via an 8-second hysteresis sliding window, and dynamically automates OS-level Focus / Do Not Disturb (DND) states.

Crucially, AuraFocus eliminates the "black box" anxiety of automated desktop agents through an immutable **"Decision Taken" Audit Engine**, providing verifiable real-time logging and rich retrospective analytics.

---

## 2. Problem Statement

1. **Static Scheduling Fails Dynamic Work:** Knowledge work is non-linear. An engineer may perform deep code review at 11:00 PM and attend an all-hands meeting at 10:00 AM. Hardcoded schedules fail to match actual cognitive states.
2. **Manual Focus Toggling Imposes Cognitive Friction:** Forcing users to manually toggle DND modes creates decision fatigue and fails precisely when users are most focused or stressed.
3. **Cloud-Based Solutions Threaten Privacy:** Sending window titles, document names, or process lists to remote generative Large Language Models (LLMs) leaks sensitive intellectual property, violates corporate compliance (SOC2, HIPAA, GDPR), and introduces 500ms–3000ms network round-trip latencies.
4. **Generative Small Language Models (SLMs) Waste Resources:** Running local generative SLMs (e.g., 3B–7B parameter models) causes memory bloat (>2–4 GB RAM), high CPU/GPU fan spin, battery drain, and non-deterministic formatting or hallucinations.
5. **The "Black Box" Problem:** Users mistrust automated systems when they do not understand why a notification was suppressed or allowed through.

---

## 3. User Personas

### Persona 1: Devin — Staff Software Engineer & Systems Developer
- **Behavior:** Spends hours writing code in VS Code, analyzing flamegraphs in a terminal, and compiling kernels. Frequently switches to documentation in a browser or checks a single Slack message for build statuses.
- **Pain Point:** High-priority focus is broken by non-critical notifications. However, a naive auto-silencer toggles DND every time Devin glances at Slack for 3 seconds, causing notification flapping.
- **Needs:** Zero-latency intent detection, robust anti-flapping dampening, and total assurance that no code or window titles leave the machine.

### Persona 2: Sarah — Senior Technical Product Manager
- **Behavior:** Switches between Zoom/Teams video conferences, Google Slides presentations, Jira planning boards, and Slack triage.
- **Pain Point:** Embarrassing personal notifications pop up while screen-sharing or presenting during meetings. Deep document writing gets derailed by routine chat pings.
- **Needs:** Instant, deterministic DND activation during presentations/meetings, smooth return to normal notifications during triage, and visibility into what notifications were withheld.

### Persona 3: Alex — Security & Compliance Auditor
- **Behavior:** Works in locked-down enterprise environments handling confidential security disclosures and proprietary codebases.
- **Pain Point:** Enterprise policy forbids background tools that transmit telemetry, process titles, or telemetry data over the network.
- **Needs:** Air-gapped, zero-network-call architecture, verifiable offline models, and an append-only cryptographic audit trail (`decisions.jsonl`) showing every automated intervention.

---

## 4. Product Requirements & Scope

### 4.1 Functional Requirements (FR)

| ID | Requirement | Description | Priority |
| :--- | :--- | :--- | :--- |
| **FR-01** | **Active Context Sensing** | Daemon must extract active foreground window title, process executable name, and fullscreen status without stealing focus or raising OS security alerts. | P0 |
| **FR-02** | **Non-Autoregressive Intent Evaluation** | Evaluate context in sub-40ms using Laya. Return categorical focus mode (`deep_work`, `communication_meeting`, `casual_browsing`, `media_or_gaming`), calibrated binary silence decision (`should_silence`), and urgency score (`1..10`). | P0 |
| **FR-03** | **Anti-Flapping / Transient Window Smoothing** | Implement an 8-second sliding window hysteresis buffer to ignore momentary context switches (<5s) such as quick alt-tabs or copy-paste operations. | P0 |
| **FR-04** | **OS Focus Actuation** | Directly actuate OS Focus Assist / DND states on Windows via WinRT/WNF/Registry APIs, with an architectural abstraction layer for POSIX/Linux fallbacks. | P0 |
| **FR-05** | **"Decision Taken" Real-Time Audit Engine** | Record every poll cycle to an append-only `decisions.jsonl` audit log capturing process, title, classification, confidence, debounce state, and applied OS actions. | P0 |
| **FR-06** | **Decision Summary Reporting Tool** | Provide a standalone CLI (`report.py`) that parses `decisions.jsonl` and outputs an executive Markdown summary of classification distributions, suppression rates, and calibration metrics. | P0 |
| **FR-07** | **System Tray & User Sovereignty** | System tray interface (`pystray`) providing real-time state visualization, manual overrides ("Force DND", "Force Normal", "Auto Laya"), and immediate report triggering. | P1 |
| **FR-08** | **Strict Context Truncation Guarantee** | Enforce a hard ceiling of 512 tokens on all input buffers before dispatching to the decision engine, preventing buffer overflow or memory degradation. | P0 |

### 4.2 Non-Functional Requirements (NFR)

| Metric | Target Specification | Validation Method |
| :--- | :--- | :--- |
| **Memory Footprint (RSS)** | **< 600 MB** resident RAM in steady-state (including model weights). | Continuous memory profiling via `psutil` during sustained 8-hour execution. |
| **CPU Utilization** | **< 1.0%** average single-core CPU consumption during active 1Hz polling. | Windows Performance Monitor / `psutil` sampling over 10,000 cycles. |
| **Inference Latency** | **< 40 ms** (p95) per evaluation cycle; total sensor-to-actuation loop **< 50 ms**. | High-resolution wall-clock instrumentation (`time.perf_counter_ns`). |
| **Network Traffic** | **Strictly Zero (0.0 KB)** outbound/inbound network traffic. Zero external telemetry. | Socket sandboxing / Wireshark packet capture audit during operation. |
| **Disk I/O** | **< 2 KB/sec** sequential append-only disk write rate for the audit trail. | Asynchronous file writer with buffered stream flushing. |
| **Cross-Platform Readiness** | Native Windows 10/11 primary target; clean headless POSIX stub interfaces. | Automated platform detection and modular driver dependency injection. |

---

## 5. "Decision Taken" Audit Engine & Reporting Requirements

The audit log is the central trust mechanism of AuraFocus. Every evaluation tick appends a single JSON record to `decisions.jsonl`.

### 5.1 Telemetry Record Fields
1. `timestamp`: ISO-8601 UTC timestamp with microsecond resolution.
2. `process_name`: Executable basename of the foreground window (e.g., `devenv.exe`, `slack.exe`).
3. `window_title`: Sanitized, truncated active window title (e.g., `PRD.md - Visual Studio Code`).
4. `is_fullscreen`: Boolean indicating whether the foreground window occupies the entire display viewport.
5. `laya_mode`: Predicted categorical intent (`deep_work`, `communication_meeting`, `casual_browsing`, `media_or_gaming`).
6. `confidence`: Calibrated probability score ($P \in [0.0, 1.0]$) for the selected mode.
7. `urgency_score`: Ordinal priority score ($1..10$) indicating cognitive focus depth.
8. `should_silence`: Calibrated boolean recommendation from Laya's `noul` primitive.
9. `debounce_action_applied`: Boolean indicating whether the hysteresis engine suppressed a state change due to anti-flapping rules.
10. `os_dnd_state`: Actual OS focus state enforced (`focus_assist_priority`, `focus_assist_alarms_only`, `normal`).

### 5.2 Reporting CLI Requirements (`report.py`)
The reporting utility must analyze historical decision logs and output structured Markdown containing:
- **Executive Metrics:** Total cycles observed, total active time monitored, average evaluation latency.
- **Intent Distribution:** Breakdown of time and cycles across all 4 modes.
- **Notification Suppression & Bypass Rates:** Total notifications suppressed vs. allowed through.
- **Confidence Calibration:** Mean and p90 confidence across classes.
- **Chronological Focus State Transitions:** Log of actual OS state changes and debounce interventions.

---

## 6. Release Criteria & Quality Gates

1. **Zero Flapping Gate:** Zero state toggling during simulated 3-second application switches.
2. **Latency Gate:** Average decision cycle $\le 40\text{ ms}$; maximum single-cycle spike $\le 80\text{ ms}$.
3. **Data Integrity Gate:** 100% valid JSON lines generated in `decisions.jsonl`; zero log corruption upon hard termination (`SIGINT` / `SIGTERM`).
4. **Privacy Gate:** Verified zero network socket creation throughout the runtime lifecycle.
