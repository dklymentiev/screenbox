"""Knowledge tools: add, search, compile, merge."""
import json
import time

from ..knowledge import get_store, detect_app


from . import get_window_title as _get_window_title


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_add_knowledge(desktop_id: str, app: str = "", fact: str = "",
                              triggers: str = "", kind: str = "",
                              intent: str = "", step: str = "") -> str:
        """Save a knowledge fact about an application for future sessions.

        When you discover something useful (shortcut, behavior, workaround),
        save it so future agents benefit automatically.

        Args:
            desktop_id: Desktop where the app is running
            app: App name (e.g. "LibreOffice Calc"). Auto-detected from active window if empty.
            fact: The knowledge to save (e.g. "Alt+Y confirms Yes in dialogs")
            triggers: Comma-separated trigger words for search (e.g. "dialog,confirmation,yes")
            kind: Knowledge type: "app" (default), "os", "site", "flow"
        """
        t0 = time.time()
        store = get_store()

        # Auto-detect app from active window if not specified
        app_slug = None
        app_name = app
        if not app:
            d = get_desktop(desktop_id)
            title = _get_window_title(d)
            if title:
                app_slug, app_name = detect_app(title)
        if not app_slug:
            from ..knowledge import _slugify
            app_slug = _slugify(app_name or "unknown")

        fact_kind = kind if kind in ("app", "os", "site", "flow") else "app"
        trigger_list = [t.strip() for t in triggers.split(",") if t.strip()]
        result = store.add_fact(app_slug, fact, trigger_list,
                                kind=fact_kind, name=app_name or "",
                                desktop_id=desktop_id)

        log_action(desktop_id, "desktop_add_knowledge",
                   {"app": app_slug, "kind": fact_kind, "fact": fact[:100]}, "saved", t0,
                   intent=intent, step=step)
        return json.dumps({
            "saved": True,
            "app": app_name or app_slug,
            "kind": fact_kind,
            "fact": result.get("text", fact),
            "total_facts": len(store._load(app_slug, fact_kind, desktop_id).get("facts", [])),
        })

    @mcp.tool()
    def desktop_knowledge_search(desktop_id: str, query: str = "",
                                 app: str = "", kind: str = "",
                                 intent: str = "", step: str = "") -> str:
        """Search or list knowledge base.

        Without parameters: returns full list of all available knowledge files.
        With query: searches facts by trigger words.
        With app: searches within a specific app.
        With kind: filters by type (app, os, site, flow).

        Examples:
            desktop_knowledge_search("desk-1")                          -- list all
            desktop_knowledge_search("desk-1", query="dialog confirm")  -- search
            desktop_knowledge_search("desk-1", kind="flow")             -- list flows
            desktop_knowledge_search("desk-1", app="chrome")            -- search chrome facts

        Args:
            desktop_id: Desktop
            query: Search terms. Empty = list all available knowledge.
            app: App name to search in. Auto-detected from active window if empty.
            kind: Knowledge type: "app", "os", "site", "flow". Empty = all types.
        """
        t0 = time.time()
        store = get_store()

        # No query, no app, no kind = return ALL knowledge with facts
        if not query and not app and not kind:
            entries = store.list_all(desktop_id)
            if not entries:
                # Fallback to global knowledge
                entries = store.list_all()
            log_action(desktop_id, "desktop_knowledge_search",
                       {"mode": "list_all"}, {"count": len(entries)}, t0,
                       intent=intent, step=step)
            if not entries:
                return json.dumps({"entries": [], "message": "No knowledge files found. Use desktop_add_knowledge to create."})
            # Include fact texts so agent can read content in one call
            detailed = []
            for e in entries:
                data = store._load(e["slug"], e.get("kind", "app"), desktop_id)
                detailed.append({
                    "slug": e["slug"],
                    "name": data.get("name", e["slug"]),
                    "kind": e.get("kind", "app"),
                    "facts": [f["text"] for f in data.get("facts", [])],
                })
            return json.dumps({"entries": detailed, "total": len(detailed)}, indent=2)

        # Kind-only = list entries of that kind
        fact_kind = kind if kind in ("app", "os", "site", "flow") else ""

        if fact_kind and not query and not app:
            entries = [e for e in store.list_all(desktop_id) if e["kind"] == fact_kind]
            # For flows: include fact texts so agent can read the scenario
            if fact_kind == "flow" and entries:
                detailed = []
                for e in entries:
                    data = store._load(e["slug"], "flow", desktop_id)
                    detailed.append({
                        "name": data.get("name", e["slug"]),
                        "facts": [f["text"] for f in data.get("facts", [])],
                        "references": data.get("references", []),
                    })
                log_action(desktop_id, "desktop_knowledge_search",
                           {"mode": "list", "kind": fact_kind}, {"count": len(entries)}, t0,
                           intent=intent, step=step)
                return json.dumps({"kind": "flow", "results": detailed}, indent=2)
            log_action(desktop_id, "desktop_knowledge_search",
                       {"mode": "list", "kind": fact_kind}, {"count": len(entries)}, t0,
                       intent=intent, step=step)
            if not entries:
                return json.dumps({"entries": [], "message": f"No {fact_kind} knowledge found."})
            return json.dumps({"entries": entries, "kind": fact_kind, "total": len(entries)}, indent=2)

        if not fact_kind:
            fact_kind = "app"

        query_words = query.lower().split() if query else []

        # Flow search
        if fact_kind == "flow":
            results = store.search_flows(query_words, limit=5, desktop_id=desktop_id)
            log_action(desktop_id, "desktop_knowledge_search",
                       {"kind": "flow", "query": query}, {"results": len(results)}, t0,
                       intent=intent, step=step)
            if not results:
                return json.dumps({"results": [], "message": "No matching flows found"})
            return json.dumps({
                "kind": "flow",
                "results": [{
                    "name": f.get("name", ""),
                    "facts": [fact["text"] for fact in f.get("facts", [])[:5]],
                    "references": f.get("references", []),
                } for f in results],
            }, indent=2)

        # App search
        app_slug = None
        if not app:
            d = get_desktop(desktop_id)
            title = _get_window_title(d)
            if title:
                app_slug, _ = detect_app(title)
        if not app_slug:
            from ..knowledge import _slugify
            app_slug = _slugify(app) if app else None

        if not app_slug:
            return json.dumps({"error": "Cannot detect app. Specify app parameter."})

        results = store.search(app_slug, query_words, kind=fact_kind, limit=5, desktop_id=desktop_id)

        log_action(desktop_id, "desktop_knowledge_search",
                   {"app": app_slug, "kind": fact_kind, "query": query},
                   {"results": len(results)}, t0,
                   intent=intent, step=step)

        if not results:
            return json.dumps({"results": [], "message": f"No knowledge found for {app_slug}"})

        return json.dumps({
            "app": app_slug,
            "kind": fact_kind,
            "results": [{"text": f["text"], "triggers": f.get("triggers", [])} for f in results],
        }, indent=2)

    @mcp.tool()
    def desktop_compile_knowledge(desktop_id: str, session_id: str = "latest",
                                  task: str = "",
                                  intent: str = "", step: str = "") -> str:
        """Compile action logs into knowledge facts using LLM.

        Reads session action log, sends to LLM for analysis, returns
        candidate facts ready for review and merge.

        Args:
            desktop_id: Desktop whose logs to compile
            session_id: Session ID ("latest" = most recent session)
            task: Optional task description for better context
        """
        t0 = time.time()
        from ..llm_client import is_configured
        if not is_configured():
            return json.dumps({"error": "LLM not configured. Set SCREENBOX_LLM_KEY env var."})

        from ..knowledge_compiler import compile_from_log, _read_session_log

        # Read session log
        entries, actual_sid = _read_session_log(desktop_id, session_id)
        if not entries:
            return json.dumps({"error": f"No log entries found for {desktop_id}/{session_id}"})

        # Gather existing facts for context
        store = get_store()
        all_entries = store.list_all(desktop_id)
        existing = []
        for e in all_entries:
            data = store._load(e["slug"], e.get("kind", "app"), desktop_id)
            existing.extend(data.get("facts", []))

        # Compile
        try:
            candidates = compile_from_log(entries, task, existing)
        except RuntimeError as e:
            log_action(desktop_id, "desktop_compile_knowledge",
                       {"session_id": actual_sid, "error": str(e)}, "error", t0,
                       intent=intent, step=step)
            return json.dumps({"error": str(e)})

        # Suggest target from most common domain
        domains = {}
        for c in candidates:
            d = c.get("domain", "unknown")
            domains[d] = domains.get(d, 0) + 1
        suggested = max(domains, key=domains.get) if domains else "unknown"

        log_action(desktop_id, "desktop_compile_knowledge",
                   {"session_id": actual_sid, "task": task[:100] if task else ""},
                   {"candidates": len(candidates)}, t0,
                   intent=intent, step=step)

        return json.dumps({
            "candidates": candidates,
            "suggested_target": {"slug": suggested, "kind": "app"},
            "session_id": actual_sid,
            "log_entries_analyzed": len(entries),
        }, indent=2)

    @mcp.tool()
    def desktop_merge_knowledge(desktop_id: str, facts: str = "",
                                target: str = "", kind: str = "app",
                                mode: str = "preview",
                                intent: str = "", step: str = "") -> str:
        """Preview or apply merge of compiled knowledge facts.

        Args:
            desktop_id: Target desktop for knowledge storage
            facts: JSON array of fact objects (from desktop_compile_knowledge)
            target: Target slug (e.g. "libreoffice-calc", "chrome")
            kind: Knowledge kind: "app", "os", "flow"
            mode: "preview" = show diff, "apply" = save changes
        """
        t0 = time.time()
        if not facts or not target:
            return json.dumps({"error": "facts and target are required"})

        try:
            fact_list = json.loads(facts) if isinstance(facts, str) else facts
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid facts JSON: {e}"})

        from ..knowledge_compiler import preview_merge, apply_merge

        diff = preview_merge(fact_list, target, kind, desktop_id)

        if mode == "apply":
            result = apply_merge(diff, target, kind, desktop_id)
            log_action(desktop_id, "desktop_merge_knowledge",
                       {"target": target, "kind": kind, "mode": "apply"},
                       result, t0, intent=intent, step=step)
            return json.dumps({"applied": True, **result}, indent=2)

        # Preview mode
        log_action(desktop_id, "desktop_merge_knowledge",
                   {"target": target, "kind": kind, "mode": "preview"},
                   {"new": len(diff["new"]), "updated": len(diff["updated"]),
                    "unchanged": len(diff["unchanged"])}, t0,
                   intent=intent, step=step)
        return json.dumps({"mode": "preview", "diff": diff}, indent=2)
