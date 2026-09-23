"""AuraFocus Window Context & Sensory Subsystem.

Provides active foreground window inspection, process name resolution,
fullscreen detection, and strict context sanitization (<512 tokens)
for Windows, with clean POSIX/Linux fallback stubs.
"""

from __future__ import annotations

import datetime
import os
import platform
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from config import CONFIG, AuraConfig

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

# Windows-specific API imports with runtime guard
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes
    try:
        import win32api
        import win32con
        import win32gui
        import win32process
    except ImportError:
        win32api = None  # type: ignore
        win32con = None  # type: ignore
        win32gui = None  # type: ignore
        win32process = None  # type: ignore


@dataclass(frozen=True)
class WindowContext:
    """Immutable snapshot of the active desktop foreground window."""

    timestamp: float
    iso_timestamp: str
    hwnd: int
    process_id: int
    process_name: str
    window_title: str
    is_fullscreen: bool
    is_valid: bool

    def to_laya_envelope(self, max_tokens: int = 512) -> dict[str, str | bool | int]:
        """Convert window context into a sanitized dictionary payload.

        Enforces strict character limits to guarantee total payload is < 512 tokens.
        """
        # Truncate title preserving head and tail (e.g. 'PRD.md - ... - Visual Studio Code')
        title = self.window_title
        max_title = CONFIG.WINDOW_TITLE_MAX_CHARS
        if len(title) > max_title:
            half = (max_title - 5) // 2
            title = f"{title[:half]} ... {title[-half:]}"

        return {
            "process": self.process_name[:CONFIG.PROCESS_NAME_MAX_CHARS],
            "title": title,
            "fullscreen": self.is_fullscreen,
        }

    def to_dict(self) -> dict:
        """Serialize context to dictionary for JSONL logging."""
        return {
            "timestamp": self.iso_timestamp,
            "hwnd": self.hwnd,
            "process_id": self.process_id,
            "process_name": self.process_name,
            "window_title": self.window_title,
            "is_fullscreen": self.is_fullscreen,
            "is_valid": self.is_valid,
        }


def sanitize_string(text: str) -> str:
    """Remove control characters, excessive whitespace, and non-printable bytes."""
    if not text:
        return ""
    # Strip non-printable ASCII/Unicode control characters except basic spaces
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
    # Collapse multiple whitespace characters
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class BaseWindowMonitor(ABC):
    """Abstract interface for desktop active window monitors."""

    @abstractmethod
    def get_active_window(self) -> WindowContext:
        """Sample the current active foreground window and return a WindowContext snapshot."""
        raise NotImplementedError


