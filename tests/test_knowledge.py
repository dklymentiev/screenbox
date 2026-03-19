"""Unit tests for knowledge module (knowledge.py).

Tests:
- App detection from window titles
- Slugification
- KnowledgeStore: add, search, dedup, outline, hints
"""

import json
import pytest
from pathlib import Path

from screenbox.knowledge import (
    detect_app, _slugify, KnowledgeStore, APP_PATTERNS,
)


class TestDetectApp:
    def test_libreoffice_calc(self):
        slug, name = detect_app("Untitled 1 - LibreOffice Calc")
        assert slug == "libreoffice-calc"
        assert name == "LibreOffice Calc"

    def test_libreoffice_writer(self):
        slug, name = detect_app("document.odt - LibreOffice Writer")
        assert slug == "libreoffice-writer"

    def test_libreoffice_generic(self):
        slug, name = detect_app("Start Center - LibreOffice")
        assert slug == "libreoffice"

    def test_gimp(self):
        slug, name = detect_app("*Untitled - GIMP")
        assert slug == "gimp"
        assert name == "GIMP"

    def test_chrome(self):
        slug, name = detect_app("Google - Google Chrome")
        assert slug == "chrome"

    def test_chromium(self):
        slug, name = detect_app("Tab - Chromium")
        assert slug == "chrome"

    def test_firefox(self):
        slug, name = detect_app("Mozilla Firefox")
        assert slug == "firefox"

    def test_vscode(self):
        slug, name = detect_app("main.py - project - Visual Studio Code")
        assert slug == "vscode"

    def test_terminal(self):
        slug, name = detect_app("admin@server: ~/project - Terminal")
        assert slug == "terminal"

    def test_fallback_dash_pattern(self):
        slug, name = detect_app("My File - Gedit")
        assert slug == "pluma"  # gedit matched by pluma/gedit pattern
        assert name == "Text Editor"

    def test_empty_title(self):
        slug, name = detect_app("")
        assert slug is None
        assert name is None

    def test_no_match(self):
        slug, name = detect_app("desktop")
        assert slug is None


class TestSlugify:
    def test_simple(self):
        assert _slugify("LibreOffice Calc") == "libreoffice-calc"

    def test_special_chars(self):
        assert _slugify("GNU/Image Editor!") == "gnu-image-editor"

    def test_strips_dashes(self):
        assert _slugify("  -hello- ") == "hello"


