"""desktop_chrome dispatcher tool."""
import json
import time
from mcp.server.fastmcp import Image


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_chrome(desktop_id: str, action: str,
                       url: str = "", script: str = "", selector: str = "",
                       tab_id: int = 0, timeout_ms: int = 10000,
                       text: str = "", cookies_json: str = "",
                       condition: str = "",
                       ignore: bool = True, clear: bool = False,
                       width: int = 0, height: int = 0,
                       scale: float = 1.0, mobile: bool = False,
                       user_agent: str = "",
                       latitude: float = 0.0, longitude: float = 0.0,
                       intent: str = "",
                       step: str = ""):
        """Chrome browser operations.

        PRIORITY METHODS (use these first):
          page_map       -- semantic structure: headings, links, forms (like screenshot for browser)
          page_read      -- full main content as clean text, no ads (limit ~15k chars)
          view_read      -- text of visible viewport + scroll indicator
          cursor_read    -- innerText of element under mouse cursor (move mouse first)

        Other actions:
          navigate(url) | eval(script) | content(selector?) | tabs | new_tab(url?)
          close_tab(tab_id?) | switch_tab(tab_id) | back | forward
          wait_for(condition, timeout_ms?) | screenshot | search(text)
          extract | dom | page_info | cookies(url?) | set_cookies(cookies_json)
          clear_cookies(url?) | pdf | performance | console_start | console_stop
          console_get(clear?) | ready | ssl_errors(ignore?) | network
          click(selector) | type(selector, text, clear?) | emulate(...) | geolocation(...)

        Args:
            desktop_id: Desktop with Chrome browser
            action: Action name from list above
            url: URL for navigate/new_tab/cookies/clear_cookies
            script: JavaScript for eval
            selector: CSS selector for content/click/type
            tab_id: Tab ID for close_tab/switch_tab
            timeout_ms: Timeout for wait_for (default 10000)
            text: Text for search/type
            cookies_json: JSON array for set_cookies
            condition: For wait_for ("url:...", "title:...", "selector:...")
            ignore: For ssl_errors (default True)
            clear: For type (clear field) or console_get (clear after)
            width: For emulate
            height: For emulate
            scale: Device scale for emulate
            mobile: Mobile flag for emulate
            user_agent: UA string for emulate
            latitude: For geolocation
            longitude: For geolocation
        """
        t0 = time.time()
        d = get_desktop(desktop_id)

        b = d.browser

        # Priority methods
        if action == "page_map":
            result = b.page_map()
        elif action == "page_read":
            result = b.page_read()
        elif action == "view_read":
            result = b.view_read()
        elif action == "cursor_read":
            result = b.cursor_read()
        # Navigation
        elif action == "navigate":
            result = b.navigate(url)
        elif action == "eval":
            result = b.execute_js(script)
        elif action == "content":
            result = b.get_content(selector or None)
        elif action == "tabs":
            result = b.get_tabs()
        elif action == "new_tab":
            result = b.new_tab(url or "about:blank")
        elif action == "close_tab":
            result = b.close_tab(tab_id or None)
        elif action == "switch_tab":
            result = b.switch_tab(tab_id)
        elif action == "back":
            result = b.go_back()
        elif action == "forward":
            result = b.go_forward()
        elif action == "wait_for":
            result = b.wait_for(condition, timeout_ms)
        elif action == "screenshot":
            data = b.browser_screenshot()
            if not data:
                return json.dumps({"error": "Browser screenshot failed"})
            log_action(desktop_id, "desktop_chrome",
                       {"action": "screenshot"}, f"[{len(data)} bytes]", t0,
                       intent=intent, step=step)
            return Image(data=data, format="jpeg")
        elif action == "search":
            result = b.chrome_search(text)
        elif action == "semantics":
            result = b.page_map()
        elif action == "extract":
            result = b.cursor_read()
        elif action == "dom":
            result = b.dom_snapshot()
        elif action == "page_info":
            result = b.get_page_info()
        elif action == "cookies":
            result = b.get_cookies(url or None)
        elif action == "set_cookies":
            cookies = json.loads(cookies_json) if cookies_json else []
            result = b.set_cookies(cookies)
        elif action == "clear_cookies":
            result = b.clear_cookies(url or None)
        elif action == "pdf":
            data = b.save_pdf()
            if not data:
                return json.dumps({"error": "PDF generation failed"})
            import base64 as b64
            result = {"pdf_base64": b64.b64encode(data).decode("ascii"),
                      "size": len(data)}
        elif action == "performance":
            result = b.performance_metrics()
        elif action == "console_start":
            result = b.console_start()
        elif action == "console_stop":
            result = b.console_stop()
        elif action == "console_get":
            result = b.console_get(clear)
        elif action == "ready":
            result = b.chrome_ready()
        elif action == "ssl_errors":
            result = b.ignore_ssl_errors(ignore)
        elif action == "network":
            result = b.network_get()
        elif action == "click":
            result = b.ext_click(selector)
        elif action == "type":
            result = b.ext_type(selector, text, clear)
        elif action == "emulate":
            result = b.emulate_device(width, height, scale, mobile,
                                      user_agent or None)
        elif action == "geolocation":
            result = b.set_geolocation(latitude, longitude)
        else:
            return json.dumps({"error": f"Unknown chrome action: {action}",
                               "available": [
                                   "page_map", "page_read", "view_read", "cursor_read",
                                   "navigate", "eval", "content", "tabs",
                                   "new_tab", "close_tab", "switch_tab",
                                   "back", "forward", "wait_for", "screenshot",
                                   "search", "semantics", "extract", "dom", "page_info",
                                   "cookies", "set_cookies", "clear_cookies",
                                   "pdf", "performance", "console_start",
                                   "console_stop", "console_get", "ready",
                                   "ssl_errors", "network", "click", "type",
                                   "emulate", "geolocation"]})

        log_action(desktop_id, "desktop_chrome", {"action": action}, result, t0, intent=intent, step=step)
        return json.dumps(result, indent=2)
