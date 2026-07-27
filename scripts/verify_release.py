#!/usr/bin/env python3
"""Verify the structure, architecture and required config of an assembled release."""

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

from dandanplay_credentials import (
    DandanplayCredentialError,
    DandanplayCredentials,
    credential_fingerprint,
    load_credentials,
    verify_patched_lua,
)


class VerificationError(RuntimeError):
    pass


SUPPORTED_PLATFORMS = ("windows-x64", "macos-arm64", "macos-x64")
MACOS_ARCHES = {"macos-arm64": "arm64", "macos-x64": "x86_64"}


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


def verify_config(
    config: Path,
    platform: str,
    credentials: DandanplayCredentials,
) -> Dict[str, str]:
    required = [
        config / "mpv.conf",
        config / "input.conf",
        config / "profiles.conf",
        config / "script-opts" / "uosc.conf",
        config / "script-opts" / "uosc_danmaku.conf",
        config / "script-opts" / "uosc_videotogether.conf",
        config / "scripts" / "uosc" / "main.lua",
        config / "scripts" / "uosc_danmaku" / "main.lua",
        config / "scripts" / "uosc_danmaku" / "apis" / "dandanplay.lua",
        config / "scripts" / "uosc_videotogether" / "main.lua",
        config / "scripts" / "thumbfast.lua",
        config / "scripts" / "mpv_enjoy_danmaku_bridge.lua",
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
    dandanplay_api = (
        config / "scripts" / "uosc_danmaku" / "apis" / "dandanplay.lua"
    ).read_text(encoding="utf-8")
    uosc_conf = (config / "script-opts" / "uosc.conf").read_text(encoding="utf-8")
    require("local uosc_version = '5.12.0'" in uosc_main, "Unexpected uosc version")
    require('VERSION = "2.2.0"' in danmaku_main, "Unexpected uosc_danmaku version")
    require("require(\"modules/update\")" not in danmaku_main, "Danmaku updater still loads")
    require(
        "file_info.size >= 16 * 1024 * 1024" in dandanplay_api,
        "Danmaku exact-size hash patch is absent",
    )
    require(
        "file_info.size > 16 * 1024 * 1024" not in dandanplay_api,
        "Danmaku exact-size hash bug is still present",
    )
    require("script-binding uosc/update" not in uosc_main, "uosc updater is still in its menu")
    require("button:danmaku" in uosc_conf, "uosc danmaku search button is absent")
    require("button:danmaku_menu" in uosc_conf, "uosc danmaku menu button is absent")
    require("button:videotogether" in uosc_conf, "uosc VideoTogether button is absent")
    require(len(uosc_conf) > 10000, "uosc.conf does not look like the complete upstream config")
    verify_patched_lua(dandanplay_api, credentials)
    require(
        "AES.ECB.decrypt(KEY, Base64.decode(appid))" in dandanplay_api,
        "dandanplay AppId decryption changed unexpectedly",
    )
    require(
        "AES.ECB.decrypt(KEY, Base64.decode(app_accept))" in dandanplay_api,
        "dandanplay AppSecret decryption changed unexpectedly",
    )
    require(
        "X-Signature: %s" in dandanplay_api and "X-Timestamp: %s" in dandanplay_api,
        "dandanplay signed request headers changed unexpectedly",
    )

    if platform == "windows-x64":
        ziggy = config / "scripts" / "uosc" / "bin" / "ziggy-windows.exe"
        videotogether_agent = (
            config
            / "scripts"
            / "uosc_videotogether"
            / "bin"
            / "uosc-videotogether-agent-windows.exe"
        )
        expected = "x86-64"
    else:
        ziggy = config / "scripts" / "uosc" / "bin" / "ziggy-darwin"
        videotogether_agent = (
            config
            / "scripts"
            / "uosc_videotogether"
            / "bin"
            / "uosc-videotogether-agent-darwin"
        )
        expected = MACOS_ARCHES[platform]
    require(ziggy.is_file(), "Missing platform ziggy binary")
    require(videotogether_agent.is_file(), "Missing VideoTogether agent binary")
    ziggy_description = file_description(ziggy)
    agent_description = file_description(videotogether_agent)
    if "unavailable" not in ziggy_description:
        require(
            expected in ziggy_description,
            "Wrong ziggy architecture: {}".format(ziggy_description),
        )
        if platform != "windows-x64":
            require(
                "universal binary" not in ziggy_description,
                "ziggy must not be Universal",
            )
    if "unavailable" not in agent_description:
        require(
            expected in agent_description,
            "Wrong VideoTogether agent architecture: {}".format(agent_description),
        )
        if platform != "windows-x64":
            require(
                "universal binary" not in agent_description,
                "VideoTogether agent must not be Universal",
            )
    return {
        "dandanplay_credentials": credential_fingerprint(credentials),
        "videotogether_agent": agent_description,
        "ziggy": ziggy_description,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=SUPPORTED_PLATFORMS)
    parser.add_argument("--release", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        credentials = load_credentials(os.environ)
        require(args.release.is_dir(), "Release directory does not exist")
        require((args.release / "SBOM.spdx.json").is_file(), "Missing SPDX SBOM")
        require((args.release / "dependencies.lock.json").is_file(), "Missing dependency lock")
        require(
            (args.release / "RELEASE-NOTES.zh-CN.md").is_file(),
            "Missing release notes",
        )
        require((args.release / "LICENSES").is_dir(), "Missing licenses")
        require(
            (args.release / "LICENSES" / "mpv-enjoy-MIT.md").is_file(),
            "Missing mpv-enjoy MIT license",
        )
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
            app = args.release / "mpv-enjoy.app"
            require(app.is_dir(), "Missing mpv-enjoy.app")
            launcher = app / "Contents" / "MacOS" / "mpv"
            helper = app / "Contents" / "Resources" / "macos-launcher.sh"
            require(launcher.is_file(), "Missing native macOS launcher")
            require(os.access(str(launcher), os.X_OK), "macOS launcher is not executable")
            require(helper.is_file(), "Missing macOS launcher helper")
            require((app / "Contents" / "MacOS" / "yt-dlp").is_file(), "Missing macOS yt-dlp")
            config = app / "Contents" / "Resources" / "config-template"
            description = file_description(app / "Contents" / "MacOS" / "mpv-bin")
            launcher_description = file_description(launcher)
            yt_dlp_description = file_description(app / "Contents" / "MacOS" / "yt-dlp")
            architecture = MACOS_ARCHES[args.platform]
            if "unavailable" not in description:
                require(
                    architecture in description,
                    "Wrong mpv architecture: {}".format(description),
                )
                require("universal binary" not in description, "mpv-bin must not be Universal")
            if "unavailable" not in launcher_description:
                require(
                    "Mach-O" in launcher_description and architecture in launcher_description,
                    "macOS launcher must be a native {} Mach-O executable: {}".format(
                        architecture,
                        launcher_description,
                    ),
                )
                require(
                    "universal binary" not in launcher_description,
                    "macOS launcher must not be Universal",
                )
            if "unavailable" not in yt_dlp_description:
                require(
                    architecture in yt_dlp_description,
                    "Wrong yt-dlp architecture: {}".format(yt_dlp_description),
                )
                require(
                    "universal binary" not in yt_dlp_description,
                    "yt-dlp must not be Universal",
                )
            with (app / "Contents" / "Info.plist").open("rb") as handle:
                plist = plistlib.load(handle)
            require(plist.get("CFBundleExecutable") == "mpv", "Unexpected CFBundleExecutable")
            if sys.platform == "darwin":
                with tempfile.TemporaryDirectory(prefix="mpv-enjoy-smoke-") as temporary:
                    environment = os.environ.copy()
                    environment["MPV_ENJOY_HOME"] = temporary
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
            report["yt_dlp"] = yt_dlp_description
        report.update(verify_config(config, args.platform, credentials))
    except (
        DandanplayCredentialError,
        VerificationError,
        OSError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
