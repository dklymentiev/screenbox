"""desktop_file dispatcher tool."""
import json
import shlex
import time


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_file(desktop_id: str, action: str,
                     path: str = "", content_base64: str = "",
                     dest_dir: str = "/home/screenbox",
                     intent: str = "",
                     step: str = "") -> str:
        """File operations in the desktop container.

        Actions:
          upload(path, content_base64): Upload file
          download(path): Download file (returns base64)
          list(path?): List directory contents
          upload_tar(content_base64, dest_dir?): Upload and extract tar.gz

        Args:
            desktop_id: Desktop
            action: Action name
            path: File path inside container
            content_base64: Base64-encoded file content
            dest_dir: Extract dir for upload_tar (default /home/screenbox)
        """
        t0 = time.time()
        mgr = get_manager()

        if action == "upload":
            import base64 as b64
            try:
                data = b64.b64decode(content_base64)
            except Exception:
                return json.dumps({"error": "Invalid base64 content"})
            ok = mgr.file_upload(desktop_id, path, data)
            result = {"uploaded": ok, "path": path, "size": len(data)}
        elif action == "download":
            data = mgr.file_download(desktop_id, path)
            if data is None:
                return json.dumps({"error": f"File not found: {path}"})
            import base64 as b64
            encoded = b64.b64encode(data).decode("ascii")
            result = {"path": path, "size": len(data),
                      "content_base64": encoded}
        elif action == "list":
            d = get_desktop(desktop_id)
            target = path or "/home/screenbox"
            r = d.shell(f"ls -la {shlex.quote(target)}")
            result = {"path": target,
                      "output": r.get("stdout", "")}
        elif action == "upload_tar":
            import base64 as b64
            try:
                data = b64.b64decode(content_base64)
            except Exception:
                return json.dumps({"error": "Invalid base64 content"})
            ok = mgr.file_upload_tar(desktop_id, data, dest_dir)
            result = {"extracted": ok, "dest_dir": dest_dir, "size": len(data)}
        else:
            return json.dumps({"error": f"Unknown file action: {action}",
                               "available": [
                                   "upload", "download", "list", "upload_tar"]})

        log_action(desktop_id, "desktop_file",
                   {"action": action, "path": path}, result, t0,
                   intent=intent, step=step)
        return json.dumps(result, indent=2)
