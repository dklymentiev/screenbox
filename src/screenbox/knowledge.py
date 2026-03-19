"""Screenbox Knowledge v2 -- typed knowledge with semantic cross-reference.

Four knowledge kinds:
  - app:  Application-specific (Pluma, Chrome, GIMP). Tagged with OS/version.
  - os:   System-level (Debian, Ubuntu). General desktop facts.
  - site: Website-specific (Reddit blocks, etc).
  - flow: System-agnostic workflows (web research, spreadsheet data entry).
          Flows have "references" to abstract app categories (browser, text-editor).
          At injection time, references resolve to actual apps via desktop_info().

Storage: ~/.screenbox/knowledge/
  {app-slug}.json           -- kind=app or kind=os
  _flows/{flow-name}.json   -- kind=flow
  _sites/{site-name}.json   -- kind=site

JSON format (v2):
{
  "name": "Chrome",
  "kind": "app",
  "tags": ["chrome", "chromium"],
  "references": [],           # only for flows
  "facts": [
    {
      "text": "Navigate: click address bar -> ctrl+a -> type URL -> Return",
      "triggers": ["navigate", "url", "address"],
      "added": "2026-03-14T06:00:00Z",
      "source": "agent-learned"
    }
  ]
}
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# App name -> slug mapping (window title patterns)
APP_PATTERNS = [
    (re.compile(r"LibreOffice Calc", re.I), "libreoffice-calc", "LibreOffice Calc"),
    (re.compile(r"LibreOffice Writer", re.I), "libreoffice-writer", "LibreOffice Writer"),
    (re.compile(r"LibreOffice Impress", re.I), "libreoffice-impress", "LibreOffice Impress"),
    (re.compile(r"LibreOffice", re.I), "libreoffice", "LibreOffice"),
    (re.compile(r"GIMP", re.I), "gimp", "GIMP"),
    (re.compile(r"Inkscape", re.I), "inkscape", "Inkscape"),
    (re.compile(r"Firefox", re.I), "firefox", "Firefox"),
    (re.compile(r"Chrom(e|ium)", re.I), "chrome", "Chrome"),
    (re.compile(r"Thunderbird", re.I), "thunderbird", "Thunderbird"),
    (re.compile(r"Visual Studio Code|VSCod", re.I), "vscode", "VS Code"),
    (re.compile(r"File Manager|Thunar|Nautilus|Nemo|PCManFM", re.I), "file-manager", "File Manager"),
    (re.compile(r"Terminal|xterm|xfce4-terminal|bash", re.I), "terminal", "Terminal"),
    (re.compile(r"Calculator|mate-calc|galculator|gnome-calculator", re.I), "mate-calc", "Calculator"),
    (re.compile(r"pluma|gedit|Text Editor", re.I), "pluma", "Text Editor"),
]

# Abstract app category -> APP_PATTERNS slugs that satisfy it
APP_CATEGORIES = {
    "browser": ["chrome", "firefox"],
    "text-editor": ["pluma", "vscode"],
    "spreadsheet": ["libreoffice-calc"],
    "terminal": ["terminal"],
    "file-manager": ["file-manager"],
    "image-editor": ["gimp", "inkscape"],
    "presentation": ["libreoffice-impress"],
    "word-processor": ["libreoffice-writer"],
}


def _slugify(name: str) -> str:
    """Convert app name to filesystem-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def detect_app(window_title: str) -> tuple[Optional[str], Optional[str]]:
    """Detect app from window title. Returns (slug, display_name) or (None, None)."""
    if not window_title:
        return None, None
    for pattern, slug, display_name in APP_PATTERNS:
        if pattern.search(window_title):
            return slug, display_name
    # Fallback: use first word(s) before " - " as app name
    if " - " in window_title:
        parts = window_title.rsplit(" - ", 1)
        if len(parts) == 2 and len(parts[1].strip()) > 2:
            name = parts[1].strip()
            return _slugify(name), name
    return None, None


