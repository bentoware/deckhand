from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DevAddonReloadTests(unittest.TestCase):
    def test_sync_addon_copies_files_and_removes_stale_files(self) -> None:
        dev_addon_reload = load_script("dev_addon_reload")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.joinpath("deckhand", "nested").mkdir(parents=True)
            source.joinpath("deckhand", "addon.py").write_text("print('new')\n", encoding="utf-8")
            source.joinpath("deckhand", "nested", "asset.txt").write_text("asset\n", encoding="utf-8")
            target.joinpath("stale.txt").parent.mkdir(parents=True)
            target.joinpath("stale.txt").write_text("old\n", encoding="utf-8")

            result = dev_addon_reload.sync_addon(source, target)

            self.assertTrue(target.joinpath("deckhand", "addon.py").exists())
            self.assertTrue(target.joinpath("deckhand", "nested", "asset.txt").exists())
            self.assertFalse(target.joinpath("stale.txt").exists())
            self.assertTrue(result["pythonChanged"])

    def test_sync_addon_ignores_python_caches(self) -> None:
        dev_addon_reload = load_script("dev_addon_reload")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.joinpath("__pycache__").mkdir(parents=True)
            source.joinpath("__pycache__", "addon.cpython-313.pyc").write_bytes(b"cache")
            source.joinpath("manifest.json").write_text("{}", encoding="utf-8")
            target.joinpath("__pycache__").mkdir(parents=True)
            target.joinpath("__pycache__", "old.pyc").write_bytes(b"old")

            dev_addon_reload.sync_addon(source, target)

            self.assertTrue(target.joinpath("manifest.json").exists())
            self.assertFalse(target.joinpath("__pycache__").exists())

    def test_sync_addon_preserves_installed_companion_bin(self) -> None:
        dev_addon_reload = load_script("dev_addon_reload")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.joinpath("manifest.json").parent.mkdir(parents=True)
            source.joinpath("manifest.json").write_text("{}", encoding="utf-8")
            binary = target / "bin" / dev_addon_reload.platform_tag() / dev_addon_reload.SERVER_BINARY
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

            dev_addon_reload.sync_addon(source, target)

            self.assertTrue(binary.exists())

    def test_generate_mcp_catalog_runs_repo_generator(self) -> None:
        dev_addon_reload = load_script("dev_addon_reload")

        calls = []
        original_run = dev_addon_reload.subprocess.run
        dev_addon_reload.subprocess.run = lambda command, check: calls.append((command, check))
        try:
            dev_addon_reload.generate_mcp_catalog(Path("/repo"))
        finally:
            dev_addon_reload.subprocess.run = original_run

        self.assertEqual(len(calls), 1)
        command, check = calls[0]
        self.assertTrue(check)
        self.assertEqual(command[0], dev_addon_reload.sys.executable)
        self.assertEqual(command[1], "/repo/scripts/generate_mcp_catalog.py")

    def test_install_companion_binary_places_platform_binary(self) -> None:
        dev_addon_reload = load_script("dev_addon_reload")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / dev_addon_reload.SERVER_BINARY
            target = root / "target"
            source.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

            result = dev_addon_reload.install_companion_binary(source, target)

            destination = target / "bin" / dev_addon_reload.platform_tag() / dev_addon_reload.SERVER_BINARY
            self.assertEqual(result["destination"], str(destination))
            self.assertTrue(destination.exists())
            self.assertTrue(destination.stat().st_mode & 0o111)

    def test_generated_mcp_catalog_matches_command_catalog(self) -> None:
        generate_mcp_catalog = load_script("generate_mcp_catalog")

        generated = json.loads(
            (ROOT / "crates" / "deckhand-server" / "src" / "generated" / "mcp_tool_inventory.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(generated, generate_mcp_catalog.build_payload())
        names = [tool["name"] for tool in generated["tools"]]
        self.assertIn("anki_app_get_state", names)
        self.assertNotIn("anki_context_get_current", names)
        self.assertIn("anki_note_search", names)
        self.assertIn("anki_run_python", names)
        self.assertNotIn("anki_review_answer_current", names)
        self.assertTrue(all(name.startswith("anki_") for name in names))
        self.assertTrue(all(str(Path.home()) not in tool["description"] for tool in generated["tools"]))


if __name__ == "__main__":
    unittest.main()
