"""Knowledge Compilation Pipeline -- extract declarative facts from session logs.

Three functions:
  compile_from_log()  -- LLM call: action log -> candidate facts
  preview_merge()     -- pure code: diff candidates vs existing facts
  apply_merge()       -- write merged facts to KnowledgeStore
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .knowledge import get_store
from .llm_client import chat_completion

log = logging.getLogger(__name__)

# --- Compilation prompt ---

SYSTEM_PROMPT = """\
You are a knowledge compiler for Screenbox, a virtual desktop system for AI agents.

Your task: analyze action logs from an agent session and extract DECLARATIVE knowledge facts.

## Rules

1. Facts MUST be DECLARATIVE (describe UI state and behavior), NEVER imperative (instructions).
   GOOD: "Tip of Day dialog: dismissed with Enter. Escape does not work."
   GOOD: "Save As dialog: filename field at bottom, File type dropdown below it. Default format is ODS."
   BAD: "Press Enter to close Tip of Day dialog."
   BAD: "First click the filename field, then type the name."

2. Each fact must include:
   - "text": the declarative statement (1-2 sentences max)
   - "level": one of "os" (desktop environment), "app" (specific application), "flow" (multi-step process)
   - "domain": lowercase slug identifying the app or domain (e.g. "libreoffice-calc", "chrome", "file-manager")
   - "triggers": array of 3-8 lowercase keywords for search matching

3. Focus on NON-OBVIOUS discoveries: workarounds, unexpected behavior, timing issues, keyboard shortcuts that work or don't.

4. Skip trivial actions (typing text, basic navigation) unless they revealed unexpected behavior.

5. For "flow" level facts: describe the SEQUENCE as a declarative observation, not instructions.
   GOOD: "LibreOffice Calc xlsx export path: Ctrl+Shift+S > format dropdown > xlsx filter. Default is ODS."
   BAD: "To export xlsx: press Ctrl+Shift+S, select xlsx from dropdown."

6. If an action failed or was retried, that's valuable -- describe what didn't work and what did.

## Output format

