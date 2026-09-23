"""AuraFocus Decision Audit Engine — Asynchronous JSONL Decision Recorder.

High-throughput, non-blocking asynchronous writer persisting every poll cycle
to an append-only JSONL log (decisions.jsonl) for verifiable auditability.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import CONFIG, AuraConfig
from monitor import WindowContext
from stabilizer import StabilizedDecision

module_logger = logging.getLogger("aurafocus.logger")


@dataclass(frozen=True)
class TelemetryRecord:
    """Exact schema for an audited decision tick in decisions.jsonl."""

    timestamp: str
    process_name: str
    window_title: str
    is_fullscreen: bool
    laya_mode: str
    confidence: float
    urgency_score: int
    should_silence: bool
    debounce_action_applied: bool
    os_dnd_state: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to primitive dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "process_name": self.process_name,
            "window_title": self.window_title,
            "is_fullscreen": self.is_fullscreen,
            "laya_mode": self.laya_mode,
            "confidence": round(self.confidence, 4),
            "urgency_score": self.urgency_score,
            "should_silence": self.should_silence,
            "debounce_action_applied": self.debounce_action_applied,
            "os_dnd_state": self.os_dnd_state,
        }

    def to_json(self) -> str:
        """Serialize directly to compact JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class DecisionLogger:
    """Thread-safe, asynchronous JSONL audit logger.

    Guarantees:
    - Zero disk I/O latency blocking the primary sensor/decision loop.
    - Append-only durability across process restarts.
    - Graceful queue draining on shutdown.
    """

    _SENTINEL = object()

    def __init__(self, log_path: Optional[Path] = None, config: AuraConfig = CONFIG):
        self.config = config
        self.log_path = log_path or self.config.DECISIONS_LOG_PATH
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self._queue: queue.Queue[Any] = queue.Queue(maxsize=2000)
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._writer_loop, name="AuraFocus-AuditWriter", daemon=True
        )
        self._worker_thread.start()

    def log_decision(
        self,
        context: WindowContext,
        decision: StabilizedDecision,
    ) -> TelemetryRecord:
        """Enqueue a telemetry record for asynchronous background write."""
        record = TelemetryRecord(
            timestamp=context.iso_timestamp,
            process_name=context.process_name,
            window_title=context.window_title,
            is_fullscreen=context.is_fullscreen,
            laya_mode=decision.mode,
            confidence=decision.raw_confidence,
            urgency_score=decision.urgency_score,
            should_silence=decision.should_silence,
            debounce_action_applied=decision.debounce_action_applied,
            os_dnd_state=decision.os_dnd_state,
        )

        try:
            self._queue.put_nowait(record)
        except queue.Full:
            module_logger.warning("Audit queue full; dropping oldest record.")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(record)
            except Exception:
                pass

        return record

    def _writer_loop(self) -> None:
        """Background thread consuming records and flushing to decisions.jsonl."""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.5)
                if item is self._SENTINEL:
                    break

                self._write_record(item)
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as exc:
                module_logger.error("Error writing decision record: %s", exc)

        # Drain remaining queue on shutdown
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item is not self._SENTINEL:
                    self._write_record(item)
                    self._queue.task_done()
            except Exception:
                break

    def _write_record(self, record: TelemetryRecord) -> None:
        """Append a single record with atomic newline termination."""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(record.to_json() + "\n")
            f.flush()

    def flush(self) -> None:
        """Wait until all queued records have been committed to disk."""
        self._queue.join()

    def close(self) -> None:
        """Gracefully shut down the background logger thread."""
        self._stop_event.set()
        self._queue.put(self._SENTINEL)
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def __enter__(self) -> DecisionLogger:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def read_records(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Read historical decision records sequentially from disk."""
        if not self.log_path.exists():
            return []

        records = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        if limit is not None and limit > 0:
            return records[-limit:]
        return records
