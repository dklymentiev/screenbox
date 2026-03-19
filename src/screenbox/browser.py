"""Browser interaction via Chrome extension and CDP.

Handles all browser-specific logic: navigation, tabs, content extraction,
page semantics, CDP commands (PDF, DOM, performance, emulation).

The BrowserClient wraps ExtensionClient (WebSocket to ws-bridge) and provides
high-level methods. It receives a reference to the parent Desktop for exec access.
"""

import base64
import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .desktop import Desktop

log = logging.getLogger("screenbox.browser")


class BrowserClient:
    """Browser interaction for a Screenbox desktop.

    Provides high-level browser methods grouped by purpose:
    - Navigation: navigate, go_back, go_forward
    - Tabs: get_tabs, new_tab, close_tab, switch_tab
    - Content: page_map, page_read, view_read, cursor_read
    - DOM interaction: ext_click, ext_type, get_content, chrome_search
    - Cookies: get_cookies, set_cookies, clear_cookies
    - CDP: save_pdf, dom_snapshot, performance, emulate, geolocation, ssl
    - DevTools: console_start/stop/get, network_get, browser_screenshot
    - Health: chrome_ready, health_check
    """

    def __init__(self, desktop: "Desktop"):
        self._d = desktop

    # -- Shortcuts to desktop internals --

    def _ext_cmd(self, command: str, params: dict = None, timeout: int = 10) -> dict:
        return self._d._ext_cmd(command, params, timeout)

    def _exec(self, cmd: list[str], timeout: int = 10) -> str:
        return self._d._exec(cmd, timeout)

    def _exec_bytes(self, cmd: list[str], timeout: int = 10) -> bytes:
        return self._d._exec_bytes(cmd, timeout)

    def _xdotool(self, *args: str) -> str:
        return self._d._xdotool(*args)

    def _log(self, tool: str, args: dict, **kwargs):
        return self._d._log(tool, args, **kwargs)

    # ================================================================
    # Navigation
    # ================================================================

    def navigate(self, url: str) -> dict:
        """Navigate Chrome to URL via extension."""
        t0 = time.time()
        try:
            ws_t = time.time()
            result = self._ext_cmd("navigate", {"url": url}, timeout=10)
            self._d._last_ws_ms = int((time.time() - ws_t) * 1000)
            self._log("navigate", {"url": url}, result=result, start_time=t0)
            return result
        except Exception as e:
            log.warning("Extension navigate failed (%s), falling back to xdotool", e)
            # Check browser is actually running before xdotool fallback
            check = self._d.shell("pgrep -x chromium || pgrep -x chrome", timeout=3)
            if not (check.get("stdout") or "").strip():
                self._log("navigate", {"url": url},
                          error="Browser is not running", start_time=t0)
                return {"success": False, "url": url,
                        "error": "Browser is not running. "
                        "Launch it: desktop_manage(action='app_launch', app='chrome')"}
            self._log("navigate", {"url": url},
                      error=f"extension failed: {e}, falling back to xdotool",
                      start_time=t0)
            from .desktop import DISPLAY
            self._xdotool("key", "ctrl+l")
            self._exec(["sleep", "0.3"])
            self._xdotool("key", "ctrl+a")
            cmd = ["env", "DISPLAY=" + DISPLAY, "xdotool", "type",
                   "--clearmodifiers", "--delay", "0", url]
            self._exec(cmd)
            self._xdotool("key", "Return")
            return {"success": True, "url": url, "method": "xdotool",
                    "warning": "Used xdotool fallback (extension unavailable). "
                    "Chrome semantics (page_map, type by selector) may not work."}

    def go_back(self) -> dict:
        """Navigate back in browser history."""
        return self.execute_js("history.back(); 'ok'")

    def go_forward(self) -> dict:
        """Navigate forward in browser history."""
        return self.execute_js("history.forward(); 'ok'")

    def wait_for(self, condition: str, timeout_ms: int = 10000) -> dict:
        """Wait for browser condition (url:, title:, selector:)."""
        try:
            return self._ext_cmd("waitFor", {
                "condition": condition, "timeout": timeout_ms
            }, timeout=max(15, timeout_ms // 1000 + 5))
        except Exception as e:
            return {"error": str(e)}

    # ================================================================
    # Tabs
    # ================================================================

    def get_tabs(self) -> dict:
        """Get list of browser tabs via extension."""
        try:
            return self._ext_cmd("getTabs")
        except Exception as e:
            return {"error": f"getTabs failed: {str(e)}"}

    def new_tab(self, url: str = "about:blank") -> dict:
        """Open a new browser tab."""
        try:
            return self._ext_cmd("newTab", {"url": url})
        except Exception as e:
            self._xdotool("key", "ctrl+t")
            if url != "about:blank":
                self._exec(["sleep", "0.5"])
                return self.navigate(url)
            return {"method": "xdotool"}

    def close_tab(self, tab_id: int = None) -> dict:
        """Close a browser tab."""
        if tab_id is not None:
            try:
                return self._ext_cmd("closeTab", {"tabId": tab_id})
            except Exception:
                pass
        self._xdotool("key", "ctrl+w")
        return {"method": "xdotool"}

    def switch_tab(self, tab_id: int) -> dict:
        """Switch to a specific browser tab."""
        try:
            return self._ext_cmd("switchTab", {"tabId": tab_id})
        except Exception as e:
            return {"error": f"switchTab failed: {str(e)}"}

    # ================================================================
    # Content Perception (the 4 primary methods)
    # ================================================================

    def page_map(self) -> dict:
        """Get semantic structure of the page.

        Returns headings, sections, links, forms, interactive elements.
        This is the browser equivalent of desktop_screenshot -- the first
        thing an agent should call when opening a page.
        """
        t0 = time.time()
        try:
            # Fast check: empty pages have nothing to map
            try:
                info = self._ext_cmd("getPageInfo", timeout=3)
                url = info.get("url", "") if isinstance(info, dict) else ""
                if not url or url in ("about:blank", "chrome://newtab/", "about:newtab"):
                    self._log("page_map", {}, result={"elements": 0, "skipped": "empty_page"}, start_time=t0)
                    return {"e": [], "n": 0, "t": "", "u": url, "v": [0, 0],
                            "note": "Page is empty or blank. Navigate to a URL first."}
            except Exception:
                pass  # getPageInfo failed, try getSemantics anyway
            ws_t = time.time()
            result = self._ext_cmd("getSemantics", {"compact": True})
            self._d._last_ws_ms = int((time.time() - ws_t) * 1000)
            n = result.get("n", "?")
            self._log("page_map", {}, result={"elements": n}, start_time=t0)
            return result
        except Exception as e:
            import traceback
            log.error("page_map failed: %s\n%s", e, traceback.format_exc())
            self._log("page_map", {}, error=str(e), start_time=t0)
            return {"error": f"page_map failed: {str(e)}"}

    def page_read(self, max_chars: int = 15000) -> dict:
        """Get full main content of the page as clean text.

        Auto-finds <article>, <main>, or largest content block.
        Strips ads, navigation, footers. Returns clean readable text.
        """
        t0 = time.time()
        # Guard: skip extension call for empty/internal pages
        try:
            tab_json = self._exec(
                ["curl", "-s", "--max-time", "1", "http://localhost:9222/json"], timeout=2)
            import json as _json
            tabs = _json.loads(tab_json) if tab_json.strip() else []
            active_url = tabs[0].get("url", "") if tabs else ""
            if active_url in ("about:blank", "") or active_url.startswith("chrome://"):
                result = {"chars": 0, "selector": "body", "text": "", "title": "", "url": active_url}
                self._log("page_read", {"max_chars": max_chars, "skipped": "empty_page"}, start_time=t0)
                return result
        except Exception:
            pass  # Proceed with normal page_read if guard check fails
        try:
            result = self._ext_cmd("pageRead", {"maxChars": max_chars}, timeout=15)
            self._log("page_read", {"max_chars": max_chars}, start_time=t0)
            return result
        except Exception as e:
            self._log("page_read", {}, error=str(e), start_time=t0)
            return {"error": f"page_read failed: {str(e)}"}

    def view_read(self) -> dict:
        """Get text of currently visible viewport area.

        Returns only what's visible on screen right now, plus scroll indicator:
        {text: "...", scroll: "down", remaining: "~3 screens"} or
        {text: "...", scroll: "none"}

        Agent reads page like a human: view_read -> scroll -> view_read.
        """
        t0 = time.time()
        try:
            result = self._ext_cmd("viewRead", {}, timeout=10)
            self._log("view_read", {}, start_time=t0)
            return result
        except Exception as e:
            self._log("view_read", {}, error=str(e), start_time=t0)
            return {"error": f"view_read failed: {str(e)}"}

    def cursor_read(self) -> dict:
        """Get innerText of DOM element under current mouse cursor.

        Move mouse to area of interest first, then call cursor_read.
        Walks up DOM to find meaningful content block (article, section, div).
        Returns clean text without needing CSS selectors. Max ~5000 chars.

        All coordinate translation (screen -> viewport) is handled internally.
        """
        t0 = time.time()
        try:
            cx = self._d.get_cursor_position().get("X", 0)
            cy = self._d.get_cursor_position().get("Y", 0)
            bounds = self._ext_cmd("getWindowBounds")
            win = bounds.get("window", {})
            vp = bounds.get("viewport", {})
            vp_y_offset = win.get("height", 0) - vp.get("height", 0)
            vp_x = cx - win.get("left", 0)
            vp_y = cy - win.get("top", 0) - vp_y_offset
            result = self._ext_cmd("extract", {"x": vp_x, "y": vp_y})
            self._log("cursor_read", {"screen_x": cx, "screen_y": cy,
                                       "vp_x": vp_x, "vp_y": vp_y}, start_time=t0)
            return result
        except Exception as e:
            self._log("cursor_read", {}, error=str(e), start_time=t0)
            return {"error": f"cursor_read failed: {str(e)}"}

    # ================================================================
    # DOM interaction (via extension)
    # ================================================================

    def ext_click(self, selector: str) -> dict:
        """Click element by CSS selector via extension."""
        t0 = time.time()
        try:
            ws_t = time.time()
            result = self._ext_cmd("click", {"selector": selector})
            self._d._last_ws_ms = int((time.time() - ws_t) * 1000)
            self._log("ext_click", {"selector": selector}, start_time=t0)
            return result
        except Exception as e:
            self._log("ext_click", {"selector": selector}, error=str(e), start_time=t0)
            return {"error": f"Extension click failed: {str(e)}"}

    def ext_type(self, selector: str, text: str, clear: bool = False) -> dict:
        """Type text into element by CSS selector via extension."""
        try:
            return self._ext_cmd("type", {
                "selector": selector, "text": text, "clear": clear
            })
        except Exception as e:
            return {"error": f"Extension type failed: {str(e)}"}

    def get_content(self, selector: str = None) -> dict:
        """Get text content of element or page via extension."""
        t0 = time.time()
        try:
            params = {}
            if selector:
                params["selector"] = selector
            ws_t = time.time()
            result = self._ext_cmd("getContent", params)
            self._d._last_ws_ms = int((time.time() - ws_t) * 1000)
            self._log("get_content", {"selector": selector}, start_time=t0)
            return result
        except Exception as e:
            self._log("get_content", {"selector": selector}, error=str(e), start_time=t0)
            return {"error": f"getContent failed: {str(e)}"}

    def get_page_info(self) -> dict:
        """Get page info (URL, title, elements, forms) via extension."""
        try:
            return self._ext_cmd("getPageInfo")
        except Exception as e:
            return {"error": f"getPageInfo failed: {str(e)}"}

    def execute_js(self, script: str) -> dict:
        """Execute JavaScript in page context via extension.

        Note: Some sites block eval via CSP (Content Security Policy).
        If you get a CSP error, use Chrome semantic tools instead:
        - page_read/view_read for reading text
        - page_map for interactive elements
        - type(selector, text) for filling forms
        - click(selector) for clicking
        These work via extension content scripts and are NOT affected by CSP.
        """
        t0 = time.time()
        try:
            ws_t = time.time()
            result = self._ext_cmd("execute", {"script": script})
            self._d._last_ws_ms = int((time.time() - ws_t) * 1000)
            # Enhance CSP error with helpful guidance
            if isinstance(result, dict) and "error" in result:
                err = str(result.get("error", ""))
                if "Content Security Policy" in err or "unsafe-eval" in err:
                    result["error"] = (
                        f"{err}\n\n"
                        "This site blocks eval via CSP. Use Chrome semantic tools instead:\n"
                        "- page_read / view_read -- read page text\n"
                        "- page_map -- get interactive elements\n"
                        "- type(selector, text) -- fill form fields\n"
                        "- click(selector) -- click elements"
                    )
            self._log("execute_js", {"script": script[:100]}, start_time=t0)
            return result
        except Exception as e:
            self._log("execute_js", {"script": script[:100]}, error=str(e), start_time=t0)
            return {"error": f"JS execution failed: {str(e)}"}

    def chrome_search(self, text: str) -> dict:
        """Ctrl+F search in Chrome page."""
        from .desktop import DISPLAY
        self._xdotool("key", "ctrl+f")
        self._exec(["sleep", "0.3"])
        cmd = ["env", f"DISPLAY={DISPLAY}", "xdotool", "type",
               "--clearmodifiers", "--delay", "0", text]
        self._exec(cmd)
        self._xdotool("key", "Return")
        return {"searched": True, "text": text}

    # ================================================================
    # Cookies
    # ================================================================

    def get_cookies(self, url: str = None) -> dict:
        """Get browser cookies, optionally filtered by URL."""
        try:
            params = {}
            if url:
                params["url"] = url
            return self._ext_cmd("getCookies", params)
        except Exception as e:
            return {"error": f"getCookies failed: {str(e)}"}

    def set_cookies(self, cookies: list) -> dict:
        """Set browser cookies."""
        try:
            return self._ext_cmd("setCookies", {"cookies": cookies})
        except Exception as e:
            return {"error": f"setCookies failed: {str(e)}"}

    def clear_cookies(self, url: str = None) -> dict:
        """Clear browser cookies, optionally for specific URL."""
        try:
            params = {}
            if url:
                params["url"] = url
            return self._ext_cmd("clearCookies", params)
        except Exception as e:
            return {"error": f"clearCookies failed: {str(e)}"}

    # ================================================================
    # CDP (Chrome DevTools Protocol)
    # ================================================================

    def _cdp_command(self, method: str, params: dict = None, binary_result: str = None,
                     timeout: int = 15) -> dict | bytes:
        """Execute a CDP command via Python WebSocket inside container."""
        params_repr = repr(params or {})
        if binary_result:
            output_handler = (
                f"      sys.stdout.buffer.write(base64.b64decode(r['result']['{binary_result}']))\n"
            )
        else:
            output_handler = (
                "      sys.stdout.buffer.write(json.dumps(r.get('result',r)).encode())\n"
            )

        script = (
            "import json,sys,os,base64,socket,struct,urllib.request\n"
            "tabs=json.loads(urllib.request.urlopen('http://localhost:9222/json').read())\n"
            "if not tabs: sys.exit(1)\n"
            "ws_url=tabs[0].get('webSocketDebuggerUrl','')\n"
            "if not ws_url: sys.exit(1)\n"
            "host_port=ws_url.split('//')[1].split('/')[0]\n"
            "path='/'+'/'.join(ws_url.split('//')[1].split('/')[1:])\n"
            "host,port=host_port.split(':')\n"
            "s=socket.socket(); s.settimeout(10); s.connect((host,int(port)))\n"
            "key=base64.b64encode(os.urandom(16)).decode()\n"
            "s.sendall(f'GET {path} HTTP/1.1\\r\\nHost: {host_port}\\r\\n"
            "Upgrade: websocket\\r\\nConnection: Upgrade\\r\\n"
            "Sec-WebSocket-Key: {key}\\r\\nSec-WebSocket-Version: 13\\r\\n\\r\\n'.encode())\n"
            "resp=b''\n"
            "while b'\\r\\n\\r\\n' not in resp: resp+=s.recv(4096)\n"
            f"msg=json.dumps({{'id':1,'method':'{method}','params':{params_repr}}})\n"
            "payload=msg.encode()\n"
            "mask=os.urandom(4)\n"
            "frame=b'\\x81'\n"
            "L=len(payload)\n"
            "if L<126: frame+=struct.pack('B',0x80|L)\n"
            "elif L<65536: frame+=struct.pack('!BH',0x80|126,L)\n"
            "else: frame+=struct.pack('!BQ',0x80|127,L)\n"
            "frame+=mask+bytes(b^mask[i%4] for i,b in enumerate(payload))\n"
            "s.sendall(frame)\n"
            "data=b''\n"
            "while True:\n"
            "  chunk=s.recv(65536)\n"
            "  if not chunk: break\n"
            "  data+=chunk\n"
            "  try:\n"
            "    txt=data[data.index(b'{'):]\n"
            "    r=json.loads(txt)\n"
            "    if 'result' in r:\n"
            + output_handler +
            "      break\n"
            "  except: pass\n"
            "s.close()\n"
        )
        raw = self._exec_bytes(["python3", "-c", script], timeout=timeout)
        if binary_result:
            return raw if raw and len(raw) > 100 else b""
        try:
            return json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError):
            return {"raw": raw.decode("utf-8", errors="replace")[:500]} if raw else {}

    def _cdp_eval(self, expression: str) -> dict:
        """Evaluate JS via CDP Runtime.evaluate (bypasses CSP)."""
        try:
            return self._cdp_command("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            })
        except Exception as e:
            return {"error": str(e)}

    def save_pdf(self) -> bytes:
        """Save current Chrome page as PDF via CDP."""
        try:
            return self._cdp_command("Page.printToPDF",
                                     {"printBackground": True}, binary_result="data")
        except Exception:
            return b""

    def dom_snapshot(self) -> dict:
        """Get full DOM tree snapshot via CDP."""
        try:
            return self._cdp_command("DOMSnapshot.captureSnapshot",
                                     {"computedStyles": [], "includeDOMRects": True})
        except Exception as e:
            return {"error": str(e)}

    def performance_metrics(self) -> dict:
        """Get page performance metrics via CDP."""
        try:
            return self._cdp_command("Performance.getMetrics")
        except Exception as e:
            return {"error": str(e)}

    def emulate_device(self, width: int, height: int, device_scale: float = 1.0,
                       mobile: bool = False, user_agent: str = None) -> dict:
        """Set device emulation via CDP."""
        params = {
            "width": width, "height": height,
            "deviceScaleFactor": device_scale, "mobile": mobile,
        }
        try:
            result = self._cdp_command("Emulation.setDeviceMetricsOverride", params)
            if user_agent:
                self._cdp_command("Emulation.setUserAgentOverride", {"userAgent": user_agent})
            return {"emulated": True, "width": width, "height": height, "mobile": mobile}
        except Exception as e:
            return {"error": str(e)}

    def set_geolocation(self, latitude: float, longitude: float, accuracy: float = 100) -> dict:
        """Override geolocation via CDP."""
        try:
            self._cdp_command("Emulation.setGeolocationOverride", {
                "latitude": latitude, "longitude": longitude, "accuracy": accuracy
            })
            return {"set": True, "latitude": latitude, "longitude": longitude}
        except Exception as e:
            return {"error": str(e)}

    def ignore_ssl_errors(self, ignore: bool = True) -> dict:
        """Ignore or restore SSL certificate errors via CDP."""
        try:
            self._cdp_command("Security.setIgnoreCertificateErrors", {"ignore": ignore})
            return {"ignore_ssl": ignore}
        except Exception as e:
            return {"error": str(e)}

    # ================================================================
    # Browser screenshots & DevTools
    # ================================================================

    def browser_screenshot(self, quality: int = 75) -> bytes:
        """Capture Chrome viewport screenshot via DevTools (no OS chrome)."""
        try:
            result = self._ext_cmd("screenshot", {"quality": quality})
            if result.get("data"):
                return base64.b64decode(result["data"])
        except Exception:
            pass
        try:
            return self._cdp_command("Page.captureScreenshot",
                                     {"format": "jpeg", "quality": quality},
                                     binary_result="data")
        except Exception:
            return b""

    def console_start(self) -> dict:
        """Start capturing console logs by injecting a JS hook via CDP."""
        script = """(() => {
            if (!window.__screenbox_console) {
                window.__screenbox_console = [];
                const orig = {};
                ['log','warn','error','info','debug'].forEach(m => {
                    orig[m] = console[m];
                    console[m] = function(...args) {
                        window.__screenbox_console.push({
                            level: m,
                            ts: Date.now(),
                            args: args.map(a => {
                                try { return typeof a === 'object' ? JSON.stringify(a) : String(a); }
                                catch(e) { return String(a); }
                            })
                        });
                        if (window.__screenbox_console.length > 1000)
                            window.__screenbox_console.shift();
                        orig[m].apply(console, args);
                    };
                });
            }
            return 'started';
        })()"""
        return self._cdp_eval(script)

    def console_stop(self) -> dict:
        """Stop capturing console logs and restore original methods."""
        script = """(() => {
            if (window.__screenbox_console) { delete window.__screenbox_console; }
            return 'stopped';
        })()"""
        return self._cdp_eval(script)

    def console_get(self, clear: bool = False) -> dict:
        """Get captured console logs."""
        if clear:
            script = """(() => {
                const logs = window.__screenbox_console || [];
                window.__screenbox_console = [];
                return logs;
            })()"""
        else:
            script = "(window.__screenbox_console || [])"
        result = self._cdp_eval(script)
        if isinstance(result, dict) and "result" in result:
            r = result["result"]
            if isinstance(r, dict) and "value" in r:
                val = r["value"]
                if isinstance(val, list):
                    return {"logs": val}
                if isinstance(val, str):
                    try:
                        return {"logs": json.loads(val)}
                    except (json.JSONDecodeError, TypeError):
                        pass
            return {"logs": [], "raw": str(r)[:500]}
        return result

    def network_get(self) -> dict:
        """Get network requests via Performance API."""
        script = """(() => {
            return performance.getEntriesByType('resource').map(e => ({
                name: e.name,
                type: e.initiatorType,
                duration: Math.round(e.duration),
                size: e.transferSize || 0,
                start: Math.round(e.startTime),
                status: e.responseStatus || 0
            }));
        })()"""
        result = self._cdp_eval(script)
        if isinstance(result, dict) and "result" in result:
            r = result["result"]
            if isinstance(r, dict) and "value" in r:
                val = r["value"]
                if isinstance(val, list):
                    return {"entries": val}
            return {"entries": [], "raw": str(r)[:500]}
        return result

    # ================================================================
    # Health
    # ================================================================

    def chrome_ready(self) -> dict:
        """Quick check: is Chrome DevTools responding?"""
        try:
            out = self._exec(["bash", "-c",
                              "curl -s http://localhost:9222/json 2>/dev/null"],
                             timeout=5)
            if out.strip().startswith("["):
                tabs = json.loads(out.strip())
                return {"ready": True, "tabs": len(tabs)}
            return {"ready": False, "raw": out[:200]}
        except Exception as e:
            return {"ready": False, "error": str(e)}

    def health_check(self) -> dict:
        """Comprehensive health check: Xvnc, VNC, Chrome, WS bridge."""
        checks = {}
        script = (
            "echo XVFB=$(xdpyinfo -display :99 >/dev/null 2>&1 && echo 1 || echo 0);"
            "echo VNC=$(bash -c 'echo >/dev/tcp/localhost/5900' 2>/dev/null && echo 1 || echo 0);"
            "echo CHROME=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:9222/json 2>/dev/null);"
            "echo WS=$(bash -c 'echo >/dev/tcp/localhost/8765' 2>/dev/null && echo 1 || echo 0);"
            "echo READY=$(test -f /tmp/.screenbox-ready && echo 1 || echo 0)"
        )
        try:
            out = self._exec(["bash", "-c", script], timeout=10)
            for line in out.strip().split("\n"):
                if "=" not in line:
                    continue
                k, v = line.strip().split("=", 1)
                if k == "XVFB":
                    checks["xvfb"] = v == "1"
                elif k == "VNC":
                    checks["vnc"] = v == "1"
                elif k == "CHROME":
                    checks["chrome"] = v == "200"
                elif k == "WS":
                    checks["ws_bridge"] = v == "1"
                elif k == "READY":
                    checks["ready_file"] = v == "1"
        except Exception as e:
            checks["error"] = str(e)

        checks["healthy"] = all(checks.get(k, False) for k in ("xvfb", "vnc", "chrome"))
        if not checks["healthy"]:
            failed = [k for k in ("xvfb", "vnc", "chrome", "ws_bridge")
                      if not checks.get(k, False)]
            self._log("health_check", {}, error=f"unhealthy: {','.join(failed)}")
        return checks