def _resolve_category(category: str, desktop_info: dict) -> Optional[str]:
    """Resolve abstract category to actual app slug using desktop_info.

    E.g. "browser" -> "chrome" if Chromium is installed on this desktop.
    """
    apps = desktop_info.get("apps", {})
    if category in apps and apps[category]:
        return apps[category].get("slug")
    # Fallback: check APP_CATEGORIES for known slugs
    candidates = APP_CATEGORIES.get(category, [])
    for slug in candidates:
        # Check if any installed app matches
        for app_info in apps.values():
            if app_info and app_info.get("slug") == slug:
                return slug
    return None


class DesktopInfo:
    """Cached system info for a desktop container."""

    def __init__(self):
        self._cache: dict[str, dict] = {}  # desktop_id -> info dict

    def get(self, desktop_id: str, desktop) -> dict:
        """Get or probe desktop system info. Cached per desktop_id."""
        if desktop_id in self._cache:
            return self._cache[desktop_id]
        info = self._probe(desktop)
        self._cache[desktop_id] = info
        return info

    def invalidate(self, desktop_id: str):
        """Clear cache for a desktop."""
        self._cache.pop(desktop_id, None)

    def _probe(self, desktop) -> dict:
        """Probe desktop container for OS and installed apps."""
        info = {"os": {}, "apps": {}}
        try:
            # OS info
            os_result = desktop.shell("cat /etc/os-release 2>/dev/null", timeout=5)
            os_release = os_result.get("stdout", "") if isinstance(os_result, dict) else str(os_result)
            os_data = {}
            for line in os_release.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    os_data[k] = v.strip('"')
            info["os"] = {
                "name": os_data.get("ID", "unknown"),
                "version": os_data.get("VERSION_ID", ""),
                "pretty": os_data.get("PRETTY_NAME", ""),
            }

            # Installed apps -- probe common binaries
            app_probes = {
                "browser": [
                    ("chromium", "chromium --version 2>/dev/null || chromium-browser --version 2>/dev/null"),
                    ("firefox", "firefox --version 2>/dev/null"),
                ],
                "text-editor": [
                    ("pluma", "pluma --version 2>/dev/null"),
                    ("gedit", "gedit --version 2>/dev/null"),
                ],
                "spreadsheet": [
                    ("libreoffice-calc", "libreoffice --calc --version 2>/dev/null"),
                ],
                "terminal": [
                    ("mate-terminal", "mate-terminal --version 2>/dev/null"),
                    ("xfce4-terminal", "xfce4-terminal --version 2>/dev/null"),
                ],
                "image-editor": [
                    ("gimp", "gimp --version 2>/dev/null"),
                ],
                "file-manager": [
                    ("caja", "caja --version 2>/dev/null"),
                    ("thunar", "thunar --version 2>/dev/null"),
                ],
            }

            # Run all probes in one shell call for speed
            probe_cmds = []
            probe_map = []  # (category, slug, index)
            for category, probes in app_probes.items():
                for slug, cmd in probes:
                    probe_cmds.append(f"echo '###PROBE:{category}:{slug}###'; {cmd}; echo '###END###'")
                    probe_map.append((category, slug))

            probe_result = desktop.shell(" ; ".join(probe_cmds), timeout=15)
            result = probe_result.get("stdout", "") if isinstance(probe_result, dict) else str(probe_result)

            # Parse probe results
            sections = result.split("###PROBE:")
            for section in sections[1:]:  # skip empty first
                header, _, body = section.partition("###\n")
                body = body.split("###END###")[0].strip()
                if not body:
                    continue
                parts = header.split(":")
                if len(parts) >= 2:
                    category, slug = parts[0], parts[1]
                    # Extract version from output
                    version = ""
                    for line in body.split("\n"):
                        line = line.strip()
                        if line:
                            # Try to find version number pattern
                            m = re.search(r"(\d+\.\d+[\.\d]*)", line)
                            if m:
                                version = m.group(1)
                            break
                    if category not in info["apps"]:
                        info["apps"][category] = {"slug": slug, "version": version}

        except Exception as e:
            logger.debug("desktop_info probe failed: %s", e)

        return info


