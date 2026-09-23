"""AuraFocus State Stabilization Subsystem — Hysteresis & Anti-Flapping Engine.

Implements an 8-second sliding window to suppress transient context switches
(e.g., brief Alt+Tabs to Slack or browser), preventing rapid OS Focus Assist toggling.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from brain import DecisionOutput
from config import CONFIG, AuraConfig
from monitor import WindowContext


@dataclass(frozen=True)
class StabilizedDecision:
    """Immutable output from the hysteresis stabilizer."""

    mode: str
    should_silence: bool
    os_dnd_state: str
    debounce_action_applied: bool
    raw_mode: str
    raw_confidence: float
    urgency_score: int
    is_fullscreen: bool


class HysteresisStabilizer:
    """Sliding-window hysteresis state machine.

    Invariants:
    - Maintains a FIFO queue of the last N = window_seconds / poll_interval decisions.
    - Requires candidate mode to persist for >= activation_ticks with sufficient confidence
      before committing an OS state change.
    - Fullscreen Fast-Path: Immediate state change for media/gaming or meetings in fullscreen.
    - Supports manual user override ("Auto Laya", "Force DND", "Force Normal").
    """

    def __init__(self, config: AuraConfig = CONFIG):
        self.config = config
        self.window_size = int(
            self.config.HYSTERESIS_WINDOW_SECONDS / self.config.POLL_INTERVAL_SECONDS
        )
        self.activation_ticks = self.config.HYSTERESIS_ACTIVATION_TICKS

        # Sliding deque holding tuples: (mode, confidence, should_silence, is_fullscreen)
        self._history: Deque[Tuple[str, float, bool, bool]] = collections.deque(
            maxlen=self.window_size
        )

        # Active committed states
        self.current_mode: str = "casual_browsing"
        self.current_silence: bool = False
        self.os_dnd_state: str = "normal"

        # Manual override state: None, "force_dnd", "force_normal"
        self.manual_override: Optional[str] = None

    def set_override(self, override: Optional[str]) -> None:
        """Set or clear manual user override.

        Values:
        - None: Auto Laya mode
        - "force_dnd": Lock OS DND to Priority/Alarms
        - "force_normal": Lock OS DND to Normal
        """
        self.manual_override = override
        if override == "force_dnd":
            self.current_silence = True
            self.os_dnd_state = "focus_assist_priority"
        elif override == "force_normal":
            self.current_silence = False
            self.os_dnd_state = "normal"

    def _resolve_os_dnd_state(self, mode: str, should_silence: bool, is_fullscreen: bool) -> str:
        """Map focus mode and silence intent to Windows Focus Assist profile."""
        if not should_silence:
            return "normal"
        if mode == "media_or_gaming" or (mode == "communication_meeting" and is_fullscreen):
            return "focus_assist_alarms_only"
        if mode == "deep_work":
            return "focus_assist_alarms_only" if is_fullscreen else "focus_assist_priority"
        return "focus_assist_priority" if should_silence else "normal"

    def process(self, context: WindowContext, raw: DecisionOutput) -> StabilizedDecision:
        """Ingest raw decision, update sliding window, and apply hysteresis filtering."""
        # Check Manual Override first
        if self.manual_override is not None:
            return StabilizedDecision(
                mode=self.current_mode,
                should_silence=self.current_silence,
                os_dnd_state=self.os_dnd_state,
                debounce_action_applied=False,
                raw_mode=raw.mode,
                raw_confidence=raw.confidence,
                urgency_score=raw.urgency_score,
                is_fullscreen=context.is_fullscreen,
            )

        # Record into sliding history deque
        entry = (raw.mode, raw.confidence, raw.should_silence, context.is_fullscreen)
        self._history.append(entry)

        debounce_action_applied = False
        target_mode = self.current_mode
        target_silence = self.current_silence

        # Fast-Path: Fullscreen override for meetings or gaming/media
        if (
            self.config.FULLSCREEN_OVERRIDE_ENABLED
            and context.is_fullscreen
            and raw.mode in ("media_or_gaming", "communication_meeting")
            and raw.confidence >= self.config.FULLSCREEN_CONFIDENCE_THRESHOLD
        ):
            target_mode = raw.mode
            target_silence = True
            self.current_mode = target_mode
            self.current_silence = target_silence
            self.os_dnd_state = self._resolve_os_dnd_state(
                target_mode, target_silence, context.is_fullscreen
            )
            return StabilizedDecision(
                mode=target_mode,
                should_silence=target_silence,
                os_dnd_state=self.os_dnd_state,
                debounce_action_applied=False,
                raw_mode=raw.mode,
                raw_confidence=raw.confidence,
                urgency_score=raw.urgency_score,
                is_fullscreen=context.is_fullscreen,
            )

        # Evaluate candidate transition
        candidate_mode = raw.mode
        if candidate_mode == self.current_mode:
            # Steady state: update silence flag if confidence is high
            target_mode = self.current_mode
            target_silence = raw.should_silence
            debounce_action_applied = False
        else:
            # Check persistence in the sliding window
            # Count observations of candidate_mode meeting confidence threshold
            qualified_ticks = sum(
                1
                for m, c, s, fs in self._history
                if m == candidate_mode and c >= self.config.MIN_CONFIDENCE_THRESHOLD
            )

            if qualified_ticks >= self.activation_ticks:
                # Candidate has persisted long enough -> commit state transition
                target_mode = candidate_mode
                target_silence = raw.should_silence
                debounce_action_applied = False
            else:
                # Candidate is transient -> damp/suppress change
                target_mode = self.current_mode
                target_silence = self.current_silence
                debounce_action_applied = True

        # Commit state updates
        self.current_mode = target_mode
        self.current_silence = target_silence
        self.os_dnd_state = self._resolve_os_dnd_state(
            target_mode, target_silence, context.is_fullscreen
        )

        return StabilizedDecision(
            mode=target_mode,
            should_silence=target_silence,
            os_dnd_state=self.os_dnd_state,
            debounce_action_applied=debounce_action_applied,
            raw_mode=raw.mode,
            raw_confidence=raw.confidence,
            urgency_score=raw.urgency_score,
            is_fullscreen=context.is_fullscreen,
        )
