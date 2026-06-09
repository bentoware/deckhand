from __future__ import annotations

import importlib.util
import contextlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_build():
    path = ROOT / "scripts" / "build.py"
    spec = importlib.util.spec_from_file_location("build", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildSystemTests(unittest.TestCase):
    def test_build_command_generates_then_builds_rust(self) -> None:
        build = load_build()
        calls: list[list[str]] = []
        original_run = build.subprocess.run
        build.subprocess.run = lambda command, cwd, check: calls.append(command)
        try:
            self.assertEqual(build.main(["build"]), 0)
        finally:
            build.subprocess.run = original_run

        self.assertEqual(
            calls,
            [
                [build.sys.executable, "scripts/generate_mcp_catalog.py"],
                ["cargo", "build", "-p", "deckhand-server"],
            ],
        )

    def test_check_command_runs_all_validation_steps(self) -> None:
        build = load_build()
        calls: list[list[str]] = []
        original_run = build.subprocess.run
        build.subprocess.run = lambda command, cwd, check: calls.append(command)
        try:
            self.assertEqual(build.main(["check"]), 0)
        finally:
            build.subprocess.run = original_run

        self.assertEqual(
            calls,
            [
                [build.sys.executable, "scripts/generate_mcp_catalog.py", "--check"],
                [build.sys.executable, "scripts/check_tool_surface.py"],
                [build.sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                ["cargo", "test", "-p", "deckhand-server"],
            ],
        )

    def test_sync_command_generates_builds_and_copies_debug_companion(self) -> None:
        build = load_build()
        calls: list[list[str]] = []
        original_run = build.subprocess.run
        build.subprocess.run = lambda command, cwd, check: calls.append(command)
        try:
            self.assertEqual(build.main(["sync", "--", "--restart-anki"]), 0)
        finally:
            build.subprocess.run = original_run

        self.assertEqual(
            calls,
            [
                [build.sys.executable, "scripts/generate_mcp_catalog.py"],
                ["cargo", "build", "-p", "deckhand-server"],
                [
                    build.sys.executable,
                    "scripts/dev_addon_reload.py",
                    "--skip-mcp-catalog",
                    "--restart-anki",
                    "--companion-binary",
                    str(build.built_server_path(release=False)),
                ],
            ],
        )

    def test_inspect_mcp_opens_live_http_companion_endpoint(self) -> None:
        build = load_build()
        calls: list[list[str]] = []
        original_run = build.subprocess.run
        build.subprocess.run = lambda command, cwd, check: calls.append(command)
        try:
            self.assertEqual(build.main(["inspect-mcp"]), 0)
        finally:
            build.subprocess.run = original_run

        self.assertEqual(
            calls,
            [
                [
                    "npx",
                    "--yes",
                    "@modelcontextprotocol/inspector",
                    "--transport",
                    "http",
                    "--server-url",
                    "http://127.0.0.1:28765/mcp",
                ],
            ],
        )

    def test_inspect_mcp_passes_inspector_args_before_transport_options(self) -> None:
        build = load_build()
        calls: list[list[str]] = []
        original_run = build.subprocess.run
        build.subprocess.run = lambda command, cwd, check: calls.append(command)
        try:
            self.assertEqual(build.main(["inspect-mcp", "--", "--client-port", "8080"]), 0)
        finally:
            build.subprocess.run = original_run

        self.assertEqual(
            calls,
            [
                [
                    "npx",
                    "--yes",
                    "@modelcontextprotocol/inspector",
                    "--client-port",
                    "8080",
                    "--transport",
                    "http",
                    "--server-url",
                    "http://127.0.0.1:28765/mcp",
                ],
            ],
        )

    def test_inspect_mcp_accepts_url_override(self) -> None:
        build = load_build()
        calls: list[list[str]] = []
        original_run = build.subprocess.run
        build.subprocess.run = lambda command, cwd, check: calls.append(command)
        try:
            self.assertEqual(build.main(["inspect-mcp", "--url", "http://127.0.0.1:18888/mcp"]), 0)
        finally:
            build.subprocess.run = original_run

        self.assertEqual(
            calls,
            [
                [
                    "npx",
                    "--yes",
                    "@modelcontextprotocol/inspector",
                    "--transport",
                    "http",
                    "--server-url",
                    "http://127.0.0.1:18888/mcp",
                ],
            ],
        )

    def test_clean_room_passes_args_to_reset_script(self) -> None:
        build = load_build()
        calls: list[list[str]] = []
        original_run = build.subprocess.run
        build.subprocess.run = lambda command, cwd, check: calls.append(command)
        try:
            self.assertEqual(build.main(["clean-room", "--", "--apply", "--restart-anki"]), 0)
        finally:
            build.subprocess.run = original_run

        self.assertEqual(
            calls,
            [[build.sys.executable, "scripts/clean_room_install.py", "--apply", "--restart-anki"]],
        )

    def test_build_runner_no_longer_exposes_removed_dev_observer(self) -> None:
        build = load_build()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build.main(["dev"])

    def test_package_addon_writes_archive_without_python_caches(self) -> None:
        build = load_build()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = Path(temp_dir) / "deckhand.ankiaddon"
            server = root / "target" / "release" / build.SERVER_BINARY
            server.parent.mkdir(parents=True, exist_ok=True)
            server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            server.chmod(0o755)
            calls: list[list[str]] = []
            original_run = build.subprocess.run
            original_built_server_path = build.built_server_path
            build.subprocess.run = lambda command, cwd, check: calls.append(command)
            build.built_server_path = lambda release=True: server
            try:
                build.package_addon(output, skip_build=True)
            finally:
                build.subprocess.run = original_run
                build.built_server_path = original_built_server_path

            self.assertEqual(calls, [[build.sys.executable, "scripts/generate_mcp_catalog.py"]])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())

        self.assertIn("manifest.json", names)
        self.assertIn("__init__.py", names)
        self.assertIn("config.json", names)
        self.assertIn("deckhand/webengine_tools.py", names)
        self.assertTrue(all("deckhand/anki_lens/" not in name for name in names))
        self.assertIn(f"bin/{build.platform_tag()}/{build.SERVER_BINARY}", names)
        self.assertNotIn("deckhand/web/manager.html", names)
        self.assertNotIn("deckhand/web/projects.html", names)
        self.assertTrue(all("__pycache__" not in name for name in names))
        self.assertTrue(all(not name.endswith((".pyc", ".pyo")) for name in names))

    def test_package_addon_excludes_legacy_manager_and_projects_pages(self) -> None:
        build = load_build()

        self.assertFalse(build.is_package_relative_path(Path("deckhand/web/manager.html")))
        self.assertFalse(build.is_package_relative_path(Path("deckhand/web/projects.html")))
        self.assertFalse(build.is_package_relative_path(Path("deckhand/anki_lens/web/inspect.js")))
        self.assertTrue(build.is_package_relative_path(Path("deckhand/webengine_tools.py")))

    def test_package_addon_boundary_rejects_desktop_product_files(self) -> None:
        build = load_build()

        with self.assertRaisesRegex(RuntimeError, "package boundary violation"):
            build.validate_addon_package_boundary(
                [
                    Path("manifest.json"),
                    Path("deckhand/webengine_tools.py"),
                    Path(".codex-plugin/plugin.json"),
                    Path("desktop/package.json"),
                    Path("renderer/main.tsx"),
                ]
            )

    def test_package_addon_builds_release_server_by_default(self) -> None:
        build = load_build()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "deckhand-test.ankiaddon"
            server = root / "target" / "release" / build.SERVER_BINARY
            server.parent.mkdir(parents=True, exist_ok=True)
            server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            server.chmod(0o755)
            calls: list[list[str]] = []
            original_run = build.subprocess.run
            original_built_server_path = build.built_server_path
            build.subprocess.run = lambda command, cwd, check: calls.append(command)
            build.built_server_path = lambda release=True: server
            try:
                build.package_addon(output)
            finally:
                build.subprocess.run = original_run
                build.built_server_path = original_built_server_path

        self.assertEqual(
            calls[:2],
            [
                [build.sys.executable, "scripts/generate_mcp_catalog.py"],
                ["cargo", "build", "-p", "deckhand-server", "--release"],
            ],
        )

    def test_package_addon_bundles_multiple_prebuilt_companion_binaries(self) -> None:
        build = load_build()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "deckhand-multi.ankiaddon"
            macos = root / "macos" / "deckhand-server"
            windows = root / "windows" / "deckhand-server.exe"
            for binary in [macos, windows]:
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_text("companion\n", encoding="utf-8")
                binary.chmod(0o755)
            calls: list[list[str]] = []
            original_run = build.subprocess.run
            build.subprocess.run = lambda command, cwd, check: calls.append(command)
            try:
                build.package_addon(
                    output,
                    skip_build=True,
                    companion_binaries={
                        "macos-aarch64": macos,
                        "windows-x86_64": windows,
                    },
                )
            finally:
                build.subprocess.run = original_run

            self.assertEqual(calls, [[build.sys.executable, "scripts/generate_mcp_catalog.py"]])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())

        self.assertIn("bin/macos-aarch64/deckhand-server", names)
        self.assertIn("bin/windows-x86_64/deckhand-server.exe", names)
        self.assertNotIn("bin/windows-x86_64/deckhand-server", names)

    def test_package_addon_cli_accepts_repeated_companion_binaries(self) -> None:
        build = load_build()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "deckhand-cli.ankiaddon"
            macos = root / "macos" / "deckhand-server"
            linux = root / "linux" / "deckhand-server"
            for binary in [macos, linux]:
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_text("companion\n", encoding="utf-8")
                binary.chmod(0o755)
            calls: list[list[str]] = []
            original_run = build.subprocess.run
            build.subprocess.run = lambda command, cwd, check: calls.append(command)
            try:
                self.assertEqual(
                    build.main(
                        [
                            "package-addon",
                            "--skip-build",
                            "--output",
                            str(output),
                            "--companion-binary",
                            f"macos-aarch64={macos}",
                            "--companion-binary",
                            f"linux-x86_64={linux}",
                        ]
                    ),
                    0,
                )
            finally:
                build.subprocess.run = original_run

            self.assertEqual(calls, [[build.sys.executable, "scripts/generate_mcp_catalog.py"]])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())

        self.assertIn("bin/macos-aarch64/deckhand-server", names)
        self.assertIn("bin/linux-x86_64/deckhand-server", names)

    def test_parse_companion_binary_requires_platform_mapping(self) -> None:
        build = load_build()

        tag, path = build.parse_companion_binary("linux-x86_64=artifacts/deckhand-server")
        self.assertEqual(tag, "linux-x86_64")
        self.assertEqual(path, Path("artifacts/deckhand-server"))
        with self.assertRaises(build.argparse.ArgumentTypeError):
            build.parse_companion_binary("artifacts/deckhand-server")


if __name__ == "__main__":
    unittest.main()
