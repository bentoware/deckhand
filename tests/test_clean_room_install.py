from __future__ import annotations

import importlib.util
import contextlib
import io
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_clean_room():
    path = ROOT / "scripts" / "clean_room_install.py"
    spec = importlib.util.spec_from_file_location("clean_room_install", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CleanRoomInstallTests(unittest.TestCase):
    def test_process_patterns_are_deckhand_only(self) -> None:
        clean_room = load_clean_room()

        self.assertEqual(clean_room.PROCESS_PATTERNS, ["deckhand-server"])

    def test_main_moves_only_deckhand_owned_paths(self) -> None:
        clean_room = load_clean_room()
        stopped: list[bool] = []
        moved: list[Path] = []
        original_stop_processes = clean_room.stop_processes
        original_move_to_backup = clean_room.move_to_backup

        clean_room.stop_processes = lambda *, apply: stopped.append(apply)
        clean_room.move_to_backup = lambda path, backup_dir, *, apply: moved.append(path)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                result = clean_room.main([])
        finally:
            clean_room.stop_processes = original_stop_processes
            clean_room.move_to_backup = original_move_to_backup

        self.assertEqual(result, 0)
        self.assertEqual(stopped, [False])
        self.assertEqual(
            moved,
            [clean_room.ANKI_ADDON, clean_room.DECKHAND_SUPPORT, clean_room.DECKHAND_LOGS],
        )


if __name__ == "__main__":
    unittest.main()
