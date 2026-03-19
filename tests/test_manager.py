"""Unit tests for DesktopManager."""

import json
import subprocess
import time
import pytest
from unittest.mock import MagicMock, patch, call

from screenbox.config import Config
from screenbox.manager import DesktopManager, DesktopInfo, DesktopState


class TestDesktopState:
    def test_enum_values(self):
        assert DesktopState.STOPPED == "stopped"
        assert DesktopState.RUNNING == "running"
        assert DesktopState.PAUSED == "paused"
        assert DesktopState.ERROR == "error"
        assert DesktopState.HUMAN_CONTROLLED == "human_controlled"


class TestDesktopInfo:
    def test_defaults(self):
        info = DesktopInfo(desktop_id="test", state=DesktopState.STOPPED)
        assert info.desktop_id == "test"
        assert info.state == DesktopState.STOPPED
        assert info.container_id is None
        assert info.vnc_port is None
        assert info.novnc_port is None
        assert info.ws_port is None
        assert info.label is None
        assert info.error is None


class TestDesktopManager:
    @pytest.fixture
    def config(self, tmp_path):
        return Config(base_dir=tmp_path)

    @pytest.fixture
    def manager(self, config):
        with patch("subprocess.run") as mock_run:
            # docker info succeeds, no existing containers
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr="",
            )
            mgr = DesktopManager(config)
        return mgr

    def test_init_checks_docker(self, config):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mgr = DesktopManager(config)
            # docker info should be called
            assert mock_run.call_count >= 1

    def test_init_docker_not_found(self, config):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(RuntimeError, match="Docker not found"):
                DesktopManager(config)

    def test_init_docker_permission_denied(self, config):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="permission denied"
            )
            with pytest.raises(RuntimeError, match="permission denied"):
                DesktopManager(config)

    def test_init_docker_not_running(self, config):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="Is the docker daemon running?"
            )
            with pytest.raises(RuntimeError, match="not running"):
                DesktopManager(config)

    def test_list_empty(self, manager):
        assert manager.list_desktops() == []

    def test_create_desktop(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="abc123def456", stderr="",
            )
            info = manager.create("work", label="Work Desktop", url="https://example.com")

        assert info.desktop_id == "work"
        assert info.state == DesktopState.RUNNING
        assert info.container_id == "abc123def456"
        assert info.label == "Work Desktop"
        assert info.novnc_port is not None
        assert info.vnc_port is not None
        assert info.ws_port is not None

    def test_create_idempotent(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="container1", stderr="",
            )
            info1 = manager.create("same")
            info2 = manager.create("same")
            # Second create returns existing running desktop
            assert info1 is info2

    def test_create_max_reached(self, manager):
        manager.config._data["max_desktops"] = 1
        manager.config._save()  # Persist so reload() keeps the value
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="container1", stderr="",
            )
            manager.create("first")
            with pytest.raises(RuntimeError, match="Maximum desktops"):
                manager.create("second")

    def test_create_docker_failure(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="out of memory",
            )
            with pytest.raises(RuntimeError, match="Failed to create"):
                manager.create("bad")

    def test_create_timeout(self, manager):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
            with pytest.raises(RuntimeError, match="timed out"):
                manager.create("slow")

    def test_destroy(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="c1", stderr="")
            manager.create("temp")
        with patch.object(manager, "snapshot", return_value="snap.tar.gz"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert manager.destroy("temp") is True
            assert manager.get("temp") is None

    def test_destroy_nonexistent(self, manager):
        assert manager.destroy("nonexistent") is False

    def test_pause_resume(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="c1", stderr="")
            manager.create("pausable")
        with patch.object(manager, "snapshot", return_value="snap.tar.gz"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert manager.pause("pausable") is True
            assert manager.get("pausable").state == DesktopState.PAUSED

            assert manager.resume("pausable") is True
            assert manager.get("pausable").state == DesktopState.RUNNING

    def test_pause_wrong_state(self, manager):
        assert manager.pause("nonexistent") is False

    def test_resume_wrong_state(self, manager):
        assert manager.resume("nonexistent") is False

    def test_snapshot(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="c1", stderr="")
            manager.create("snapme")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"tardata", stderr=b"")
            result = manager.snapshot("snapme", "backup1")
            assert result is not None
            assert "backup1" in result

    def test_restore(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="c1", stderr="")
            manager.create("restme")
        # Create a snapshot file first
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"tardata", stderr=b"")
            manager.snapshot("restme", "snap1")
        # Restore from it
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = manager.restore("restme")
            assert result is True

    def test_restore_nonexistent(self, manager):
        """restore() returns False when no snapshots exist."""
        result = manager.restore("ghost")
        assert result is False

    def test_get(self, manager):
        assert manager.get("nope") is None
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="c1", stderr="")
            manager.create("exists")
            assert manager.get("exists") is not None

    def test_touch(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="c1", stderr="")
            manager.create("touchme")
            before = manager.get("touchme").last_tool_call
            time.sleep(0.01)
            manager.touch("touchme")
            after = manager.get("touchme").last_tool_call
            assert after > (before or 0)

    def test_exec(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=b"hello", stderr=b"",
            )
            manager.create("execme")
            result = manager.exec("execme", ["echo", "hello"])
            assert result.stdout == b"hello"

    def test_exec_not_running(self, manager):
        with pytest.raises(RuntimeError, match="not running"):
            manager.exec("nonexistent", ["echo"])

    def test_port_allocation(self, manager):
        # First call gets base port
        ports1 = manager._next_ports()
        assert ports1["rdp"] == 16080

        # Simulate desktop using those ports
        manager._desktops["test1"] = DesktopInfo(
            desktop_id="test1", state=DesktopState.RUNNING, rdp_port=ports1["rdp"])
        ports2 = manager._next_ports()
        assert ports2["rdp"] != ports1["rdp"]
        assert ports2["rdp"] - ports1["rdp"] == 10

        # After removing desktop, ports are reused
        del manager._desktops["test1"]
        ports3 = manager._next_ports()
        assert ports3["rdp"] == 16080

    def test_list_desktops(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="c1", stderr="")
            manager.create("a", label="Desktop A")
            manager.create("b", label="Desktop B")

        desktops = manager.list_desktops()
        assert len(desktops) == 2
        ids = [d["desktop_id"] for d in desktops]
        assert "a" in ids
        assert "b" in ids

    def test_recover_existing(self, config):
        """Test container recovery on startup."""
        with patch("subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                if "docker" in cmd and "info" in cmd:
                    return MagicMock(returncode=0, stdout="", stderr="")
                if "docker" in cmd and "ps" in cmd:
                    return MagicMock(
                        returncode=0,
                        stdout="screenbox-recovered\tUp 5 minutes\tabc123\n",
                        stderr="",
                    )
                if "docker" in cmd and "port" in cmd:
                    return MagicMock(
                        returncode=0,
                        stdout="3389/tcp -> 0.0.0.0:16080\n5900/tcp -> 0.0.0.0:16081\n8765/tcp -> 0.0.0.0:16082\n",
                        stderr="",
                    )
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect
            mgr = DesktopManager(config)

        info = mgr.get("recovered")
        assert info is not None
        assert info.state == DesktopState.RUNNING
        assert info.rdp_port == 16080
        assert info.ws_port == 16082
