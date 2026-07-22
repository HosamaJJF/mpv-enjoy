import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fetch_dependencies import DependencyError, load_lock, safe_extract_tar  # noqa: E402
from generate_sbom import build_sbom  # noqa: E402


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
        self.assertEqual(set(self.lock["platform_assets"]), {"windows-x64", "macos-arm64"})

    def test_expected_component_versions(self):
        components = self.lock["components"]
        self.assertEqual(components["mpv"]["version"], "0.41.0")
        self.assertEqual(components["uosc"]["version"], "5.12.0")
        self.assertEqual(components["uosc_danmaku"]["version"], "2.2.0")
        self.assertEqual(
            components["uosc_danmaku"]["commit"],
            "8fb2107d1e04ce1fd700496ca7d2e4a62182016a",
        )

    def test_sbom_has_dependency_relationships(self):
        sbom = build_sbom(self.lock, "macos-arm64")
        self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
        names = {package["name"] for package in sbom["packages"]}
        self.assertIn("mpv-lazy-enjoy", names)
        self.assertIn("uosc", names)
        self.assertIn("uosc_danmaku", names)
        self.assertIn("yt-dlp-binary-macos-arm64", names)


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

    def test_uosc_controls_include_all_danmaku_entry_points(self):
        overrides = json.loads(
            (PROJECT_ROOT / "config" / "uosc-overrides.json").read_text(encoding="utf-8")
        )
        controls = overrides["common"]["controls"]
        self.assertIn("button:danmaku", controls)
        self.assertIn("show_danmaku@uosc_danmaku", controls)
        self.assertIn("button:danmaku_menu", controls)

    def test_danmaku_defaults_are_manual(self):
        config = (
            PROJECT_ROOT / "config" / "common" / "script-opts" / "uosc_danmaku.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("auto_load=no", config)
        self.assertIn("autoload_for_url=no", config)
        self.assertIn("history_path=~~/danmaku-history.json", config)

    def test_macos_launcher_is_isolated_and_does_not_disable_gatekeeper(self):
        launcher = (PROJECT_ROOT / "scripts" / "macos-launcher.sh").read_text(
            encoding="utf-8"
        )
        native_launcher = (PROJECT_ROOT / "scripts" / "macos-launcher.c").read_text(
            encoding="utf-8"
        )
        self.assertIn("Library/Application Support/mpv-lazy-enjoy", launcher)
        self.assertIn('--config-dir="$MPV_LAZY_ENJOY_CONFIG_DIR"', launcher)
        self.assertIn("_NSGetExecutablePath", native_launcher)
        self.assertIn('child_argv[0] = "/bin/sh"', native_launcher)
        self.assertNotIn("spctl --master-disable", launcher)
        self.assertNotIn("xattr -dr", launcher)

    def test_danmaku_bridge_reannounces_uosc_and_buttons(self):
        bridge = (
            PROJECT_ROOT
            / "config"
            / "common"
            / "scripts"
            / "mpv_lazy_enjoy_danmaku_bridge.lua"
        ).read_text(encoding="utf-8")
        self.assertIn("set-button", bridge)
        self.assertIn("uosc-version", bridge)
        self.assertIn("mp.add_timeout", bridge)


class ScriptSyntaxTests(unittest.TestCase):
    def test_python_scripts_compile(self):
        for script in (PROJECT_ROOT / "scripts").glob("*.py"):
            source = script.read_text(encoding="utf-8")
            compile(source, str(script), "exec")

    def test_shell_scripts_parse(self):
        scripts = [
            ("bash", "scripts/build-windows-msys2.sh"),
            ("bash", "scripts/build-macos-arm64.sh"),
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
