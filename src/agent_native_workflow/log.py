from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


class Logger:
    """Pipeline logger with text and JSON Lines modes.

    Writes to both stdout and an optional log file.
    """

    def __init__(self, log_file: Path | None = None, json_mode: bool | None = None) -> None:
        self._log_file = log_file
        self._json_mode = (
            json_mode
            if json_mode is not None
            else (os.environ.get("LOG_FORMAT", "").lower() == "json")
        )
        self._start_time = time.time()

        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)

    def info(self, message: str, **extra: object) -> None:
        self._emit("info", message, **extra)

    def warn(self, message: str, **extra: object) -> None:
        self._emit("warn", message, **extra)

    def error(self, message: str, **extra: object) -> None:
        self._emit("error", message, **extra)

    def phase_start(self, phase: str, **extra: object) -> None:
        self._emit("info", f"[{phase}] Started", phase=phase, event="phase_start", **extra)

    def phase_end(self, phase: str, result: str, **extra: object) -> None:
        self._emit(
            "info",
            f"[{phase}] {result.upper()}",
            phase=phase,
            event="phase_end",
            result=result,
            **extra,
        )

    def _emit(self, level: str, message: str, **extra: object) -> None:
        if self._json_mode:
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "elapsed_s": round(time.time() - self._start_time, 2),
                "level": level,
                "msg": message,
                **extra,
            }
            line = json.dumps(record, ensure_ascii=False)
        else:
            ts = time.strftime("%H:%M:%S")
            line = f"[{ts}] {message}"

        print(line, file=sys.stderr if level == "warn" else sys.stdout)

        if self._log_file:
            with self._log_file.open("a") as f:
                f.write(line + "\n")
