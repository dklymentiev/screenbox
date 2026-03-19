"""Functional tests for File System API.

Tests the full cycle of file operations, snapshots, cloning,
and practical scenarios like profile upload/restore.

Requires Docker and screenbox-base:debian-test image.
Run: pytest tests/test_filesystem_api.py -v
"""

import base64
import io
import json
import os
import shutil
import tarfile
import time

import pytest

from src.screenbox.config import Config
from src.screenbox.manager import DesktopManager, DesktopState


@pytest.fixture(scope="module")
def mgr():
    """Create a DesktopManager with temp base dir."""
    import tempfile
    base = tempfile.mkdtemp(prefix="screenbox-test-")
    config = Config(base_dir=base)
    # Use debian-test image
    config._data["image"] = "screenbox-base:debian-test"
    config._data["port_bind_address"] = "127.0.0.1"
    config._data["max_desktops"] = 10  # extra headroom for tests
    m = DesktopManager(config)
    yield m
    # Cleanup all test desktops
    for did in list(m._desktops.keys()):
        try:
            m.destroy(did)
        except Exception:
            pass
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def desktop(mgr):
    """Create a single test desktop for the module."""
    info = mgr.create("fs-test-1", label="filesystem-api-test")
    assert info.state == DesktopState.RUNNING
    # Wait for services
    time.sleep(3)
    yield "fs-test-1"


class TestFilesLs:
    """Test desktop_files_ls."""

    def test_ls_home(self, mgr, desktop):
        result = mgr.file_ls(desktop, "/home/screenbox")
        assert result is not None
        assert "screenbox" in result
        assert ".config" in result or "downloads" in result

    def test_ls_nonexistent(self, mgr, desktop):
        result = mgr.file_ls(desktop, "/nonexistent/path")
        assert result is not None
        assert "Error" in result or "No such file" in result

    def test_ls_downloads(self, mgr, desktop):
        result = mgr.file_ls(desktop, "/home/screenbox/downloads")
        assert result is not None


class TestFilesUploadDownload:
    """Test upload and download of files."""

    def test_upload_text(self, mgr, desktop):
        data = b"Hello from test!\nLine 2"
        ok = mgr.file_upload(desktop, "/home/screenbox/downloads/test.txt", data)
        assert ok is True

    def test_download_text(self, mgr, desktop):
        content = mgr.file_download(desktop, "/home/screenbox/downloads/test.txt")
        assert content == b"Hello from test!\nLine 2"

    def test_upload_binary(self, mgr, desktop):
        data = bytes(range(256)) * 10  # 2560 bytes
        ok = mgr.file_upload(desktop, "/home/screenbox/downloads/test.bin", data)
        assert ok is True
        downloaded = mgr.file_download(desktop, "/home/screenbox/downloads/test.bin")
        assert downloaded == data

    def test_upload_json(self, mgr, desktop):
        obj = {"users": [{"name": "Alice"}, {"name": "Bob"}], "count": 2}
        data = json.dumps(obj).encode()
        ok = mgr.file_upload(desktop, "/home/screenbox/workspace/config.json", data)
        assert ok is True
        downloaded = mgr.file_download(desktop, "/home/screenbox/workspace/config.json")
        assert json.loads(downloaded) == obj

    def test_upload_creates_nested_dirs(self, mgr, desktop):
        data = b"deep file"
        ok = mgr.file_upload(desktop, "/home/screenbox/workspace/a/b/c/deep.txt", data)
        assert ok is True
        downloaded = mgr.file_download(desktop, "/home/screenbox/workspace/a/b/c/deep.txt")
        assert downloaded == data

    def test_download_nonexistent(self, mgr, desktop):
        result = mgr.file_download(desktop, "/home/screenbox/no-such-file.xyz")
        assert result is None

    def test_upload_overwrite(self, mgr, desktop):
        mgr.file_upload(desktop, "/home/screenbox/downloads/overwrite.txt", b"version 1")
        mgr.file_upload(desktop, "/home/screenbox/downloads/overwrite.txt", b"version 2")
        content = mgr.file_download(desktop, "/home/screenbox/downloads/overwrite.txt")
        assert content == b"version 2"


