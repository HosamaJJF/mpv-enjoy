#!/usr/bin/env python3
"""Assemble mpv-lazy-enjoy around a platform-native mpv build."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

from fetch_dependencies import (
    DEFAULT_CACHE,
    DependencyError,
    component_specs,
    download_artifact,
    extract_component,
    load_lock,
    platform_asset_spec,
    sha256_file,
)
from generate_sbom import build_sbom


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_COMPONENTS = ("mpv", "uosc", "uosc_danmaku", "thumbfast", "yt_dlp_source")


class AssemblyError(RuntimeError):
    pass


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def copy_file(source: Path, destination: Path, executable: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))
    if executable:
        destination.chmod(destination.stat().st_mode | 0o755)


def copy_common_config(config_dir: Path, platform: str) -> None:
    common = PROJECT_ROOT / "config" / "common"
    for name in ("mpv.conf", "input.conf", "profiles.conf", "user.conf"):
        copy_file(common / name, config_dir / name)
    copy_file(PROJECT_ROOT / "config" / "platform" / (platform + ".conf"), config_dir / "platform.conf")
    shutil.copytree(
        str(common / "script-opts"), str(config_dir / "script-opts"), dirs_exist_ok=True
    )
    if (common / "scripts").is_dir():
        shutil.copytree(str(common / "scripts"), str(config_dir / "scripts"), dirs_exist_ok=True)


def patch_assignment(text: str, key: str, value: str) -> str:
    pattern = re.compile(r"^" + re.escape(key) + r"=.*$", re.MULTILINE)
    updated, count = pattern.subn(lambda _match: key + "=" + value, text, count=1)
    if count != 1:
        raise AssemblyError("Could not patch uosc option: {}".format(key))
    return updated


def configure_uosc(uosc_source: Path, config_dir: Path, platform: str) -> None:
    scripts_dir = config_dir / "scripts"
    target = scripts_dir / "uosc"
    shutil.copytree(str(uosc_source / "src" / "uosc"), str(target))
    shutil.copytree(str(uosc_source / "src" / "fonts"), str(config_dir / "fonts"))

    main_path = target / "main.lua"
    main = main_path.read_text(encoding="utf-8")
    if "local uosc_version = '5.12.0'" not in main:
        raise AssemblyError("Unexpected uosc source version")
    update_menu = "\t\t\t\t{title = t('Update uosc'), value = 'script-binding uosc/update'},\n"
    update_binding = (
        "bind_command('update', function()\n"
        "\tif not Elements:has('updater') then require('elements/Updater'):new() end\n"
        "end)"
    )
    managed_binding = (
        "bind_command('update', function()\n"
        "\tmp.osd_message('uosc is managed by mpv-lazy-enjoy; update the whole package instead.')\n"
        "end)"
    )
    if update_menu not in main or update_binding not in main:
        raise AssemblyError("uosc updater patch no longer matches upstream")
    main = main.replace(update_menu, "", 1).replace(update_binding, managed_binding, 1)
    write_text(main_path, main)

    overrides_path = PROJECT_ROOT / "config" / "uosc-overrides.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    values: Dict[str, str] = dict(overrides["common"])
    values.update(overrides[platform])
    config = (uosc_source / "src" / "uosc.conf").read_text(encoding="utf-8")
    for key, value in values.items():
        config = patch_assignment(config, key, value)
    write_text(config_dir / "script-opts" / "uosc.conf", config)


def build_uosc_ziggy(uosc_source: Path, config_dir: Path, platform: str) -> None:
    go = shutil.which("go")
    if go is None:
        raise AssemblyError("Go 1.21 or newer is required to build uosc ziggy")
    if platform == "windows-x64":
        goos, goarch, filename = "windows", "amd64", "ziggy-windows.exe"
    else:
        goos, goarch, filename = "darwin", "arm64", "ziggy-darwin"
    output = config_dir / "scripts" / "uosc" / "bin" / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "GOOS": goos,
            "GOARCH": goarch,
            "CGO_ENABLED": "0",
            "GOCACHE": str(PROJECT_ROOT / ".cache" / "go-build"),
            "GOMODCACHE": str(PROJECT_ROOT / ".cache" / "go-mod"),
        }
    )
    command = [
        go,
        "build",
        "-trimpath",
        "-ldflags=-s -w -buildid=",
        "-o",
        str(output),
        "./src/ziggy",
    ]
    subprocess.run(command, cwd=str(uosc_source), env=environment, check=True)
    output.chmod(output.stat().st_mode | 0o755)


def configure_danmaku(source: Path, config_dir: Path) -> None:
    target = config_dir / "scripts" / "uosc_danmaku"
    shutil.copytree(str(source), str(target), ignore=shutil.ignore_patterns(".git*"))
    main_path = target / "main.lua"
    main = main_path.read_text(encoding="utf-8")
    if 'VERSION = "2.2.0"' not in main:
        raise AssemblyError("Unexpected uosc_danmaku source version")
    require_line = 'require("modules/update")\n'
    update_line = 'mp.register_script_message("check-update", check_for_update)'
    replacement = (
        'mp.register_script_message("check-update", function()\n'
        '    show_message("uosc_danmaku 由 mpv-lazy-enjoy 管理，请更新整个整合包", 3)\n'
        "end)"
    )
    if require_line not in main or update_line not in main:
        raise AssemblyError("uosc_danmaku updater patch no longer matches upstream")
    main = main.replace(require_line, "", 1).replace(update_line, replacement, 1)
    write_text(main_path, main)


def configure_thumbfast(source: Path, config_dir: Path) -> None:
    copy_file(source / "thumbfast.lua", config_dir / "scripts" / "thumbfast.lua")


def copy_licenses_and_sources(
    release_root: Path,
    extracted: Dict[str, Path],
    archives: Dict[str, Path],
) -> None:
    licenses = release_root / "LICENSES"
    sources = release_root / "sources"
    licenses.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    copy_file(PROJECT_ROOT / "LICENSE", licenses / "mpv-lazy-enjoy-MIT.txt")
    copy_file(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md", release_root / "THIRD_PARTY_NOTICES.md")

    candidates = {
        "mpv-GPL-2.0.txt": extracted["mpv"] / "LICENSE.GPL",
        "mpv-LGPL-2.1.txt": extracted["mpv"] / "LICENSE.LGPL",
        "uosc-LGPL-2.1.txt": extracted["uosc"] / "LICENSE.LGPL",
        "uosc_danmaku-MIT.txt": extracted["uosc_danmaku"] / "LICENSE",
        "thumbfast-MPL-2.0.txt": extracted["thumbfast"] / "LICENSE",
        "yt-dlp-Unlicense.txt": extracted["yt_dlp_source"] / "LICENSE",
    }
    for destination_name, source in candidates.items():
        if not source.is_file():
            raise AssemblyError("Missing upstream license: {}".format(source))
        copy_file(source, licenses / destination_name)
    yt_third_party = extracted["yt_dlp_source"] / "THIRD_PARTY_LICENSES.txt"
    if yt_third_party.is_file():
        copy_file(yt_third_party, licenses / "yt-dlp-THIRD_PARTY_LICENSES.txt")
    for name, archive in archives.items():
        copy_file(archive, sources / archive.name)


def write_metadata(
    release_root: Path,
    lock: Dict[str, object],
    platform: str,
    build_manifest: Optional[Path],
) -> None:
    copy_file(PROJECT_ROOT / "dependencies.lock.json", release_root / "dependencies.lock.json")
    if build_manifest is not None:
        if not build_manifest.is_file():
            raise AssemblyError("Build manifest does not exist: {}".format(build_manifest))
        copy_file(build_manifest, release_root / "BUILD-DEPENDENCIES.txt")
    sbom = build_sbom(lock, platform)
    with (release_root / "SBOM.spdx.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(sbom, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    with (release_root / "SHA256SUMS").open("w", encoding="utf-8", newline="\n") as handle:
        for path in sorted(release_root.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                handle.write("{}  {}\n".format(sha256_file(path), path.relative_to(release_root)))


def write_release_readme(release_root: Path, platform: str) -> None:
    if platform == "windows-x64":
        instructions = (
            "解压后直接运行 `mpv.exe`。配置位于 `portable_config`，个人修改可写入 "
            "`portable_config/user.conf`。"
        )
    else:
        instructions = (
            "将 `mpv-lazy-enjoy.app` 拖入“应用程序”。首次运行若被 Gatekeeper 拦截，请在 Finder "
            "中右键选择“打开”，或前往“系统设置 → 隐私与安全性 → 仍要打开”。不要全局关闭 "
            "Gatekeeper。配置位于 `~/Library/Application Support/mpv-lazy-enjoy/config`。"
        )
    text = """# mpv-lazy-enjoy {platform}

