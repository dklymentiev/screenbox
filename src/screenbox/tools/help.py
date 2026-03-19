"""desktop_help tool."""
import json


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_help() -> str:
        """Screenbox interaction strategy guide. Call when unsure about workflow.

        Returns the recommended interaction patterns and anti-patterns.
        """
        return json.dumps({
            "strategy": "Use the HIGHEST-LEVEL method available. Escalate only on failure.",
            "priority": [
                {"level": 1, "method": "OS API",
                 "when": "Window management (minimize, maximize, activate, close, resize)",
                 "tools": ["desktop_window(action=activate/minimize/maximize/close/resize)"],
                 "why": "Instant, reliable, no coordinates needed"},
                {"level": 2, "method": "Keyboard",
                 "when": "UI interaction: menus, dialogs, form fields, navigation",
                 "tools": ["desktop_key", "desktop_type"],
                 "examples": ["Tab/Shift+Tab between fields", "Enter to confirm", "Escape to cancel",
                              "Alt+F4 to close", "Ctrl+S to save"],
                 "why": "Faster and more reliable than clicking"},
                {"level": 3, "method": "Look + Click",
                 "when": "Buttons, icons, links -- elements that need precise coordinates",
                 "tools": ["desktop_look", "desktop_click"],
                 "flow": "desktop_screenshot -> desktop_look(cell=N) -> desktop_click(x, y)",
                 "why": "Accurate coordinates from OCR, no guessing"},
                {"level": 4, "method": "Chrome semantics",
                 "when": "Web page elements in Chrome",
                 "tools": ["desktop_chrome(action=page_map/eval/click)"],
                 "why": "DOM-level precision for web content"},
            ],
            "batch": "Use desktop_batch when confident in a sequence (type+Tab+type+Enter etc.)",
            "anti_patterns": [
                "NEVER click without desktop_look first",
                "NEVER guess coordinates from full screenshot",
                "NEVER click title bar buttons -- use desktop_window",
                "NEVER hover twice in menus -- second hover kills submenu",
                "NEVER loop >2 times on wrong coordinates -- escalate or ask user",
            ],
        }, indent=2)