class TestKnowledgeStore:
    @pytest.fixture
    def store(self, tmp_path):
        return KnowledgeStore(base_dir=tmp_path)

    def test_add_fact(self, store):
        fact = store.add_fact("test-app", "Press F5 to refresh", ["refresh", "reload"])
        assert fact["text"] == "Press F5 to refresh"
        assert "refresh" in fact["triggers"]
        assert fact["source"] == "agent-learned"

    def test_add_fact_persists(self, store):
        store.add_fact("test-app", "Fact 1", ["tag1"])
        # Clear cache to force reload from disk
        store._cache.clear()
        data = store._load("test-app")
        assert len(data["facts"]) == 1
        assert data["facts"][0]["text"] == "Fact 1"

    def test_add_fact_dedup(self, store):
        store.add_fact("test-app", "Same fact", ["a"])
        store.add_fact("test-app", "Same fact", ["b"])
        store.add_fact("test-app", "SAME FACT", ["c"])  # case-insensitive dedup
        data = store._load("test-app")
        assert len(data["facts"]) == 1

    def test_add_multiple_facts(self, store):
        store.add_fact("app", "Fact 1", ["tag1"])
        store.add_fact("app", "Fact 2", ["tag2"])
        store.add_fact("app", "Fact 3", ["tag3"])
        data = store._load("app")
        assert len(data["facts"]) == 3

    def test_search_by_trigger(self, store):
        store.add_fact("app", "Use Ctrl+S to save", ["save", "shortcut"])
        store.add_fact("app", "Use Ctrl+Z to undo", ["undo", "shortcut"])
        store.add_fact("app", "Double-click to select word", ["select", "mouse"])

        results = store.search("app", ["save", "shortcut"])
        assert len(results) >= 1
        assert results[0]["text"] == "Use Ctrl+S to save"

    def test_search_by_text_content(self, store):
        store.add_fact("app", "Alt+Y confirms Yes in dialogs", ["dialog"])
        results = store.search("app", ["confirms"])
        assert len(results) == 1

    def test_search_no_context(self, store):
        store.add_fact("app", "Fact 1", ["a"])
        store.add_fact("app", "Fact 2", ["b"])
        # No context words -> returns recent facts
        results = store.search("app", [])
        assert len(results) == 2

    def test_search_short_words_ignored(self, store):
        store.add_fact("app", "Some fact", ["ok"])
        # Words <= 2 chars are filtered out from context
        results = store.search("app", ["ab", "cd"])
        # No context words long enough -> returns recent facts as fallback
        assert len(results) == 1  # returns recent

    def test_search_empty_app(self, store):
        results = store.search("nonexistent", ["anything"])
        assert results == []

    def test_get_outline(self, store):
        store.add_fact("calc", "Fact 1", ["a"], name="LibreOffice Calc")
        store.add_fact("calc", "Fact 2", ["b"], name="LibreOffice Calc")
        outline = store.get_outline("calc")
        assert "[Knowledge: LibreOffice Calc -- 2 facts]" in outline
        assert "Fact 1" in outline
        assert "Fact 2" in outline

    def test_get_outline_empty(self, store):
        outline = store.get_outline("nonexistent")
        assert outline == ""

    def test_get_outline_truncates_long_facts(self, store):
        store.add_fact("app", "A" * 200, [])
        outline = store.get_outline("app")
        assert "..." in outline

    def test_get_outline_limits_to_10(self, store):
        for i in range(15):
            store.add_fact("app", f"Fact number {i}", [f"tag{i}"])
        outline = store.get_outline("app")
        assert "and 5 more" in outline

    def test_get_hints_for_window(self, store):
        store.add_fact("libreoffice-calc", "Use Alt+Y", ["dialog"],
                       name="LibreOffice Calc")
        hints = store.get_hints_for_window("desktop-1", "Sheet1 - LibreOffice Calc")
        assert hints is not None
        assert "LibreOffice Calc" in hints

    def test_get_hints_for_window_with_context(self, store):
        store.add_fact("gimp", "Shift+Q to disable Quick Mask", ["quick", "mask", "select"],
                       name="GIMP")
        store.add_fact("gimp", "Paths tool is better for curves", ["paths", "curves"],
                       name="GIMP")
        hints = store.get_hints_for_window("d1", "*GIMP", ["quick", "mask"])
        assert "Quick Mask" in hints

    def test_get_hints_unknown_app(self, store):
        hints = store.get_hints_for_window("d1", "Unknown App Window")
        assert hints is None

    def test_get_hints_no_facts(self, store):
        hints = store.get_hints_for_window("d1", "LibreOffice Calc")
        assert hints is None

    def test_active_app_tracking(self, store):
        store.add_fact("chrome", "Tip", ["tip"], name="Chrome")
        store.get_hints_for_window("d1", "Google Chrome")
        assert store.get_active_app("d1") == "chrome"

    def test_load_missing_file(self, store):
        data = store._load("no-such-app")
        assert data["name"] == "no-such-app"
        assert data["facts"] == []

    def test_load_corrupt_json(self, store):
        path = store.knowledge_dir / "bad.json"
        path.write_text("not valid json{{{", encoding="utf-8")
        data = store._load("bad")
        assert data["facts"] == []

    def test_app_name_preserved(self, store):
        store.add_fact("calc", "Tip", ["a"], name="LibreOffice Calc")
        data = store._load("calc")
        assert data["name"] == "LibreOffice Calc"
