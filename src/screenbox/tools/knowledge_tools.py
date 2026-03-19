"""desktop_add_knowledge and desktop_knowledge_search tools."""
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
