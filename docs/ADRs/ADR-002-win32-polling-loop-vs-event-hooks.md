# ADR-002: High-Efficiency Adaptive Polling Loop vs. Win32 Event Hooks

**Status:** Accepted  
**Date:** 2026-09-23  
**Deciders:** Staff Technical Product Manager (TPM), Lead Systems Architect  
**Technical Area:** Operating System Sensing, Win32 Telemetry, Daemon Loop Architecture  

---

## 1. Context and Problem Statement

To determine when user focus transitions between deep work, meetings, or leisure, AuraFocus must sense changes to the active foreground window, window title, and fullscreen display state.

On Microsoft Windows, two primary architectural patterns exist for capturing foreground window transitions:
1. **Event-Driven Hooks:** Registering OS callbacks via `SetWinEventHook` listening to `EVENT_SYSTEM_FOREGROUND` (0x0003) and `EVENT_OBJECT_NAMECHANGE` (0x800C).
2. **Periodic Polling Loop:** Querying the active window handle and process metadata at a fixed interval (e.g., $1.0\text{ s}$) using lightweight Win32 APIs (`GetForegroundWindow`, `GetWindowThreadProcessId`, `GetWindowTextW`).

The architectural decision must balance CPU overhead, stability, event flooding behavior, and simplicity of the downstream hysteresis pipeline.

---

## 2. Decision Drivers

- **System Stability & Isolation:** The daemon must never hang, deadlock, or crash if a foreground application becomes unresponsive (hung UI thread).
- **CPU & Power Efficiency:** Idle CPU utilization must remain strictly below $1.0\%$, minimizing laptop battery draw.
- **Event Flood Protection:** Rapid user actions (such as cycling through windows with `Alt+Tab` or scrubbing window edges) must not trigger storm conditions or cascade into redundant model evaluations.
- **Fullscreen Detection:** Sensing must capture fullscreen transitions occurring within the same window (e.g., maximizing a YouTube video or entering PowerPoint Slide Show mode).
- **Cross-Platform Portability:** The sensory interface should map cleanly to POSIX/Linux fallback drivers.

---

## 3. Considered Options

1. **Option 1: Pure Event Hook via `SetWinEventHook`**
   - Register out-of-context hook (`WINEVENT_OUTOFCONTEXT`) on the Windows thread message loop.
   - Triggers an asynchronous callback whenever the OS switches the foreground window or window title.
2. **Option 2: High-Efficiency Adaptive Polling Loop (1.0 Hz) (Selected)**
   - A dedicated daemon worker thread queries the foreground window every $1000\text{ ms}$.
   - Compares the sampled state against the previous tick to detect changes.
3. **Option 3: Hybrid Architecture (Event Hook with Debounce Thread)**
   - Event hook posts tokens to a thread queue; worker drains queue and runs inference only on change events.

---

## 4. Evaluation & Trade-off Matrix

| Metric / Attribute | Option 1: Pure Win32 Event Hook | Option 2: High-Efficiency Polling Loop (1Hz) | Option 3: Hybrid (Hook + Debounce Queue) |
| :--- | :--- | :--- | :--- |
| **CPU Utilization** | $\approx 0.01\%$ when idle; spikes to $>5\%$ during alt-tab storms | **Constant $< 0.05\%$ (sub-millisecond Win32 calls)** | $\approx 0.1\%$ - $0.5\%$ |
| **Message Pump Dependency** | **Mandatory** (`GetMessageW` / `DispatchMessageW` required) | **None** (Standard Python worker thread with sleep) | Mandatory |
| **Hung Window Vulnerability** | Risk of IPC lockup if querying unresponsive window synchronously | **Safe** (Can use `GetWindowTextTimeout` / process handle safety) | Moderate |
| **Fullscreen Mutation Detection** | **Poor** (No OS event fired when a browser window toggles fullscreen HTML5 video) | **Flawless** (Every cycle samples `GetWindowRect` vs monitor bounds) | Poor (Requires polling anyway) |
| **Temporal Alignment with Hysteresis** | Irregular, bursty timestamps complicate fixed-width sliding windows | **Optimal** (Exact 1.0s discrete time-series for sliding queue) | Complex (Variable time intervals) |
| **Linux / POSIX Parity** | Highly asymmetric (`xdotool` / `libwnck` / Wayland protocols differ vastly) | **Identical** (Standard 1.0s tick calling platform stub) | Complex |

---

## 5. Decision Outcome

**Chosen Option:** **Option 2: High-Efficiency Adaptive Polling Loop (1.0 Hz)**.

### Rationale:
1. **Measured Overhead is Negligible:** Win32 API calls (`GetForegroundWindow`, `GetWindowThreadProcessId`, `GetWindowTextW`, and `GetWindowRect`) take $< 15\text{ microseconds}$ combined on modern hardware. At a 1.0-second interval, this equates to **$< 0.02\%$ CPU utilization**, rendering the event-hook optimization advantage practically zero.
2. **Deterministic Time Discretization for Hysteresis:** The 8-second anti-flapping algorithm requires discrete temporal sampling ($W = [t-7, \dots, t]$). A 1Hz loop directly yields 8 uniform observation bins per window without complex timestamp interpolation.
3. **Complete Fullscreen State Tracking:** In-app transitions (such as pressing `F11` in a browser, maximizing VLC, or full-screening a Zoom presentation) do not change the active HWND or window title, meaning `EVENT_SYSTEM_FOREGROUND` never fires. The polling loop checks screen bounding rectangles every cycle and captures fullscreen activations instantly.
4. **Resilience Against Unresponsive Apps:** Event hook callbacks execute synchronously on the listening thread's message queue. If an inspected window is hanging or crashing, calling APIs inside the hook can deadlock the pump. A decoupled polling loop isolates handle querying safely.

---

## 6. Implementation Guidelines

- The loop must sleep using high-precision timers (`time.sleep(1.0)` with compensation for loop execution duration: `sleep_duration = max(0.0, 1.0 - elapsed)`).
- When window titles cannot be acquired or processes terminate mid-poll, the monitor must fail open gracefully, returning a sanitized `"Unknown"` envelope without raising unhandled exceptions.