class KnowledgeStore:
    """Typed knowledge storage with flow support and cross-references."""

    def __init__(self, base_dir: str | Path | None = None):
        if base_dir:
            self.knowledge_dir = Path(base_dir) / "knowledge"
        else:
            self.knowledge_dir = Path.home() / ".screenbox" / "knowledge"
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}  # path_key -> loaded JSON
        self._active_apps: dict[str, str] = {}  # desktop_id -> last detected slug
        self._desktop_info = DesktopInfo()

    def _base_for(self, desktop_id: str = None) -> Path:
        """Get base knowledge directory. Per-desktop or global."""
        if desktop_id:
            d = self.knowledge_dir / "_desktops" / desktop_id
            d.mkdir(parents=True, exist_ok=True)
            return d
        return self.knowledge_dir

    def _path_for(self, slug: str, kind: str = "app", desktop_id: str = None) -> Path:
        """Get file path for a knowledge entry."""
        base = self._base_for(desktop_id)
        if kind == "flow":
            d = base / "_flows"
            d.mkdir(exist_ok=True)
            return d / f"{slug}.json"
        elif kind == "site":
            d = base / "_sites"
            d.mkdir(exist_ok=True)
            return d / f"{slug}.json"
        else:  # app, os
            return base / f"{slug}.json"

    def _load(self, slug: str, kind: str = "app", desktop_id: str = None) -> dict:
        """Load knowledge file. Desktop-specific first, then global fallback."""
        cache_key = f"{desktop_id or 'global'}:{kind}:{slug}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        # Try desktop-specific first
        if desktop_id:
            path = self._path_for(slug, kind, desktop_id)
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._cache[cache_key] = data
                    return data
                except (json.JSONDecodeError, OSError):
                    pass
        # Fallback to global
        path = self._path_for(slug, kind)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._cache[cache_key] = data
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"name": slug, "kind": kind, "tags": [], "facts": []}

    def _save(self, slug: str, data: dict, desktop_id: str = None):
        """Save knowledge file and update cache."""
        kind = data.get("kind", "app")
        path = self._path_for(slug, kind, desktop_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._cache[f"{desktop_id or 'global'}:{kind}:{slug}"] = data

    def _scan_dir(self, base: Path, scope: str = "global") -> list[dict]:
        """Scan a knowledge directory for entries."""
        entries = []
        if not base.is_dir():
            return entries
        # App-level knowledge (root .json files)
        for path in sorted(base.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append({
                    "slug": path.stem,
                    "name": data.get("name", data.get("app", path.stem)),
                    "kind": data.get("kind", "app"),
                    "facts": len(data.get("facts", [])),
                    "scope": scope,
                })
            except (json.JSONDecodeError, OSError):
                continue
        # Subdirectories (_flows, _sites)
        for subdir in ("_flows", "_sites"):
            sub = base / subdir
            if not sub.is_dir():
                continue
            kind = subdir.strip("_")
            if kind.endswith("s"):
                kind = kind[:-1]
            for path in sorted(sub.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    entries.append({
                        "slug": path.stem,
                        "name": data.get("name", path.stem),
                        "kind": data.get("kind", kind),
                        "facts": len(data.get("facts", [])),
                        "scope": scope,
                    })
                except (json.JSONDecodeError, OSError):
                    continue
        return entries

    def list_all(self, desktop_id: str = None) -> list[dict]:
        """List knowledge entries. With desktop_id: only desktop-specific entries."""
        if not desktop_id:
            return self._scan_dir(self.knowledge_dir)
        desktop_dir = self.knowledge_dir / "_desktops" / desktop_id
        return self._scan_dir(desktop_dir, scope=desktop_id)

    def list_desktops(self) -> list[str]:
        """List desktop IDs that have their own knowledge."""
        desktops_dir = self.knowledge_dir / "_desktops"
        if not desktops_dir.is_dir():
            return []
        return sorted(d.name for d in desktops_dir.iterdir() if d.is_dir())

    def _list_flows(self, desktop_id: str = None) -> list[dict]:
        """List all flow knowledge entries. Desktop-specific + global."""
        results = []
        seen_slugs = set()
        # Desktop-specific first
        if desktop_id:
            ddir = self.knowledge_dir / "_desktops" / desktop_id / "_flows"
            if ddir.exists():
                for path in ddir.glob("*.json"):
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        results.append(data)
                        seen_slugs.add(path.stem)
                    except (json.JSONDecodeError, OSError):
                        continue
        # Global
        flows_dir = self.knowledge_dir / "_flows"
        if flows_dir.exists():
            for path in flows_dir.glob("*.json"):
                if path.stem not in seen_slugs:
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        results.append(data)
                    except (json.JSONDecodeError, OSError):
                        continue
        return results

    def _list_sites(self, desktop_id: str = None) -> list[dict]:
        """List all site knowledge entries. Desktop-specific + global."""
        results = []
        seen_slugs = set()
        if desktop_id:
            ddir = self.knowledge_dir / "_desktops" / desktop_id / "_sites"
            if ddir.exists():
                for path in ddir.glob("*.json"):
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        results.append(data)
                        seen_slugs.add(path.stem)
                    except (json.JSONDecodeError, OSError):
                        continue
        sites_dir = self.knowledge_dir / "_sites"
        if sites_dir.exists():
            for path in sites_dir.glob("*.json"):
                if path.stem not in seen_slugs:
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        results.append(data)
                    except (json.JSONDecodeError, OSError):
                        continue
        return results

    def add_fact(self, slug: str, text: str, triggers: list[str],
                 kind: str = "app", name: str = "",
                 source: str = "agent-learned",
                 desktop_id: str = None) -> dict:
        """Add a knowledge fact. Returns the new fact."""
        data = self._load(slug, kind, desktop_id)
        if name:
            data["name"] = name
        data["kind"] = kind

        # Deduplicate
        text_lower = text.lower().strip()
        for existing in data.get("facts", []):
            if existing.get("text", "").lower().strip() == text_lower:
                return existing

        from datetime import datetime, timezone
        fact = {
            "text": text,
            "triggers": [t.lower().strip() for t in triggers if t.strip()],
            "added": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
        data.setdefault("facts", []).append(fact)
        self._save(slug, data, desktop_id)
        return fact

    def search(self, slug: str, context_words: list[str], kind: str = "app",
               limit: int = 5, desktop_id: str = None) -> list[dict]:
        """Search facts by trigger word overlap. Returns most relevant facts."""
        data = self._load(slug, kind, desktop_id)
        facts = data.get("facts", [])
        if not facts:
            return []

        context_set = {w.lower() for w in context_words if len(w) > 2}
        if not context_set:
            return facts[-limit:]

        scored = []
        for fact in facts:
            triggers = set(fact.get("triggers", []))
            overlap = len(triggers & context_set)
            text_lower = fact.get("text", "").lower()
            text_hits = sum(1 for w in context_set if w in text_lower)
            score = overlap * 2 + text_hits
            if score > 0:
                scored.append((score, fact))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:limit]]

    def search_flows(self, context_words: list[str], limit: int = 2,
                      desktop_id: str = None) -> list[dict]:
        """Find matching flows by trigger overlap.

        Returns list of flow dicts with 'score' added.
        """
        context_set = {w.lower() for w in context_words if len(w) > 2}
        if not context_set:
            return []

        scored = []
        for flow in self._list_flows(desktop_id):
            # Score flow by trigger overlap across all its facts + its own tags
            total_score = 0
            flow_tags = set(flow.get("tags", []))
            total_score += len(flow_tags & context_set) * 3  # tags weight more

            for fact in flow.get("facts", []):
                triggers = set(fact.get("triggers", []))
                total_score += len(triggers & context_set)

            if total_score > 0:
                scored.append((total_score, flow))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:limit]]

    def get_outline(self, slug: str, kind: str = "app", desktop_id: str = None) -> str:
        """Get compact outline of all knowledge for an entry."""
        data = self._load(slug, kind, desktop_id)
        facts = data.get("facts", [])
        if not facts:
            return ""
        name = data.get("name", slug)
        kind_label = data.get("kind", kind)
        lines = [f"[Knowledge: {name} -- {len(facts)} facts]"]
        for fact in facts[-10:]:
            text = fact.get("text", "")
            if len(text) > 100:
                text = text[:97] + "..."
            lines.append(f"  - {text}")
        if len(facts) > 10:
            lines.append(f"  ... and {len(facts) - 10} more (use desktop_search_knowledge to find specific)")
        return "\n".join(lines)

    def get_desktop_info(self, desktop_id: str, desktop) -> dict:
        """Get cached system info for a desktop."""
        return self._desktop_info.get(desktop_id, desktop)

    def get_hints_for_window(self, desktop_id: str, window_title: str,
                             desktop=None,
                             context_words: list[str] = None) -> Optional[str]:
        """Build knowledge hints: app knowledge + matching flows + resolved references.

        This is the main injection point called from screenshot/look responses.
        """
        parts = []

        # 1. App knowledge (from window title detection) -- always show outline
        slug, app_name = detect_app(window_title)
        if slug:
            self._active_apps[desktop_id] = slug
            outline = self.get_outline(slug, desktop_id=desktop_id)
            if outline:
                parts.append(outline)

        # 2. Flow knowledge (search all flows by context)
        flow_context = list(context_words) if context_words else []
        # Add window title words as extra context
        if window_title:
            flow_context.extend(window_title.lower().split())
        if slug:
            flow_context.append(slug)

        matching_flows = self.search_flows(flow_context, limit=1, desktop_id=desktop_id)
        for flow in matching_flows:
            flow_name = flow.get("name", "Unknown Flow")
            flow_facts = flow.get("facts", [])
            if flow_facts:
                lines = [f"[Flow: {flow_name} -- {len(flow_facts)} tips]"]
                for fact in flow_facts[:5]:
                    text = fact.get("text", "")
                    if len(text) > 120:
                        text = text[:117] + "..."
                    lines.append(f"  - {text}")
                parts.append("\n".join(lines))

            # 3. Resolve flow references to app knowledge
            references = flow.get("references", [])
            if references and desktop:
                info = self._desktop_info.get(desktop_id, desktop)
                for ref_category in references:
                    ref_slug = _resolve_category(ref_category, info)
                    if ref_slug and ref_slug != slug:  # Don't duplicate active app
                        ref_data = self._load(ref_slug, desktop_id=desktop_id)
                        ref_facts = ref_data.get("facts", [])
                        if ref_facts:
                            ref_name = ref_data.get("name", ref_slug)
                            # Get version info
                            app_info = info.get("apps", {}).get(ref_category, {})
                            version = app_info.get("version", "") if app_info else ""
                            os_name = info.get("os", {}).get("name", "")
                            label = ref_name
                            if version:
                                label += f" {version}"
                            if os_name:
                                label += f" on {os_name}"
                            # Pick most relevant facts for this flow
                            relevant = self.search(ref_slug, flow_context, limit=2, desktop_id=desktop_id)
                            if relevant:
                                lines = [f"[Referenced: {label} -- {len(relevant)} relevant]"]
                                for fact in relevant:
                                    text = fact.get("text", "")
                                    if len(text) > 120:
                                        text = text[:117] + "..."
                                    lines.append(f"  - {text}")
                                parts.append("\n".join(lines))

        if not parts:
            return None
        return "\n\n".join(parts)

    def get_active_app(self, desktop_id: str) -> Optional[str]:
        """Get last detected app slug for a desktop."""
        return self._active_apps.get(desktop_id)


# Global singleton
_store: Optional[KnowledgeStore] = None


def get_store(base_dir=None) -> KnowledgeStore:
    """Get or create the global KnowledgeStore."""
    global _store
    if _store is None:
        if not base_dir:
            base_dir = os.environ.get("SCREENBOX_BASE_DIR")
        _store = KnowledgeStore(base_dir)
    return _store
