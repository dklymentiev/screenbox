#!/usr/bin/env python3
"""Pre-launch extension setup for Screenbox.

Pre-launch setup steps:
1. Copy extension files to profile dir (if not already there)
2. Patch wsPort/desktopId directly in background.js (SCREENBOX_CONFIG block)
3. Also patch any CRX-installed copies in Default/Extensions/
4. Register extension in Chrome Preferences (extensions.settings, location=4)
5. Remove stale CRX entries (location=7) that conflict
6. Clear Service Worker and Code Cache dirs

This ensures the extension connects immediately on Chrome launch,
without needing CDP -> chrome.storage chain.
"""

import hashlib
import json
import os
import re
import shutil
import time

EXTENSION_SRC = "/opt/screenbox/extension"
PROFILE_DIR = os.environ.get("CHROME_PROFILE", "/home/screenbox/.config/chromium")
WS_PORT = int(os.environ.get("WS_BRIDGE_PORT", "8765"))
DESKTOP_ID = os.environ.get("DESKTOP_ID", "screenbox")


def chrome_extension_id(path: str) -> str:
    """Compute Chrome extension ID from absolute path."""
    path_bytes = path.encode("utf-8")
    digest = hashlib.sha256(path_bytes).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def patch_config_block(ext_dir: str, ws_port: int, desktop_id: str):
    """Patch wsPort/desktopId directly in background.js SCREENBOX_CONFIG block."""
    bg = os.path.join(ext_dir, "background.js")
    if not os.path.exists(bg):
        return False

    with open(bg, "r") as f:
        content = f.read()

    pattern = (
        r"(// SCREENBOX_CONFIG_START\n)"
        r"let wsPort = .*?\n"
        r"let desktopId = .*?\n"
        r"(// SCREENBOX_CONFIG_END)"
    )
    replacement = (
        r"\g<1>"
        f"let wsPort = {ws_port};\n"
        f"let desktopId = '{desktop_id}';\n"
        r"\g<2>"
    )
    new_content, count = re.subn(pattern, replacement, content)
    if count > 0:
        with open(bg, "w") as f:
            f.write(new_content)
        print(f"[setup-extension] Patched {bg}: wsPort={ws_port}, desktopId={desktop_id}")
        return True
    else:
        print(f"[setup-extension] WARNING: SCREENBOX_CONFIG block not found in {bg}")
        return False


def setup():
    """Full extension setup -- copy, patch, register, clean."""
    profile_dir = PROFILE_DIR
    default_dir = os.path.join(profile_dir, "Default")
    os.makedirs(default_dir, exist_ok=True)

    ext_dest = os.path.join(profile_dir, "screenbox-extension")

    # Step 1: Copy or sync extension files to profile
    if os.path.exists(ext_dest):
        # Sync background.js (may have been updated in image)
        src_bg = os.path.join(EXTENSION_SRC, "background.js")
        dst_bg = os.path.join(ext_dest, "background.js")
        if os.path.exists(src_bg):
            shutil.copy2(src_bg, dst_bg)
            print(f"[setup-extension] Synced background.js to profile")
    else:
        shutil.copytree(EXTENSION_SRC, ext_dest)
        print(f"[setup-extension] Extension copied to {ext_dest}")

    # Step 2: Patch config directly in background.js
    patch_config_block(ext_dest, WS_PORT, DESKTOP_ID)

    # Step 2b: Also patch any CRX-installed copies in Default/Extensions/
    exts_dir = os.path.join(default_dir, "Extensions")
    if os.path.isdir(exts_dir):
        for ext_id_dir_name in os.listdir(exts_dir):
            ext_id_dir = os.path.join(exts_dir, ext_id_dir_name)
            if not os.path.isdir(ext_id_dir):
                continue
            for version_dir_name in os.listdir(ext_id_dir):
                bg = os.path.join(ext_id_dir, version_dir_name, "background.js")
                if os.path.exists(bg):
                    with open(bg, "r") as f:
                        head = f.read(200)
                    if "SCREENBOX_CONFIG" in head:
                        patch_config_block(
                            os.path.join(ext_id_dir, version_dir_name),
                            WS_PORT, DESKTOP_ID
                        )
                        print(f"[setup-extension] Patched CRX copy: {ext_id_dir_name}")

    # Step 3: Register in Chrome Preferences
    prefs_file = os.path.join(default_dir, "Preferences")
    prefs = {}
    if os.path.exists(prefs_file):
        try:
            with open(prefs_file) as f:
                prefs = json.load(f)
        except Exception:
            prefs = {}

    ext_path = os.path.abspath(ext_dest)
    ext_id = chrome_extension_id(ext_path)

    with open(os.path.join(EXTENSION_SRC, "manifest.json")) as f:
        manifest = json.load(f)

    permissions = manifest.get("permissions", [])
    host_permissions = manifest.get("host_permissions", [])
    content_script_hosts = []
    for cs in manifest.get("content_scripts", []):
        content_script_hosts.extend(cs.get("matches", []))

    now_ts = str(int(time.time()))

    ext_entry = {
        "active_permissions": {
            "api": permissions,
            "explicit_host": host_permissions,
            "manifest_permissions": [],
            "scriptable_host": content_script_hosts,
        },
        "commands": {},
        "content_settings": [],
        "creation_flags": 1,
        "first_install_time": now_ts,
        "from_webstore": False,
        "granted_permissions": {
            "api": permissions,
            "explicit_host": host_permissions,
            "manifest_permissions": [],
            "scriptable_host": content_script_hosts,
        },
        "last_update_time": now_ts,
        "location": 4,
        "manifest": manifest,
        "path": ext_path,
        "was_installed_by_default": False,
        "was_installed_by_oem": False,
    }

    prefs.setdefault("extensions", {})
    prefs["extensions"].setdefault("settings", {})

    # Step 4: Remove stale CRX entries (location=7)
    stale_ids = []
    for eid, einfo in prefs["extensions"]["settings"].items():
        if eid == ext_id:
            continue
        if einfo.get("location") == 7:
            name = einfo.get("manifest", {}).get("name", "")
            if "screenbox" in name.lower():
                stale_ids.append(eid)
    for eid in stale_ids:
        del prefs["extensions"]["settings"][eid]
        print(f"[setup-extension] Removed stale CRX entry: {eid}")
        crx_dir = os.path.join(default_dir, "Extensions", eid)
        if os.path.isdir(crx_dir):
            shutil.rmtree(crx_dir)

    # Keep original timestamps if updating
    existing = prefs["extensions"]["settings"].get(ext_id)
    if existing:
        ext_entry["first_install_time"] = existing.get("first_install_time", now_ts)

    prefs["extensions"]["settings"][ext_id] = ext_entry

    # Developer mode needed for unpacked extensions
    prefs["extensions"].setdefault("ui", {})
    prefs["extensions"]["ui"]["developer_mode"] = True

    with open(prefs_file, "w") as f:
        json.dump(prefs, f)

    print(f"[setup-extension] Extension registered: id={ext_id}")

    # Step 5: Clear Service Worker and Code Cache
    for cache_dir in ("Service Worker", "Code Cache"):
        cache_path = os.path.join(default_dir, cache_dir)
        if os.path.exists(cache_path):
            shutil.rmtree(cache_path)
            print(f"[setup-extension] Cleared cache: {cache_dir}")


if __name__ == "__main__":
    setup()
