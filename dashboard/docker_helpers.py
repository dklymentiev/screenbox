"""Screenbox Dashboard -- host system helpers (no docker dependency)."""

import os
import subprocess

from config import DESKTOPS_DIR, DISK_QUOTA_MB


def _get_system_stats() -> dict:
    """Get host system statistics."""
    stats: dict = {}
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
            total = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", 0)
            stats["mem_total_mb"] = round(total / 1024)
            stats["mem_available_mb"] = round(avail / 1024)
            stats["mem_used_pct"] = round((total - avail) / total * 100, 1) if total else 0
    except Exception:
        pass
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            stats["load_1m"] = float(parts[0])
            stats["load_5m"] = float(parts[1])
    except Exception:
        pass
    stats["cpu_cores"] = os.cpu_count() or 1
    try:
        df = subprocess.run(
            ["df", "-BM", "--output=size,used,avail,pcent", "/"],
            capture_output=True, text=True, timeout=5,
        )
        lines = df.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            stats["disk_total_mb"] = int(parts[0].rstrip("M"))
            stats["disk_used_mb"] = int(parts[1].rstrip("M"))
            stats["disk_available_mb"] = int(parts[2].rstrip("M"))
            stats["disk_used_pct"] = int(parts[3].rstrip("%"))
    except Exception:
        pass
    return stats


def _list_storage() -> dict:
    """List persistent storage for all desktops."""
    items = []
    if os.path.isdir(DESKTOPS_DIR):
        for name in sorted(os.listdir(DESKTOPS_DIR)):
            if name.startswith("."):
                continue
            path = os.path.join(DESKTOPS_DIR, name)
            if not os.path.isdir(path):
                continue
            try:
                du = subprocess.run(
                    ["du", "-sm", path],
                    capture_output=True, text=True, timeout=5,
                )
                mb = float(du.stdout.split()[0])
            except Exception:
                mb = 0.0
            items.append({"id": name, "size_mb": round(mb, 1)})
    return {"items": items, "quota_mb": DISK_QUOTA_MB}


def _delete_storage(desktop_id: str) -> dict:
    """Delete persistent storage for a desktop."""
    path = os.path.join(DESKTOPS_DIR, desktop_id)
    if not os.path.isdir(path):
        return {"error": f"No storage for '{desktop_id}'"}
    try:
        import shutil
        shutil.rmtree(path)
        return {"ok": True, "id": desktop_id}
    except Exception as e:
        return {"error": str(e)}