class TestFilesUploadTar:
    """Test tar.gz upload (directory upload)."""

    def test_upload_tar_directory(self, mgr, desktop):
        """Create a tar.gz with multiple files and upload."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # Add a text file
            content1 = b"file one content"
            ti1 = tarfile.TarInfo(name="project/readme.txt")
            ti1.size = len(content1)
            tar.addfile(ti1, io.BytesIO(content1))

            # Add a config file
            content2 = b'{"setting": true}'
            ti2 = tarfile.TarInfo(name="project/config.json")
            ti2.size = len(content2)
            tar.addfile(ti2, io.BytesIO(content2))

            # Add nested file
            content3 = b"nested content"
            ti3 = tarfile.TarInfo(name="project/src/main.py")
            ti3.size = len(content3)
            tar.addfile(ti3, io.BytesIO(content3))

        tar_data = buf.getvalue()
        ok = mgr.file_upload_tar(desktop, tar_data, "/home/screenbox/workspace")
        assert ok is True

        # Verify files extracted
        r1 = mgr.file_download(desktop, "/home/screenbox/workspace/project/readme.txt")
        assert r1 == b"file one content"

        r2 = mgr.file_download(desktop, "/home/screenbox/workspace/project/config.json")
        assert json.loads(r2) == {"setting": True}

        r3 = mgr.file_download(desktop, "/home/screenbox/workspace/project/src/main.py")
        assert r3 == b"nested content"

    def test_upload_chrome_profile_simulation(self, mgr, desktop):
        """Simulate uploading a Chrome profile as tar.gz."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # Simulate Chrome Default profile structure
            prefs = json.dumps({"profile": {"name": "Test User"}}).encode()
            ti = tarfile.TarInfo(name="Default/Preferences")
            ti.size = len(prefs)
            tar.addfile(ti, io.BytesIO(prefs))

            cookies = b"fake-cookie-db-content"
            ti2 = tarfile.TarInfo(name="Default/Cookies")
            ti2.size = len(cookies)
            tar.addfile(ti2, io.BytesIO(cookies))

        tar_data = buf.getvalue()
        ok = mgr.file_upload_tar(desktop, tar_data,
                                  "/home/screenbox/.config/chromium")
        assert ok is True

        # Verify profile files
        prefs_dl = mgr.file_download(desktop,
                                      "/home/screenbox/.config/chromium/Default/Preferences")
        assert b"Test User" in prefs_dl


class TestDiskUsage:
    """Test disk usage reporting."""

    def test_disk_usage(self, mgr, desktop):
        usage = mgr.disk_usage(desktop)
        assert usage is not None
        assert "total" in usage
        assert usage["total"] > 0

    def test_disk_usage_has_expected_keys(self, mgr, desktop):
        usage = mgr.disk_usage(desktop)
        # At minimum we should have total
        assert "total" in usage
        # May also have .config, .cache, downloads, workspace
        # depending on what du can access


