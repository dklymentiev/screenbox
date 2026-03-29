"""Desktop Manager -- Docker container lifecycle.

Creates, destroys, pauses, resumes, snapshots, and restores
Docker containers that serve as virtual desktops.
"""

import gzip as gzip_module
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .config import Config
from .logger import log_lifecycle

log = logging.getLogger("screenbox.manager")

CONTAINER_PREFIX = "screenbox-"
_DESKTOP_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")


def _validate_desktop_id(desktop_id: str) -> str:
    """Validate and sanitize desktop_id to prevent injection."""
    if not desktop_id or not _DESKTOP_ID_RE.match(desktop_id):
        raise ValueError(
            f"Invalid desktop_id '{desktop_id}'. "
            "Must be 1-63 chars, alphanumeric/dash/underscore, start with alphanumeric."
        )
    return desktop_id


def _validate_container_path(path: str) -> str:
    """Validate container path to prevent traversal attacks."""
    if not path or not path.startswith("/"):
        raise ValueError(f"Container path must be absolute: {path}")
    # Normalize and check for traversal
    from pathlib import PurePosixPath
    normalized = str(PurePosixPath(path))
    if ".." in normalized.split("/"):
        raise ValueError(f"Path traversal not allowed: {path}")
    return normalized


class DesktopState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    SAVED = "saved"
    ERROR = "error"
    HUMAN_CONTROLLED = "human_controlled"


# Callback type for state change notifications
# signature: (event: str, desktop_id: str, data: dict) -> None
StateChangeCallback = Optional["Callable[[str, str, dict], None]"]


@dataclass
class DesktopInfo:
    desktop_id: str
    state: DesktopState
    container_id: Optional[str] = None
    vnc_port: Optional[int] = None
    novnc_port: Optional[int] = None  # legacy alias for rdp_port
    rdp_port: Optional[int] = None
    ws_port: Optional[int] = None
    created_at: Optional[float] = None
    last_tool_call: Optional[float] = None
    label: Optional[str] = None
    error: Optional[str] = None
    acquired_by: Optional[str] = None
    acquired_at: Optional[float] = None
    session_token: Optional[str] = None
    screen_width: int = 1280
    screen_height: int = 720
    image: Optional[str] = None


