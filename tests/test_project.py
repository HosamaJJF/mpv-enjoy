import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fetch_dependencies import DependencyError, load_lock, safe_extract_tar  # noqa: E402
from generate_sbom import build_sbom  # noqa: E402
from assemble import (  # noqa: E402
    configure_videotogether,
    update_info_plist,
    write_metadata,
)
from collect_windows_runtime import msys_virtual_path, parse_ldd_references  # noqa: E402


class DependencyLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = load_lock()

    def test_all_downloads_are_pinned_and_hashed(self):
        specs = list(self.lock["components"].values())
        specs.extend(
            platform["yt_dlp"] for platform in self.lock["platform_assets"].values()
        )
        for spec in specs:
            self.assertRegex(spec["sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn("/latest/", spec["url"])
            self.assertNotIn("/main/", spec["url"])

    def test_target_architectures_are_exact(self):
        self.assertEqual(self.lock["project_version"], "1.1.0")
        self.assertEqual(
            set(self.lock["platform_assets"]),
            {"windows-x64", "macos-arm64", "macos-x64"},
        )

    def test_expected_component_versions(self):
        components = self.lock["components"]
        self.assertEqual(components["mpv"]["version"], "0.41.0")
        self.assertEqual(components["uosc"]["version"], "5.12.0")
        self.assertEqual(components["uosc_danmaku"]["version"], "2.2.0")
        self.assertEqual(components["uosc_videotogether"]["version"], "1.0.0")
        self.assertEqual(
            components["uosc_danmaku"]["commit"],
            "8fb2107d1e04ce1fd700496ca7d2e4a62182016a",
        )
        self.assertEqual(
            components["uosc_videotogether"]["commit"],
            "1a4dc93f435eac1c0871a8b1e802155f19862375",
        )

    def test_sbom_has_dependency_relationships(self):
        sbom = build_sbom(self.lock, "macos-x64")
        self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
        names = {package["name"] for package in sbom["packages"]}
        self.assertIn("mpv-enjoy", names)
        self.assertIn("uosc", names)
        self.assertIn("uosc_danmaku", names)
        self.assertIn("uosc_videotogether", names)
        self.assertIn("yt-dlp-binary-macos-x64", names)

        project = next(
            package for package in sbom["packages"] if package["name"] == "mpv-enjoy"
        )
        self.assertEqual(project["licenseDeclared"], "MIT")
        self.assertEqual(project["licenseConcluded"], "MIT")

    def test_license_consolidates_project_and_third_party_terms(self):
        license_text = (PROJECT_ROOT / "LICENSE.MD").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 mpv-enjoy contributors", license_text)
        self.assertIn("mpv-player/mpv", license_text)
        self.assertIn("Tony15246/uosc_danmaku", license_text)
        self.assertIn("HosamaJJF/uosc_videotogether", license_text)
        self.assertFalse((PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").exists())

    def test_release_metadata_includes_versioned_notes(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "release"
            release.mkdir()

            write_metadata(release, self.lock, "windows-x64", None)

            notes = release / "RELEASE-NOTES.zh-CN.md"
            self.assertTrue(notes.is_file())
            self.assertIn("mpv-enjoy 1.1.0", notes.read_text(encoding="utf-8"))
            checksums = (release / "SHA256SUMS").read_text(encoding="utf-8")
            self.assertIn("RELEASE-NOTES.zh-CN.md", checksums)


class ArchiveSafetyTests(unittest.TestCase):
    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo("../escaped.txt")
                payload = b"nope"
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
            with self.assertRaises(DependencyError):
                safe_extract_tar(archive, root / "output")
            self.assertFalse((root / "escaped.txt").exists())


class WindowsRuntimeTests(unittest.TestCase):
    def test_parses_modern_msys2_ldd_paths(self):
        output = "\n".join(
            [
                "KERNEL32.dll => C:\\Windows\\System32\\KERNEL32.dll (0x7ffa0000)",
                "avcodec-62.dll => D:\\a\\_temp\\msys64\\clang64\\bin\\avcodec-62.dll",
                "missing.dll => not found",
            ]
        )
        self.assertEqual(
            list(parse_ldd_references(output)),
            [
                "C:\\Windows\\System32\\KERNEL32.dll",
                "D:\\a\\_temp\\msys64\\clang64\\bin\\avcodec-62.dll",
            ],
        )

    def test_maps_msys_virtual_path_from_python_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "msys64"
            executable = root / "clang64" / "bin" / "python3.exe"
            expected = root / "clang64" / "bin" / "libass-9.dll"
            expected.parent.mkdir(parents=True)
            expected.touch()
            self.assertEqual(
                msys_virtual_path("/clang64/bin/libass-9.dll", executable, "CLANG64"),
                expected.resolve(),
            )


class ConfigurationTests(unittest.TestCase):
    def test_common_config_has_no_platform_specific_acceleration_stack(self):
        config = (PROJECT_ROOT / "config" / "common" / "mpv.conf").read_text(
            encoding="utf-8"
        ).lower()
        banned = ["vapoursynth", "glsl-shaders", "nvidia", "cuda", "directml", "tensorr"]
        for token in banned:
            self.assertNotIn(token, config)
        self.assertIn("vo=gpu-next", config)
        self.assertIn("hwdec=auto", config)

    def test_uosc_controls_match_integrated_layout(self):
        overrides = json.loads(
            (PROJECT_ROOT / "config" / "uosc-overrides.json").read_text(encoding="utf-8")
        )
        controls = overrides["common"]["controls"]
        self.assertEqual(
            controls,
            "menu,gap,<video,audio>subtitles,<has_many_audio>audio,"
            "<has_many_video>video,<has_many_edition>editions,"
            "<stream>stream-quality,button:danmaku,"
            "cycle:toggle_on:show_danmaku@uosc_danmaku:"
            "on=toggle_on/off=toggle_off?弹幕开关,"
            "button:danmaku_menu,button:videotogether,gap,space,"
            "<video,audio>speed,space,shuffle,loop-playlist,loop-file,"
            "gap,prev,items,next,gap,play-pause,gap,fullscreen",
        )

    def test_videotogether_config_avoids_existing_shortcut_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            plugin = source / "scripts" / "uosc_videotogether"
            options = source / "script-opts"
            plugin.mkdir(parents=True)
            options.mkdir(parents=True)
            (plugin / "main.lua").write_text("-- test\n", encoding="utf-8")
            (options / "uosc_videotogether.conf").write_text(
                "server=https://example.com\nmenu_key=Ctrl+Shift+v\n",
                encoding="utf-8",
            )

            configure_videotogether(source, output)

            config = (
                output / "script-opts" / "uosc_videotogether.conf"
            ).read_text(encoding="utf-8")
            self.assertIn("menu_key=\n", config)
            self.assertNotIn("menu_key=Ctrl+Shift+v", config)
            self.assertTrue(
                (
                    output / "scripts" / "uosc_videotogether" / "main.lua"
                ).is_file()
            )

    def test_macos_architectures_have_matching_platform_config(self):
        overrides = json.loads(
            (PROJECT_ROOT / "config" / "uosc-overrides.json").read_text(encoding="utf-8")
        )
        for platform in ("macos-arm64", "macos-x64"):
            self.assertIn(platform, overrides)
            self.assertTrue(
                (PROJECT_ROOT / "config" / "platform" / (platform + ".conf")).is_file()
            )

    def test_danmaku_defaults_are_manual(self):
        config = (
            PROJECT_ROOT / "config" / "common" / "script-opts" / "uosc_danmaku.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("auto_load=no", config)
        self.assertIn("autoload_for_url=no", config)
        self.assertIn("history_path=~~/danmaku-history.json", config)
        self.assertIn("vf_fps=yes", config)
        self.assertIn("fps=60/1.001", config)
        self.assertIn("fontsize=30", config)

    def test_release_version_is_consistent(self):
        paths = [
            PROJECT_ROOT / "README.md",
            PROJECT_ROOT / "scripts" / "build-windows-msys2.sh",
            PROJECT_ROOT / "scripts" / "build-macos.sh",
            PROJECT_ROOT / ".github" / "workflows" / "build.yml",
        ]
        for path in paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("mpv-enjoy-1.1.0", text)
                self.assertNotIn("mpv-enjoy-1.0.0", text)

    def test_release_notes_cover_1_1_0_changes(self):
        notes = (
            PROJECT_ROOT / "release-notes" / "v1.1.0.md"
        ).read_text(encoding="utf-8")
        self.assertIn("VideoTogether", notes)
        self.assertIn("播放/暂停", notes)
        self.assertIn("vf_fps=yes", notes)
        self.assertIn("vf_fps=no", notes)
        self.assertIn("fontsize=30", notes)
        self.assertIn("portable_config/script-opts/uosc_danmaku.conf", notes)
        self.assertIn(
            "~/Library/Application Support/mpv-enjoy/config/script-opts/"
            "uosc_danmaku.conf",
            notes,
        )

    def test_readme_describes_1_1_0_user_facing_changes(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## 1.1.0 更新", readme)
        self.assertIn("VideoTogether", readme)
        self.assertIn("播放/暂停", readme)
        self.assertIn("vf_fps=yes", readme)
        self.assertIn("vf_fps=no", readme)
        self.assertIn("fontsize=30", readme)
        self.assertIn("release-notes/v1.1.0.md", readme)

    def test_macos_launcher_uses_app_support_and_does_not_disable_gatekeeper(self):
        launcher = (PROJECT_ROOT / "scripts" / "macos-launcher.sh").read_text(
            encoding="utf-8"
        )
        native_launcher = (PROJECT_ROOT / "scripts" / "macos-launcher.c").read_text(
            encoding="utf-8"
        )
        self.assertIn("Library/Application Support/mpv-enjoy", launcher)
        self.assertIn('--config-dir="$MPV_ENJOY_CONFIG_DIR"', launcher)
        self.assertIn("_NSGetExecutablePath", native_launcher)
        self.assertIn('child_argv[0] = "/bin/sh"', native_launcher)
        self.assertIn("uosc_videotogether.conf", launcher)
        self.assertNotIn("spctl --master-disable", launcher)
        self.assertNotIn("xattr -dr", launcher)

    def test_macos_bundle_uses_release_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "mpv-enjoy.app"
            plist_path = app / "Contents" / "Info.plist"
            plist_path.parent.mkdir(parents=True)
            with plist_path.open("wb") as handle:
                plistlib.dump({"CFBundleExecutable": "mpv"}, handle)

            update_info_plist(app, "1.1.0")

            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(plist["CFBundleShortVersionString"], "1.1.0")
            self.assertEqual(plist["CFBundleVersion"], "1.1.0")

    def test_danmaku_bridge_reannounces_uosc_and_buttons(self):
        bridge = (
            PROJECT_ROOT
            / "config"
            / "common"
            / "scripts"
            / "mpv_enjoy_danmaku_bridge.lua"
        ).read_text(encoding="utf-8")
        self.assertIn("set-button", bridge)
        self.assertIn("uosc-version", bridge)
        self.assertIn("mp.add_timeout", bridge)

    def test_macos_build_has_separate_native_architectures(self):
        script = (PROJECT_ROOT / "scripts" / "build-macos.sh").read_text(encoding="utf-8")
        self.assertIn("macos-arm64", script)
        self.assertIn("macos-x64", script)
        self.assertIn("MPV_ENJOY_MACHO_ARCH=x86_64", script)
        self.assertIn('[[ "$MPV_ENJOY_DESCRIPTION" == *"universal binary"* ]]', script)


class ScriptSyntaxTests(unittest.TestCase):
    def test_python_scripts_compile(self):
        for script in (PROJECT_ROOT / "scripts").glob("*.py"):
            source = script.read_text(encoding="utf-8")
            compile(source, str(script), "exec")

    def test_shell_scripts_parse(self):
        # Native Windows Python resolves bash.exe to the WSL launcher even when
        # the parent workflow shell is MSYS2. Its sh.exe is the usable MSYS2
        # parser in that environment.
        bash_parser = "sh" if os.name == "nt" and os.environ.get("MSYSTEM") else "bash"
        scripts = [
            (bash_parser, "scripts/build-windows-msys2.sh"),
            (bash_parser, "scripts/build-macos.sh"),
            ("sh", "scripts/macos-launcher.sh"),
        ]
        for shell, script in scripts:
            with self.subTest(script=script):
                result = subprocess.run(
                    [shell, "-n", script],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    "{} failed:\nstdout: {}\nstderr: {}".format(
                        script, result.stdout, result.stderr
                    ),
                )


if __name__ == "__main__":
    unittest.main()
