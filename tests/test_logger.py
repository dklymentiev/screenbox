"""Unit tests for ActionLogger."""

import json
import pytest

from screenbox.logger import ActionLogger


class TestActionLogger:
    def test_creates_log_file(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop")
        logger.log("desktop_click", {"x": 100, "y": 200})
        assert logger.log_file.exists()

    def test_writes_jsonl(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop")
        logger.log("desktop_click", {"x": 100, "y": 200}, result={"clicked": True})

        lines = logger.log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "desktop_click"
        assert entry["args"]["x"] == 100
        assert entry["result"]["clicked"] is True

    def test_multiple_entries(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop")
        logger.log("desktop_click", {"x": 1, "y": 2})
        logger.log("desktop_type", {"text": "hello"})
        logger.log("desktop_key", {"combo": "Return"})

        lines = logger.log_file.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_redacts_text_by_default(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop", log_keystrokes=False)
        logger.log("desktop_type", {"text": "secret password"})

        entry = json.loads(logger.log_file.read_text().strip())
        assert "secret" not in entry["args"]["text"]
        assert "REDACTED" in entry["args"]["text"]
        assert "15 chars" in entry["args"]["text"]

    def test_logs_keystrokes_when_enabled(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop", log_keystrokes=True)
        logger.log("desktop_type", {"text": "visible text"})

        entry = json.loads(logger.log_file.read_text().strip())
        assert entry["args"]["text"] == "visible text"

    def test_truncates_large_results(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop")
        large_result = "x" * 5000
        logger.log("desktop_screenshot", {}, result=large_result)

        entry = json.loads(logger.log_file.read_text().strip())
        assert "5000 chars" in entry["result"]

    def test_duration_ms(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop")
        logger.log("desktop_click", {"x": 0, "y": 0}, duration_ms=42)

        entry = json.loads(logger.log_file.read_text().strip())
        assert entry["duration_ms"] == 42

    def test_has_timestamp(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop")
        logger.log("desktop_click", {"x": 0, "y": 0})

        entry = json.loads(logger.log_file.read_text().strip())
        assert "timestamp" in entry
        assert isinstance(entry["timestamp"], str)
        assert entry["timestamp"].endswith("Z")

    def test_non_type_tools_not_redacted(self, tmp_path):
        logger = ActionLogger(tmp_path, "test-desktop", log_keystrokes=False)
        logger.log("desktop_click", {"text": "not redacted here"})

        entry = json.loads(logger.log_file.read_text().strip())
        assert entry["args"]["text"] == "not redacted here"

    def test_separate_log_files_per_desktop(self, tmp_path):
        logger1 = ActionLogger(tmp_path, "desktop-a")
        logger2 = ActionLogger(tmp_path, "desktop-b")
        logger1.log("test", {})
        logger2.log("test", {})
        assert (tmp_path / "desktop-a.jsonl").exists()
        assert (tmp_path / "desktop-b.jsonl").exists()
