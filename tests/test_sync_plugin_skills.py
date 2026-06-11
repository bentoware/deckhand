from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_sync():
    path = ROOT / "scripts" / "sync_plugin_skills.py"
    spec = importlib.util.spec_from_file_location("sync_plugin_skills", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginOpenaiYamlTests(unittest.TestCase):
    def test_injects_icon_lines_after_short_description(self) -> None:
        sync = load_sync()
        source = (
            "interface:\n"
            '  display_name: "Deckhand"\n'
            '  short_description: "Make cards"\n'
            '  default_prompt: "Use $deckhand."\n'
        )
        expected = (
            "interface:\n"
            '  display_name: "Deckhand"\n'
            '  short_description: "Make cards"\n'
            '  icon_small: "./assets/icon.png"\n'
            '  icon_large: "./assets/logo.png"\n'
            '  default_prompt: "Use $deckhand."\n'
        )
        self.assertEqual(sync.plugin_openai_yaml(source), expected)

    def test_leaves_source_with_icons_untouched(self) -> None:
        sync = load_sync()
        source = 'interface:\n  icon_small: "./assets/icon.png"\n'
        self.assertEqual(sync.plugin_openai_yaml(source), source)

    def test_leaves_source_without_short_description_untouched(self) -> None:
        sync = load_sync()
        source = "interface:\n  display_name: x\n"
        self.assertEqual(sync.plugin_openai_yaml(source), source)


class SyncSkillsTests(unittest.TestCase):
    def make_tree(self, sync) -> tuple[Path, Path]:
        base = Path(tempfile.mkdtemp())
        source = base / "skills" / "demo"
        dest = base / "plugin" / "demo"
        (source / "commands").mkdir(parents=True)
        (source / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        (source / "commands" / "create.md").write_text("create\n", encoding="utf-8")
        sync.SOURCE_ROOT = base / "skills"
        sync.PLUGIN_SKILLS_ROOT = base / "plugin"
        return source, dest

    def test_check_reports_missing_then_sync_repairs(self) -> None:
        sync = load_sync()
        source, dest = self.make_tree(sync)

        drift = sync.sync_skills(check=True)
        self.assertEqual(len(drift), 2)

        sync.sync_skills(check=False)
        self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "# demo\n")
        self.assertEqual((dest / "commands" / "create.md").read_text(encoding="utf-8"), "create\n")
        self.assertEqual(sync.sync_skills(check=True), [])

    def test_sync_removes_orphans_but_preserves_assets(self) -> None:
        sync = load_sync()
        source, dest = self.make_tree(sync)
        sync.sync_skills(check=False)
        (dest / "commands" / "orphan.md").write_text("old\n", encoding="utf-8")
        (dest / "assets").mkdir()
        (dest / "assets" / "icon.png").write_bytes(b"png")

        sync.sync_skills(check=False)
        self.assertFalse((dest / "commands" / "orphan.md").exists())
        self.assertTrue((dest / "assets" / "icon.png").exists())
        self.assertEqual(sync.sync_skills(check=True), [])


if __name__ == "__main__":
    unittest.main()
