"""AuraFocus Brain Subsystem — Laya Decision Engine.

Manages model initialization, strict token truncation (<512 tokens),
typed question schemas (choice, score, noul), and high-speed
non-autoregressive inference.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from config import CONFIG, AuraConfig
from monitor import WindowContext

# Configure module logger
logger = logging.getLogger("aurafocus.brain")

# Attempt importing laya
try:
    import laya
except ImportError:
    laya = None  # type: ignore


@dataclass(frozen=True)
class DecisionOutput:
    """Immutable structured evaluation produced by the decision engine."""

    mode: str
    confidence: float
    urgency_score: int
    should_silence: bool
    latency_ms: float
    raw_answers: Dict[str, Any]
    used_fallback: bool


class CalibratedSemanticClassifier:
    """High-speed, offline calibrated semantic decision engine.

    Serves as an instant, zero-network fallback when neural model weights
    are unavailable or when running in strict air-gapped test environments.
    Strictly conforms to Laya's typed question contracts (choice, score, noul).
    """

    # Keyword semantic weight tables
    DEEP_WORK_PATTERNS = [
        r"\b(code|devenv|pycharm|idea|sublime|nvim|vim|emacs|vscode|visual studio)\b",
        r"\b(terminal|bash|powershell|cmd|alacritty|wezterm|iterm|tmux)\b",
        r"\b(git|github|gitlab|pull request|commit|merge|branch)\b",
        r"\b(debug|profiler|jupyter|notebook|latex|overleaf|matlab|rstudio)\b",
        r"\b(\.py|\.rs|\.go|\.cpp|\.c|\.ts|\.js|\.java|\.md|\.json|\.yaml)\b",
        r"\b(documentation|stackoverflow|docs|arxiv|rfc|api reference)\b",
    ]

    COMMUNICATION_PATTERNS = [
        r"\b(slack|teams|zoom|webex|google meet|meet\.google|discord|skype)\b",
        r"\b(outlook|thunderbird|mail|gmail|inbox|huddle|call|conference)\b",
    ]

    MEDIA_GAMING_PATTERNS = [
        r"\b(steam|epicgames|riotclient|gta|cyberpunk|valorant|minecraft)\b",
        r"\b(netflix|twitch|spotify|vlc|mpv|hulu|disney\+|prime video)\b",
        r"\b(youtube|gaming|watch\?v=)\b",
    ]

    CASUAL_BROWSING_PATTERNS = [
        r"\b(reddit|twitter|x\.com|facebook|instagram|tiktok|shopping|amazon|ebay)\b",
        r"\b(news|cnn|bbc|hacker news|wikipedia|tab|search)\b",
    ]

    def evaluate(self, state: Dict[str, Any]) -> Tuple[str, float, int, bool]:
        """Perform semantic evaluation and return calibrated (mode, confidence, score, should_silence)."""
        process = str(state.get("process", "")).lower()
        title = str(state.get("title", "")).lower()
        is_fullscreen = bool(state.get("fullscreen", False))
        combined = f"{process} {title}"

        # 1. Fullscreen Media / Gaming Fast-Path
        if is_fullscreen:
            for pattern in self.MEDIA_GAMING_PATTERNS:
                if re.search(pattern, combined):
                    return ("media_or_gaming", 0.96, 9, True)
            for pattern in self.COMMUNICATION_PATTERNS:
                if re.search(pattern, combined):
                    return ("communication_meeting", 0.94, 9, True)

        # 2. Deep Work Evaluation
        for pattern in self.DEEP_WORK_PATTERNS:
            if re.search(pattern, combined):
                # High-focus deep work
                urgency = 8
                confidence = 0.92
                return ("deep_work", confidence, urgency, True)

        # 3. Communication / Meeting Evaluation
        for pattern in self.COMMUNICATION_PATTERNS:
            if re.search(pattern, combined):
                # In meeting vs idle chat
                in_call = bool(re.search(r"\b(call|meeting|huddle|presenting|sharing)\b", combined))
                confidence = 0.91 if in_call else 0.82
                urgency = 7 if in_call else 4
                should_silence = in_call or is_fullscreen
                return ("communication_meeting", confidence, urgency, should_silence)

        # 4. Media & Gaming
        for pattern in self.MEDIA_GAMING_PATTERNS:
            if re.search(pattern, combined):
                return ("media_or_gaming", 0.88, 8 if is_fullscreen else 5, True)

        # 5. Casual Browsing
        for pattern in self.CASUAL_BROWSING_PATTERNS:
            if re.search(pattern, combined):
                return ("casual_browsing", 0.85, 2, False)

        # Default neutral baseline
        if process in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"):
            return ("casual_browsing", 0.70, 3, False)

        return ("deep_work", 0.65, 5, False)


class LayaBrain:
    """Orchestrates Laya non-autoregressive decision inference.

    Features:
    - Enforces strict token limit (<512 tokens).
    - Queries typed Laya questions (Choice, Score, Noul).
    - Sub-40ms execution target.
    - Seamless fallback to local calibrated semantic classifier.
    """

    def __init__(self, config: AuraConfig = CONFIG, force_fallback: bool = False):
        self.config = config
        self.force_fallback = force_fallback
        self._fallback_engine = CalibratedSemanticClassifier()
        self._agent: Optional[Any] = None
        self._questions = self._build_questions()

        if not force_fallback and laya is not None:
            self._try_load_model()
        else:
            logger.info("Using CalibratedSemanticClassifier as primary decision engine.")

    def _build_questions(self) -> Dict[str, Dict[str, Any]]:
        """Construct Laya's typed question schemas."""
        return {
            "mode": {
                "type": "choice",
                "instructions": (
                    "Classify user activity into exactly one focus mode: "
                    "'deep_work' (programming, technical reading, docs), "
                    "'communication_meeting' (video calls, chat, email), "
                    "'casual_browsing' (social media, casual web), "
                    "or 'media_or_gaming' (games, full-screen video)."
                ),
                "criteria": {
                    "deep_work": "Coding, IDE, terminal, technical research, CAD",
                    "communication_meeting": "Slack, Zoom, Teams, Google Meet, email, calls",
                    "casual_browsing": "Social networks, news, shopping, casual reading",
                    "media_or_gaming": "Video games, movies, full screen entertainment",
                },
            },
            "urgency": {
                "type": "score",
                "instructions": "Rate how critical and uninterrupted this focus must be from 1 to 10.",
                "criteria": [f"Level {i}" for i in range(1, 11)],
            },
            "should_silence": {
                "type": "noul",
                "instructions": "Should OS notifications and sounds be silenced to protect current focus?",
                "criteria": {
                    "false": "Allow routine desktop notification toasts and alert chimes",
                    "true": "Silence all notifications; enable Focus Assist / Do Not Disturb",
                },
            },
        }

    def _try_load_model(self) -> None:
        """Attempt to load Laya model locally."""
        try:
            logger.info("Initializing Laya agent from checkpoint: %s", self.config.MODEL_NAME)
            # Use local cache if available; offline-first
            self._agent = laya.load(
                self.config.MODEL_NAME,
                subfolder=self.config.MODEL_SUBFOLDER,
            )
            logger.info("Laya decision engine loaded successfully.")
        except Exception as exc:
            logger.warning(
                "Could not load neural Laya weights (%s). Falling back to CalibratedSemanticClassifier.",
                exc,
            )
            self._agent = None

    def sanitize_and_truncate_envelope(self, context: WindowContext) -> Dict[str, Any]:
        """Strictly guarantees that input context payload is well within the 512 token limit.

        Rule: 1 token ~= 3.5 characters.
        A 512 token ceiling gives ~1792 characters max.
        We cap the total serialized state envelope at < 600 characters (~170 tokens),
        providing a >2x safety margin for question instructions and special tokens.
        """
        envelope = context.to_laya_envelope(max_tokens=self.config.MAX_CONTEXT_TOKENS)

        # Enforce max title length
        title_str = str(envelope.get("title", ""))
        if len(title_str) > self.config.WINDOW_TITLE_MAX_CHARS:
            half = (self.config.WINDOW_TITLE_MAX_CHARS - 5) // 2
            envelope["title"] = f"{title_str[:half]} ... {title_str[-half:]}"

        # Enforce max process length
        proc_str = str(envelope.get("process", ""))
        if len(proc_str) > self.config.PROCESS_NAME_MAX_CHARS:
            envelope["process"] = proc_str[: self.config.PROCESS_NAME_MAX_CHARS]

        return envelope

    def evaluate(self, context: WindowContext) -> DecisionOutput:
        """Execute non-autoregressive decision inference on the active window context."""
        start_time = time.perf_counter()
        state = self.sanitize_and_truncate_envelope(context)

        # Neural Laya inference path
        if self._agent is not None and not self.force_fallback:
            try:
                # Single-pass forward evaluation across all typed questions
                result = self._agent.system_one(state, self._questions)
                answers = result.get("answers", {})

                # Extract typed answers
                mode_ans = answers.get("mode", {})
                mode = mode_ans.get("choice", "deep_work")
                confidence = float(mode_ans.get("confidence", 0.85))

                urgency_ans = answers.get("urgency", {})
                urgency_score = int(round(float(urgency_ans.get("score", 5))))
                urgency_score = max(1, min(10, urgency_score))

                noul_ans = answers.get("should_silence", {})
                noul_prob = float(noul_ans.get("noul", 0.5))
                should_silence = noul_prob >= 0.5

                latency_ms = (time.perf_counter() - start_time) * 1000.0
                return DecisionOutput(
                    mode=mode,
                    confidence=confidence,
                    urgency_score=urgency_score,
                    should_silence=should_silence,
                    latency_ms=latency_ms,
                    raw_answers=answers,
                    used_fallback=False,
                )
            except Exception as exc:
                logger.error("Laya neural evaluation error: %s. Engaging fallback.", exc)

        # Calibrated semantic engine path
        mode, conf, score, should_silence = self._fallback_engine.evaluate(state)
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        raw_answers = {
            "mode": {"choice": mode, "confidence": conf},
            "urgency": {"score": score},
            "should_silence": {"noul": 1.0 if should_silence else 0.0},
        }

        return DecisionOutput(
            mode=mode,
            confidence=conf,
            urgency_score=score,
            should_silence=should_silence,
            latency_ms=latency_ms,
            raw_answers=raw_answers,
            used_fallback=True,
        )