class TestSnapshots:
    """Test snapshot save/restore/list cycle."""

    def test_snapshot_save(self, mgr, desktop):
        filename = mgr.snapshot(desktop, "test-snap")
        assert filename is not None
        assert "test-snap" in filename
        assert ".tar.gz" in filename  # may end with .tar.gz or .tar.gz.age

    def test_snapshot_list(self, mgr, desktop):
        snaps = mgr.list_snapshots(desktop)
        assert len(snaps) >= 1
        assert ".tar.gz" in snaps[0]["name"]
        assert snaps[0]["size_mb"] >= 0

    def test_snapshot_restore_cycle(self, mgr, desktop):
        """Full cycle: create file -> snapshot -> delete file -> restore -> file is back."""
        # Create unique marker file
        marker = f"marker-{time.time()}".encode()
        mgr.file_upload(desktop, "/home/screenbox/marker.txt", marker)

        # Snapshot
        snap_name = mgr.snapshot(desktop, "restore-test")
        assert snap_name is not None

        # Delete marker
        mgr.exec(desktop, ["rm", "/home/screenbox/marker.txt"])
        assert mgr.file_download(desktop, "/home/screenbox/marker.txt") is None

        # Restore
        ok = mgr.restore(desktop, snap_name)
        assert ok is True

        # Marker is back
        restored = mgr.file_download(desktop, "/home/screenbox/marker.txt")
        assert restored == marker

    def test_snapshot_restore_latest(self, mgr, desktop):
        """Restore without specifying name uses latest."""
        mgr.file_upload(desktop, "/home/screenbox/latest-test.txt", b"latest")
        mgr.snapshot(desktop, "latest-snap")

        mgr.exec(desktop, ["rm", "/home/screenbox/latest-test.txt"])
        ok = mgr.restore(desktop)  # no name = latest
        assert ok is True
        assert mgr.file_download(desktop, "/home/screenbox/latest-test.txt") == b"latest"

    def test_snapshot_retention(self, mgr, desktop):
        """Only keeps last 5 snapshots."""
        for i in range(7):
            mgr.snapshot(desktop, f"retention-{i}")
        snaps = mgr.list_snapshots(desktop)
        assert len(snaps) <= 5


class TestSnapshotClone:
    """Test cloning snapshots between desktops."""

    def test_clone_and_restore(self, mgr):
        """Create desktop A with data, snapshot, clone to B, restore in B."""
        # Create desktop A
        info_a = mgr.create("fs-clone-a", label="clone source")
        time.sleep(3)

        # Upload unique data to A
        mgr.file_upload("fs-clone-a", "/home/screenbox/secret.txt", b"clone-secret-data")

        # Snapshot A
        snap = mgr.snapshot("fs-clone-a", "for-clone")
        assert snap is not None

        # Clone snapshot to B
        ok = mgr.snapshot_clone("fs-clone-a", "fs-clone-b")
        assert ok is True

        # Create desktop B
        info_b = mgr.create("fs-clone-b", label="clone target")
        time.sleep(3)

        # Restore B from cloned snapshot
        ok = mgr.restore("fs-clone-b")
        assert ok is True

        # Verify B has A's data
        content = mgr.file_download("fs-clone-b", "/home/screenbox/secret.txt")
        assert content == b"clone-secret-data"

        # Cleanup
        mgr.destroy("fs-clone-a")
        mgr.destroy("fs-clone-b")


class TestAutoSnapshot:
    """Test auto-snapshot on destroy."""

    def test_destroy_with_snapshot(self, mgr):
        """Destroy with save_snapshot=True preserves data."""
        info = mgr.create("fs-auto-snap", label="auto-snap test")
        time.sleep(3)

        # Upload data
        mgr.file_upload("fs-auto-snap", "/home/screenbox/important.txt", b"do-not-lose")

        # Destroy with snapshot
        ok = mgr.destroy("fs-auto-snap", auto_snapshot=True)
        assert ok is True

        # Verify snapshot was saved
        snaps = mgr.list_snapshots("fs-auto-snap")
        assert len(snaps) >= 1
        assert "auto-before-destroy" in snaps[-1]["name"]

        # Create new desktop and restore
        mgr.create("fs-auto-snap", label="restored")
        time.sleep(3)
        ok = mgr.restore("fs-auto-snap")
        assert ok is True

        content = mgr.file_download("fs-auto-snap", "/home/screenbox/important.txt")
        assert content == b"do-not-lose"

        # Cleanup
        mgr.destroy("fs-auto-snap")


