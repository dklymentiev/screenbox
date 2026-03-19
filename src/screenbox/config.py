"""Screenbox configuration management.

Config file: ~/.screenbox/config.json
Platform-aware defaults on first run.
"""

import json
import os
import platform
from pathlib import Path
from typing import Any


def _detect_platform_defaults() -> dict:
    """Detect OS and set appropriate defaults."""
    system = platform.system().lower()
    if system == "darwin":
        return {"max_desktops": 3, "memory_per_desktop": "2048m"}
    elif system == "linux" and os.path.exists("/proc/version"):
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return {"max_desktops": 3, "memory_per_desktop": "2048m"}
    return {"max_desktops": 5, "memory_per_desktop": "2048m"}


DEFAULT_CONFIG = {
    "max_desktops": 10,
    "memory_per_desktop": "2048m",
    "dashboard_port": 6080,
    "log_screenshots": False,
    "log_keystrokes": False,
    "default_viewport": "1920x1080",
    "idle_pause_minutes": 20,
    "chrome_args": [],
    "image": "screenbox:latest",
    "look_factor": 0.24,
    "lease_ttl": 600,  # seconds; 0 = no auto-expiry
    "auto_snapshot_minutes": 30,  # 0 = disabled; periodic auto-snapshot interval
    "encrypt_snapshots": True,  # encrypt snapshots with age
}


class Config:
    """Screenbox configuration."""

    def __init__(self, base_dir: str | Path | None = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        elif os.environ.get("SCREENBOX_BASE_DIR"):
            self.base_dir = Path(os.environ["SCREENBOX_BASE_DIR"])
        else:
            self.base_dir = Path.home() / ".screenbox"

        self.config_file = self.base_dir / "config.json"
        self._data: dict[str, Any] = {}
        self._ensure_dirs()
        self._load()

    def _ensure_dirs(self):
        """Create ~/.screenbox directory structure."""
        for subdir in ("logs", "logs/screenshots"):
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _load(self):
        """Load config from file, creating with platform defaults if needed."""
        if self.config_file.exists():
            with open(self.config_file) as f:
                self._data = json.load(f)
        else:
            self._data = {**DEFAULT_CONFIG, **_detect_platform_defaults()}
            self._save()

    def reload(self):
        """Re-read config from disk (hot reload)."""
        self._load()

    def _save(self):
        """Write config to disk with restrictive permissions."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w") as f:
            json.dump(self._data, f, indent=2)
        try:
            os.chmod(self.config_file, 0o600)
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    @property
    def viewport_width(self) -> int:
        return int(self.get("default_viewport", "1280x720").split("x")[0])

    @property
    def viewport_height(self) -> int:
        return int(self.get("default_viewport", "1280x720").split("x")[1])

    @property
    def max_desktops(self) -> int:
        return int(self.get("max_desktops", 5))

    @property
    def memory_limit(self) -> str:
        return self.get("memory_per_desktop", "512m")

    @property
    def image(self) -> str:
        return self.get("image", "screenbox:latest")

    @property
    def docker_network(self) -> str | None:
        return os.environ.get("SCREENBOX_DOCKER_NETWORK") or self.get("docker_network", None)

    @property
    def port_bind_address(self) -> str:
        return self.get("port_bind_address", "127.0.0.1")

    @property
    def look_factor(self) -> float:
        """Factor for adaptive look radius: radius = min(screen_w, screen_h) * factor."""
        return float(self.get("look_factor", 0.24))

    @property
    def desktops_dir(self) -> Path:
        return self.base_dir / "desktops"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def snapshots_dir(self) -> Path:
        return self.base_dir / "snapshots"

    def desktop_dir(self, desktop_id: str) -> Path:
        """Get or create desktop data directory (metadata only, no bind mounts)."""
        d = self.desktops_dir / desktop_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def snapshot_dir(self, desktop_id: str) -> Path:
        """Get or create snapshot directory for a desktop."""
        d = self.snapshots_dir / desktop_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def auto_snapshot_minutes(self) -> int:
        return int(self.get("auto_snapshot_minutes", 0))

    @property
    def encrypt_snapshots(self) -> bool:
        return bool(self.get("encrypt_snapshots", True))

    @property
    def age_key_file(self) -> Path:
        return self.base_dir / "age-key.txt"

    def ensure_age_key(self) -> str:
        """Get or create age encryption key. Returns path to key file."""
        key_file = self.age_key_file
        if key_file.exists():
            return str(key_file)
        # Generate new key pair
        import subprocess
        result = subprocess.run(
            ["age-keygen"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to generate age key: {result.stderr}")
        key_file.write_text(result.stdout)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        return str(key_file)
