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
    configure_danmaku,
    configure_videotogether,
    update_info_plist,
    write_metadata,
)
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

TEST_DANDANPLAY_CREDENTIALS = DandanplayCredentials(
    base64.b64encode(b"A" * 16).decode("ascii"),
    base64.b64encode(b"B" * 32).decode("ascii"),
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
        self.assertEqual(self.lock["project_version"], "1.1.3")
        self.assertEqual(
            set(self.lock["platform_assets"]),
            {"windows-x64", "macos-arm64", "macos-x64"},
        )

    def test_expected_component_versions(self):
        components = self.lock["components"]
        self.assertEqual(components["mpv"]["version"], "0.41.0")
        self.assertEqual(components["uosc"]["version"], "5.12.0")
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
            self.assertEqual(
                notes.read_text(encoding="utf-8").strip(),
                "为弹弹play服务接口申请并替换使用了mpv-enjoy专属的appid，"
                "以减轻因上游uosc_danmaku配额用尽导致的晚间弹幕插件无法正常工作的问题",
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
    def _write_danmaku_source(self, source, hash_operator):
        (source / "apis").mkdir(parents=True)
        (source / "main.lua").write_text(
            'VERSION = "2.2.0"\n'
            'require("modules/update")\n'
            'mp.register_script_message("check-update", check_for_update)\n',
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
                self.assertIn("mpv-enjoy-1.1.3", text)
                self.assertNotIn("mpv-enjoy-1.1.2", text)

    def test_release_notes_match_1_1_3_description(self):
        notes = (
            PROJECT_ROOT / "release-notes" / "v1.1.3.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            notes.strip(),
            "为弹弹play服务接口申请并替换使用了mpv-enjoy专属的appid，"
            "以减轻因上游uosc_danmaku配额用尽导致的晚间弹幕插件无法正常工作的问题",
        )

    def test_readme_lists_videotogether_with_integrated_components(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        introduction = readme.split("## 修改配置", 1)[0]
        self.assertIn("uosc_videotogether", introduction)
        self.assertNotIn("## 1.1.3 更新", readme)

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

            update_info_plist(app, "1.1.3")

            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(plist["CFBundleShortVersionString"], "1.1.3")
            self.assertEqual(plist["CFBundleVersion"], "1.1.3")

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

    def test_macos_release_publishes_only_dmg_from_ci_artifacts(self):
        script = (PROJECT_ROOT / "scripts" / "build-macos.sh").read_text(encoding="utf-8")
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "build.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("mpv-enjoy-1.1.3-$MPV_ENJOY_PLATFORM.dmg", script)
        self.assertNotIn("mpv-enjoy-1.1.3-$MPV_ENJOY_PLATFORM.zip", script)
        self.assertNotIn("mpv-enjoy-1.1.3-${{ matrix.platform }}.zip", workflow)
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
            'mp.register_script_message("check-update", check_for_update)\n',
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