class DesktopManager:
    """Manages Docker containers as virtual desktops."""

    def __init__(self, config: Config, on_state_change=None):
        self.config = config
        self._desktops: dict[str, DesktopInfo] = {}
        self._snapshot_active: set[str] = set()  # prevent concurrent snapshots
        self._grid_state: dict[str, tuple[int, int]] = {}  # desktop_id -> (cols, rows)
        self._port_lock = threading.Lock()
        self._on_state_change = on_state_change
        self._check_docker()
        self._recover_existing()
        self._start_sync_loop()

    def _start_sync_loop(self):
        """Periodically sync in-memory state with Docker containers."""
        def _sync():
            while True:
                time.sleep(30)
                try:
                    self._sync_with_docker()
                except Exception:
                    pass
        t = threading.Thread(target=_sync, daemon=True)
        t.start()

    def _sync_with_docker(self):
        """Remove ghost desktops and update states from Docker."""
        try:
            result = subprocess.run(
                ["docker", "ps", "-a",
                 "--filter", f"name={CONTAINER_PREFIX}",
                 "--format", "{{.Names}}\t{{.Status}}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                log.warning("docker ps failed, skipping ghost cleanup")
                return
            # Build set of existing containers
            existing = {}
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                name, status = parts[0], parts[1]
                did = name.removeprefix(CONTAINER_PREFIX)
                if did in self._COMPOSE_SERVICES:
                    continue
                if name.endswith("-mcp-1") or name.endswith("-socket-proxy-1"):
                    continue
                existing[did] = status

            # Remove ghosts (in memory but not in Docker)
            # Safety: only remove if docker ps returned at least some containers
            # (empty result may mean docker/proxy is not ready yet)
            if not existing and self._desktops:
                log.warning("docker ps returned no containers but we have %d in memory — skipping ghost cleanup", len(self._desktops))
                return
            ghosts = [did for did in list(self._desktops.keys())
                      if did not in existing
                      and self._desktops[did].state != DesktopState.SAVED]
            for did in ghosts:
                log.info("Removing ghost desktop %s (container no longer exists)", did)
                del self._desktops[did]
                self._emit("destroyed", did)

            # Update states for existing
            for did, status in existing.items():
                if did not in self._desktops:
                    continue
                info = self._desktops[did]
                if "Up" in status and "(Paused)" in status:
                    if info.state != DesktopState.PAUSED:
                        info.state = DesktopState.PAUSED
                elif "Up" in status:
                    if info.state not in (DesktopState.RUNNING, DesktopState.HUMAN_CONTROLLED):
                        info.state = DesktopState.RUNNING
                elif "Exited" in status or "Created" in status:
                    if info.state != DesktopState.STOPPED:
                        info.state = DesktopState.STOPPED
        except Exception as e:
            log.debug("Docker sync failed: %s", e)

    def _emit(self, event: str, desktop_id: str, **data):
        """Notify state change callback if registered."""
        if self._on_state_change:
            try:
                self._on_state_change(event, desktop_id, data)
            except Exception:
                log.exception("Error in state change callback")

    def _check_docker(self):
        """Pre-flight: verify Docker is available and running (with retry for proxy startup)."""
        for attempt in range(5):
            try:
                result = subprocess.run(
                    ["docker", "info"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    return
                err = result.stderr.lower()
                if "permission denied" in err:
                    raise RuntimeError(
                        "Docker permission denied. Add your user to the docker group: "
                        "sudo usermod -aG docker $USER"
                    )
                if attempt < 4 and ("cannot connect" in err or "no such host" in err):
                    log.warning("Docker not ready (attempt %d/5), retrying in 2s...", attempt + 1)
                    time.sleep(2)
                    continue
                if "cannot connect" in err or "is the docker daemon running" in err:
                    raise RuntimeError(
                        "Docker daemon is not running. Start it with: "
                        "sudo systemctl start docker"
                    )
                raise RuntimeError(f"Docker error: {result.stderr[:200]}")
            except FileNotFoundError:
                raise RuntimeError(
                    "Docker not found. Install Docker: https://docs.docker.com/get-docker/"
                )

    def _check_desktop_image(self):
        """Verify desktop image exists. Called before create()."""
        image = self.config.image
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Desktop image '{image}' not found. "
                    f"Run: ./setup.sh to build it."
                )
        except FileNotFoundError:
            pass  # docker not found — _check_docker handles this

    # Compose service names to exclude from desktop recovery (container_name or service suffix)
    _COMPOSE_SERVICES = {
        "screenbox-mcp", "screenbox-dashboard", "screenbox-socket-proxy",
    }
    # Compose label key -- containers with this label are infrastructure, not desktops
    _COMPOSE_LABEL = "com.docker.compose.service"

    def _recover_existing(self):
        """Find existing screenbox containers and recover state.

        Discovers ALL containers matching the screenbox- prefix, not just
        those with the desktop label. This ensures desktops created by the
        dashboard, older versions, or manual docker run are picked up.
        Compose infrastructure containers are excluded by name.
        """
        try:
            # Discover all screenbox-* containers, skip compose infrastructure
            found = set()
            fmt = "{{.Names}}\t{{.Status}}\t{{.ID}}\t{{.Label \"com.docker.compose.service\"}}"
            result = subprocess.run(
                ["docker", "ps", "-a",
                 "--filter", f"name={CONTAINER_PREFIX}",
                 "--format", fmt],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                name, status, container_id = parts[0], parts[1], parts[2]
                compose_service = parts[3] if len(parts) > 3 else ""

                # Skip compose infrastructure containers (MCP server, dashboard, proxy)
                if compose_service in self._COMPOSE_SERVICES:
                    continue

                desktop_id = name.removeprefix(CONTAINER_PREFIX)
                if desktop_id in found:
                    continue
                found.add(desktop_id)

                state = DesktopState.STOPPED
                if "Up" in status:
                    state = DesktopState.RUNNING
                    if "(Paused)" in status:
                        state = DesktopState.PAUSED

                ports = self._get_container_ports(container_id)
                # Get image name
                try:
                    img_result = subprocess.run(
                        ["docker", "inspect", "--format", "{{.Config.Image}}", container_id],
                        capture_output=True, text=True, timeout=5,
                    )
                    container_image = img_result.stdout.strip() or None
                except Exception:
                    container_image = None
                info = DesktopInfo(
                    desktop_id=desktop_id,
                    state=state,
                    container_id=container_id,
                    rdp_port=ports.get("rdp"),
                    novnc_port=ports.get("rdp"),
                    vnc_port=ports.get("vnc"),
                    ws_port=ports.get("ws"),
                    image=container_image,
                )
                self._desktops[desktop_id] = info

                # Ensure desktop is on the managed network (needed for VNC proxy)
                if self.config.docker_network and state == DesktopState.RUNNING:
                    subprocess.run(
                        ["docker", "network", "connect",
                         self.config.docker_network, name],
                        capture_output=True, text=True, timeout=10,
                    )

                log.info("Recovered desktop %s (state=%s)", desktop_id, state.value)

            if found:
                log.info("Recovered %d desktop(s): %s", len(found), ", ".join(sorted(found)))

            # Discover saved desktops (data on volume, no container)
            desktops_dir = os.path.join(self.config.base_dir, "desktops")
            if os.path.isdir(desktops_dir):
                for name in os.listdir(desktops_dir):
                    if name in self._desktops or name in found:
                        continue  # already recovered as running/stopped
                    dossier = os.path.join(desktops_dir, name)
                    if not os.path.isdir(dossier):
                        continue
                    # Check if home volume also exists
                    home_vol = f"screenbox-{name}-home"
                    vol_check = subprocess.run(
                        ["docker", "volume", "inspect", home_vol],
                        capture_output=True, text=True, timeout=5,
                    )
                    if vol_check.returncode != 0:
                        continue  # no home volume = incomplete data, skip
                    self._desktops[name] = DesktopInfo(
                        desktop_id=name,
                        state=DesktopState.SAVED,
                    )
                    log.info("Found saved desktop: %s", name)
        except Exception as e:
            log.warning("Failed to recover existing containers: %s", e)

    def _get_container_ports(self, container_id: str) -> dict:
        """Get mapped ports from a container."""
        try:
            result = subprocess.run(
                ["docker", "port", container_id],
                capture_output=True, text=True, timeout=5,
            )
            ports = {}
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Format: 3389/tcp -> 0.0.0.0:49153
                if "3389" in line:
                    ports["rdp"] = int(line.split(":")[-1])
                elif "5900" in line:
                    ports["vnc"] = int(line.split(":")[-1])
                elif "8765" in line:
                    ports["ws"] = int(line.split(":")[-1])
            return ports
        except Exception:
            return {}

    def _next_ports(self) -> dict[str, int]:
        """Allocate next available ports, reusing freed slots. Thread-safe."""
        with self._port_lock:
            used_bases = set()
            for info in self._desktops.values():
                if info.rdp_port:
                    used_bases.add(info.rdp_port - (info.rdp_port % 10))

            base = 16080
            while base in used_bases:
                base += 10

            return {
                "rdp": base,
                "vnc": base + 1,
                "ws": base + 2,
            }

    def list_desktops(self) -> list[dict]:
        """List all desktops with their state."""
        return [
            {
                "desktop_id": info.desktop_id,
                "state": info.state.value,
                "rdp_port": info.rdp_port if info.state == DesktopState.RUNNING else None,
                "novnc_port": info.rdp_port if info.state == DesktopState.RUNNING else None,
                "label": info.label,
                "created_at": info.created_at,
                "last_tool_call": info.last_tool_call,
            }
            for info in self._desktops.values()
        ]

    def create(self, desktop_id: str, label: Optional[str] = None,
               url: str = "none",
               resolution: Optional[str] = None,
               image: Optional[str] = None,
               profile_json: Optional[str] = None,
               profile_name: Optional[str] = None) -> DesktopInfo:
        """Create and start a new desktop container.

        Args:
            image: Docker image to use (e.g. "screenbox:mate"). Defaults to config image.
            profile_json: JSON string with profile data (written to dossier).
            profile_name: Name of the profile template used (for meta.json).
        """
        _validate_desktop_id(desktop_id)
        if desktop_id in self._desktops:
            existing = self._desktops[desktop_id]
            if existing.state == DesktopState.RUNNING:
                return existing
            # Stopped container -- remove and recreate
            self._remove_container(desktop_id)

        self.config.reload()
        if len([d for d in self._desktops.values()
                if d.state in (DesktopState.RUNNING, DesktopState.PAUSED)]) >= self.config.max_desktops:
            raise RuntimeError(
                f"Maximum desktops ({self.config.max_desktops}) reached. "
                "Destroy or pause existing desktops first."
            )

        # Parse per-desktop resolution or fall back to config default
        if resolution:
            match = re.match(r"^(\d{3,5})x(\d{3,5})$", resolution)
            if not match:
                raise ValueError(
                    f"Invalid resolution '{resolution}'. Use format 'WIDTHxHEIGHT' (e.g. '1920x1080')."
                )
            w, h = int(match.group(1)), int(match.group(2))
        else:
            w, h = self.config.viewport_width, self.config.viewport_height

        ports = self._next_ports()
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        desktop_dir = self.config.desktop_dir(desktop_id)

        info = DesktopInfo(
            desktop_id=desktop_id,
            state=DesktopState.STARTING,
            rdp_port=ports["rdp"],
            novnc_port=ports["rdp"],
            vnc_port=ports["vnc"],
            ws_port=ports["ws"],
            label=label,
            created_at=time.time(),
            screen_width=w,
            screen_height=h,
            image=image,
        )
        self._desktops[desktop_id] = info
        self._emit("starting", desktop_id, label=label)

        viewport = f"{w}x{h}"

        # Named volume for persistent home directory.
        # Data survives container restart/recreation on any OS (Linux/Windows/macOS).
        # Volume is NOT removed on destroy -- only explicit `docker volume rm` deletes it.
        volume_name = f"screenbox-{desktop_id}-home"

        # Shared data volume (same volume as MCP -- profiles, recordings, knowledge)
        data_volume = os.environ.get("SCREENBOX_DATA_VOLUME", "screenbox_screenbox-data")

        # Prepare desktop dossier: profile, meta, recordings dir
        dossier_dir = os.path.join(self.config.base_dir, "desktops", desktop_id)
        rec_dir_inside = os.path.join(dossier_dir, "recordings")
        os.makedirs(rec_dir_inside, mode=0o755, exist_ok=True)
        os.makedirs(os.path.join(dossier_dir, "knowledge"), mode=0o755, exist_ok=True)

        # Copy profile from template to dossier
        if profile_json:
            profile_dst = os.path.join(dossier_dir, "profile.json")
            with open(profile_dst, "w") as pf:
                pf.write(profile_json)
            log.info("Profile written to dossier for %s", desktop_id)

        # Write meta.json (update on recreate, preserve existing profile if none provided)
        meta_path = os.path.join(dossier_dir, "meta.json")
        meta = {
            "id": desktop_id, "label": label, "image": image or self.config.image,
            "profile": profile_name, "created_at": time.time(),
            "resolution": viewport,
        }
        # On recreate: keep existing profile name if not overridden
        if not profile_name and os.path.exists(meta_path):
            try:
                old_meta = json.loads(open(meta_path).read())
                meta["profile"] = old_meta.get("profile")
            except (OSError, json.JSONDecodeError):
                pass
        with open(meta_path, "w") as mf:
            json.dump(meta, mf, indent=2)

        # Paths inside desktop container (via shared volume)
        profile_container_path = f"/data/screenbox/desktops/{desktop_id}/profile.json"
        recordings_container_path = f"/data/screenbox/desktops/{desktop_id}/recordings"

        create_cmd = [
            "docker", "create",
            "--name", container_name,
            "-v", f"{volume_name}:/home/screenbox",
            "-v", f"{data_volume}:/data/screenbox",
            "-p", f"{self.config.port_bind_address}:{ports['rdp']}:3389",
            "-p", f"{self.config.port_bind_address}:{ports['vnc']}:5900",
            "-p", f"{self.config.port_bind_address}:{ports['ws']}:8765",
            "-m", self.config.memory_limit,
            "--memory-swap", self.config.memory_limit,  # no swap beyond memory limit
            "--cpus", "2",
            "--restart", "unless-stopped",
            "-e", f"SCREEN_WIDTH={w}",
            "-e", f"SCREEN_HEIGHT={h}",
            "-e", f"CHROME_URL={url}",
            "-e", f"DESKTOP_ID={desktop_id}",
            "-e", f"SCREENBOX_PROFILE={profile_container_path}",
            "-e", f"SCREENBOX_RECORDINGS={recordings_container_path}",
            "--shm-size=256m",
            "--dns", "8.8.8.8", "--dns", "1.1.1.1",
            "--label", "screenbox.desktop=true",
            image or self.config.image,
        ]

        try:
            # docker create + start separately (docker run -d hangs through proxy)
            result = subprocess.run(
                create_cmd, capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                info.state = DesktopState.ERROR
                info.error = result.stderr[:500]
                self._emit("error", desktop_id, error=result.stderr[:200])
                log_lifecycle(self.config.logs_dir, "create_failed", desktop_id, error=result.stderr[:200])
                raise RuntimeError(f"Failed to create container: {result.stderr[:200]}")

            info.container_id = result.stdout.strip()[:12]

            # Connect to managed network (needed for VNC/RDP proxy from dashboard).
            # Desktop also stays on bridge for reliable DNS (dual-network).
            if self.config.docker_network:
                subprocess.run(
                    ["docker", "network", "connect",
                     self.config.docker_network, container_name],
                    capture_output=True, text=True, timeout=10,
                )

            # Profile already written to shared volume dossier (before docker create)
            # No docker cp needed -- entrypoint reads from $SCREENBOX_PROFILE

            # Start container (separate from create to avoid docker run -d proxy hang)
            start_result = subprocess.run(
                ["docker", "start", container_name],
                capture_output=True, text=True, timeout=30,
            )
            if start_result.returncode != 0:
                info.state = DesktopState.ERROR
                info.error = start_result.stderr[:500]
                raise RuntimeError(f"Failed to start container: {start_result.stderr[:200]}")

            info.state = DesktopState.RUNNING
            self._emit("created", desktop_id, state="running",
                       label=label, rdp_port=ports["rdp"])

            log.info("Created desktop %s (container=%s, rdp=%d)",
                     desktop_id, info.container_id, ports["rdp"])

            # Wait for desktop services to be ready
            try:
                self.wait_ready(desktop_id, timeout=15)
            except Exception:
                pass  # Non-fatal: desktop may still be starting

            # Auto-restore from latest snapshot if one exists
            # Extra wait to ensure entrypoint has finished initializing home dir
            snap_dir = self.config.snapshot_dir(desktop_id)
            snaps = sorted(snap_dir.glob("snapshot-*.tar.gz*")) if snap_dir.exists() else []
            if snaps:
                time.sleep(3)  # let entrypoint finish before overwriting home
                log.info("Found %d snapshot(s) for %s, restoring latest...", len(snaps), desktop_id)
                self.restore(desktop_id)

            log_lifecycle(self.config.logs_dir, "created", desktop_id,
                         container_id=info.container_id,
                         ports={"rdp": ports["rdp"], "vnc": ports["vnc"], "ws": ports["ws"]},
                         memory=self.config.memory_limit)
            return info

        except subprocess.TimeoutExpired:
            info.state = DesktopState.ERROR
            info.error = "Container creation timed out"
            log_lifecycle(self.config.logs_dir, "create_failed", desktop_id, error="timeout")
            raise RuntimeError("Container creation timed out after 30s")

    def destroy(self, desktop_id: str, auto_snapshot: bool = True) -> bool:
        """Stop and remove a desktop container. Data is kept (state -> saved).

        Use delete_data() to remove saved data afterwards.
        """
        if desktop_id not in self._desktops:
            return False
        self._remove_container(desktop_id)
        info = self._desktops[desktop_id]
        info.state = DesktopState.SAVED
        info.ports = {}
        self._emit("state_changed", desktop_id, state="saved")
        log.info("Destroyed desktop %s (data kept as saved)", desktop_id)
        log_lifecycle(self.config.logs_dir, "destroyed", desktop_id)
        return True

    def delete_data(self, desktop_id: str) -> bool:
        """Delete all saved data for a desktop (home volume + dossier).

        Call after destroy() to fully remove a desktop, or standalone
        to clean up orphaned saved data.
        """
        import shutil
        deleted = False
        # Remove dossier (recordings, knowledge, profile)
        dossier = os.path.join(self.config.base_dir, "desktops", desktop_id)
        if os.path.isdir(dossier):
            shutil.rmtree(dossier, ignore_errors=True)
            log.info("Removed desktop data: %s", dossier)
            deleted = True
        # Remove named home volume (browser profile, installed apps, etc.)
        home_vol = f"screenbox-{desktop_id}-home"
        result = subprocess.run(
            ["docker", "volume", "rm", "-f", home_vol],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            log.info("Removed home volume: %s", home_vol)
            deleted = True
        # Remove from in-memory desktop list
        if desktop_id in self._desktops:
            del self._desktops[desktop_id]
            self._emit("destroyed", desktop_id)
        return deleted

    def _remove_container(self, desktop_id: str):
        """Force remove the container."""
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, text=True, timeout=15,
        )

    def pause(self, desktop_id: str) -> bool:
        """Pause (freeze) a running desktop. Auto-saves snapshot first."""
        info = self._desktops.get(desktop_id)
        if not info or info.state != DesktopState.RUNNING:
            return False
        # Auto-snapshot before pause so state survives Docker restart
        self.snapshot(desktop_id, "auto")
        result = subprocess.run(
            ["docker", "pause", f"{CONTAINER_PREFIX}{desktop_id}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            info.state = DesktopState.PAUSED
            self._emit("paused", desktop_id, state="paused")
            log.info("Paused desktop %s", desktop_id)
            log_lifecycle(self.config.logs_dir, "paused", desktop_id)
            return True
        return False

    def resume(self, desktop_id: str) -> bool:
        """Resume (unfreeze) a paused desktop."""
        info = self._desktops.get(desktop_id)
        if not info:
            return False
        # If cache says paused but docker is actually running, fix cache
        if info.state == DesktopState.PAUSED:
            result = subprocess.run(
                ["docker", "unpause", f"{CONTAINER_PREFIX}{desktop_id}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                info.state = DesktopState.RUNNING
                self._emit("resumed", desktop_id, state="running")
                log.info("Resumed desktop %s", desktop_id)
                log_lifecycle(self.config.logs_dir, "resumed", desktop_id)
                return True
            # unpause failed -- maybe already running, check
            check = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", f"{CONTAINER_PREFIX}{desktop_id}"],
                capture_output=True, text=True, timeout=5,
            )
            if "running" in check.stdout.strip():
                info.state = DesktopState.RUNNING
                log.info("Desktop %s was already running, fixed cache", desktop_id)
                return True
        return False

    def stop(self, desktop_id: str) -> bool:
        """Stop a running desktop container."""
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED):
            return False
        result = subprocess.run(
            ["docker", "stop", f"{CONTAINER_PREFIX}{desktop_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            info.state = DesktopState.STOPPED
            self._emit("stopped", desktop_id, state="stopped")
            log.info("Stopped desktop %s", desktop_id)
            log_lifecycle(self.config.logs_dir, "stopped", desktop_id)
            return True
        return False

    def start(self, desktop_id: str) -> bool:
        """Start a stopped desktop container."""
        info = self._desktops.get(desktop_id)
        if not info or info.state != DesktopState.STOPPED:
            return False
        result = subprocess.run(
            ["docker", "start", f"{CONTAINER_PREFIX}{desktop_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            info.state = DesktopState.RUNNING
            self._emit("started", desktop_id, state="running")
            log.info("Started desktop %s", desktop_id)
            log_lifecycle(self.config.logs_dir, "started", desktop_id)
            return True
        return False

    def snapshot(self, desktop_id: str, label: str = "") -> Optional[str]:
        """Save /home/screenbox from container as tar.gz snapshot.

        Returns snapshot filename or None on failure.
        Desktop can be running or stopped.
        """
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED, DesktopState.STOPPED):
            return None

        snap_dir = self.config.snapshot_dir(desktop_id)
        timestamp = int(time.time())
        safe_label = label.replace("/", "-").replace(" ", "_")[:50] if label else ""
        filename = f"snapshot-{timestamp}"
        if safe_label:
            filename += f"-{safe_label}"
        filename += ".tar.gz"
        snap_path = snap_dir / filename

        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        try:
            result = subprocess.run(
                ["docker", "exec", "-u", "root", container_name,
                 "tar", "cf", "-", "--warning=no-file-changed",
                 "-C", "/", "home/screenbox"],
                capture_output=True, timeout=120,
            )
            # tar exit 1 = warnings (file changed during read), still usable
            if result.returncode > 1:
                log.error("Snapshot failed for %s (exit %d): %s",
                         desktop_id, result.returncode, result.stderr[:200])
                return None
            if not result.stdout:
                log.error("Snapshot empty for %s", desktop_id)
                return None

            # Compress on MCP side (avoids orphan gzip processes inside container)
            snapshot_data = gzip_module.compress(result.stdout)

            # Encrypt with age if enabled
            if self.config.encrypt_snapshots:
                encrypted = self._age_encrypt(snapshot_data)
                if encrypted is None:
                    log.warning("Encryption failed for %s, saving unencrypted", desktop_id)
                    snap_path.write_bytes(snapshot_data)
                else:
                    filename = filename.replace(".tar.gz", ".tar.gz.age")
                    snap_path = snap_dir / filename
                    snap_path.write_bytes(encrypted)
            else:
                snap_path.write_bytes(snapshot_data)

            try:
                snap_path.chmod(0o600)
            except OSError:
                pass
        except subprocess.TimeoutExpired:
            log.error("Snapshot timed out for %s", desktop_id)
            return None

        # Enforce retention: keep last 5
        self._enforce_snapshot_retention(desktop_id, keep=5)

        size_mb = round(snap_path.stat().st_size / (1024 * 1024), 1)
        encrypted_tag = " [encrypted]" if filename.endswith(".age") else ""
        log.info("Snapshot saved: %s (%s MB)%s", filename, size_mb, encrypted_tag)
        log_lifecycle(self.config.logs_dir, "snapshot_saved", desktop_id,
                     filename=filename, size_mb=size_mb,
                     encrypted=filename.endswith(".age"))
        return filename

    def restore(self, desktop_id: str, snapshot_name: Optional[str] = None) -> bool:
        """Restore /home/screenbox from a snapshot into a running container.

        If snapshot_name is None, uses the latest snapshot.
        Returns True on success.
        """
        snap_dir = self.config.snapshot_dir(desktop_id)
        if not snap_dir.exists():
            return False

        if snapshot_name:
            snap_path = snap_dir / snapshot_name
        else:
            # Find latest snapshot (encrypted or plain)
            snaps = sorted(snap_dir.glob("snapshot-*.tar.gz*"))
            if not snaps:
                return False
            snap_path = snaps[-1]

        if not snap_path.exists():
            return False

        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED):
            return False

        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        try:
            # Decrypt if encrypted
            is_encrypted = snap_path.name.endswith(".age")
            if is_encrypted:
                tar_data = self._age_decrypt(snap_path.read_bytes())
                if tar_data is None:
                    log.error("Decryption failed for %s", snap_path.name)
                    return False
            else:
                tar_data = snap_path.read_bytes()

            result = subprocess.run(
                ["docker", "exec", "-i", "-u", "root", container_name,
                 "tar", "xzf", "-", "-C", "/",
                 "--no-same-owner", "--no-same-permissions"],
                input=tar_data,
                capture_output=True, timeout=120,
            )
            if result.returncode != 0:
                log.error("Restore failed for %s: %s", desktop_id, result.stderr[:200])
                return False
            # Fix ownership
            subprocess.run(
                ["docker", "exec", "-u", "root", container_name,
                 "chown", "-R", "screenbox:screenbox", "/home/screenbox"],
                capture_output=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            log.error("Restore timed out for %s", desktop_id)
            return False

        log.info("Restored %s from %s", desktop_id, snap_path.name)
        log_lifecycle(self.config.logs_dir, "snapshot_restored", desktop_id,
                     filename=snap_path.name)
        return True

    def list_snapshots(self, desktop_id: str) -> list[dict]:
        """List available snapshots for a desktop."""
        snap_dir = self.config.snapshot_dir(desktop_id)
        if not snap_dir.exists():
            return []
        result = []
        for f in sorted(snap_dir.glob("snapshot-*.tar.gz*")):
            stat = f.stat()
            result.append({
                "name": f.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "created": stat.st_mtime,
                "encrypted": f.name.endswith(".age"),
            })
        return result

    def _enforce_snapshot_retention(self, desktop_id: str, keep: int = 5):
        """Delete oldest snapshots beyond retention limit."""
        snap_dir = self.config.snapshot_dir(desktop_id)
        snaps = sorted(snap_dir.glob("snapshot-*.tar.gz*"))
        while len(snaps) > keep:
            oldest = snaps.pop(0)
            oldest.unlink()
            log.info("Deleted old snapshot: %s", oldest.name)

    # -- Age encryption --

    def _age_encrypt(self, data: bytes) -> Optional[bytes]:
        """Encrypt data with age using the config key."""
        try:
            key_file = self.config.ensure_age_key()
            # Extract public key (recipient) from key file
            with open(key_file) as f:
                for line in f:
                    if line.startswith("# public key:"):
                        recipient = line.split(":", 1)[1].strip()
                        break
                else:
                    log.error("No public key found in %s", key_file)
                    return None
            result = subprocess.run(
                ["age", "-r", recipient],
                input=data, capture_output=True, timeout=60,
            )
            if result.returncode != 0:
                log.error("age encrypt failed: %s", result.stderr[:200])
                return None
            return result.stdout
        except Exception as e:
            log.error("age encrypt error: %s", e)
            return None

    def _age_decrypt(self, data: bytes) -> Optional[bytes]:
        """Decrypt age-encrypted data using the config key."""
        try:
            key_file = self.config.ensure_age_key()
            result = subprocess.run(
                ["age", "-d", "-i", key_file],
                input=data, capture_output=True, timeout=60,
            )
            if result.returncode != 0:
                log.error("age decrypt failed: %s", result.stderr[:200])
                return None
            return result.stdout
        except Exception as e:
            log.error("age decrypt error: %s", e)
            return None

    # -- File operations (docker cp) --

    def file_upload(self, desktop_id: str, container_path: str, data: bytes) -> bool:
        """Upload file into container via docker cp."""
        container_path = _validate_container_path(container_path)
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED):
            return False
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        try:
            # docker cp reads from stdin with -
            # Use tar to send single file
            import tarfile
            import io
            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w") as tar:
                ti = tarfile.TarInfo(name=container_path.split("/")[-1])
                ti.size = len(data)
                ti.uid = 1000
                ti.gid = 1000
                tar.addfile(ti, io.BytesIO(data))
            tar_buf.seek(0)

            dest_dir = "/".join(container_path.split("/")[:-1]) or "/tmp"
            # Ensure directory exists
            subprocess.run(
                ["docker", "exec", "-u", "root", container_name,
                 "mkdir", "-p", dest_dir],
                capture_output=True, timeout=5,
            )
            result = subprocess.run(
                ["docker", "cp", "-", f"{container_name}:{dest_dir}"],
                input=tar_buf.read(), capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                log.error("Upload failed: %s", result.stderr[:200])
                return False
            # Fix ownership
            subprocess.run(
                ["docker", "exec", "-u", "root", container_name,
                 "chown", "screenbox:screenbox", container_path],
                capture_output=True, timeout=5,
            )
            return True
        except Exception as e:
            log.error("Upload error: %s", e)
            return False

    def file_download(self, desktop_id: str, container_path: str) -> Optional[bytes]:
        """Download file from container via docker cp."""
        container_path = _validate_container_path(container_path)
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED):
            return None
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        try:
            result = subprocess.run(
                ["docker", "exec", "-u", "screenbox", container_name,
                 "cat", container_path],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                return None
            return result.stdout
        except Exception:
            return None

    def file_ls(self, desktop_id: str, container_path: str = "/home/screenbox") -> Optional[str]:
        """List files in container."""
        container_path = _validate_container_path(container_path)
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED):
            return None
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        try:
            result = subprocess.run(
                ["docker", "exec", "-u", "screenbox", container_name,
                 "ls", "-la", container_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return f"Error: {result.stderr[:200]}"
            return result.stdout
        except Exception as e:
            return f"Error: {e}"

    def file_upload_tar(self, desktop_id: str, tar_data: bytes,
                        dest_dir: str = "/home/screenbox") -> bool:
        """Upload and extract a tar.gz archive into container."""
        dest_dir = _validate_container_path(dest_dir)
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED):
            return False
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        try:
            # Ensure dest exists
            subprocess.run(
                ["docker", "exec", "-u", "root", container_name,
                 "mkdir", "-p", dest_dir],
                capture_output=True, timeout=5,
            )
            result = subprocess.run(
                ["docker", "exec", "-i", "-u", "root", container_name,
                 "tar", "xzf", "-", "-C", dest_dir,
                 "--no-same-owner", "--no-same-permissions"],
                input=tar_data, capture_output=True, timeout=120,
            )
            if result.returncode > 1:
                log.error("Upload tar failed: %s", result.stderr[:200])
                return False
            # Fix ownership
            subprocess.run(
                ["docker", "exec", "-u", "root", container_name,
                 "chown", "-R", "screenbox:screenbox", dest_dir],
                capture_output=True, timeout=30,
            )
            return True
        except Exception as e:
            log.error("Upload tar error: %s", e)
            return False

    def disk_usage(self, desktop_id: str) -> Optional[dict]:
        """Get disk usage of key directories inside container."""
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.PAUSED):
            return None
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        try:
            result = subprocess.run(
                ["docker", "exec", "-u", "root", container_name,
                 "du", "-sm",
                 "/home/screenbox",
                 "/home/screenbox/.config",
                 "/home/screenbox/.cache",
                 "/home/screenbox/downloads",
                 "/home/screenbox/workspace"],
                capture_output=True, text=True, timeout=30,
            )
            usage = {}
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    path = parts[1].replace("/home/screenbox", "").strip("/") or "total"
                    usage[path] = int(parts[0])
            return usage
        except Exception as e:
            log.error("Disk usage error: %s", e)
            return None

    def snapshot_clone(self, source_desktop_id: str, target_desktop_id: str,
                       snapshot_name: Optional[str] = None) -> bool:
        """Clone a snapshot from one desktop to another.

        Copies snapshot file so target can restore from it.
        If snapshot_name is None, uses the latest snapshot.
        """
        src_dir = self.config.snapshot_dir(source_desktop_id)
        if snapshot_name:
            src_path = src_dir / snapshot_name
        else:
            snaps = sorted(src_dir.glob("snapshot-*.tar.gz"))
            if not snaps:
                return False
            src_path = snaps[-1]

        if not src_path.exists():
            return False

        dst_dir = self.config.snapshot_dir(target_desktop_id)
        dst_path = dst_dir / f"snapshot-{int(time.time())}-cloned-from-{source_desktop_id}.tar.gz"
        shutil.copy2(src_path, dst_path)
        log.info("Cloned snapshot %s -> %s", src_path.name, dst_path.name)
        return True

    def get_memory_info(self, desktop_id: str) -> Optional[dict]:
        """Read cgroup memory usage for a desktop container.

        Returns dict with used_mb, limit_mb, percent, warning (if >80%).
        Returns None if desktop not running or read fails.
        """
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.HUMAN_CONTROLLED):
            return None
        try:
            result = subprocess.run(
                ["docker", "exec", f"{CONTAINER_PREFIX}{desktop_id}",
                 "bash", "-c",
                 "cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max 2>/dev/null || "
                 "cat /sys/fs/cgroup/memory/memory.usage_in_bytes /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return None
            usage = int(lines[0])
            limit_str = lines[1].strip()
            if limit_str == "max":
                return {"used_mb": usage // (1024 * 1024), "limit_mb": None, "percent": 0}
            limit = int(limit_str)
            used_mb = usage // (1024 * 1024)
            limit_mb = limit // (1024 * 1024)
            percent = round(usage / limit * 100, 1) if limit > 0 else 0
            mem = {"used_mb": used_mb, "limit_mb": limit_mb, "percent": percent}
            if percent > 80:
                mem["warning"] = f"HIGH MEMORY: {used_mb}MB / {limit_mb}MB ({percent}%). Desktop may crash. Consider closing apps or increasing limit."
            return mem
        except Exception:
            return None

    def get(self, desktop_id: str) -> Optional[DesktopInfo]:
        """Get desktop info, refreshing state from Docker if stale."""
        info = self._desktops.get(desktop_id)
        if info and info.state in (DesktopState.PAUSED, DesktopState.STOPPED):
            # Re-check actual Docker state (may have changed externally)
            try:
                result = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Status}}:{{.State.Paused}}",
                     f"{CONTAINER_PREFIX}{desktop_id}"],
                    capture_output=True, text=True, timeout=5,
                )
                line = result.stdout.strip()
                if "running:false" in line:
                    info.state = DesktopState.RUNNING
                elif "running:true" in line:
                    info.state = DesktopState.PAUSED
                elif "exited" in line:
                    info.state = DesktopState.STOPPED
            except Exception:
                pass
        return info

    def touch(self, desktop_id: str) -> None:
        """Update last_tool_call timestamp."""
        info = self._desktops.get(desktop_id)
        if info:
            info.last_tool_call = time.time()

    def exec(self, desktop_id: str, cmd: list[str], timeout: int = 10,
             user: str = "screenbox") -> subprocess.CompletedProcess:
        """Execute command inside a desktop container."""
        info = self._desktops.get(desktop_id)
        if not info or info.state not in (DesktopState.RUNNING, DesktopState.HUMAN_CONTROLLED):
            raise RuntimeError(f"Desktop {desktop_id} is not running (state={info.state.value if info else 'unknown'})")
        self.touch(desktop_id)
        return subprocess.run(
            ["docker", "exec", "-u", user, f"{CONTAINER_PREFIX}{desktop_id}"] + cmd,
            capture_output=True, timeout=timeout,
        )

    def wait_ready(self, desktop_id: str, timeout: int = 15) -> bool:
        """Wait for desktop core services: X display + ws-bridge.

        Polls every 0.5s. Chrome starts asynchronously -- agents handle
        Chrome readiness via app_launch or extension reconnect.
        Returns True if ready, False if timed out.
        """
        container_name = f"{CONTAINER_PREFIX}{desktop_id}"
        deadline = time.time() + timeout
        check_cmd = [
            "docker", "exec", "-u", "screenbox", container_name,
            "bash", "-c",
            # X display exists + ws-bridge listening on 8765
            "test -e /tmp/.X11-unix/X99 && "
            "bash -c 'echo >/dev/tcp/127.0.0.1/8765' 2>/dev/null && echo READY"
        ]
        while time.time() < deadline:
            try:
                result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=5)
                if "READY" in result.stdout:
                    elapsed = timeout - (deadline - time.time())
                    log.info("Desktop %s ready (%.1fs)", desktop_id, elapsed)
                    return True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                break
            except Exception:
                pass
            time.sleep(1)
        log.warning("Desktop %s not ready after %ds", desktop_id, timeout)
        return False

    # -- Agent acquire/release --

    @property
    def lease_ttl(self) -> int:
        """Lease TTL in seconds. 0 = no expiry."""
        return int(self.config.get("lease_ttl", 600))

    def acquire(self, desktop_id: str, agent_id: str) -> dict:
        """Acquire exclusive lock on a desktop for an agent.

        Returns dict with acquired status, session_token, and lease_ttl.
        Session token must be passed to validate_session() on subsequent calls.
        """
        import uuid
        info = self._desktops.get(desktop_id)
        if not info:
            return {"acquired": False, "error": f"Desktop '{desktop_id}' not found"}
        if info.state != DesktopState.RUNNING:
            return {"acquired": False, "error": f"Desktop not running (state={info.state.value})"}
        if info.acquired_by and info.acquired_by != agent_id:
            return {"acquired": False, "error": f"Locked by agent '{info.acquired_by}'"}
        # Re-acquire by same agent: refresh token
        info.acquired_by = agent_id
        info.acquired_at = time.time()
        info.session_token = uuid.uuid4().hex[:16]
        log_lifecycle(self.config.logs_dir, "acquired", desktop_id, agent_id=agent_id)
        return {
            "acquired": True,
            "desktop_id": desktop_id,
            "agent_id": agent_id,
            "session_token": info.session_token,
            "lease_ttl": self.lease_ttl,
        }

    def release(self, desktop_id: str, agent_id: str = None) -> bool:
        """Release agent lock on a desktop.

        If agent_id given, only releases if it matches current holder.
        """
        info = self._desktops.get(desktop_id)
        if not info or not info.acquired_by:
            return False
        if agent_id and info.acquired_by != agent_id:
            return False
        released_by = info.acquired_by
        info.acquired_by = None
        info.acquired_at = None
        info.session_token = None
        log_lifecycle(self.config.logs_dir, "released", desktop_id, agent_id=released_by)
        return True

    def heartbeat(self, desktop_id: str, agent_id: str) -> bool:
        """Refresh agent lock timestamp (keep-alive)."""
        info = self._desktops.get(desktop_id)
        if not info or info.acquired_by != agent_id:
            return False
        info.acquired_at = time.time()
        return True

    def validate_session(self, desktop_id: str, agent_id: str,
                         session_token: str = None) -> Optional[str]:
        """Validate agent access and optionally session token.

        Returns None if valid, error string if invalid.
        Also serves as implicit heartbeat (refreshes acquired_at).
        """
        info = self._desktops.get(desktop_id)
        if not info:
            return None  # unmanaged desktop, allow
        if not info.acquired_by:
            return None  # not locked, anyone can use
        if info.acquired_by != agent_id:
            return f"Desktop locked by agent '{info.acquired_by}'"
        if info.session_token and session_token and session_token != info.session_token:
            return f"Invalid session token (desktop locked by another session of '{agent_id}')"
        # Implicit heartbeat
        info.acquired_at = time.time()
        return None

    def smart_acquire(self, agent_id: str, label: str = None,
                      desktop_id: str = None) -> dict:
        """Smart acquire: reuse existing, pick idle, or fail with guidance.

        Priority:
        1. Specific desktop_id (if provided)
        2. Desktop already acquired by this agent
        3. Any idle running desktop
        4. Error with available desktops list
        """
        import uuid

        # Case 1: specific desktop requested
        if desktop_id:
            return self.acquire(desktop_id, agent_id)

        # Case 2: agent already has a desktop
        for did, info in self._desktops.items():
            if info.acquired_by == agent_id and info.state == DesktopState.RUNNING:
                # Refresh token
                info.acquired_at = time.time()
                info.session_token = uuid.uuid4().hex[:16]
                return {
                    "acquired": True,
                    "desktop_id": did,
                    "agent_id": agent_id,
                    "session_token": info.session_token,
                    "lease_ttl": self.lease_ttl,
                    "reused": True,
                }

        # Case 3: find any idle running desktop
        for did, info in self._desktops.items():
            if info.state == DesktopState.RUNNING and not info.acquired_by:
                return self.acquire(did, agent_id)

        # Case 4: no available desktop
        running = [d.desktop_id for d in self._desktops.values()
                   if d.state == DesktopState.RUNNING]
        paused = [d.desktop_id for d in self._desktops.values()
                  if d.state == DesktopState.PAUSED]
        return {
            "acquired": False,
            "error": "No idle desktop available",
            "running_locked": running,
            "paused": paused,
            "hint": "Create a new desktop or resume a paused one",
        }

    def delete_snapshots(self, desktop_id: str) -> bool:
        """Delete all snapshots for a desktop."""
        snap_dir = self.config.snapshot_dir(desktop_id)
        if not snap_dir.exists():
            return False
        shutil.rmtree(snap_dir)
        log_lifecycle(self.config.logs_dir, "snapshots_deleted", desktop_id)
        return True

    # -- Idle auto-pause --

    def check_idle_desktops(self) -> list[str]:
        """Pause idle desktops and auto-release expired leases. Returns list of paused IDs."""
        now = time.time()
        paused = []

        # 1. Log expired leases (no auto-release -- admin-only)
        ttl = self.lease_ttl
        if ttl > 0:
            for desktop_id, info in list(self._desktops.items()):
                if not info.acquired_by or not info.acquired_at:
                    continue
                elapsed = now - info.acquired_at
                if elapsed > ttl:
                    log.info("Lease stale for %s (agent=%s, %ds since heartbeat) -- admin release required",
                             desktop_id, info.acquired_by, int(elapsed))

        # 2. Auto-snapshot running desktops
        auto_snap_min = self.config.auto_snapshot_minutes
        if auto_snap_min > 0:
            auto_snap_threshold = auto_snap_min * 60
            for desktop_id, info in list(self._desktops.items()):
                if info.state != DesktopState.RUNNING:
                    continue
                last_snap = getattr(info, '_last_auto_snapshot', 0)
                if now - last_snap >= auto_snap_threshold:
                    if desktop_id in self._snapshot_active:
                        log.debug("Skipping auto-snapshot for %s -- previous still running", desktop_id)
                        continue
                    try:
                        self._snapshot_active.add(desktop_id)
                        snap_name = self.snapshot(desktop_id, "auto")
                        if snap_name:
                            info._last_auto_snapshot = now
                            log.info("Auto-snapshot for %s: %s", desktop_id, snap_name)
                    except Exception as e:
                        log.warning("Auto-snapshot failed for %s: %s", desktop_id, e)
                    finally:
                        self._snapshot_active.discard(desktop_id)

        # 3. Auto-pause idle desktops
        idle_minutes = self.config.get("idle_pause_minutes", 0)
        if not idle_minutes or idle_minutes <= 0:
            return paused

        idle_threshold = idle_minutes * 60
        for desktop_id, info in list(self._desktops.items()):
            if info.state != DesktopState.RUNNING:
                continue
            # Skip desktops acquired by an agent (they're actively being used)
            if info.acquired_by:
                continue
            last_activity = info.last_tool_call or info.created_at or now
            idle_seconds = now - last_activity
            if idle_seconds >= idle_threshold:
                log.info("Auto-pausing idle desktop %s (idle %.0f min)",
                         desktop_id, idle_seconds / 60)
                if self.pause(desktop_id):
                    log_lifecycle(self.config.logs_dir, "auto_paused", desktop_id,
                                 idle_minutes=round(idle_seconds / 60, 1))
                    paused.append(desktop_id)

        return paused

    def start_idle_checker(self, interval_seconds: int = 60) -> None:
        """Start background thread to check for idle desktops."""
        def _checker():
            while True:
                time.sleep(interval_seconds)
                try:
                    self.check_idle_desktops()
                except Exception as e:
                    log.warning("Idle checker error: %s", e)

        t = threading.Thread(target=_checker, daemon=True, name="idle-checker")
        t.start()
        log.info("Idle checker started (interval=%ds, threshold=%d min)",
                 interval_seconds, self.config.get("idle_pause_minutes", 0))
