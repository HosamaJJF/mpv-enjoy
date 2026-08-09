import base64
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
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
    AssemblyError,
    assemble_windows,
    copy_macos_vulkan_resources,
    configure_danmaku,
    configure_uosc,
    configure_videotogether,
    update_info_plist,
    write_metadata,
)
from build_home import validate_source, write_integrated_config  # noqa: E402
from collect_windows_runtime import msys_virtual_path, parse_ldd_references  # noqa: E402
from dandanplay_credentials import (  # noqa: E402
    APP_ID_ENV,
    APP_SECRET_ENV,
    DandanplayCredentialError,
    DandanplayCredentials,
    PINNED_UPSTREAM_RUNTIME_AES_KEY,
    UPSTREAM_APP_ID_AES_B64,
    UPSTREAM_APP_SECRET_AES_B64,
    load_credentials,
    upstream_app_id_assignment,
    upstream_app_secret_assignment,
    verify_patched_lua,
)
from encode_dandanplay_credentials import (  # noqa: E402
    AES_KEY,
    EncodingError,
    encrypt_with_openssl,
    zero_pad,
)
from verify_release import (  # noqa: E402
    is_github_hosted_intel_runner,
    metal_display_available,
)

TEST_DANDANPLAY_CREDENTIALS = DandanplayCredentials(
    base64.b64encode(b"A" * 16).decode("ascii"),
    base64.b64encode(b"B" * 32).decode("ascii"),
)

