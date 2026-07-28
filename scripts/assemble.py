#!/usr/bin/env python3
"""Assemble mpv-enjoy around a platform-native mpv build."""

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
from dandanplay_credentials import (
    DandanplayCredentialError,
    DandanplayCredentials,
    app_id_assignment,
    app_secret_assignment,
    load_credentials,
    upstream_app_id_assignment,
    upstream_app_secret_assignment,
    verify_patched_lua,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_COMPONENTS = (
    "mpv",
    "uosc",
    "uosc_danmaku",
    "uosc_videotogether",
    "thumbfast",
    "yt_dlp_source",
)
SUPPORTED_PLATFORMS = ("windows-x64", "macos-arm64", "macos-x64")
MACOS_ARCHES = {
    "macos-arm64": {"macho": "arm64", "go": "arm64"},
    "macos-x64": {"macho": "x86_64", "go": "amd64"},
}


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
        "\tmp.osd_message('uosc is managed by mpv-enjoy; update the whole package instead.')\n"
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
        goos, goarch, filename = "darwin", MACOS_ARCHES[platform]["go"], "ziggy-darwin"
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


def _replace_exactly_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise AssemblyError(label + " patch no longer matches upstream")
    return text.replace(old, new, 1)


def configure_danmaku(
    source: Path,
    config_dir: Path,
    credentials: DandanplayCredentials,
) -> None:
    target = config_dir / "scripts" / "uosc_danmaku"
    shutil.copytree(str(source), str(target), ignore=shutil.ignore_patterns(".git*"))

    dandanplay_path = target / "apis" / "dandanplay.lua"
    dandanplay = dandanplay_path.read_text(encoding="utf-8")
    # Temporary backport of https://github.com/Tony15246/uosc_danmaku/pull/396.
    old_hash_threshold = (
        "    if file_info and file_info.size > 16 * 1024 * 1024 then\n"
    )
    fixed_hash_threshold = (
        "    if file_info and file_info.size >= 16 * 1024 * 1024 then\n"
    )
    if dandanplay.count(old_hash_threshold) != 1:
        raise AssemblyError(
            "uosc_danmaku hash threshold patch no longer matches upstream"
        )
    dandanplay = dandanplay.replace(
        old_hash_threshold, fixed_hash_threshold, 1
    )
    write_text(dandanplay_path, dandanplay)

    main_path = target / "main.lua"
    main = main_path.read_text(encoding="utf-8")
    if 'VERSION = "2.2.0"' not in main:
        raise AssemblyError("Unexpected uosc_danmaku source version")
    # Keep the switch enabled only for the lifetime of the current mpv process.
    # Each new file must be initialized when that session switch is on, even
    # while an asynchronous request from the previous file is being aborted.
    old_video_eligibility = (
        '    local fps = mp.get_property_number("container-fps", 0)\n'
        '    local duration = mp.get_property_number("duration", 0)\n'
        '    if not video or video["image"] or video["albumart"] or fps < 23 or duration < 60 then\n'
    )
    fixed_video_eligibility = (
        '    local duration = mp.get_property_number("duration", 0)\n'
        '    if not video or video["image"] or video["albumart"] or duration < 60 then\n'
    )
    main = _replace_exactly_once(
        main,
        old_video_eligibility,
        fixed_video_eligibility,
        "uosc_danmaku video eligibility",
    )
    old_visibility_restore = (
        "    read_danmaku_source_record(path)\n"
        "\n"
        "    if not get_danmaku_visibility() then\n"
        "        return\n"
        "    end\n"
    )
    fixed_session_restore = (
        "    read_danmaku_source_record(path)\n"
        "\n"
        '    toggle_danmaku_switch(ENABLED and "on" or "off")\n'
        "    if not ENABLED then\n"
        "        return\n"
        "    end\n"
    )
    main = _replace_exactly_once(
        main,
        old_visibility_restore,
        fixed_session_restore,
        "uosc_danmaku session switch state",
    )
    main = _replace_exactly_once(
        main,
        "    if filename == nil or dir == nil then\n"
        "        return\n"
        "    end\n",
        "    if filename == nil or dir == nil then\n"
        "        if not (options.autoload_for_url and is_protocol(path)) then\n"
        '            show_message("加载弹幕初始化...", 3)\n'
        "            init(path)\n"
        "        end\n"
        "        return\n"
        "    end\n",
        "uosc_danmaku URL file switch initialization",
    )
    main = _replace_exactly_once(
        main,
        "    if ENABLED and COMMENTS == nil and not is_async_running() then\n"
        "        init(path)\n"
        "    end\n",
        '    show_message("加载弹幕初始化...", 3)\n'
        "    init(path)\n",
        "uosc_danmaku file switch initialization",
    )
    require_line = 'require("modules/update")\n'
    update_line = 'mp.register_script_message("check-update", check_for_update)'
    replacement = (
        'mp.register_script_message("check-update", function()\n'
        '    show_message("uosc_danmaku 由 mpv-enjoy 管理，请更新整个整合包", 3)\n'
        "end)"
    )
    if require_line not in main or update_line not in main:
        raise AssemblyError("uosc_danmaku updater patch no longer matches upstream")
    main = main.replace(require_line, "", 1).replace(update_line, replacement, 1)
    write_text(main_path, main)

    api_path = target / "apis" / "dandanplay.lua"
    api = api_path.read_text(encoding="utf-8")
    api = _replace_exactly_once(
        api,
        upstream_app_id_assignment(),
        app_id_assignment(credentials.app_id_aes_b64),
        "dandanplay AppId",
    )
    api = _replace_exactly_once(
        api,
        upstream_app_secret_assignment(),
        app_secret_assignment(credentials.app_secret_aes_b64),
        "dandanplay AppSecret",
    )
    verify_patched_lua(api, credentials)
    write_text(api_path, api)


def configure_videotogether(source: Path, config_dir: Path) -> None:
    copy_file(
        source / "scripts" / "uosc_videotogether" / "main.lua",
        config_dir / "scripts" / "uosc_videotogether" / "main.lua",
    )
    config = (source / "script-opts" / "uosc_videotogether.conf").read_text(
        encoding="utf-8"
    )
    # mpv-enjoy already reserves Ctrl+Shift+v for uosc/paste-to-open.
    config = patch_assignment(config, "menu_key", "")
    write_text(
        config_dir / "script-opts" / "uosc_videotogether.conf",
        config,
    )


def build_videotogether_agent(source: Path, config_dir: Path, platform: str) -> None:
    go = shutil.which("go")
    if go is None:
        raise AssemblyError("Go 1.23 or newer is required to build uosc_videotogether")
    if platform == "windows-x64":
        goos, goarch, filename = (
            "windows",
            "amd64",
            "uosc-videotogether-agent-windows.exe",
        )
    else:
        goos, goarch, filename = (
            "darwin",
            MACOS_ARCHES[platform]["go"],
            "uosc-videotogether-agent-darwin",
        )
    output = config_dir / "scripts" / "uosc_videotogether" / "bin" / filename
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
        "./cmd/uosc-videotogether-agent",
    ]
    subprocess.run(command, cwd=str(source), env=environment, check=True)
    output.chmod(output.stat().st_mode | 0o755)


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
    copy_file(PROJECT_ROOT / "LICENSE.MD", licenses / "mpv-enjoy-MIT.md")

    candidates = {
        "mpv-GPL-2.0.txt": extracted["mpv"] / "LICENSE.GPL",
        "mpv-LGPL-2.1.txt": extracted["mpv"] / "LICENSE.LGPL",
        "uosc-LGPL-2.1.txt": extracted["uosc"] / "LICENSE.LGPL",
        "uosc_danmaku-MIT.txt": extracted["uosc_danmaku"] / "LICENSE",
        "uosc_videotogether-MIT.txt": extracted["uosc_videotogether"] / "LICENSE",
        "gorilla-websocket-BSD-2-Clause.txt": (
            extracted["uosc_videotogether"]
            / "LICENSES"
            / "gorilla-websocket-BSD-2-Clause.txt"
        ),
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
    release_notes = (
        PROJECT_ROOT
        / "release-notes"
        / ("v" + str(lock["project_version"]) + ".md")
    )
    if not release_notes.is_file():
        raise AssemblyError("Missing release notes: {}".format(release_notes))
    copy_file(release_notes, release_root / "RELEASE-NOTES.zh-CN.md")
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
            "解压后直接运行 `mpv.exe`。配置位于 `portable_config`，自定义选项可写入 "
            "`portable_config/user.conf`。"
        )
    else:
        instructions = (
            "将 `mpv-enjoy.app` 拖入“应用程序”。首次运行若被 Gatekeeper 拦截，请在 Finder "
            "中右键选择“打开”，或前往“系统设置 → 隐私与安全性 → 仍要打开”。不要全局关闭 "
            "Gatekeeper。配置位于 `~/Library/Application Support/mpv-enjoy/config`。"
        )
    text = """# mpv-enjoy {platform}

{instructions}

弹幕默认采用手动搜索：`Ctrl+d` 打开搜索，`j` 开关弹幕；uosc 控制栏也提供搜索、开关和设置按钮。
VideoTogether 可通过 uosc 控制栏的“一起看”按钮创建或加入房间。
本版本更新内容见 `RELEASE-NOTES.zh-CN.md`。

弹幕数据服务由弹弹play开放弹幕网络提供：https://www.dandanplay.com/ 。

依赖版本见 `dependencies.lock.json`，许可证见 `LICENSES`，
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
    plist["CFBundleIdentifier"] = "io.github.hosamajjf.mpv-enjoy"
    plist["CFBundleName"] = "mpv-enjoy"
    plist["CFBundleDisplayName"] = "mpv-enjoy"
    release_version = project_version.split("-")[0]
    plist["CFBundleShortVersionString"] = release_version
    plist["CFBundleVersion"] = release_version
    plist["LSMinimumSystemVersion"] = "14.0"
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=True)


def compile_macos_launcher(destination: Path, architecture: str) -> None:
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
            architecture,
            "-mmacosx-version-min=14.0",
            str(PROJECT_ROOT / "scripts" / "macos-launcher.c"),
            "-o",
            str(destination),
        ],
        check=True,
    )
    destination.chmod(destination.stat().st_mode | 0o755)


def copy_macos_binary_for_arch(source: Path, destination: Path, architecture: str) -> None:
    lipo = shutil.which("lipo")
    if lipo is None:
        raise AssemblyError("lipo is required to assemble a macOS release")
    result = subprocess.run(
        [lipo, "-archs", str(source)], text=True, capture_output=True, check=True
    )
    architectures = result.stdout.split()
    if architecture not in architectures:
        raise AssemblyError(
            "{} does not contain required architecture {}".format(source, architecture)
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if len(architectures) == 1:
        copy_file(source, destination, executable=True)
    else:
        subprocess.run(
            [lipo, str(source), "-thin", architecture, "-output", str(destination)],
            check=True,
        )
        destination.chmod(destination.stat().st_mode | 0o755)


def assemble_macos(
    mpv_path: Path,
    release_root: Path,
    config_dir: Path,
    yt_dlp: Path,
    project_version: str,
    platform: str,
) -> None:
    if not mpv_path.is_dir() or mpv_path.suffix != ".app":
        raise AssemblyError("macOS --mpv must point to an mpv.app bundle")
    architecture = MACOS_ARCHES[platform]["macho"]
    app = release_root / "mpv-enjoy.app"
    shutil.copytree(str(mpv_path), str(app), symlinks=True)
    for placeholder in app.rglob(".gitkeep"):
        placeholder.unlink()
    macos_dir = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    binary = macos_dir / "mpv"
    if not binary.is_file():
        raise AssemblyError("mpv.app is missing Contents/MacOS/mpv")
    binary.rename(macos_dir / "mpv-bin")
    compile_macos_launcher(binary, architecture)
    copy_file(PROJECT_ROOT / "scripts" / "macos-launcher.sh", resources / "macos-launcher.sh")
    copy_macos_binary_for_arch(yt_dlp, macos_dir / "yt-dlp", architecture)
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
    parser.add_argument("--platform", required=True, choices=SUPPORTED_PLATFORMS)
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
        credentials = load_credentials(os.environ)
        if args.output.exists():
            if not args.force:
                raise AssemblyError("Output already exists: {}".format(args.output))
            shutil.rmtree(str(args.output))
        lock = load_lock()
        components = component_specs(lock)
        archives: Dict[str, Path] = {}
        extracted: Dict[str, Path] = {}
        with tempfile.TemporaryDirectory(prefix="mpv-enjoy-sources-") as temporary:
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
                configure_danmaku(
                    extracted["uosc_danmaku"], config_dir, credentials
                )
                configure_videotogether(extracted["uosc_videotogether"], config_dir)
                build_videotogether_agent(
                    extracted["uosc_videotogether"], config_dir, args.platform
                )
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
                        args.platform,
                    )
                copy_licenses_and_sources(release_root, extracted, archives)
                write_release_readme(release_root, args.platform)
                write_metadata(release_root, lock, args.platform, args.build_manifest)
                release_root.rename(args.output)
            finally:
                if staging.exists():
                    shutil.rmtree(str(staging))
    except (
        AssemblyError,
        DandanplayCredentialError,
        DependencyError,
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