{instructions}

弹幕默认采用手动搜索：`Ctrl+d` 打开搜索，`j` 开关弹幕；uosc 控制栏也提供搜索、开关和设置按钮。
包内不含用户历史、缓存、私有路径、VapourSynth、外置着色器或厂商专用 GPU 组件。

依赖版本见 `dependencies.lock.json`，许可证见 `THIRD_PARTY_NOTICES.md` 和 `LICENSES`，
对应上游源码归档位于 `sources`。
""".format(platform=platform, instructions=instructions)
    write_text(release_root / "README-FIRST.zh-CN.md", text)


def assemble_windows(mpv_path: Path, release_root: Path, config_dir: Path, yt_dlp: Path) -> None:
    if mpv_path.is_file():
        if mpv_path.name.lower() != "mpv.exe":
            raise AssemblyError("Windows --mpv file must be mpv.exe")
        copy_file(mpv_path, release_root / "mpv.exe", executable=True)
    elif (mpv_path / "mpv.exe").is_file():
        shutil.copytree(str(mpv_path), str(release_root), dirs_exist_ok=True)
    else:
        raise AssemblyError("Windows mpv runtime does not contain mpv.exe")
    shutil.copytree(str(config_dir), str(release_root / "portable_config"))
    copy_file(yt_dlp, release_root / "yt-dlp.exe", executable=True)


def update_info_plist(app: Path, project_version: str) -> None:
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    plist["CFBundleIdentifier"] = "org.mpv-lazy-enjoy.player"
    plist["CFBundleName"] = "mpv-lazy-enjoy"
    plist["CFBundleDisplayName"] = "mpv-lazy-enjoy"
    plist["CFBundleShortVersionString"] = project_version.split("-")[0]
    plist["CFBundleVersion"] = "1"
    plist["LSMinimumSystemVersion"] = "14.0"
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=True)


def compile_macos_launcher(destination: Path) -> None:
    clang = shutil.which("clang")
    if clang is None:
        raise AssemblyError("clang is required to build the macOS launcher")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            clang,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Os",
            "-arch",
            "arm64",
            "-mmacosx-version-min=14.0",
            str(PROJECT_ROOT / "scripts" / "macos-launcher.c"),
            "-o",
            str(destination),
        ],
        check=True,
    )
    destination.chmod(destination.stat().st_mode | 0o755)


def assemble_macos(
    mpv_path: Path,
    release_root: Path,
    config_dir: Path,
    yt_dlp: Path,
    project_version: str,
) -> None:
    if not mpv_path.is_dir() or mpv_path.suffix != ".app":
        raise AssemblyError("macOS --mpv must point to an mpv.app bundle")
    app = release_root / "mpv-lazy-enjoy.app"
    shutil.copytree(str(mpv_path), str(app), symlinks=True)
    for placeholder in app.rglob(".gitkeep"):
        placeholder.unlink()
    macos_dir = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    binary = macos_dir / "mpv"
    if not binary.is_file():
        raise AssemblyError("mpv.app is missing Contents/MacOS/mpv")
    binary.rename(macos_dir / "mpv-bin")
    compile_macos_launcher(binary)
    copy_file(PROJECT_ROOT / "scripts" / "macos-launcher.sh", resources / "macos-launcher.sh")
    copy_file(yt_dlp, macos_dir / "yt-dlp", executable=True)
    shutil.copytree(str(config_dir), str(resources / "config-template"))
    update_info_plist(app, project_version)


def _assert_output_target(output: Path) -> None:
    resolved = output.resolve()
    project = PROJECT_ROOT.resolve()
    if resolved == project or project not in resolved.parents:
        raise AssemblyError("--output must be a child of the project directory")
    if len(resolved.relative_to(project).parts) < 2:
        raise AssemblyError("--output must be nested below a build or dist directory")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=["windows-x64", "macos-arm64"])
    parser.add_argument("--mpv", required=True, type=Path, help="mpv runtime directory or .app")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    try:
        args.mpv = args.mpv.resolve()
        args.output = args.output.resolve()
        args.cache = args.cache.resolve()
        if args.build_manifest is not None:
            args.build_manifest = args.build_manifest.resolve()
        _assert_output_target(args.output)
        if args.output.exists():
            if not args.force:
                raise AssemblyError("Output already exists: {}".format(args.output))
            shutil.rmtree(str(args.output))
        lock = load_lock()
        components = component_specs(lock)
        archives: Dict[str, Path] = {}
        extracted: Dict[str, Path] = {}
        with tempfile.TemporaryDirectory(prefix="mpv-lazy-enjoy-sources-") as temporary:
            extract_root = Path(temporary)
            for name in REQUIRED_COMPONENTS:
                archives[name] = download_artifact(name, components[name], args.cache)
                extracted[name] = extract_component(
                    name, components[name], args.cache, extract_root / name
                )
            yt_spec = platform_asset_spec(lock, args.platform)
            yt_dlp = download_artifact("yt_dlp@" + args.platform, yt_spec, args.cache)

            args.output.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=args.output.name + ".", dir=str(args.output.parent)))
            try:
                config_dir = staging / "config-work"
                copy_common_config(config_dir, args.platform)
                configure_uosc(extracted["uosc"], config_dir, args.platform)
                build_uosc_ziggy(extracted["uosc"], config_dir, args.platform)
                configure_danmaku(extracted["uosc_danmaku"], config_dir)
                configure_thumbfast(extracted["thumbfast"], config_dir)

                release_root = staging / "release"
                release_root.mkdir()
                if args.platform == "windows-x64":
                    assemble_windows(args.mpv, release_root, config_dir, yt_dlp)
                else:
                    assemble_macos(
                        args.mpv,
                        release_root,
                        config_dir,
                        yt_dlp,
                        str(lock["project_version"]),
                    )
                copy_licenses_and_sources(release_root, extracted, archives)
                write_release_readme(release_root, args.platform)
                write_metadata(release_root, lock, args.platform, args.build_manifest)
                release_root.rename(args.output)
            finally:
                if staging.exists():
                    shutil.rmtree(str(staging))
    except (AssemblyError, DependencyError, OSError, subprocess.CalledProcessError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
