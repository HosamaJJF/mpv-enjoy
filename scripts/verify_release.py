#!/usr/bin/env python3
"""Verify structure, architecture and privacy properties of an assembled release."""

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


class VerificationError(RuntimeError):
    pass


BANNED_CONFIG_PATTERNS = {
    "VapourSynth": re.compile(r"vapoursynth", re.IGNORECASE),
    "external GLSL shaders": re.compile(r"glsl[-_]shaders?", re.IGNORECASE),
    "NVIDIA-specific config": re.compile(r"nvidia|nvenc|nvdec", re.IGNORECASE),
    "CUDA config": re.compile(r"cuda", re.IGNORECASE),
    "DirectML config": re.compile(r"directml|\bdml\b", re.IGNORECASE),
    "TensorRT config": re.compile(r"tensorr|\btrt\b", re.IGNORECASE),
    "legacy cache path": re.compile(r"~~/_cache"),
    "private Windows user path": re.compile(r"[A-Za-z]:[\\/]Users[\\/]"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def file_description(path: Path) -> str:
    command = shutil.which("file")
    if command is None:
        return "file utility unavailable"
    return subprocess.run(
        [command, str(path)], text=True, capture_output=True, check=True
    ).stdout.strip()


def verify_config(config: Path, platform: str) -> Dict[str, str]:
    required = [
        config / "mpv.conf",
        config / "input.conf",
        config / "profiles.conf",
        config / "script-opts" / "uosc.conf",
        config / "script-opts" / "uosc_danmaku.conf",
        config / "scripts" / "uosc" / "main.lua",
        config / "scripts" / "uosc_danmaku" / "main.lua",
        config / "scripts" / "thumbfast.lua",
        config / "scripts" / "mpv_lazy_enjoy_danmaku_bridge.lua",
    ]
    for path in required:
        require(path.is_file(), "Missing config component: {}".format(path))
    runtime_names = {"danmaku-history.json", "watch_later", "cache", "state"}
    for path in config.rglob("*"):
        require(path.name not in runtime_names, "Release contains runtime state: {}".format(path))

    config_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in config.rglob("*")
        if path.is_file() and path.suffix.lower() in {".conf", ".json"}
    )
    for label, pattern in BANNED_CONFIG_PATTERNS.items():
        require(pattern.search(config_text) is None, "Config contains {}".format(label))

    uosc_main = (config / "scripts" / "uosc" / "main.lua").read_text(encoding="utf-8")
    danmaku_main = (config / "scripts" / "uosc_danmaku" / "main.lua").read_text(
        encoding="utf-8"
    )
    uosc_conf = (config / "script-opts" / "uosc.conf").read_text(encoding="utf-8")
    require("local uosc_version = '5.12.0'" in uosc_main, "Unexpected uosc version")
    require('VERSION = "2.2.0"' in danmaku_main, "Unexpected uosc_danmaku version")
    require("require(\"modules/update\")" not in danmaku_main, "Danmaku updater still loads")
    require("script-binding uosc/update" not in uosc_main, "uosc updater is still in its menu")
    require("button:danmaku" in uosc_conf, "uosc danmaku search button is absent")
    require("button:danmaku_menu" in uosc_conf, "uosc danmaku menu button is absent")
    require(len(uosc_conf) > 10000, "uosc.conf does not look like the complete upstream config")

    if platform == "windows-x64":
        ziggy = config / "scripts" / "uosc" / "bin" / "ziggy-windows.exe"
        expected = "x86-64"
    else:
        ziggy = config / "scripts" / "uosc" / "bin" / "ziggy-darwin"
        expected = "arm64"
    require(ziggy.is_file(), "Missing platform ziggy binary")
    description = file_description(ziggy)
    if "unavailable" not in description:
        require(expected in description, "Wrong ziggy architecture: {}".format(description))
    return {"ziggy": description}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=["windows-x64", "macos-arm64"])
    parser.add_argument("--release", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        require(args.release.is_dir(), "Release directory does not exist")
        require((args.release / "SBOM.spdx.json").is_file(), "Missing SPDX SBOM")
        require((args.release / "dependencies.lock.json").is_file(), "Missing dependency lock")
        require((args.release / "LICENSES").is_dir(), "Missing licenses")
        require((args.release / "sources").is_dir(), "Missing corresponding source archives")
        json.loads((args.release / "SBOM.spdx.json").read_text(encoding="utf-8"))

        report: Dict[str, str] = {"platform": args.platform}
        if args.platform == "windows-x64":
            require((args.release / "mpv.exe").is_file(), "Missing mpv.exe")
            require((args.release / "yt-dlp.exe").is_file(), "Missing yt-dlp.exe")
            config = args.release / "portable_config"
            description = file_description(args.release / "mpv.exe")
            if "unavailable" not in description:
                require("x86-64" in description or "PE32+" in description, "Wrong mpv architecture")
            report["mpv"] = description
        else:
            app = args.release / "mpv-lazy-enjoy.app"
            require(app.is_dir(), "Missing mpv-lazy-enjoy.app")
            launcher = app / "Contents" / "MacOS" / "mpv"
            helper = app / "Contents" / "Resources" / "macos-launcher.sh"
            require(launcher.is_file(), "Missing native macOS launcher")
            require(os.access(str(launcher), os.X_OK), "macOS launcher is not executable")
            require(helper.is_file(), "Missing macOS launcher helper")
            require((app / "Contents" / "MacOS" / "yt-dlp").is_file(), "Missing macOS yt-dlp")
            config = app / "Contents" / "Resources" / "config-template"
            description = file_description(app / "Contents" / "MacOS" / "mpv-bin")
            launcher_description = file_description(launcher)
            if "unavailable" not in description:
                require("arm64" in description, "Wrong mpv architecture: {}".format(description))
                require("x86_64" not in description, "mpv-bin must be native arm64, not Universal")
            if "unavailable" not in launcher_description:
                require(
                    "Mach-O" in launcher_description and "arm64" in launcher_description,
                    "macOS launcher must be a native arm64 Mach-O executable: {}".format(
                        launcher_description
                    ),
                )
                require(
                    "x86_64" not in launcher_description,
                    "macOS launcher must not be Universal",
                )
            with (app / "Contents" / "Info.plist").open("rb") as handle:
                plist = plistlib.load(handle)
            require(plist.get("CFBundleExecutable") == "mpv", "Unexpected CFBundleExecutable")
            if sys.platform == "darwin":
                with tempfile.TemporaryDirectory(prefix="mpv-lazy-enjoy-smoke-") as temporary:
                    environment = os.environ.copy()
                    environment["MPV_LAZY_ENJOY_HOME"] = temporary
                    smoke = subprocess.run(
                        [str(launcher), "--version"],
                        text=True,
                        capture_output=True,
                        env=environment,
                        timeout=30,
                    )
                require(
                    smoke.returncode == 0 and "mpv" in smoke.stdout.lower(),
                    "macOS launcher smoke test failed: {}".format(
                        (smoke.stderr or smoke.stdout).strip()
                    ),
                )
            report["mpv"] = description
            report["launcher"] = launcher_description
        report.update(verify_config(config, args.platform))
    except (VerificationError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
