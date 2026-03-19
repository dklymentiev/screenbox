"""Unit tests for setup-extension.py."""

import json
import os
import sys
import pytest


# Import the module directly
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "docker"))


class TestChromeExtensionId:
    def test_deterministic(self):
        from importlib import import_module
        mod = import_module("setup-extension")
        id1 = mod.chrome_extension_id("/home/test/ext")
        id2 = mod.chrome_extension_id("/home/test/ext")
        assert id1 == id2

    def test_different_paths_different_ids(self):
        from importlib import import_module
        mod = import_module("setup-extension")
        id1 = mod.chrome_extension_id("/path/a")
        id2 = mod.chrome_extension_id("/path/b")
        assert id1 != id2

    def test_32_chars(self):
        from importlib import import_module
        mod = import_module("setup-extension")
        ext_id = mod.chrome_extension_id("/test")
        assert len(ext_id) == 32

    def test_only_lowercase_a_to_p(self):
        from importlib import import_module
        mod = import_module("setup-extension")
        ext_id = mod.chrome_extension_id("/any/path")
        for c in ext_id:
            assert c in "abcdefghijklmnop"


class TestPatchConfigBlock:
    def test_patches_wsport(self, tmp_path):
        from importlib import import_module
        mod = import_module("setup-extension")

        bg_file = tmp_path / "background.js"
        bg_file.write_text(
            "// SCREENBOX_CONFIG_START\n"
            "let wsPort = 8765;\n"
            "let desktopId = 'default';\n"
            "// SCREENBOX_CONFIG_END\n"
            "// rest of code\n"
        )

        mod.patch_config_block(str(tmp_path), 9999, "screenbox")

        content = bg_file.read_text()
        assert "let wsPort = 9999;" in content
        assert "let desktopId = 'screenbox';" in content
        assert "rest of code" in content

    def test_no_match_no_change(self, tmp_path):
        from importlib import import_module
        mod = import_module("setup-extension")

        bg_file = tmp_path / "background.js"
        original = "// some other js\nlet x = 1;\n"
        bg_file.write_text(original)

        result = mod.patch_config_block(str(tmp_path), 9999, "screenbox")
        assert bg_file.read_text() == original
        assert result is False

    def test_missing_file(self, tmp_path):
        from importlib import import_module
        mod = import_module("setup-extension")
        result = mod.patch_config_block(str(tmp_path), 9999, "screenbox")
        assert result is False


class TestSetup:
    def test_creates_preferences(self, tmp_path):
        from importlib import import_module
        mod = import_module("setup-extension")

        ext_dir = tmp_path / "extension"
        ext_dir.mkdir()
        manifest = {
            "manifest_version": 3,
            "name": "Test",
            "version": "1.0",
            "permissions": ["activeTab"],
            "host_permissions": ["<all_urls>"],
            "content_scripts": [{"matches": ["<all_urls>"], "js": ["content.js"]}],
        }
        (ext_dir / "manifest.json").write_text(json.dumps(manifest))
        (ext_dir / "background.js").write_text(
            "// SCREENBOX_CONFIG_START\n"
            "let wsPort = 8765;\n"
            "let desktopId = 'default';\n"
            "// SCREENBOX_CONFIG_END\n"
        )

        profile_dir = tmp_path / "chrome-profile"

        # Monkey-patch module globals
        orig_ext = mod.EXTENSION_SRC
        orig_profile = mod.PROFILE_DIR
        mod.EXTENSION_SRC = str(ext_dir)
        mod.PROFILE_DIR = str(profile_dir)

        try:
            mod.setup()

            prefs_file = profile_dir / "Default" / "Preferences"
            assert prefs_file.exists()

            prefs = json.loads(prefs_file.read_text())
            settings = prefs["extensions"]["settings"]
            assert len(settings) == 1

            ext_id = list(settings.keys())[0]
            assert len(ext_id) == 32
            assert settings[ext_id]["location"] == 4
            assert settings[ext_id]["manifest"]["name"] == "Test"
            assert prefs["extensions"]["ui"]["developer_mode"] is True
        finally:
            mod.EXTENSION_SRC = orig_ext
            mod.PROFILE_DIR = orig_profile
