#!/usr/bin/env python3
"""Build the pinned mpv-enjoy Home source as the integrated application shell."""

import argparse
import json
import os
from pathlib import Path
import platform as host_platform
import plistlib
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

from fetch_dependencies import load_lock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PLATFORMS: Dict[str, Dict[str, str]] = {
    "windows-x64": {
        "target": "x86_64-pc-windows-msvc",
        "host": "AMD64",
    },
    "macos-arm64": {
        "target": "aarch64-apple-darwin",
        "host": "arm64",
    },
    "macos-x64": {
        "target": "x86_64-apple-darwin",
        "host": "x86_64",
    },
}


class HomeBuildError(RuntimeError):
    pass


def _assert_output_target(output: Path) -> None:
    resolved = output.resolve()
    project = PROJECT_ROOT.resolve()
    if resolved == project or project not in resolved.parents:
        raise HomeBuildError("--output must be a child of the project directory")
    if len(resolved.relative_to(project).parts) < 2:
        raise HomeBuildError("--output must be nested below a build or dist directory")


def _assert_source_target(source: Path) -> None:
    try:
        source.resolve().relative_to((PROJECT_ROOT / "build").resolve())
    except ValueError as error:
        raise HomeBuildError("--source must be inside the project build directory") from error


def validate_source(source: Path, expected_version: str) -> None:
    package_path = source / "package.json"
    tauri_path = source / "src-tauri" / "tauri.conf.json"
    cargo_path = source / "src-tauri" / "Cargo.toml"
    lock_path = source / "src-tauri" / "Cargo.lock"
    npm_lock_path = source / "package-lock.json"
    required = (package_path, tauri_path, cargo_path, lock_path, npm_lock_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise HomeBuildError("Home source is incomplete: {}".format(", ".join(missing)))

    package = json.loads(package_path.read_text(encoding="utf-8"))
    tauri = json.loads(tauri_path.read_text(encoding="utf-8"))
    cargo_text = cargo_path.read_text(encoding="utf-8")
    cargo_package = cargo_text.split("[package]", 1)
    cargo_version = None
    if len(cargo_package) == 2:
        match = re.search(
            r'^version\s*=\s*"([^"]+)"\s*$',
            cargo_package[1].split("[", 1)[0],
            re.MULTILINE,
        )
        if match:
            cargo_version = match.group(1)
    versions = {
        str(package.get("version")),
        str(tauri.get("version")),
        str(cargo_version),
    }
    if versions != {expected_version}:
        raise HomeBuildError(
            "Home source versions do not match lock: expected {}, got {}".format(
                expected_version, ", ".join(sorted(versions))
            )
        )
    if tauri.get("identifier") != "io.github.hosamajjf.mpv-enjoy-home":
        raise HomeBuildError("Unexpected standalone Home application identifier")


def write_integrated_config(source: Path, project_version: str) -> Path:
    original = source / "src-tauri" / "tauri.conf.json"
    config = json.loads(original.read_text(encoding="utf-8"))
    config["productName"] = "mpv-enjoy"
    config["version"] = project_version
    config["identifier"] = "io.github.hosamajjf.mpv-enjoy"
    for window in config.get("app", {}).get("windows", []):
        if window.get("label") == "main":
            window["title"] = "mpv-enjoy"
    destination = source / "src-tauri" / "tauri.integrated.conf.json"
    destination.write_bytes(
        (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return destination


def run(command: List[str], source: Path) -> None:
    subprocess.run(command, cwd=str(source), check=True)


def cargo_target_directory(source: Path) -> Path:
    configured = os.environ.get("CARGO_TARGET_DIR")
    if configured:
        target = Path(configured)
        if not target.is_absolute():
            target = source / target
        return target.resolve()
    return source / "src-tauri" / "target"


def built_artifact(source: Path, platform: str) -> Path:
    target = SUPPORTED_PLATFORMS[platform]["target"]
    release = cargo_target_directory(source) / target / "release"
    if platform == "windows-x64":
        return release / "mpv-enjoy-home.exe"
    return release / "bundle" / "macos" / "mpv-enjoy.app"


def validate_artifact(artifact: Path, platform: str) -> None:
    if platform == "windows-x64":
        if not artifact.is_file():
            raise HomeBuildError("Missing Home executable: {}".format(artifact))
        return
    if not artifact.is_dir() or artifact.suffix != ".app":
        raise HomeBuildError("Missing Home application bundle: {}".format(artifact))
    plist_path = artifact / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise HomeBuildError("Home application bundle has no Info.plist")
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    executable = plist.get("CFBundleExecutable")
    if not isinstance(executable, str) or not (
        artifact / "Contents" / "MacOS" / executable
    ).is_file():
        raise HomeBuildError("Home application bundle has no executable")


def copy_artifact(artifact: Path, output: Path, platform: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.is_dir():
            shutil.rmtree(str(output))
        else:
            output.unlink()
    if platform == "windows-x64":
        shutil.copy2(str(artifact), str(output))
    else:
        shutil.copytree(str(artifact), str(output), symlinks=True)


def install_node_dependencies(source: Path, npm: str) -> None:
    run([npm, "ci"], source)


def run_quality_checks(source: Path, npm: str, cargo: str) -> None:
    run([npm, "run", "check"], source)
    run([npm, "run", "format:check"], source)
    run(
        [cargo, "fmt", "--manifest-path", "src-tauri/Cargo.toml", "--check"],
        source,
    )
    run(
        [
            cargo,
            "clippy",
            "--manifest-path",
            "src-tauri/Cargo.toml",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
        source,
    )
    run(
        [cargo, "test", "--manifest-path", "src-tauri/Cargo.toml", "--locked"],
        source,
    )


def generate_release_metadata(source: Path, metadata_output: Path, npm: str) -> None:
    run(
        [
            npm,
            "run",
            "release:metadata",
            "--",
            str(metadata_output),
        ],
        source,
    )
    inventory = metadata_output / "THIRD-PARTY-LICENSES.json"
    if not inventory.is_file():
        raise HomeBuildError("Home dependency inventory was not generated")


def build_package(
    source: Path,
    output: Path,
    metadata_output: Path,
    platform: str,
    config: Path,
    npm: str,
) -> None:
    generate_release_metadata(source, metadata_output, npm)
    target = SUPPORTED_PLATFORMS[platform]["target"]
    command = [
        npm,
        "run",
        "tauri",
        "--",
        "build",
        "--target",
        target,
        "--config",
        str(config),
        "--ci",
    ]
    if platform == "windows-x64":
        command.append("--no-bundle")
    else:
        command.extend(["--bundles", "app", "--no-sign"])
    run(command, source)

    artifact = built_artifact(source, platform)
    validate_artifact(artifact, platform)
    copy_artifact(artifact, output, platform)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("all", "checks", "package"),
        default="all",
        help="Run all tasks, quality checks only, or release packaging only",
    )
    parser.add_argument("--platform", required=True, choices=SUPPORTED_PLATFORMS)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metadata-output", type=Path)
    args = parser.parse_args(argv)

    try:
        args.source = args.source.resolve()
        if args.mode in {"all", "package"}:
            if args.output is None or args.metadata_output is None:
                raise HomeBuildError(
                    "--output and --metadata-output are required for packaging"
                )
            args.output = args.output.resolve()
            args.metadata_output = args.metadata_output.resolve()
            _assert_output_target(args.output)
            _assert_output_target(args.metadata_output)
        _assert_source_target(args.source)
        if not args.source.is_dir():
            raise HomeBuildError("Home source directory does not exist: {}".format(args.source))
        spec = dict(load_lock()["components"]["mpv_enjoy_home"])
        validate_source(args.source, str(spec["version"]))

        expected_host = SUPPORTED_PLATFORMS[args.platform]["host"].lower()
        actual_host = host_platform.machine().lower()
        if args.platform == "windows-x64" and (
            os.name != "nt" or actual_host not in {"amd64", "x86_64"}
        ):
            raise HomeBuildError("windows-x64 Home must be built on x64 Windows")
        if args.platform.startswith("macos-") and actual_host != expected_host:
            raise HomeBuildError(
                "{} must be built natively on {}, got {}".format(
                    args.platform, expected_host, actual_host
                )
            )
        npm = "npm.cmd" if os.name == "nt" else "npm"
        cargo = "cargo.exe" if os.name == "nt" else "cargo"
        install_node_dependencies(args.source, npm)
        if args.mode in {"all", "checks"}:
            run_quality_checks(args.source, npm, cargo)
        if args.mode in {"all", "package"}:
            config = write_integrated_config(
                args.source, str(load_lock()["project_version"])
            )
            build_package(
                args.source,
                args.output,
                args.metadata_output,
                args.platform,
                config,
                npm,
            )
    except (
        HomeBuildError,
        KeyError,
        OSError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    if args.mode == "checks":
        print("Home quality checks passed for {}".format(args.platform))
    else:
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
