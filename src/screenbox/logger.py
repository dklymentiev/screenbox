"""Structured action logger -- per-desktop session logs in JSONL format.

Directory layout:
  ~/.screenbox/logs/
  +-- events.jsonl                       # lifecycle events (all desktops)
  +-- {desktop_id}/
      +-- sessions.json                  # session index (list of sessions)
      +-- {session_id}.jsonl             # action log for one session

Compatible with Grafana Loki, ELK, Datadog, OpenTelemetry collectors.

Field reference (per action entry):
  timestamp  -- ISO 8601 UTC (Loki/ELK native parse)
  level      -- info | warn | error
  msg        -- short event description (full-text searchable)
  service    -- always "screenbox"
  desktop_id -- per-desktop label
  session_id -- unique session identifier
  tool       -- MCP tool name
  args       -- tool arguments (sensitive fields redacted)
  duration_ms-- execution time
  result     -- tool result (large payloads truncated)
  error      -- error message if level=error
  intent     -- why the agent performed this action
  step       -- logical step grouping ID
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SERVICE = "screenbox"


def _gen_session_id() -> str:
    """Generate a unique session ID (32 hex chars)."""
    return os.urandom(16).hex()


def _iso_now() -> str:
    """ISO 8601 UTC timestamp with milliseconds."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ActionLogger:
    """JSONL action logger for a desktop session.

    Logs to: {logs_dir}/{desktop_id}/{session_id}.jsonl
    Also maintains a session index at: {logs_dir}/{desktop_id}/sessions.json
    """

    def __init__(self, logs_dir: Path | str, desktop_id: str, log_keystrokes: bool = False,
                 session_id: str = None, agent_id: str = None):
        self.desktop_id = desktop_id
        self.log_keystrokes = log_keystrokes
        self.session_id = session_id or _gen_session_id()
        self._agent_id = agent_id
        self._action_count = 0
        self._start_time = _iso_now()

        # Per-desktop session directory
        self._desktop_dir = Path(logs_dir) / desktop_id
        self._desktop_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self._desktop_dir / f"{self.session_id}.jsonl"

        # Also keep flat file for backward compat / quick tail
        self._flat_file = Path(logs_dir) / f"{desktop_id}.jsonl"

        # Register session in index
        self._update_index("started")

    def log(self, tool: str, args: dict, result: Any = None,
            duration_ms: Optional[int] = None, agent_id: Optional[str] = None,
            error: Optional[str] = None,
            intent: Optional[str] = None, step: Optional[str] = None,
            **extra):
        """Write one structured action entry."""
        safe_args = self._redact(tool, args)
        level = "error" if error else "info"

        entry = {
            "timestamp": _iso_now(),
            "level": level,
            "msg": f"{tool}",
            "service": SERVICE,
            "desktop_id": self.desktop_id,
            "session_id": self.session_id,
            "tool": tool,
            "args": safe_args,
            "duration_ms": duration_ms,
        }
        if agent_id:
            entry["agent_id"] = agent_id
        if intent:
            entry["intent"] = intent
        if step:
            entry["step"] = step
        if error:
            entry["error"] = error
        if result is not None:
            if isinstance(result, str) and len(result) > 1000:
                entry["result"] = f"[{len(result)} chars]"
            elif isinstance(result, bytes):
                entry["result"] = f"[{len(result)} bytes]"
            else:
                entry["result"] = result
        for k, v in extra.items():
            if v is not None:
                entry[k] = v

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        # Write to session file
        with open(self.log_file, "a") as f:
            f.write(line)
        # Write to flat file (backward compat)
        with open(self._flat_file, "a") as f:
            f.write(line)

        self._action_count += 1

    def read_recent(self, limit: int = 10) -> list[dict]:
        """Read last N action entries from the flat log file."""
        if not self._flat_file.exists():
            return []
        try:
            with open(self._flat_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                read_size = min(size, 256 * 1024)
                f.seek(size - read_size)
                chunk = f.read().decode("utf-8", errors="replace")
            entries = []
            for line in chunk.strip().split("\n")[-(limit + 1):]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return entries[-limit:]
        except OSError:
            return []

    def close(self):
        """Mark session as ended in the index."""
        self._update_index("ended")

    def _update_index(self, event: str):
        """Update sessions.json index for this desktop."""
        index_file = self._desktop_dir / "sessions.json"
        try:
            if index_file.exists():
                with open(index_file) as f:
                    index = json.load(f)
            else:
                index = []
        except (json.JSONDecodeError, OSError):
            index = []

        # Find existing session entry or create new
        existing = None
        for s in index:
            if s.get("session_id") == self.session_id:
                existing = s
                break

        if event == "started":
            if not existing:
                entry = {
                    "session_id": self.session_id,
                    "started": self._start_time,
                    "agent_id": self._agent_id,
                    "log_file": f"{self.session_id}.jsonl",
                    "status": "active",
                }
                index.append(entry)
        elif event == "ended":
            if existing:
                existing["ended"] = _iso_now()
                existing["actions"] = self._action_count
                existing["status"] = "completed"

        try:
            with open(index_file, "w") as f:
                json.dump(index, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def _redact(self, tool: str, args: dict) -> dict:
        """Redact sensitive fields if log_keystrokes is False."""
        if self.log_keystrokes:
            return args

        safe = dict(args)
        if tool in ("desktop_type",) and "text" in safe:
            safe["text"] = f"[REDACTED {len(safe['text'])} chars]"
        return safe


def log_lifecycle(logs_dir: Path | str, event: str, desktop_id: str,
                  agent_id: Optional[str] = None, session_id: Optional[str] = None,
                  **kwargs):
    """Log lifecycle event (create/destroy/pause/resume) to events.jsonl.

    Same structured format as action logs for unified querying.
    """
    level = "error" if "fail" in event else "info"
    entry = {
        "timestamp": _iso_now(),
        "level": level,
        "msg": event,
        "service": SERVICE,
        "desktop_id": desktop_id,
    }
    if session_id:
        entry["session_id"] = session_id
    if agent_id:
        entry["agent_id"] = agent_id
    if "error" in kwargs:
        entry["error"] = kwargs.pop("error")
        entry["level"] = "error"
    if kwargs:
        entry["details"] = kwargs

    events_file = Path(logs_dir) / "events.jsonl"
    try:
        with open(events_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
