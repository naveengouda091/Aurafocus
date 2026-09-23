"""AuraFocus Configuration Module.

Centralized configuration management for sensing, inference, hysteresis stabilization,
audit logging, and actuation parameters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set, Tuple


@dataclass(frozen=True)
class AuraConfig:
    """Immutable runtime configuration schema for AuraFocus daemon."""

    # Project Paths
    ROOT_DIR: Path = field(default_factory=lambda: Path(__file__).resolve().parent)
    DATA_DIR: Path = field(default_factory=lambda: Path(__file__).resolve().parent / "data")
    LOG_DIR: Path = field(default_factory=lambda: Path(__file__).resolve().parent / "logs")
    DECISIONS_LOG_PATH: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent / "decisions.jsonl"
    )
    REPORTS_DIR: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent / "reports"
    )

    # Core Polling & Sensing Parameters
    POLL_INTERVAL_SECONDS: float = 1.0
    WINDOW_TITLE_MAX_CHARS: int = 384
    PROCESS_NAME_MAX_CHARS: int = 64
    MAX_CONTEXT_TOKENS: int = 512

    # Laya Decision Engine Parameters
    MODEL_NAME: str = "convaiinnovations/laya"
    MODEL_SUBFOLDER: str = "typed-decisions"
    OFFLINE_ONLY: bool = True  # Strict zero-cloud enforcement
    MIN_CONFIDENCE_THRESHOLD: float = 0.65
    URGENCY_LEVELS: int = 10

    # Categorical Focus Modes
    FOCUS_MODES: Tuple[str, ...] = (
        "deep_work",
        "communication_meeting",
        "casual_browsing",
        "media_or_gaming",
    )

    # Hysteresis / Anti-Flapping Parameters
    HYSTERESIS_WINDOW_SECONDS: int = 8
    HYSTERESIS_ACTIVATION_TICKS: int = 5
    FULLSCREEN_OVERRIDE_ENABLED: bool = True
    FULLSCREEN_CONFIDENCE_THRESHOLD: float = 0.85

    # System Processes to Filter / Ignore
    IGNORED_PROCESSES: Set[str] = field(
        default_factory=lambda: {
            "explorer.exe",
            "SearchHost.exe",
            "ShellExperienceHost.exe",
            "StartMenuExperienceHost.exe",
            "LockApp.exe",
            "ScreenClippingHost.exe",
            "TextInputHost.exe",
            "Taskmgr.exe",
            "dwm.exe",
        }
    )

    # Actuation Flags
    ENABLE_OS_ACTUATION: bool = True
    DRY_RUN: bool = False

    def ensure_directories(self) -> None:
        """Create necessary data, log, and report directories."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# Default global instance
CONFIG = AuraConfig()
CONFIG.ensure_directories()
