#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ADDON = ROOT / "addon" / "deckhand"
ADDON_PACKAGE = DIST / "deckhand.ankiaddon"
SERVER_BINARY = "deckhand-server.exe" if platform.system().lower() == "windows" else "deckhand-server"
SERVER_BINARY_BASENAME = "deckhand-server"
IGNORED_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
ADDON_PACKAGE_FORBIDDEN_PARTS = {
    "apps",
    "desktop",
    "renderer",
    "node_modules",
    "dist-desktop",
}
ADDON_PACKAGE_FORBIDDEN_FILENAMES = {
    "components.json",
    "package-lock.json",
    "package.json",
    "tailwind.config.js",
    "tailwind.config.ts",
    "vite.config.js",
    "vite.config.ts",
}
ADDON_PACKAGE_EXCLUDED_PATHS = {
    Path("deckhand/web/manager.html"),
    Path("deckhand/web/projects.html"),
}
ADDON_PACKAGE_EXCLUDED_PARTS = {
    "anki_lens",
}


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=str(ROOT), check=True)


def python() -> str:
    return sys.executable


def generate() -> None:
    run([python(), "scripts/generate_mcp_catalog.py"])


def check_catalog() -> None:
    run([python(), "scripts/generate_mcp_catalog.py", "--check"])


def check_tool_surface() -> None:
    run([python(), "scripts/check_tool_surface.py"])


def test_python() -> None:
    try:
        run([python(), "-m", "unittest", "discover", "-s", "tests"])
    finally:
        clean_python_caches()


def test_rust() -> None:
    run(["cargo", "test", "-p", "deckhand-server"])


def build_rust(release: bool = False) -> None:
    command = ["cargo", "build", "-p", "deckhand-server"]
    if release:
        command.append("--release")
    run(command)


