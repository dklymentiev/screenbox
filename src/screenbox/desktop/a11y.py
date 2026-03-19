"""Accessibility (AT-SPI) mixin for Desktop."""

import json


class AccessibilityMixin:
    """AT-SPI accessibility methods for Desktop.

    Requires self._exec() to be available (provided by Desktop base class).
    """

    def _atspi_exec(self, script: str, timeout: int = 15) -> str:
        """Run a pyatspi Python script inside the container with AT-SPI env."""
        # Write script to temp file, source dbus env, then run it
        import base64 as _b64
        encoded = _b64.b64encode(script.encode()).decode()
        return self._exec([
            "bash", "-c",
            f"source /home/screenbox/.dbus-env 2>/dev/null; "
            f"echo {encoded} | base64 -d | python3"
        ], timeout=timeout)

    def accessibility_apps(self) -> dict:
        """List accessible applications on the desktop via AT-SPI."""
        script = (
            "import gi\n"
            "gi.require_version('Atspi', '2.0')\n"
            "from gi.repository import Atspi\n"
            "import json\n"
            "desktop = Atspi.get_desktop(0)\n"
            "apps = []\n"
            "for i in range(desktop.get_child_count()):\n"
            "    app = desktop.get_child_at_index(i)\n"
            "    if app:\n"
            "        apps.append({'name': app.get_name() or '', 'role': app.get_role_name() or '', 'children': app.get_child_count()})\n"
            "print(json.dumps(apps))\n"
        )
        raw = self._atspi_exec(script)
        try:
            return {"apps": json.loads(raw.strip())}
        except (json.JSONDecodeError, ValueError):
            return {"apps": [], "error": raw.strip()[:500]}

    def accessibility_tree(self, app_name: str = None, max_depth: int = 3) -> dict:
        """Get accessibility tree for an app (or full desktop).

        Args:
            app_name: Application name to inspect (None = all apps)
            max_depth: Max depth to traverse (default 3)
        """
        app_filter = repr(app_name) if app_name else "None"
        script = (
            "import gi\n"
            "gi.require_version('Atspi', '2.0')\n"
            "from gi.repository import Atspi\n"
            "import json\n"
            f"TARGET_APP = {app_filter}\n"
            f"MAX_DEPTH = {max_depth}\n"
            "def node_info(obj, depth=0):\n"
            "    if not obj or depth > MAX_DEPTH: return None\n"
            "    try:\n"
            "        info = {'name': obj.get_name() or '', 'role': obj.get_role_name() or ''}\n"
            "        try:\n"
            "            c = obj.get_component()\n"
            "            if c:\n"
            "                r = c.get_extents(Atspi.CoordType.SCREEN)\n"
            "                info['rect'] = [r.x, r.y, r.width, r.height]\n"
            "        except: pass\n"
            "        states = []\n"
            "        try:\n"
            "            ss = obj.get_state_set()\n"
            "            for s in [Atspi.StateType.FOCUSED, Atspi.StateType.CHECKED, Atspi.StateType.SELECTED, Atspi.StateType.SENSITIVE, Atspi.StateType.VISIBLE, Atspi.StateType.SHOWING]:\n"
            "                if ss.contains(s): states.append(Atspi.StateType.get_name(s))\n"
            "        except: pass\n"
            "        if states: info['states'] = states\n"
            "        try:\n"
            "            v = obj.get_text(0, -1) if hasattr(obj, 'get_text') else None\n"
            "            if v: info['text'] = v[:200]\n"
            "        except: pass\n"
            "        children = []\n"
            "        for i in range(obj.get_child_count()):\n"
            "            ch = node_info(obj.get_child_at_index(i), depth+1)\n"
            "            if ch: children.append(ch)\n"
            "        if children: info['children'] = children\n"
            "        return info\n"
            "    except: return None\n"
            "desktop = Atspi.get_desktop(0)\n"
            "result = []\n"
            "for i in range(desktop.get_child_count()):\n"
            "    app = desktop.get_child_at_index(i)\n"
            "    if not app: continue\n"
            "    aname = app.get_name() or ''\n"
            "    if TARGET_APP and TARGET_APP.lower() not in aname.lower(): continue\n"
            "    info = node_info(app)\n"
            "    if info: result.append(info)\n"
            "print(json.dumps(result, ensure_ascii=False))\n"
        )
        raw = self._atspi_exec(script, timeout=20)
        try:
            return {"tree": json.loads(raw.strip())}
        except (json.JSONDecodeError, ValueError):
            return {"tree": [], "error": raw.strip()[:500]}

    def accessibility_find(self, role: str = None, name: str = None, app_name: str = None) -> dict:
        """Find accessible elements by role and/or name.

        Args:
            role: AT-SPI role name (e.g. 'push button', 'menu item', 'text')
            name: Element name (substring match, case-insensitive)
            app_name: Limit search to this application
        """
        role_filter = repr(role) if role else "None"
        name_filter = repr(name) if name else "None"
        app_filter = repr(app_name) if app_name else "None"
        script = (
            "import gi\n"
            "gi.require_version('Atspi', '2.0')\n"
            "from gi.repository import Atspi\n"
            "import json\n"
            f"ROLE_FILTER = {role_filter}\n"
            f"NAME_FILTER = {name_filter}\n"
            f"APP_FILTER = {app_filter}\n"
            "results = []\n"
            "def search(obj, path='', depth=0):\n"
            "    if not obj or depth > 15 or len(results) >= 50: return\n"
            "    try:\n"
            "        n = obj.get_name() or ''\n"
            "        r = obj.get_role_name() or ''\n"
            "        match = True\n"
            "        if ROLE_FILTER and ROLE_FILTER.lower() != r.lower(): match = False\n"
            "        if NAME_FILTER and NAME_FILTER.lower() not in n.lower(): match = False\n"
            "        if match and (ROLE_FILTER or NAME_FILTER):\n"
            "            info = {'name': n, 'role': r, 'path': path}\n"
            "            try:\n"
            "                c = obj.get_component()\n"
            "                if c:\n"
            "                    ext = c.get_extents(Atspi.CoordType.SCREEN)\n"
            "                    info['rect'] = [ext.x, ext.y, ext.width, ext.height]\n"
            "            except: pass\n"
            "            try:\n"
            "                ss = obj.get_state_set()\n"
            "                if ss.contains(Atspi.StateType.SENSITIVE): info['clickable'] = True\n"
            "            except: pass\n"
            "            results.append(info)\n"
            "        for i in range(obj.get_child_count()):\n"
            "            search(obj.get_child_at_index(i), f'{path}/{i}', depth+1)\n"
            "    except: pass\n"
            "desktop = Atspi.get_desktop(0)\n"
            "for i in range(desktop.get_child_count()):\n"
            "    app = desktop.get_child_at_index(i)\n"
            "    if not app: continue\n"
            "    aname = app.get_name() or ''\n"
            "    if APP_FILTER and APP_FILTER.lower() not in aname.lower(): continue\n"
            "    search(app, aname)\n"
            "print(json.dumps(results, ensure_ascii=False))\n"
        )
        raw = self._atspi_exec(script, timeout=20)
        try:
            return {"elements": json.loads(raw.strip())}
        except (json.JSONDecodeError, ValueError):
            return {"elements": [], "error": raw.strip()[:500]}

    def accessibility_activate(self, app_name: str, element_path: str) -> dict:
        """Activate (click/press) an accessible element by its path.

        Args:
            app_name: Application name
            element_path: Path from accessibility_find (e.g. 'Caja/0/1/3')
        """
        script = (
            "import gi\n"
            "gi.require_version('Atspi', '2.0')\n"
            "from gi.repository import Atspi\n"
            "import json\n"
            f"APP_NAME = {repr(app_name)}\n"
            f"ELEMENT_PATH = {repr(element_path)}\n"
            "desktop = Atspi.get_desktop(0)\n"
            "target_app = None\n"
            "for i in range(desktop.get_child_count()):\n"
            "    app = desktop.get_child_at_index(i)\n"
            "    if app and APP_NAME.lower() in (app.get_name() or '').lower():\n"
            "        target_app = app\n"
            "        break\n"
            "if not target_app:\n"
            "    print(json.dumps({'error': f'App not found: {APP_NAME}'}))\n"
            "    raise SystemExit(0)\n"
            "# Navigate path: 'AppName/0/1/3' -> indices [0, 1, 3]\n"
            "parts = ELEMENT_PATH.split('/')\n"
            "indices = [int(p) for p in parts if p.isdigit()]\n"
            "obj = target_app\n"
            "for idx in indices:\n"
            "    if idx < obj.get_child_count():\n"
            "        obj = obj.get_child_at_index(idx)\n"
            "    else:\n"
            "        print(json.dumps({'error': f'Index {idx} out of range at {obj.get_name()}'}))\n"
            "        raise SystemExit(0)\n"
            "# Try action interface first\n"
            "done = False\n"
            "try:\n"
            "    ai = obj.get_action_iface()\n"
            "    if ai and ai.get_n_actions() > 0:\n"
            "        ai.do_action(0)\n"
            "        done = True\n"
            "except: pass\n"
            "if not done:\n"
            "    # Fall back to clicking center of element\n"
            "    try:\n"
            "        c = obj.get_component()\n"
            "        if c:\n"
            "            ext = c.get_extents(Atspi.CoordType.SCREEN)\n"
            "            cx = ext.x + ext.width // 2\n"
            "            cy = ext.y + ext.height // 2\n"
            "            import subprocess\n"
            "            subprocess.run(['xdotool', 'mousemove', str(cx), str(cy)], env={'DISPLAY': ':99'})\n"
            "            subprocess.run(['xdotool', 'click', '1'], env={'DISPLAY': ':99'})\n"
            "            done = True\n"
            "    except: pass\n"
            "print(json.dumps({'activated': done, 'name': obj.get_name() or '', 'role': obj.get_role_name() or ''}))\n"
        )
        raw = self._atspi_exec(script, timeout=15)
        try:
            return json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            return {"activated": False, "error": raw.strip()[:500]}

    def accessibility_set_text(self, app_name: str, element_path: str, text: str) -> dict:
        """Set text value on an accessible text element.

        Args:
            app_name: Application name
            element_path: Path from accessibility_find
            text: Text to set
        """
        script = (
            "import gi\n"
            "gi.require_version('Atspi', '2.0')\n"
            "from gi.repository import Atspi\n"
            "import json\n"
            f"APP_NAME = {repr(app_name)}\n"
            f"ELEMENT_PATH = {repr(element_path)}\n"
            f"TEXT = {repr(text)}\n"
            "desktop = Atspi.get_desktop(0)\n"
            "target_app = None\n"
            "for i in range(desktop.get_child_count()):\n"
            "    app = desktop.get_child_at_index(i)\n"
            "    if app and APP_NAME.lower() in (app.get_name() or '').lower():\n"
            "        target_app = app\n"
            "        break\n"
            "if not target_app:\n"
            "    print(json.dumps({'error': f'App not found: {APP_NAME}'}))\n"
            "    raise SystemExit(0)\n"
            "parts = ELEMENT_PATH.split('/')\n"
            "indices = [int(p) for p in parts if p.isdigit()]\n"
            "obj = target_app\n"
            "for idx in indices:\n"
            "    if idx < obj.get_child_count():\n"
            "        obj = obj.get_child_at_index(idx)\n"
            "    else:\n"
            "        print(json.dumps({'error': f'Index {idx} out of range'}))\n"
            "        raise SystemExit(0)\n"
            "done = False\n"
            "try:\n"
            "    ei = obj.get_editable_text_iface()\n"
            "    if ei:\n"
            "        # Clear existing text\n"
            "        ti = obj.get_text_iface()\n"
            "        if ti:\n"
            "            length = ti.get_character_count()\n"
            "            if length > 0: ei.delete_text(0, length)\n"
            "        ei.insert_text(0, TEXT, len(TEXT))\n"
            "        done = True\n"
            "except: pass\n"
            "if not done:\n"
            "    # Fallback: click element and type via xdotool\n"
            "    try:\n"
            "        c = obj.get_component()\n"
            "        if c:\n"
            "            ext = c.get_extents(Atspi.CoordType.SCREEN)\n"
            "            cx = ext.x + ext.width // 2\n"
            "            cy = ext.y + ext.height // 2\n"
            "            import subprocess\n"
            "            env = {'DISPLAY': ':99'}\n"
            "            subprocess.run(['xdotool', 'mousemove', str(cx), str(cy)], env=env)\n"
            "            subprocess.run(['xdotool', 'click', '1'], env=env)\n"
            "            subprocess.run(['xdotool', 'key', 'ctrl+a'], env=env)\n"
            "            subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '0', TEXT], env=env)\n"
            "            done = True\n"
            "    except: pass\n"
            "print(json.dumps({'set_text': done, 'name': obj.get_name() or '', 'role': obj.get_role_name() or ''}))\n"
        )
        raw = self._atspi_exec(script, timeout=15)
        try:
            return json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            return {"set_text": False, "error": raw.strip()[:500]}