class WindowsWindowMonitor(BaseWindowMonitor):
    """Native Windows active window inspector utilizing Win32 APIs and psutil."""

    # Shell and system window class names that should not be classified as fullscreen apps
    SHELL_CLASSES = {
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Windows.UI.Core.CoreWindow",
    }

    def __init__(self, config: AuraConfig = CONFIG):
        self.config = config
        self._user32 = ctypes.windll.user32 if IS_WINDOWS else None
        self._kernel32 = ctypes.windll.kernel32 if IS_WINDOWS else None

    def _get_process_name(self, pid: int) -> str:
        """Resolve executable name for a given PID with layered fallbacks."""
        if pid <= 0:
            return "idle"

        # Attempt 1: psutil lookup
        if psutil is not None:
            try:
                proc = psutil.Process(pid)
                return proc.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
            except Exception:
                pass

        # Attempt 2: QueryFullProcessImageNameW via kernel32 ctypes
        if self._kernel32:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = self._kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if h_proc:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = ctypes.wintypes.DWORD(1024)
                    if self._kernel32.QueryFullProcessImageNameW(
                        h_proc, 0, buf, ctypes.byref(size)
                    ):
                        return os.path.basename(buf.value)
                finally:
                    self._kernel32.CloseHandle(h_proc)

        return "unknown.exe"

    def _is_fullscreen(self, hwnd: int) -> bool:
        """Determine whether the specified HWND occupies the entire display monitor."""
        if not hwnd or not win32gui or not win32api:
            return False

        try:
            # Check window visibility and minimized state
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return False
            if win32gui.IsIconic(hwnd):
                return False

            # Exclude Windows desktop shell components
            class_name = win32gui.GetClassName(hwnd)
            if class_name in self.SHELL_CLASSES:
                return False

            # Query window client bounding box
            w_left, w_top, w_right, w_bottom = win32gui.GetWindowRect(hwnd)

            # Query the bounding box of the monitor nearest to the window
            hmonitor = win32api.MonitorFromWindow(
                hwnd, win32con.MONITOR_DEFAULTTONEAREST
            )
            if not hmonitor:
                return False

            monitor_info = win32api.GetMonitorInfo(hmonitor)
            m_left, m_top, m_right, m_bottom = monitor_info["Monitor"]

            # Fullscreen criteria: window dimensions match or exceed the monitor rect
            is_fs = (
                w_left <= m_left
                and w_top <= m_top
                and w_right >= m_right
                and w_bottom >= m_bottom
            )
            return bool(is_fs)
        except Exception:
            return False

    def get_active_window(self) -> WindowContext:
        """Sample the current foreground window on Windows."""
        now = datetime.datetime.now(datetime.timezone.utc)
        epoch = now.timestamp()
        iso_str = now.isoformat()

        if not IS_WINDOWS or not win32gui:
            return WindowContext(
                timestamp=epoch,
                iso_timestamp=iso_str,
                hwnd=0,
                process_id=0,
                process_name="unsupported_platform",
                window_title="",
                is_fullscreen=False,
                is_valid=False,
            )

        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindow(hwnd):
                return WindowContext(
                    timestamp=epoch,
                    iso_timestamp=iso_str,
                    hwnd=0,
                    process_id=0,
                    process_name="idle",
                    window_title="Desktop / Idle",
                    is_fullscreen=False,
                    is_valid=False,
                )

            # Extract PID
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = self._get_process_name(pid)

            # Extract & sanitize Window Title
            raw_title = win32gui.GetWindowText(hwnd)
            title = sanitize_string(raw_title)

            # Check Fullscreen state
            is_fullscreen = self._is_fullscreen(hwnd)

            return WindowContext(
                timestamp=epoch,
                iso_timestamp=iso_str,
                hwnd=hwnd,
                process_id=pid,
                process_name=process_name,
                window_title=title,
                is_fullscreen=is_fullscreen,
                is_valid=True,
            )
        except Exception as exc:
            return WindowContext(
                timestamp=epoch,
                iso_timestamp=iso_str,
                hwnd=0,
                process_id=0,
                process_name="error",
                window_title=f"Sensor Error: {exc}",
                is_fullscreen=False,
                is_valid=False,
            )


class LinuxWindowMonitor(BaseWindowMonitor):
    """Clean POSIX/Linux fallback monitor interface for cross-platform compatibility."""

    def __init__(self, config: AuraConfig = CONFIG):
        self.config = config

    def get_active_window(self) -> WindowContext:
        """Produce a clean fallback context on POSIX systems."""
        now = datetime.datetime.now(datetime.timezone.utc)
        return WindowContext(
            timestamp=now.timestamp(),
            iso_timestamp=now.isoformat(),
            hwnd=1001,
            process_id=os.getpid(),
            process_name="posix_terminal",
            window_title="Linux Terminal (POSIX Fallback)",
            is_fullscreen=False,
            is_valid=True,
        )


class MockWindowMonitor(BaseWindowMonitor):
    """Deterministic mock monitor for automated unit and integration tests."""

    def __init__(self, fixed_context: Optional[WindowContext] = None):
        self._context = fixed_context

    def set_context(self, context: WindowContext) -> None:
        self._context = context

    def get_active_window(self) -> WindowContext:
        if self._context:
            return self._context
        now = datetime.datetime.now(datetime.timezone.utc)
        return WindowContext(
            timestamp=now.timestamp(),
            iso_timestamp=now.isoformat(),
            hwnd=42,
            process_id=1234,
            process_name="Code.exe",
            window_title="AuraFocus - brain.py - Visual Studio Code",
            is_fullscreen=False,
            is_valid=True,
        )


def get_monitor(mock: bool = False) -> BaseWindowMonitor:
    """Factory creating the appropriate platform window monitor."""
    if mock:
        return MockWindowMonitor()
    if sys.platform == "win32":
        return WindowsWindowMonitor()
    return LinuxWindowMonitor()
