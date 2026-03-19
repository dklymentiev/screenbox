"""Browser interaction methods forwarded to Chrome extension."""


class BrowserProxyMixin:
    """Browser interaction forwarded to Chrome extension.

    Every method is a one-liner that delegates to self.browser.
    """

    # -- Navigation & page info --

    def navigate(self, url: str) -> dict:
        return self.browser.navigate(url)

    def semantics(self) -> dict:
        return self.browser.page_map()

    def get_page_info(self) -> dict:
        return self.browser.get_page_info()

    def execute_js(self, script: str) -> dict:
        return self.browser.execute_js(script)

    def go_back(self) -> dict:
        return self.browser.go_back()

    def go_forward(self) -> dict:
        return self.browser.go_forward()

    # -- Tabs --

    def get_tabs(self) -> dict:
        return self.browser.get_tabs()

    def new_tab(self, url: str = "about:blank") -> dict:
        return self.browser.new_tab(url)

    def close_tab(self, tab_id: int = None) -> dict:
        return self.browser.close_tab(tab_id)

    def switch_tab(self, tab_id: int) -> dict:
        return self.browser.switch_tab(tab_id)

    # -- Cookies --

    def get_cookies(self, url: str = None) -> dict:
        return self.browser.get_cookies(url)

    def set_cookies(self, cookies: list) -> dict:
        return self.browser.set_cookies(cookies)

    def clear_cookies(self, url: str = None) -> dict:
        return self.browser.clear_cookies(url)

    # -- DOM interaction via extension --

    def ext_click(self, selector: str) -> dict:
        return self.browser.ext_click(selector)

    def ext_type(self, selector: str, text: str, clear: bool = False) -> dict:
        return self.browser.ext_type(selector, text, clear)

    def get_content(self, selector: str = None) -> dict:
        return self.browser.get_content(selector)

    # -- CDP / low-level --

    def _cdp_command(self, method, params=None, **kw):
        return self.browser._cdp_command(method, params, **kw)

    def save_pdf(self) -> bytes:
        return self.browser.save_pdf()

    def dom_snapshot(self) -> dict:
        return self.browser.dom_snapshot()

    def performance_metrics(self) -> dict:
        return self.browser.performance_metrics()

    def emulate_device(self, width, height, device_scale=1.0, mobile=False, user_agent=None):
        return self.browser.emulate_device(width, height, device_scale, mobile, user_agent)

    def set_geolocation(self, latitude, longitude, accuracy=100):
        return self.browser.set_geolocation(latitude, longitude, accuracy)

    def ignore_ssl_errors(self, ignore=True):
        return self.browser.ignore_ssl_errors(ignore)

    # -- Health --

    def health_check(self) -> dict:
        return self.browser.health_check()

    def chrome_ready(self) -> dict:
        return self.browser.chrome_ready()

    # -- Screenshot & console/network capture --

    def browser_screenshot(self, quality: int = 75) -> bytes:
        return self.browser.browser_screenshot(quality)

    def console_start(self) -> dict:
        return self.browser.console_start()

    def console_stop(self) -> dict:
        return self.browser.console_stop()

    def console_get(self, clear: bool = False) -> dict:
        return self.browser.console_get(clear)

    def network_get(self) -> dict:
        return self.browser.network_get()

    # -- Search --

    def chrome_search(self, text: str) -> dict:
        return self.browser.chrome_search(text)