def platform_tag(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = {"darwin": "macos"}.get(system, system)
    return f"{system}-{machine}"


def built_server_path(release: bool = True) -> Path:
    profile = "release" if release else "debug"
    return ROOT / "target" / profile / SERVER_BINARY


def server_binary_name_for_platform(tag: str) -> str:
    return f"{SERVER_BINARY_BASENAME}.exe" if tag.startswith("windows-") else SERVER_BINARY_BASENAME


def sync_addon(extra_args: list[str], *, skip_companion_build: bool = False) -> None:
    generate()
    if not skip_companion_build:
        build_rust(release=False)
    command = [python(), "scripts/dev_addon_reload.py", "--skip-mcp-catalog", *extra_args]
    if not skip_companion_build:
        command.extend(["--companion-binary", str(built_server_path(release=False))])
    run(command)


def inspect_mcp(extra_args: list[str], *, server_url: str) -> None:
    run(
        [
            "npx",
            "--yes",
            "@modelcontextprotocol/inspector",
            *extra_args,
            "--transport",
            "http",
            "--server-url",
            server_url,
        ]
    )


def is_package_relative_path(path: Path) -> bool:
    return (
        path not in ADDON_PACKAGE_EXCLUDED_PATHS
        and not any(part in ADDON_PACKAGE_EXCLUDED_PARTS for part in path.parts)
        and not any(part in IGNORED_DIRS for part in path.parts)
        and path.suffix not in IGNORED_SUFFIXES
    )


def validate_addon_package_boundary(paths: list[Path]) -> None:
    violations: list[str] = []
    for path in paths:
        parts = set(path.parts)
        if (
            parts & ADDON_PACKAGE_FORBIDDEN_PARTS
            or any(part.startswith(".") and part.endswith("-plugin") for part in path.parts)
            or path.name in ADDON_PACKAGE_FORBIDDEN_FILENAMES
        ):
            violations.append(path.as_posix())
    if violations:
        joined = ", ".join(sorted(violations))
        raise RuntimeError(
            "add-on package boundary violation: desktop/product files must not be bundled "
            f"into deckhand.ankiaddon ({joined})"
        )


def parse_companion_binary(value: str) -> tuple[str, Path]:
    tag, separator, path = value.partition("=")
    if not separator or not tag or not path:
        raise argparse.ArgumentTypeError("expected PLATFORM=PATH, for example macos-aarch64=target/release/deckhand-server")
    if "/" in tag or "\\" in tag or ".." in tag:
        raise argparse.ArgumentTypeError(f"invalid platform tag: {tag}")
    return tag, Path(path)


def default_companion_binaries(release: bool) -> dict[str, Path]:
    return {platform_tag(): built_server_path(release=release)}


def validate_companion_binaries(binaries: dict[str, Path]) -> dict[str, Path]:
    if not binaries:
        raise RuntimeError("at least one companion binary is required")
    resolved: dict[str, Path] = {}
    for tag, binary in binaries.items():
        path = binary.expanduser()
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        if not path.exists():
            raise RuntimeError(f"companion binary not found for {tag}: {path}")
        if not path.is_file():
            raise RuntimeError(f"companion binary is not a file for {tag}: {path}")
        resolved[tag] = path
    return resolved


def package_addon(
    output: Path = ADDON_PACKAGE,
    release: bool = True,
    skip_build: bool = False,
    companion_binaries: dict[str, Path] | None = None,
) -> None:
    generate()
    if companion_binaries is None and not skip_build:
        build_rust(release=release)
    if not (ADDON / "manifest.json").exists():
        raise RuntimeError(f"add-on manifest not found: {ADDON / 'manifest.json'}")
    binaries = validate_companion_binaries(companion_binaries or default_companion_binaries(release))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    package_paths = [
        path.relative_to(ADDON)
        for path in sorted(ADDON.rglob("*"))
        if path.is_file() and is_package_relative_path(path.relative_to(ADDON))
    ]
    validate_addon_package_boundary(package_paths)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in package_paths:
            archive.write(ADDON / relative, relative.as_posix())
        for tag, binary in sorted(binaries.items()):
            archive.write(binary, f"bin/{tag}/{server_binary_name_for_platform(tag)}")
    print(f"wrote {output}")


def clean() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    clean_python_caches()


def clean_python_caches() -> None:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def clean_room(extra_args: list[str]) -> None:
    run([python(), "scripts/clean_room_install.py", *extra_args])


def pass_through(args: list[str]) -> list[str]:
    return args[1:] if args[:1] == ["--"] else args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deckhand Extension build runner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("generate", help="Regenerate generated MCP/Rust artifacts.")
    subparsers.add_parser("check-catalog", help="Verify generated MCP inventory is current.")
    subparsers.add_parser("test-python", help="Run Python unit tests.")
    subparsers.add_parser("test-rust", help="Run Rust unit tests.")
    subparsers.add_parser("test", help="Run catalog freshness, Python, and Rust tests.")
    subparsers.add_parser("check", help="Alias for test.")
    subparsers.add_parser("build", help="Regenerate generated artifacts and build Rust debug binaries.")
    release = subparsers.add_parser("build-release", help="Regenerate generated artifacts and build Rust release binaries.")
    release.set_defaults(release=True)
    package = subparsers.add_parser(
        "package-addon",
        help="Create a distributable .ankiaddon archive with the platform companion server bundled.",
    )
    package.add_argument("--output", type=Path, default=ADDON_PACKAGE, help="Output .ankiaddon path.")
    package.add_argument("--debug", action="store_true", help="Bundle target/debug/deckhand-server instead of release.")
    package.add_argument("--skip-build", action="store_true", help="Use an existing companion server binary.")
    package.add_argument(
        "--companion-binary",
        action="append",
        type=parse_companion_binary,
        default=[],
        metavar="PLATFORM=PATH",
        help="Bundle an already-built companion binary at bin/<platform>/deckhand-server. Repeat for multi-platform packages.",
    )
    subparsers.add_parser("clean", help="Remove build-runner dist artifacts.")
    clean_room_parser = subparsers.add_parser(
        "clean-room",
        help="Reset Deckhand-owned local dev state and reinstall from this checkout.",
    )
    clean_room_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to scripts/clean_room_install.py.")

    sync = subparsers.add_parser("sync", help="Build and sync the add-on into a local Anki profile.")
    sync.add_argument("--skip-companion-build", action="store_true", help="Do not rebuild/copy the companion server binary.")
    sync.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed through to scripts/dev_addon_reload.py.")

    inspect = subparsers.add_parser("inspect-mcp", help="Launch MCP Inspector against the live Anki companion MCP endpoint.")
    inspect.add_argument(
        "--url",
        default=os.environ.get("DECKHAND_MCP_URL", "http://127.0.0.1:18765/mcp"),
        help="Streamable HTTP MCP endpoint to inspect.",
    )
    inspect.add_argument("args", nargs=argparse.REMAINDER, help="Inspector arguments placed before the server command.")

    args = parser.parse_args(argv)

    if args.command == "generate":
        generate()
    elif args.command == "check-catalog":
        check_catalog()
    elif args.command == "test-python":
        test_python()
    elif args.command == "test-rust":
        test_rust()
    elif args.command in {"test", "check"}:
        clean_python_caches()
        check_catalog()
        check_tool_surface()
        test_python()
        test_rust()
    elif args.command == "build":
        generate()
        build_rust()
    elif args.command == "build-release":
        generate()
        build_rust(release=True)
    elif args.command == "sync":
        sync_addon(pass_through(args.args), skip_companion_build=args.skip_companion_build)
    elif args.command == "inspect-mcp":
        inspect_mcp(pass_through(args.args), server_url=args.url)
    elif args.command == "package-addon":
        package_addon(
            args.output,
            release=not args.debug,
            skip_build=args.skip_build,
            companion_binaries=dict(args.companion_binary) if args.companion_binary else None,
        )
    elif args.command == "clean":
        clean()
    elif args.command == "clean-room":
        clean_room(pass_through(args.args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
