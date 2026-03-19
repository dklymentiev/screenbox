"""Screenbox MCP tools -- 17 tools organized by function."""


import logging

_logger = logging.getLogger(__name__)


def get_window_title(desktop) -> str:
    """Get active window title from desktop container."""
    try:
        return desktop._xdotool("getactivewindow", "getwindowname").strip()
    except Exception:
        return ""


def inject_knowledge(desktop_id: str, desktop, context_words: list[str] = None) -> str:
    """Get knowledge hints for current active app. Returns hint string or empty."""
    try:
        from ..knowledge import get_store
        title = get_window_title(desktop)
        if not title:
            return ""
        store = get_store()
        hints = store.get_hints_for_window(
            desktop_id, title, desktop=desktop, context_words=context_words
        )
        return hints or ""
    except Exception as e:
        _logger.debug("Knowledge injection failed: %s", e)
        return ""


def register_all(mcp, get_desktop, get_manager, log_action):
    """Register all MCP tools on the FastMCP instance."""
    from ..catalog import APP_CATALOG

    from .screenshot import register as reg_screenshot
    from .look import register as reg_look
    from .click import register as reg_click
    from .input import register as reg_input
    from .batch import register as reg_batch
    from .help import register as reg_help
    from .chrome import register as reg_chrome
    from .window import register as reg_window
    from .find import register as reg_find
    from .file import register as reg_file
    from .manage import register as reg_manage
    from .knowledge_tools import register as reg_knowledge
    from .info import register as reg_info
    from .watch import register as reg_watch
    from ..beta.replay.tool import register as reg_replay

    for reg in [reg_screenshot, reg_look, reg_click, reg_input, reg_batch,
                reg_help, reg_chrome, reg_window, reg_find, reg_file,
                reg_manage, reg_knowledge, reg_info, reg_watch, reg_replay]:
        reg(mcp, get_desktop, get_manager, log_action, APP_CATALOG)