DANMAKU_FILE_LOADED_SOURCE = (
    'mp.register_event("file-loaded", function()\n'
    '    local path = mp.get_property("path")\n'
    '    local video = mp.get_property_native("current-tracks/video")\n'
    '    local fps = mp.get_property_number("container-fps", 0)\n'
    '    local duration = mp.get_property_number("duration", 0)\n'
    '    if not video or video["image"] or video["albumart"] or fps < 23 or duration < 60 then\n'
    "        return\n"
    "    end\n"
    "\n"
    "    read_danmaku_source_record(path)\n"
    "\n"
    "    if not get_danmaku_visibility() then\n"
    "        return\n"
    "    end\n"
    "\n"
    "    if filename == nil or dir == nil then\n"
    "        return\n"
    "    end\n"
    "\n"
    "    if ENABLED and COMMENTS == nil and not is_async_running() then\n"
    "        init(path)\n"
    "    end\n"
    "end)\n"
)


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
        self.assertEqual(self.lock["project_version"], "1.2.1")
        self.assertEqual(
            set(self.lock["platform_assets"]),
            {"windows-x64", "macos-arm64", "macos-x64"},
        )

    def test_expected_component_versions(self):
        components = self.lock["components"]
        self.assertEqual(components["mpv"]["version"], "0.41.0")
        self.assertEqual(components["mpv_enjoy_home"]["version"], "1.0.1")
        self.assertEqual(
            components["mpv_enjoy_home"]["commit"],
            "f2eee67ee19437733ec55f728a0bf912c3bd133d",
        )
        self.assertEqual(components["uosc"]["version"], "5.13.0")
        self.assertEqual(
            components["uosc"]["commit"],
            "d124c2c930d69446448022851373e00ae592390d",
        )
        self.assertEqual(components["uosc_danmaku"]["version"], "2.2.0")
        self.assertEqual(components["uosc_videotogether"]["version"], "1.0.1")
        self.assertEqual(
            components["uosc_danmaku"]["commit"],
            "8fb2107d1e04ce1fd700496ca7d2e4a62182016a",
        )
        self.assertEqual(
            components["uosc_videotogether"]["commit"],
            "deb15344e5b1a01d22d3360aab885c1175d2d64c",
        )

    def test_sbom_has_dependency_relationships(self):
        sbom = build_sbom(self.lock, "macos-x64")
        self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
        names = {package["name"] for package in sbom["packages"]}
        self.assertIn("mpv-enjoy", names)
        self.assertIn("uosc", names)
        self.assertIn("uosc_danmaku", names)
        self.assertIn("uosc_videotogether", names)
        self.assertIn("mpv_enjoy_home", names)
        self.assertIn("yt-dlp-binary-macos-x64", names)

        project = next(
            package for package in sbom["packages"] if package["name"] == "mpv-enjoy"
        )
        self.assertEqual(project["licenseDeclared"], "MIT")
        self.assertEqual(project["licenseConcluded"], "MIT")

    def test_sbom_expands_home_npm_and_rust_inventory(self):
        inventory = {
            "components": [
                {
                    "ecosystem": "npm",
                    "name": "svelte",
                    "version": "5.56.6",
                    "license": "MIT",
                    "source": "https://registry.npmjs.org/svelte/-/svelte-5.56.6.tgz",
                },
                {
                    "ecosystem": "cargo",
                    "name": "tauri",
                    "version": "2.11.5",
                    "license": "MIT OR Apache-2.0",
                    "source": "registry+https://github.com/rust-lang/crates.io-index",
                },
            ]
        }
        sbom = build_sbom(self.lock, "windows-x64", inventory)
        names = {package["name"] for package in sbom["packages"]}
        self.assertIn("mpv-enjoy-home-npm-svelte", names)
        self.assertIn("mpv-enjoy-home-cargo-tauri", names)
        home_relationships = [
            relationship
            for relationship in sbom["relationships"]
            if relationship["spdxElementId"] == "SPDXRef-mpv-enjoy-home"
        ]
        self.assertEqual(len(home_relationships), 2)

    def test_license_consolidates_project_and_third_party_terms(self):
        license_text = (PROJECT_ROOT / "LICENSE.MD").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 mpv-enjoy contributors", license_text)
        self.assertIn("mpv-player/mpv", license_text)
        self.assertIn("HosamaJJF/mpv-enjoy-home", license_text)
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
            self.assertEqual(
                notes.read_text(encoding="utf-8").strip(),
                (
                    PROJECT_ROOT / "release-notes" / "v1.2.1.md"
                ).read_text(encoding="utf-8").strip(),
            )
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
    def test_assembly_keeps_home_as_entrypoint_and_mpv_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home.exe"
            home.write_bytes(b"home")
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "mpv.exe").write_bytes(b"mpv")
            (runtime / "mpv.com").write_bytes(b"console")
            config = root / "config"
            config.mkdir()
            (config / "mpv.conf").write_text("", encoding="utf-8")
            yt_dlp = root / "yt-dlp.exe"
            yt_dlp.write_bytes(b"yt-dlp")
            release = root / "release"
            release.mkdir()

            assemble_windows(home, runtime, release, config, yt_dlp)

            self.assertEqual((release / "mpv-enjoy.exe").read_bytes(), b"home")
            self.assertEqual((release / "mpv.exe").read_bytes(), b"mpv")
            self.assertTrue((release / "mpv.com").is_file())
            self.assertTrue((release / "portable_config" / "mpv.conf").is_file())

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
    def test_home_integration_keeps_process_player_and_pinned_toolchains(self):
        windows = (PROJECT_ROOT / "scripts" / "build-windows-msys2.sh").read_text(
            encoding="utf-8"
        )
        macos = (PROJECT_ROOT / "scripts" / "build-macos.sh").read_text(
            encoding="utf-8"
        )
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "build.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("scripts/build_home.py", windows)
        self.assertIn("scripts/build_home.py", macos)
        self.assertIn("-Dlibmpv=false", windows)
        self.assertIn("-Dlibmpv=false", macos)
        self.assertIn("actions/setup-node@v6", workflow)
        self.assertIn("toolchain: 1.92.0", workflow)
        self.assertIn("mpv-player", macos)
        self.assertIn(
            "for MPV_ENJOY_TOOL in python3 meson ninja go clang", windows
        )
        home_guard = windows.index('if [[ ! -f "$MPV_ENJOY_HOME_EXECUTABLE"')
        home_tools = windows.index(
            "for MPV_ENJOY_HOME_TOOL in node npm rustc cargo"
        )
        self.assertLess(home_guard, home_tools)

    def test_home_integrated_config_uses_product_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            tauri = source / "src-tauri"
            tauri.mkdir()
            (source / "package.json").write_text(
                json.dumps({"version": "1.0.1"}), encoding="utf-8"
            )
            (source / "package-lock.json").write_text("{}\n", encoding="utf-8")
            (tauri / "Cargo.lock").write_text("", encoding="utf-8")
            (tauri / "Cargo.toml").write_text(
                '[package]\nname = "mpv-enjoy-home"\nversion = "1.0.1"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            (tauri / "tauri.conf.json").write_text(
                json.dumps(
                    {
                        "productName": "mpv-enjoy Home",
                        "version": "1.0.1",
                        "identifier": "io.github.hosamajjf.mpv-enjoy-home",
                        "app": {"windows": [{"label": "main", "title": "Home"}]},
                    }
                ),
                encoding="utf-8",
            )

            validate_source(source, "1.0.1")
            integrated_path = write_integrated_config(source, "1.2.1")
            self.assertNotIn(b"\r\n", integrated_path.read_bytes())
            integrated = json.loads(integrated_path.read_text(encoding="utf-8"))
            self.assertEqual(integrated["productName"], "mpv-enjoy")
            self.assertEqual(integrated["version"], "1.2.1")
            self.assertEqual(
                integrated["identifier"], "io.github.hosamajjf.mpv-enjoy"
            )
            self.assertEqual(integrated["app"]["windows"][0]["title"], "mpv-enjoy")

    def _write_uosc_source(self, source, audio_menu_item=True):
        scripts = source / "src" / "uosc"
        scripts.mkdir(parents=True)
        (source / "src" / "fonts").mkdir()
        audio_item = (
            "\t\t{title = t('Audio tracks'), value = "
            "'script-binding uosc/audio'},\n"
            if audio_menu_item
            else "\t\t{title = t('Audio'), value = 'script-binding uosc/audio'},\n"
        )
        (scripts / "main.lua").write_text(
            "local uosc_version = '5.13.0'\n"
            "function create_default_menu_items()\n"
            + audio_item
            + "\t\t\t\t{title = t('Update uosc'), value = "
            "'script-binding uosc/update'},\n"
            "end\n"
            "bind_command('update', function()\n"
            "\tif not Elements:has('updater') then require('elements/Updater'):new() end\n"
            "end)\n",
            encoding="utf-8",
        )
        (source / "src" / "uosc.conf").write_text(
            "controls=menu\n"
            "languages=en\n"
            "autoload=yes\n"
            "use_trash=yes\n"
            "default_directory=~/\n",
            encoding="utf-8",
        )

    def _write_danmaku_source(self, source, hash_operator):
        (source / "apis").mkdir(parents=True)
        (source / "main.lua").write_text(
            'VERSION = "2.2.0"\n'
            'require("modules/update")\n'
            'mp.register_script_message("check-update", check_for_update)\n'
            + DANMAKU_FILE_LOADED_SOURCE,
            encoding="utf-8",
        )
        (source / "apis" / "dandanplay.lua").write_text(
            "local file_info = utils.file_info(file_path)\n"
            "    if file_info and file_info.size {} 16 * 1024 * 1024 then\n".format(
                hash_operator
            )
            + upstream_app_id_assignment()
            + "\n"
            + upstream_app_secret_assignment()
            + "\n",
            encoding="utf-8",
        )

    def test_danmaku_exact_size_hash_fix_is_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            self._write_danmaku_source(source, ">")

            configure_danmaku(source, output, TEST_DANDANPLAY_CREDENTIALS)

            dandanplay = (
                output
                / "scripts"
                / "uosc_danmaku"
                / "apis"
                / "dandanplay.lua"
            ).read_text(encoding="utf-8")
            self.assertIn("file_info.size >= 16 * 1024 * 1024", dandanplay)
            self.assertNotIn("file_info.size > 16 * 1024 * 1024", dandanplay)

    def test_danmaku_hash_fix_fails_when_upstream_no_longer_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            self._write_danmaku_source(source, ">=")

            with self.assertRaisesRegex(
                AssemblyError, "hash threshold patch no longer matches upstream"
            ):
                configure_danmaku(source, output, TEST_DANDANPLAY_CREDENTIALS)

    def test_danmaku_file_switch_session_state_fix_is_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            self._write_danmaku_source(source, ">")

            configure_danmaku(source, output, TEST_DANDANPLAY_CREDENTIALS)

            main = (
                output / "scripts" / "uosc_danmaku" / "main.lua"
            ).read_text(encoding="utf-8")
            self.assertNotIn(
                'local fps = mp.get_property_number("container-fps", 0)',
                main,
            )
            self.assertNotIn("fps < 23", main)
            self.assertNotIn(
                "local should_enable = get_danmaku_visibility()",
                main,
            )
            self.assertNotIn("ENABLED = should_enable", main)
            self.assertIn(
                'toggle_danmaku_switch(ENABLED and "on" or "off")',
                main,
            )
            self.assertIn("if not ENABLED then", main)
            self.assertIn('show_message("加载弹幕初始化...", 3)', main)
            self.assertIn(
                "if not (options.autoload_for_url and is_protocol(path)) then",
                main,
            )
            self.assertIn(
                '            show_message("加载弹幕初始化...", 3)\n'
                "            init(path)\n",
                main,
            )
            self.assertIn("    init(path)\nend)", main)
            self.assertNotIn(
                "if ENABLED and COMMENTS == nil and not is_async_running() then",
                main,
            )

    def test_danmaku_file_switch_fix_fails_when_upstream_no_longer_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            self._write_danmaku_source(source, ">")
            main_path = source / "main.lua"
            main = main_path.read_text(encoding="utf-8").replace(
                "    if ENABLED and COMMENTS == nil and not is_async_running() then\n",
                "    if COMMENTS == nil then\n",
            )
            main_path.write_text(main, encoding="utf-8")

            with self.assertRaisesRegex(
                AssemblyError,
                "file switch initialization patch no longer matches upstream",
            ):
                configure_danmaku(source, output, TEST_DANDANPLAY_CREDENTIALS)

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
            "prev,play-pause,next,gap,"
            "cycle:toggle_on:show_danmaku@uosc_danmaku:"
            "on=toggle_on/off=toggle_off?弹幕开关,"
            "button:danmaku,button:danmaku_menu,gap,"
            "button:videotogether,space,"
            "<video,audio>speed,space,<video,audio>subtitles,"
            "<has_many_audio>audio,<has_many_video>video,"
            "<has_many_edition>editions,<stream>stream-quality,"
            "gap,items,gap,fullscreen",
        )

    def test_configure_uosc_adds_sync_menu_and_disables_updater(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            self._write_uosc_source(source)

            configure_uosc(source, output, "windows-x64")

            main = (output / "scripts" / "uosc" / "main.lua").read_text(
                encoding="utf-8"
            )
            config = (output / "script-opts" / "uosc.conf").read_text(
                encoding="utf-8"
            )
            self.assertIn("script-message-to mpv_enjoy_sync open-menu", main)
            self.assertNotIn("script-binding uosc/update", main)
            self.assertIn("uosc is managed by mpv-enjoy", main)
            self.assertIn("button:danmaku_menu", config)

    def test_configure_uosc_rejects_changed_sync_menu_anchor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            self._write_uosc_source(source, audio_menu_item=False)

            with self.assertRaisesRegex(
                AssemblyError, "sync menu patch no longer matches upstream"
            ):
                configure_uosc(source, root / "output", "windows-x64")

    def test_sync_menu_manages_subtitle_and_audio_delay(self):
        script = (
            PROJECT_ROOT / "config" / "common" / "scripts" / "mpv_enjoy_sync.lua"
        ).read_text(encoding="utf-8")
        self.assertIn("sub-delay", script)
        self.assertIn("audio-delay", script)
        self.assertIn("open-menu", script)
        self.assertIn("update-menu", script)
        self.assertIn("reset-all", script)

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
                self.assertIn("mpv-enjoy-1.2.1", text)
                self.assertNotIn("mpv-enjoy-1.2.0", text)

    def test_release_notes_describe_home_process_integration(self):
        notes = (
            PROJECT_ROOT / "release-notes" / "v1.2.0.md"
        ).read_text(encoding="utf-8")
        self.assertIn("首页与播放器各自为单独的程序", notes)
        self.assertIn("由首页负责管理媒体文件夹并拉起播放器", notes)
        self.assertIn("未来首页完善后嵌入libmpv", notes)

    def test_readme_lists_videotogether_with_integrated_components(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        introduction = readme.split("## 修改配置", 1)[0]
        self.assertIn("uosc_videotogether", introduction)
        self.assertNotIn("## 1.2.1 更新", readme)

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
                plistlib.dump({"CFBundleExecutable": "mpv-enjoy-home"}, handle)

            update_info_plist(app, "1.2.1")

            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(plist["CFBundleShortVersionString"], "1.2.1")
            self.assertEqual(plist["CFBundleVersion"], "1.2.1")
            self.assertEqual(plist["CFBundleExecutable"], "mpv-enjoy-home")

    def test_macos_bundle_copies_vulkan_driver_discovery_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mpv_contents = root / "mpv.app" / "Contents"
            manifest = (
                mpv_contents
                / "Resources"
                / "vulkan"
                / "icd.d"
                / "MoltenVK_icd.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"ICD":{"library_path":"../../../Frameworks/libMoltenVK.dylib"}}\n')
            layer = (
                mpv_contents
                / "Resources"
                / "vulkan"
                / "explicit_layer.d"
                / "layer.json"
            )
            layer.parent.mkdir(parents=True)
            layer.write_text("{}\n")
            app = root / "release" / "mpv-enjoy.app"

            copy_macos_vulkan_resources(mpv_contents, app)

            copied = app / "Contents" / "Resources" / "vulkan"
            self.assertEqual(
                (copied / "icd.d" / "MoltenVK_icd.json").read_text(),
                manifest.read_text(),
            )
            self.assertTrue((copied / "explicit_layer.d" / "layer.json").is_file())

    def test_macos_bundle_requires_moltenvk_icd_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(AssemblyError, "MoltenVK ICD manifest"):
                copy_macos_vulkan_resources(
                    root / "mpv.app" / "Contents",
                    root / "release" / "mpv-enjoy.app",
                )

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
        self.assertIn("'uosc-version', '5.13.0'", bridge)
        self.assertIn("mp.add_timeout", bridge)
        self.assertIn(
            "user-data/uosc_danmaku/danmaku-switch-on",
            bridge,
        )
        self.assertIn("mp.observe_property(switch_property", bridge)
        self.assertIn("sync_switch()", bridge)

    def test_macos_build_has_separate_native_architectures(self):
        script = (PROJECT_ROOT / "scripts" / "build-macos.sh").read_text(encoding="utf-8")
        self.assertIn("macos-arm64", script)
        self.assertIn("macos-x64", script)
        self.assertIn("MPV_ENJOY_MACHO_ARCH=x86_64", script)
        self.assertIn('[[ "$MPV_ENJOY_DESCRIPTION" == *"universal binary"* ]]', script)

    def test_macos_release_publishes_only_dmg_from_ci_artifacts(self):
        script = (PROJECT_ROOT / "scripts" / "build-macos.sh").read_text(encoding="utf-8")
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "build.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("mpv-enjoy-1.2.1-$MPV_ENJOY_PLATFORM.dmg", script)
        self.assertNotIn("mpv-enjoy-1.2.1-$MPV_ENJOY_PLATFORM.zip", script)
        self.assertNotIn("mpv-enjoy-1.2.1-${{ matrix.platform }}.zip", workflow)
        self.assertIn('gh run download "$GITHUB_RUN_ID"', workflow)
        self.assertIn('gh release upload "$GITHUB_REF_NAME"', workflow)

    def test_dandanplay_service_attribution_and_ci_secrets_are_present(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "build.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("弹弹play开放弹幕网络", readme)
        self.assertIn("https://www.dandanplay.com/", readme)
        self.assertIn("environment: release-credentials", workflow)
        self.assertEqual(workflow.count(APP_ID_ENV + ": ${{ secrets." + APP_ID_ENV), 2)
        self.assertEqual(
            workflow.count(APP_SECRET_ENV + ": ${{ secrets." + APP_SECRET_ENV), 2
        )


class DandanplayCredentialTests(unittest.TestCase):
    @staticmethod
    def credentials():
        return TEST_DANDANPLAY_CREDENTIALS

    @staticmethod
    def source_tree(root, api_text=None):
        source = root / "source"
        (source / "apis").mkdir(parents=True)
        (source / "main.lua").write_text(
            'VERSION = "2.2.0"\n'
            'require("modules/update")\n'
            'mp.register_script_message("check-update", check_for_update)\n'
            + DANMAKU_FILE_LOADED_SOURCE,
            encoding="utf-8",
        )
        hash_source = (
            "local file_info = utils.file_info(file_path)\n"
            "    if file_info and file_info.size > 16 * 1024 * 1024 then\n"
        )
        if api_text is None:
            api_text = (
                "function make_danmaku_request_args()\n"
                + upstream_app_id_assignment()
                + "\n"
                + upstream_app_secret_assignment()
                + "\nend\n"
            )
        (source / "apis" / "dandanplay.lua").write_text(
            hash_source + api_text,
            encoding="utf-8",
        )
        return source

    def test_load_credentials_requires_valid_non_upstream_ciphertexts(self):
        credentials = self.credentials()
        loaded = load_credentials(
            {
                APP_ID_ENV: credentials.app_id_aes_b64,
                APP_SECRET_ENV: credentials.app_secret_aes_b64,
            }
        )
        self.assertEqual(loaded, credentials)

        invalid_environments = [
            {},
            {APP_ID_ENV: "not base64", APP_SECRET_ENV: credentials.app_secret_aes_b64},
            {
                APP_ID_ENV: base64.b64encode(b"short").decode("ascii"),
                APP_SECRET_ENV: credentials.app_secret_aes_b64,
            },
            {
                APP_ID_ENV: upstream_app_id_assignment().split('"')[1],
                APP_SECRET_ENV: credentials.app_secret_aes_b64,
            },
            {
                APP_ID_ENV: credentials.app_id_aes_b64,
                APP_SECRET_ENV: upstream_app_secret_assignment().split('"')[1],
            },
        ]
        for environment in invalid_environments:
            with self.subTest(environment=environment):
                with self.assertRaises(DandanplayCredentialError):
                    load_credentials(environment)

    def test_configure_danmaku_patches_only_the_runtime_copy(self):
        credentials = self.credentials()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source_tree(root)
            config = root / "config"
            configure_danmaku(source, config, credentials)

            original_api = (source / "apis" / "dandanplay.lua").read_text(
                encoding="utf-8"
            )
            patched_api = (
                config / "scripts" / "uosc_danmaku" / "apis" / "dandanplay.lua"
            ).read_text(encoding="utf-8")
            patched_main = (
                config / "scripts" / "uosc_danmaku" / "main.lua"
            ).read_text(encoding="utf-8")

            self.assertIn(upstream_app_id_assignment(), original_api)
            self.assertIn(upstream_app_secret_assignment(), original_api)
            self.assertIn("file_info.size > 16 * 1024 * 1024", original_api)
            verify_patched_lua(patched_api, credentials)
            self.assertIn("file_info.size >= 16 * 1024 * 1024", patched_api)
            self.assertNotIn('require("modules/update")', patched_main)
            self.assertIn("由 mpv-enjoy 管理", patched_main)
            self.assertNotIn("ENABLED = should_enable", patched_main)
            self.assertIn(
                'toggle_danmaku_switch(ENABLED and "on" or "off")',
                patched_main,
            )
            self.assertIn("if not ENABLED then", patched_main)
            self.assertIn('show_message("加载弹幕初始化...", 3)', patched_main)
            self.assertIn(
                "if not (options.autoload_for_url and is_protocol(path)) then",
                patched_main,
            )
            self.assertNotIn("fps < 23", patched_main)
            self.assertNotIn("not is_async_running()", patched_main)

    def test_configure_danmaku_rejects_ambiguous_upstream_assignments(self):
        credentials = self.credentials()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api = (
                upstream_app_id_assignment()
                + "\n"
                + upstream_app_id_assignment()
                + "\n"
                + upstream_app_secret_assignment()
                + "\n"
            )
            source = self.source_tree(root, api)
            with self.assertRaises(AssemblyError):
                configure_danmaku(source, root / "config", credentials)

    def test_zero_padding_matches_upstream_aes_block_behavior(self):
        self.assertEqual(zero_pad(b"A" * 16), b"A" * 16)
        self.assertEqual(zero_pad(b"A" * 17), b"A" * 17 + b"\0" * 15)
        with self.assertRaises(EncodingError):
            zero_pad(b"")

    def test_encoder_key_matches_pinned_lua_runtime_behavior(self):
        lua_table = {index + 1: value for index, value in enumerate(range(32))}
        for index in range(len(lua_table), 0, -1):
            lua_table[index - 1] = lua_table[index]
        del lua_table[32]
        effective_key = bytes(lua_table[index] for index in range(32))

        self.assertEqual(effective_key, bytes([0x1F]) * 32)
        self.assertEqual(AES_KEY, effective_key)
        self.assertEqual(PINNED_UPSTREAM_RUNTIME_AES_KEY, effective_key)

    @unittest.skipUnless(shutil.which("openssl"), "openssl is unavailable")
    def test_openssl_encoder_matches_pinned_runtime_key_vector(self):
        ciphertext = encrypt_with_openssl(
            shutil.which("openssl"),
            bytes.fromhex("00112233445566778899aabbccddeeff"),
        )
        self.assertEqual(
            base64.b64decode(ciphertext),
            bytes.fromhex("5f43822412c9b1dfd2abe1601e1d86a9"),
        )

    @unittest.skipUnless(shutil.which("openssl"), "openssl is unavailable")
    def test_pinned_upstream_ciphertexts_use_effective_runtime_key(self):
        plaintexts = []
        for ciphertext in (
            UPSTREAM_APP_ID_AES_B64,
            UPSTREAM_APP_SECRET_AES_B64,
        ):
            result = subprocess.run(
                [
                    shutil.which("openssl"),
                    "enc",
                    "-d",
                    "-aes-256-ecb",
                    "-K",
                    AES_KEY.hex(),
                    "-nosalt",
                    "-nopad",
                ],
                input=base64.b64decode(ciphertext),
                capture_output=True,
                check=True,
            )
            plaintexts.append(result.stdout.rstrip(b"\0"))

        self.assertTrue(all(plaintexts))
        self.assertTrue(
            all(
                all(0x21 <= byte <= 0x7E for byte in plaintext)
                for plaintext in plaintexts
            )
        )

    def test_release_verifier_executes_real_lua_credential_check(self):
        verifier = (PROJECT_ROOT / "scripts" / "verify_release.py").read_text(
            encoding="utf-8"
        )
        lua_verifier = (
            PROJECT_ROOT / "scripts" / "verify_dandanplay_credentials.lua"
        ).read_text(encoding="utf-8")
        self.assertIn("verify_dandanplay_runtime", verifier)
        self.assertIn("DANDANPLAY_LUA_CREDENTIALS_OK", verifier)
        self.assertIn("table_to_zero_indexed", lua_verifier)
        self.assertNotIn("curl", lua_verifier)

    def test_release_verifier_initializes_macos_video_output(self):
        verifier = (PROJECT_ROOT / "scripts" / "verify_release.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("verify_macos_video_output", verifier)
        self.assertIn("--force-window=immediate", verifier)
        self.assertIn("MoltenVK_icd.json", verifier)

    def test_macos_video_smoke_requires_an_available_metal_display(self):
        self.assertTrue(
            metal_display_available(
                {
                    "SPDisplaysDataType": [
                        {"spdisplays_metal": "spdisplays_supported"}
                    ]
                }
            )
        )
        self.assertFalse(
            metal_display_available(
                {"SPDisplaysDataType": [{"spdisplays_metal": "spdisplays_unsupported"}]}
            )
        )
        self.assertTrue(
            metal_display_available(
                {
                    "SPDisplaysDataType": [
                        {"spdisplays_mtlgpufamilysupport": "spdisplays_metal4"}
                    ]
                }
            )
        )
        self.assertTrue(metal_display_available({"SPDisplaysDataType": []}))

    def test_macos_video_smoke_skips_only_github_hosted_intel_runner(self):
        hosted_intel = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ARCH": "X64",
            "ImageOS": "macos15",
        }
        self.assertTrue(is_github_hosted_intel_runner(hosted_intel))
        self.assertFalse(
            is_github_hosted_intel_runner({**hosted_intel, "RUNNER_ARCH": "ARM64"})
        )
        self.assertFalse(
            is_github_hosted_intel_runner(
                {key: value for key, value in hosted_intel.items() if key != "ImageOS"}
            )
        )


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
