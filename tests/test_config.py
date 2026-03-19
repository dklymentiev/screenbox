"""Unit tests for Config class."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from screenbox.config import Config, DEFAULT_CONFIG, _detect_platform_defaults


class TestDetectPlatformDefaults:
    def test_returns_dict(self):
        result = _detect_platform_defaults()
        assert isinstance(result, dict)
        assert "max_desktops" in result
        assert "memory_per_desktop" in result

    @patch("platform.system", return_value="Darwin")
    def test_macos_defaults(self, _):
        result = _detect_platform_defaults()
        assert result["max_desktops"] < DEFAULT_CONFIG["max_desktops"]  # macOS gets fewer
        assert result["memory_per_desktop"] == DEFAULT_CONFIG["memory_per_desktop"]

    @patch("platform.system", return_value="Linux")
    @patch("os.path.exists", return_value=False)
    def test_linux_defaults(self, *_):
        result = _detect_platform_defaults()
        assert result["max_desktops"] == DEFAULT_CONFIG["max_desktops"]
        assert result["memory_per_desktop"] == DEFAULT_CONFIG["memory_per_desktop"]


class TestConfig:
    def test_creates_dirs(self, tmp_config_dir):
        config = Config(base_dir=tmp_config_dir)
        assert (tmp_config_dir / "logs").is_dir()
        assert (tmp_config_dir / "logs" / "screenshots").is_dir()

    def test_creates_config_file(self, tmp_config_dir):
        config = Config(base_dir=tmp_config_dir)
        assert config.config_file.exists()
        with open(config.config_file) as f:
            data = json.load(f)
        assert "max_desktops" in data

    def test_loads_existing_config(self, tmp_config_dir):
        # Write custom config
        tmp_config_dir.mkdir(parents=True, exist_ok=True)
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"max_desktops": 10, "image": "test:v1"}))

        config = Config(base_dir=tmp_config_dir)
        assert config.max_desktops == 10
        assert config.image == "test:v1"

    def test_viewport_parsing(self, tmp_config_dir):
        config = Config(base_dir=tmp_config_dir)
        assert config.viewport_width == 1920
        assert config.viewport_height == 1080

    def test_custom_viewport(self, tmp_config_dir):
        tmp_config_dir.mkdir(parents=True, exist_ok=True)
        (tmp_config_dir / "config.json").write_text(
            json.dumps({"default_viewport": "1920x1080"})
        )
        config = Config(base_dir=tmp_config_dir)
        assert config.viewport_width == 1920
        assert config.viewport_height == 1080

    def test_properties(self, tmp_config_dir):
        config = Config(base_dir=tmp_config_dir)
        assert isinstance(config.max_desktops, int)
        assert isinstance(config.memory_limit, str)
        assert isinstance(config.image, str)
        assert isinstance(config.desktops_dir, Path)
        assert isinstance(config.logs_dir, Path)

    def test_get_with_default(self, tmp_config_dir):
        config = Config(base_dir=tmp_config_dir)
        assert config.get("nonexistent", "fallback") == "fallback"

    def test_desktop_dir_creates_dir(self, tmp_config_dir):
        config = Config(base_dir=tmp_config_dir)
        d = config.desktop_dir("test-desktop")
        assert d.is_dir()
        assert d.name == "test-desktop"

    def test_getitem(self, tmp_config_dir):
        config = Config(base_dir=tmp_config_dir)
        assert config["image"] == config.image
        with pytest.raises(KeyError):
            _ = config["nonexistent_key"]