class TestIsolation:
    """Test that desktops are fully isolated (no bind mounts)."""

    def test_no_bind_mounts(self, mgr, desktop):
        """Verify container has no bind mounts (volumes are OK)."""
        import subprocess
        result = subprocess.run(
            ["docker", "inspect", f"screenbox-{desktop}",
             "--format", "{{json .Mounts}}"],
            capture_output=True, text=True,
        )
        mounts = json.loads(result.stdout)
        bind_mounts = [m for m in mounts if m.get("Type") == "bind"]
        assert len(bind_mounts) == 0, f"Expected 0 bind mounts, got {len(bind_mounts)}: {bind_mounts}"

    def test_data_persists_via_volume(self, mgr):
        """Named volumes persist data across destroy/recreate cycles."""
        info = mgr.create("fs-isolation", label="isolation test")
        time.sleep(3)

        mgr.file_upload("fs-isolation", "/home/screenbox/temp.txt", b"temporary")
        content = mgr.file_download("fs-isolation", "/home/screenbox/temp.txt")
        assert content == b"temporary"

        # Destroy without snapshot
        mgr.destroy("fs-isolation", auto_snapshot=False)

        # Recreate -- data persists via named volume
        mgr.create("fs-isolation", label="fresh")
        time.sleep(3)
        content = mgr.file_download("fs-isolation", "/home/screenbox/temp.txt")
        assert content == b"temporary"  # volume preserves data

        mgr.destroy("fs-isolation")


class TestPracticalScenarios:
    """Real-world usage scenarios from documentation."""

    def test_scenario_download_work_results(self, mgr, desktop):
        """Agent saves work results, user downloads them."""
        # Agent creates files
        mgr.file_upload(desktop, "/home/screenbox/downloads/report.csv",
                        b"name,score\nAlice,95\nBob,87")
        mgr.file_upload(desktop, "/home/screenbox/downloads/summary.txt",
                        b"2 users processed")

        # User checks what's there
        ls = mgr.file_ls(desktop, "/home/screenbox/downloads")
        assert "report.csv" in ls
        assert "summary.txt" in ls

        # User downloads results
        csv = mgr.file_download(desktop, "/home/screenbox/downloads/report.csv")
        assert b"Alice,95" in csv

    def test_scenario_save_and_restore_workspace(self, mgr):
        """Save desktop state, destroy, restore later."""
        info = mgr.create("fs-scenario-save")
        time.sleep(3)

        # Do work
        mgr.file_upload("fs-scenario-save", "/home/screenbox/workspace/notes.md",
                        b"# Important Notes\n\n- Task 1 done\n- Task 2 pending")

        # Check disk usage before saving
        usage = mgr.disk_usage("fs-scenario-save")
        assert usage is not None

        # Save and destroy
        mgr.destroy("fs-scenario-save", auto_snapshot=True)

        # Week later... restore
        mgr.create("fs-scenario-save")
        time.sleep(3)
        mgr.restore("fs-scenario-save")

        # Notes are back
        notes = mgr.file_download("fs-scenario-save",
                                   "/home/screenbox/workspace/notes.md")
        assert b"Task 1 done" in notes

        mgr.destroy("fs-scenario-save")

    def test_scenario_transfer_between_desktops(self, mgr):
        """Download file from desktop A, upload to desktop B."""
        a = mgr.create("fs-transfer-a")
        b = mgr.create("fs-transfer-b")
        time.sleep(3)

        # Create file in A
        mgr.file_upload("fs-transfer-a", "/home/screenbox/shared.txt",
                        b"shared data between desktops")

        # Download from A
        data = mgr.file_download("fs-transfer-a", "/home/screenbox/shared.txt")
        assert data is not None

        # Upload to B
        ok = mgr.file_upload("fs-transfer-b", "/home/screenbox/shared.txt", data)
        assert ok is True

        # Verify in B
        content = mgr.file_download("fs-transfer-b", "/home/screenbox/shared.txt")
        assert content == b"shared data between desktops"

        mgr.destroy("fs-transfer-a")
        mgr.destroy("fs-transfer-b")