Return a JSON array of fact objects. Nothing else -- no markdown, no explanation, no wrapping.
Example: [{"text": "...", "level": "app", "domain": "chrome", "triggers": ["tab", "close", "ctrl+w"]}]
"""


def _format_log_for_prompt(entries: list[dict]) -> str:
    """Format log entries into compact text for the LLM prompt."""
    lines = []
    for e in entries:
        ts = e.get("timestamp", "")
        if ts and len(ts) > 19:
            ts = ts[11:19]  # HH:MM:SS

        tool = e.get("tool", e.get("msg", "?"))
        args = e.get("args", {})
        duration = e.get("duration_ms")
        intent = e.get("intent", "")

        # Strip noise from args
        clean_args = {}
        for k, v in args.items():
            if k in ("image_base64", "image", "screenshot"):
                continue
            if isinstance(v, str) and len(v) > 100:
                v = v[:97] + "..."
            clean_args[k] = v

        parts = [f"[{ts}]" if ts else ""]
        parts.append(tool)
        if clean_args:
            args_str = ", ".join(f"{k}={json.dumps(v)}" for k, v in clean_args.items())
            parts.append(f"({args_str})")
        if duration:
            parts.append(f"{duration}ms")
        if intent:
            parts.append(f"// {intent}")

        # Include error or result summary
        error = e.get("error")
        if error:
            parts.append(f"ERROR: {str(error)[:80]}")
        elif e.get("level") == "error":
            parts.append("ERROR")

        lines.append(" ".join(p for p in parts if p))

    return "\n".join(lines)


def _format_existing_facts(facts: list[dict]) -> str:
    """Format existing facts for the LLM context."""
    if not facts:
        return "(none)"
    lines = []
    for f in facts:
        lines.append(f"- {f.get('text', '')}")
    return "\n".join(lines)


def _read_session_log(desktop_id: str, session_id: str = "latest",
                      base_dir: Optional[Path] = None) -> tuple[list[dict], str]:
    """Read action log entries for a session.

    Returns (entries, actual_session_id).
    """
    store = get_store()
    logs_dir = store.knowledge_dir.parent / "logs"
    desktop_dir = logs_dir / desktop_id

    if session_id == "latest":
        # Read sessions.json to find latest
        index_file = desktop_dir / "sessions.json"
        if index_file.exists():
            with open(index_file) as f:
                sessions = json.load(f)
            if sessions:
                session_id = sessions[-1].get("session_id", "")

    # Try per-session file first
    if session_id and session_id != "latest":
        session_file = desktop_dir / f"{session_id}.jsonl"
        if session_file.exists():
            entries = _parse_jsonl(session_file)
            return entries, session_id

    # Fallback to flat file
    flat_file = logs_dir / f"{desktop_id}.jsonl"
    if flat_file.exists():
        entries = _parse_jsonl(flat_file, tail=500)
        sid = entries[-1].get("session_id", "flat") if entries else "flat"
        return entries, sid

    return [], ""


def _parse_jsonl(path: Path, tail: int = 0) -> list[dict]:
    """Parse JSONL file. If tail > 0, return only last N entries."""
    entries = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if tail > 0:
        entries = entries[-tail:]
    return entries


def compile_from_log(log_entries: list[dict],
                     task_description: str = "",
                     existing_facts: list[dict] = None) -> list[dict]:
    """Compile action log entries into candidate knowledge facts via LLM.

    Args:
        log_entries: Action log entries (from JSONL)
        task_description: Optional task context
        existing_facts: Existing facts to avoid duplicating

    Returns: List of candidate facts [{text, level, domain, triggers}]
    """
    if not log_entries:
        return []

    # Build user prompt
    log_text = _format_log_for_prompt(log_entries)
    existing_text = _format_existing_facts(existing_facts or [])

    user_parts = []
    if task_description:
        user_parts.append(f"## Task\n{task_description}\n")
    user_parts.append(f"## Existing knowledge (do not duplicate)\n{existing_text}\n")
    user_parts.append(f"## Action log ({len(log_entries)} actions)\n{log_text}")

    user_prompt = "\n".join(user_parts)

    # Call LLM
    response = chat_completion(SYSTEM_PROMPT, user_prompt, temperature=0.2, max_tokens=4096)

    # Parse JSON from response (strip markdown fences if present)
    text = response.strip()
    if text.startswith("```"):
        # Remove ```json ... ```
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        candidates = json.loads(text)
    except json.JSONDecodeError as e:
        log.error("Failed to parse LLM response as JSON: %s\nResponse: %s", e, text[:500])
        raise RuntimeError(f"LLM returned invalid JSON: {e}")

    if not isinstance(candidates, list):
        raise RuntimeError(f"LLM returned {type(candidates).__name__}, expected list")

    # Validate and normalize
    valid = []
    for c in candidates:
        if not isinstance(c, dict) or not c.get("text"):
            continue
        valid.append({
            "text": c["text"].strip(),
            "level": c.get("level", "app"),
            "domain": c.get("domain", c.get("domain_slug", "unknown")),
            "triggers": [t.lower().strip() for t in c.get("triggers", []) if t.strip()],
        })

    log.info("Compiled %d candidate facts from %d log entries", len(valid), len(log_entries))
    return valid


def preview_merge(candidates: list[dict],
                  target_slug: str,
                  target_kind: str = "app",
                  desktop_id: str = None) -> dict:
    """Compare candidate facts vs existing knowledge. Pure code, no LLM.

    Returns: {
        "new": [fact, ...],
        "updated": [{"old": fact, "new": fact}, ...],
        "unchanged": [fact, ...]
    }
    """
    store = get_store()
    existing_data = store._load(target_slug, target_kind, desktop_id)
    existing_facts = existing_data.get("facts", [])

    result = {"new": [], "updated": [], "unchanged": []}

    for candidate in candidates:
        cand_text = candidate.get("text", "").lower().strip()
        cand_triggers = set(candidate.get("triggers", []))

        # Check exact text match
        exact_match = False
        for ef in existing_facts:
            if ef.get("text", "").lower().strip() == cand_text:
                result["unchanged"].append(candidate)
                exact_match = True
                break

        if exact_match:
            continue

        # Check trigger overlap
        best_overlap = 0
        best_match = None
        for ef in existing_facts:
            ef_triggers = set(ef.get("triggers", []))
            if not cand_triggers or not ef_triggers:
                continue
            overlap = len(cand_triggers & ef_triggers) / len(cand_triggers)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = ef

        if best_overlap > 0.5 and best_match is not None:
            result["updated"].append({"old": best_match, "new": candidate})
        else:
            result["new"].append(candidate)

    return result


def apply_merge(diff: dict,
                target_slug: str,
                target_kind: str = "app",
                desktop_id: str = None) -> dict:
    """Apply merged facts to KnowledgeStore.

    Args:
        diff: Output from preview_merge()
        target_slug: Target knowledge slug
        target_kind: "app", "os", "flow"
        desktop_id: Desktop ID for per-desktop knowledge

    Returns: {new_count, updated_count, total}
    """
    store = get_store()
    data = store._load(target_slug, target_kind, desktop_id)
    facts = data.get("facts", [])
    now = datetime.now(timezone.utc).isoformat()

    new_count = 0
    updated_count = 0

    # Apply updates (replace old facts)
    for pair in diff.get("updated", []):
        old_text = pair["old"].get("text", "").lower().strip()
        new_fact = pair["new"]
        for i, ef in enumerate(facts):
            if ef.get("text", "").lower().strip() == old_text:
                facts[i] = {
                    "text": new_fact["text"],
                    "triggers": [t.lower().strip() for t in new_fact.get("triggers", [])],
                    "added": now,
                    "source": "compiled",
                }
                updated_count += 1
                break

    # Append new facts
    for new_fact in diff.get("new", []):
        facts.append({
            "text": new_fact["text"],
            "triggers": [t.lower().strip() for t in new_fact.get("triggers", [])],
            "added": now,
            "source": "compiled",
        })
        new_count += 1

    # Save
    if not data.get("name"):
        data["name"] = target_slug
    if not data.get("kind"):
        data["kind"] = target_kind
    if "tags" not in data:
        data["tags"] = []
    data["facts"] = facts
    store._save(target_slug, data, desktop_id)

    log.info("Applied merge to %s/%s: +%d new, ~%d updated, %d total",
             target_slug, target_kind, new_count, updated_count, len(facts))

    return {"new_count": new_count, "updated_count": updated_count, "total": len(facts)}
