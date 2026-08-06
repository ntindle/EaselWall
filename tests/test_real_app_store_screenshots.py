import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
COMPOSER_PATH = ROOT / "scripts" / "compose_app_store_screenshots.py"
CAPTURE_SCRIPT = ROOT / "scripts" / "capture_app_store_screenshots.sh"
INVENTORY_PATH = ROOT / "scripts" / "easelwall_capture_inventory.py"
PROVENANCE_PATH = ROOT / "scripts" / "easelwall_capture_provenance.py"
SUPPORT_PATH = ROOT / "scripts" / "easelwall_capture_support.swift"
METADATA_PATH = ROOT / "marketing" / "app-store-metadata.md"
PROJECT_SPEC = ROOT / "project.yml"
APP_SOURCE = ROOT / "Sources" / "EaselWall" / "App" / "EaselWallApp.swift"
CAPTURE_SOURCE = ROOT / "Sources" / "EaselWall" / "App" / "ScreenshotCapture.swift"
MAT_RENDERER_SOURCE = ROOT / "Sources" / "EaselWall" / "Services" / "MatRenderer.swift"

SPEC = importlib.util.spec_from_file_location("real_screenshot_composer", COMPOSER_PATH)
assert SPEC is not None and SPEC.loader is not None
COMPOSER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPOSER)

INVENTORY_SPEC = importlib.util.spec_from_file_location(
    "easelwall_capture_inventory", INVENTORY_PATH
)
assert INVENTORY_SPEC is not None and INVENTORY_SPEC.loader is not None
INVENTORY = importlib.util.module_from_spec(INVENTORY_SPEC)
sys.modules[INVENTORY_SPEC.name] = INVENTORY
INVENTORY_SPEC.loader.exec_module(INVENTORY)

PROVENANCE_SPEC = importlib.util.spec_from_file_location(
    "easelwall_capture_provenance", PROVENANCE_PATH
)
assert PROVENANCE_SPEC is not None and PROVENANCE_SPEC.loader is not None
PROVENANCE = importlib.util.module_from_spec(PROVENANCE_SPEC)
PROVENANCE_SPEC.loader.exec_module(PROVENANCE)


def write_image(path: Path, size, color, mode="RGB"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path)


def write_readiness(path: Path, image_path: Path, kind: str, window_id: int):
    payload = {
        "pid": 1234,
        "kind": kind,
        "windowID": window_id,
        "widthPoints": 520 if kind == "settings" else 0,
        "heightPoints": 360 if kind == "settings" else 0,
        "scale": 1,
    }
    with Image.open(image_path) as image:
        payload["capturePixelWidth"] = image.width
        payload["capturePixelHeight"] = image.height
    payload["captureSha256"] = COMPOSER.sha256(image_path)
    path.write_text(json.dumps(payload), encoding="utf-8")


def shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


class RealScreenshotComposerTests(unittest.TestCase):
    def make_sources(self, source: Path, include_portrait=True, include_menu_wallpaper=True):
        write_image(source / "wallpaper-landscape.png", (1600, 1000), "#ded3bf")
        if include_portrait:
            write_image(source / "wallpaper-portrait.png", (900, 1600), "#82979b")
        if include_menu_wallpaper:
            write_image(source / "menu-wallpaper-screen-1.png", (1600, 1000), "#334455")

        write_image(source / "menu.png", (560, 620), (30, 30, 30, 255), "RGBA")
        write_readiness(source / "menu.ready.json", source / "menu.png", "menu", 200)
        for tab in ("appearance", "displays", "gallery", "schedule"):
            image_path = source / f"settings-{tab}.png"
            write_image(
                image_path,
                (520, 392),
                (40, 40, 40, 255),
                "RGBA",
            )
            write_readiness(
                source / f"settings-{tab}.ready.json",
                image_path,
                "settings",
                300 + len(tab),
            )

    def test_compose_writes_exact_rgb_upload_set_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source)

            manifest = COMPOSER.compose(source, output)

            self.assertEqual(len(manifest["outputs"]), 5)
            self.assertEqual(
                [item["filename"] for item in manifest["outputs"]],
                [
                    "01-customize-mats.png",
                    "02-every-display.png",
                    "03-curated-gallery.png",
                    "04-daily-schedule.png",
                    "05-current-painting.png",
                ],
            )
            for item in manifest["outputs"]:
                path = output / item["filename"]
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1280, 800))
                    self.assertEqual(image.mode, "RGB")
                    self.assertNotIn("A", image.getbands())
                self.assertEqual(len(item["sha256"]), 64)

            written_manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(written_manifest["schemaVersion"], 1)
            self.assertIn("no mock UI", written_manifest["policy"])
            first = written_manifest["outputs"][0]
            self.assertEqual(first["uiCapture"], "settings-appearance.png")
            self.assertEqual(first["backgroundKind"], "captured-wallpaper")
            last = written_manifest["outputs"][-1]
            self.assertEqual(last["uiCapture"], "menu.png")
            self.assertEqual(last["backgroundKind"], "paired-menu-wallpaper")
            self.assertEqual(last["wallpaperCaptures"], ["menu-wallpaper-screen-1.png"])
            self.assertEqual(last["uiReadiness"], "menu.ready.json")
            self.assertEqual(len(last["uiReadinessSha256"]), 64)

    def test_menu_without_paired_wallpaper_uses_neutral_background(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source, include_menu_wallpaper=False)

            manifest = COMPOSER.compose(source, output)

            last = manifest["outputs"][-1]
            self.assertEqual(last["backgroundKind"], "neutral")
            self.assertEqual(last["wallpaperCaptures"], [])
            with Image.open(output / "05-current-painting.png") as image:
                self.assertEqual(image.getpixel((0, 0)), (32, 35, 41))

    def test_compose_supports_a_single_landscape_display(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source, include_portrait=False)

            COMPOSER.compose(source, output)

            with Image.open(output / "02-every-display.png") as image:
                self.assertEqual(image.size, (1280, 800))
                self.assertEqual(image.mode, "RGB")

    def test_rejects_full_display_capture_as_menu_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source)
            write_image(source / "menu.png", (2560, 1440), "#111111")

            with self.assertRaisesRegex(ValueError, "isolated menu capture"):
                COMPOSER.compose(source, output)

    def test_menu_requires_matching_native_readiness_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source)

            (source / "menu.ready.json").unlink()
            with self.assertRaisesRegex(ValueError, "missing native readiness provenance"):
                COMPOSER.compose(source, output)

            write_readiness(source / "menu.ready.json", source / "menu.png", "menu", 200)
            readiness = json.loads((source / "menu.ready.json").read_text())
            readiness["captureSha256"] = "0" * 64
            (source / "menu.ready.json").write_text(json.dumps(readiness))
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                COMPOSER.compose(source, output)

    def test_menu_readiness_kind_and_dimensions_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source)

            readiness_path = source / "menu.ready.json"
            readiness = json.loads(readiness_path.read_text())
            readiness["kind"] = "settings"
            readiness_path.write_text(json.dumps(readiness))
            with self.assertRaisesRegex(ValueError, "expected 'menu'"):
                COMPOSER.compose(source, output)

            write_readiness(readiness_path, source / "menu.png", "menu", 200)
            readiness = json.loads(readiness_path.read_text())
            readiness["capturePixelHeight"] += 1
            readiness_path.write_text(json.dumps(readiness))
            with self.assertRaisesRegex(ValueError, "dimensions do not match"):
                COMPOSER.compose(source, output)

    def test_settings_require_exact_capture_hash_and_pixel_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source)

            image_path = source / "settings-appearance.png"
            write_image(image_path, (520, 392), (41, 40, 40, 255), "RGBA")
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                COMPOSER.compose(source, output)

            write_readiness(
                source / "settings-appearance.ready.json",
                image_path,
                "settings",
                310,
            )
            readiness_path = source / "settings-appearance.ready.json"
            readiness = json.loads(readiness_path.read_text())
            readiness["capturePixelWidth"] += 1
            readiness_path.write_text(json.dumps(readiness))
            with self.assertRaisesRegex(ValueError, "dimensions do not match"):
                COMPOSER.compose(source, output)

    def test_existing_outputs_are_archived_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            self.make_sources(source)
            output.mkdir()
            old = output / "01-customize-mats.png"
            old.write_bytes(b"previous")
            superseded_name = output / "01-current-painting.png"
            superseded_name.write_bytes(b"previous ordering")

            COMPOSER.compose(source, output)

            archived = list((output / "archive").glob("*/01-customize-mats.png"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_bytes(), b"previous")
            superseded = list((output / "archive").glob("*/01-current-painting.png"))
            self.assertEqual(len(superseded), 1)
            self.assertEqual(superseded[0].read_bytes(), b"previous ordering")
            self.assertFalse(superseded_name.exists())


class CaptureInventoryTests(unittest.TestCase):
    def make_inventory(self, root: Path, display_ids=(101, 202)):
        manifest = root / "wallpapers-before.json"
        manifest.write_text(
            json.dumps([{"displayID": display_id} for display_id in display_ids]),
            encoding="utf-8",
        )
        marker = root / "marker"
        marker.touch()
        os.utime(marker, ns=(1_000_000_000, 1_000_000_000))
        rendered = root / "tmp" / "EaselWall"
        rendered.mkdir(parents=True)
        return manifest, marker, rendered

    def write_render(self, rendered: Path, display_id: int, timestamp: int):
        path = rendered / f"wallpaper_{display_id}_{timestamp}.png"
        path.write_bytes(b"png bytes")
        os.utime(path, ns=(2_000_000_000, 2_000_000_000))
        return path

    def test_exact_render_set_is_sorted_and_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, marker, rendered = self.make_inventory(root)
            second = self.write_render(rendered, 202, 22)
            first = self.write_render(rendered, 101, 11)

            expected = INVENTORY.expected_display_ids(manifest)
            result = INVENTORY.discover_render_set(rendered, marker, expected)

            self.assertEqual([item.display_id for item in result], [101, 202])
            self.assertEqual([item.path for item in result], [first, second])
            self.assertTrue(all(item.size > 0 for item in result))

    def test_missing_render_is_retryable_and_cli_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, marker, rendered = self.make_inventory(root)
            self.write_render(rendered, 101, 11)

            with self.assertRaises(INVENTORY.IncompleteRenderSet):
                INVENTORY.discover_render_set(
                    rendered,
                    marker,
                    INVENTORY.expected_display_ids(manifest),
                )

            completed = subprocess.run(
                [
                    "python3",
                    str(INVENTORY_PATH),
                    "--manifest",
                    str(manifest),
                    "--render-dir",
                    str(rendered),
                    "--marker",
                    str(marker),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("missing 202", completed.stderr)

    def test_extra_duplicate_and_malformed_fresh_renders_are_rejected(self):
        cases = (
            ((101, 202, 303), "unexpected display IDs"),
            ((101, 101, 202), "duplicate display IDs"),
        )
        for display_ids, message in cases:
            with self.subTest(display_ids=display_ids):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    manifest, marker, rendered = self.make_inventory(root)
                    for index, display_id in enumerate(display_ids):
                        self.write_render(rendered, display_id, index + 1)
                    with self.assertRaisesRegex(INVENTORY.RenderSetError, message):
                        INVENTORY.discover_render_set(
                            rendered,
                            marker,
                            INVENTORY.expected_display_ids(manifest),
                        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, marker, rendered = self.make_inventory(root)
            malformed = rendered / "wallpaper_101_bad.png"
            malformed.write_bytes(b"bad")
            os.utime(malformed, ns=(2_000_000_000, 2_000_000_000))
            with self.assertRaisesRegex(INVENTORY.RenderSetError, "unexpected fresh"):
                INVENTORY.discover_render_set(
                    rendered,
                    marker,
                    INVENTORY.expected_display_ids(manifest),
                )

    def test_stale_renders_are_ignored_and_duplicate_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, marker, rendered = self.make_inventory(root)
            stale = self.write_render(rendered, 101, 1)
            os.utime(stale, ns=(500_000_000, 500_000_000))
            self.write_render(rendered, 101, 2)
            self.write_render(rendered, 202, 3)

            result = INVENTORY.discover_render_set(
                rendered,
                marker,
                INVENTORY.expected_display_ids(manifest),
            )
            self.assertEqual(len(result), 2)

            manifest.write_text(
                json.dumps([{"displayID": 101}, {"displayID": 101}]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(INVENTORY.RenderSetError, "duplicate"):
                INVENTORY.expected_display_ids(manifest)


class CaptureProvenanceTests(unittest.TestCase):
    def test_finalize_readiness_binds_exact_png_and_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "menu.json"
            capture = root / "menu.png"
            ready.write_text(
                json.dumps(
                    {
                        "pid": 42,
                        "kind": "menu",
                        "windowID": None,
                        "widthPoints": 0,
                        "heightPoints": 0,
                        "scale": 2,
                    }
                ),
                encoding="utf-8",
            )
            write_image(capture, (342, 330), "#111111")

            payload = PROVENANCE.finalize_readiness(
                ready,
                capture,
                expected_kind="menu",
                expected_pid=42,
                window_id=99,
            )

            self.assertEqual(payload["windowID"], 99)
            self.assertEqual(payload["capturePixelWidth"], 342)
            self.assertEqual(payload["capturePixelHeight"], 330)
            self.assertEqual(payload["captureSha256"], COMPOSER.sha256(capture))
            self.assertEqual(json.loads(ready.read_text()), payload)

    def test_finalize_readiness_rejects_wrong_native_process_or_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "menu.json"
            capture = root / "menu.png"
            ready.write_text(json.dumps({"pid": 42, "kind": "settings"}))
            write_image(capture, (342, 330), "#111111")

            with self.assertRaisesRegex(ValueError, "expected 'menu'"):
                PROVENANCE.finalize_readiness(
                    ready,
                    capture,
                    expected_kind="menu",
                    expected_pid=42,
                    window_id=99,
                )

    def test_finalize_readiness_rejects_a_different_native_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "settings.json"
            capture = root / "settings.png"
            ready.write_text(
                json.dumps({"pid": 42, "kind": "settings", "windowID": 98})
            )
            write_image(capture, (520, 392), "#111111")

            with self.assertRaisesRegex(ValueError, "window ID.*expected 99"):
                PROVENANCE.finalize_readiness(
                    ready,
                    capture,
                    expected_kind="settings",
                    expected_pid=42,
                    window_id=99,
                )


class RealScreenshotHarnessSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture_script = CAPTURE_SCRIPT.read_text(encoding="utf-8")
        cls.inventory_source = INVENTORY_PATH.read_text(encoding="utf-8")
        cls.support_source = SUPPORT_PATH.read_text(encoding="utf-8")
        cls.metadata = METADATA_PATH.read_text(encoding="utf-8")
        cls.project_spec = PROJECT_SPEC.read_text(encoding="utf-8")
        cls.app_source = APP_SOURCE.read_text(encoding="utf-8")
        cls.capture_source = CAPTURE_SOURCE.read_text(encoding="utf-8")
        cls.mat_renderer_source = MAT_RENDERER_SOURCE.read_text(encoding="utf-8")

    def test_shell_script_parses(self):
        subprocess.run(["bash", "-n", str(CAPTURE_SCRIPT)], check=True)

    def test_capture_never_uses_display_region_or_interactive_screencapture(self):
        capture_lines = [
            line.strip()
            for line in self.capture_script.splitlines()
            if line.strip().startswith("screencapture ")
        ]
        self.assertGreaterEqual(len(capture_lines), 2)
        for line in capture_lines:
            self.assertIn(" -l ", line)
            self.assertNotIn(" -D ", line)
            self.assertNotIn(" -R ", line)
            self.assertNotIn(" -w ", line)
            self.assertNotIn(" -i ", line)

    def test_screenshot_mode_is_not_enabled_in_app_store_configuration(self):
        self.assertIn("Screenshot: debug", self.project_spec)
        self.assertIn(
            "SWIFT_ACTIVE_COMPILATION_CONDITIONS: APPSTORE SCREENSHOT_CAPTURE",
            self.project_spec,
        )
        app_store_blocks = self.project_spec.split("AppStore:")[1:]
        self.assertTrue(app_store_blocks)
        for block in app_store_blocks:
            before_next_config = block.split("Screenshot:", 1)[0]
            self.assertNotIn("SCREENSHOT_CAPTURE", before_next_config)

    def test_capture_path_exits_before_normal_app_side_effects(self):
        capture_branch = self.app_source.index("if let capture")
        login_registration = self.app_source.index("if settings.launchAtLogin")
        self.assertLess(capture_branch, login_registration)
        self.assertIn("#if SCREENSHOT_CAPTURE", self.app_source)
        self.assertIn("--screenshot-ready=", self.capture_source)
        self.assertIn("throw CaptureError.invalidTab", self.capture_source)

    def test_menu_capture_renders_and_identifies_its_own_wallpaper_set(self):
        self.assertIn(
            "--screenshot-menu --screenshot-render-wallpaper",
            self.capture_script,
        )
        self.assertIn(
            'copy_fresh_wallpapers "$menu_wallpaper_marker" "menu-wallpaper"',
            self.capture_script,
        )
        settings_capture = self.capture_script[
            self.capture_script.index("capture_settings_tab()") :
            self.capture_script.index("copy_fresh_wallpapers()")
        ]
        self.assertIn("easelwall_capture_provenance.py", settings_capture)
        self.assertIn("--kind settings", settings_capture)

    def test_external_cua_menu_mode_uses_owned_window_handshake_without_osascript(self):
        capture_menu = self.capture_script[
            self.capture_script.index("capture_menu()") :
            self.capture_script.index('if [[ "$RUN_MODE" == "compose" ]]')
        ]
        baseline = capture_menu.index('"$SUPPORT_TOOL" window-ids "$CAPTURE_PID"')
        cua_branch_start = capture_menu.index('if [[ "$CUA_MENU_MODE" -eq 1 ]]')
        cua_branch_end = capture_menu.index("\n  else\n", cua_branch_start)
        cua_branch = capture_menu[cua_branch_start:cua_branch_end]
        self.assertLess(baseline, cua_branch_start)
        self.assertIn("CUA_MENU_READY", cua_branch)
        self.assertIn("cua-menu-ready.txt", cua_branch)
        self.assertIn("local cua_attempts=720", cua_branch)
        self.assertIn("within 180 seconds", cua_branch)
        self.assertIn("Open EaselWall Menu", cua_branch)
        self.assertIn("sleep 0.25", cua_branch)
        self.assertIn(
            '"$SUPPORT_TOOL" new-window "$CAPTURE_PID" "${before[@]}"',
            cua_branch,
        )
        self.assertNotIn("osascript", cua_branch)
        default_branch = capture_menu[cua_branch_end:]
        self.assertIn("osascript", default_branch)
        self.assertIn("usage: $0 [--compose-only|--cua-menu]", self.capture_script)

    def test_compose_only_does_not_require_the_capture_toolchain(self):
        preflight = self.capture_script[
            self.capture_script.index("required_commands=(python3)") :
            self.capture_script.index("arm_signal_traps()")
        ]
        self.assertIn('if [[ "$RUN_MODE" == "capture" ]]', preflight)
        self.assertIn(
            "required_commands+=(xcodegen xcodebuild xcrun screencapture osascript defaults jq)",
            preflight,
        )
        self.assertIn('for command in "${required_commands[@]}"', preflight)

    def test_cua_trigger_opens_real_menu_view_in_native_screenshot_only_popover(self):
        self.assertIn("#if SCREENSHOT_CAPTURE", self.app_source)
        self.assertIn("showMenuForScreenshot(capture: capture)", self.app_source)
        self.assertIn('title: "Open EaselWall Menu"', self.app_source)
        self.assertIn('setAccessibilityLabel("Open EaselWall Menu")', self.app_source)
        self.assertIn("NSPopover()", self.app_source)
        self.assertIn("NSHostingController(rootView: menuView)", self.app_source)
        self.assertIn("let menuView = MenuBarView(", self.app_source)
        self.assertIn("paintingStore: paintingStore", self.app_source)
        self.assertIn("wallpaperManager: wallpaperManager", self.app_source)
        self.assertIn("screenManager: screenManager", self.app_source)
        self.assertIn("capture.writeReady(window: nil)", self.app_source)
        self.assertIn("screenshotMenuTriggerWindow?.alphaValue = 0", self.app_source)
        self.assertIn(
            "popover.contentViewController?.view.window?.makeFirstResponder(nil)",
            self.app_source,
        )

    def test_wallpaper_restore_is_backed_by_copied_files(self):
        self.assertIn("backupPath", self.support_source)
        self.assertIn("originalBytes = try Data(contentsOf: url", self.support_source)
        self.assertIn("try originalBytes.write(to: backupURL", self.support_source)
        self.assertIn("desktopImageOptions(for: screen)", self.support_source)
        self.assertIn("PropertyListSerialization.data", self.support_source)
        self.assertNotIn("NSScreen.screens.compactMap", self.support_source)

    def test_wallpaper_restore_aggregates_failures_and_restores_options(self):
        self.assertIn("var restoreFailures: [String] = []", self.support_source)
        self.assertIn("for wallpaper in saved.sorted", self.support_source)
        self.assertIn("restoreFailures.append", self.support_source)
        self.assertIn("options: options", self.support_source)
        self.assertNotIn("options: [:]", self.support_source)
        set_call = self.support_source.index("setDesktopImageURL")
        wait_call = self.support_source.index(
            "waitForWallpaperRestoreVerification(", set_call
        )
        self.assertLess(set_call, wait_call)
        verifier = self.support_source[
            self.support_source.index("wallpaperRestoreVerificationMismatch(") :
            self.support_source.index("private func saveWallpapers")
        ]
        self.assertIn("desktopImageURL(for: screen)", verifier)
        self.assertIn("desktopImageOptions(for: screen)", verifier)
        self.assertIn("desktopImageOptionsAreSemanticallyEqual", self.support_source)
        self.assertIn("restoredBytes == expectedBytes", verifier)
        self.assertIn(
            "wallpaperRestoreVerificationAttempts = 49", self.support_source
        )
        self.assertIn(
            "wallpaperRestoreVerificationInterval: TimeInterval = 0.25",
            self.support_source,
        )
        self.assertIn("Thread.sleep", verifier)
        self.assertIn("timed out after", verifier)

    def test_capture_uses_isolated_tmpdir_and_exact_manifest_render_set(self):
        self.assertIn('TMPDIR="$CAPTURE_TMPDIR/"', self.capture_script)
        self.assertIn(
            'EASELWALL_SCREENSHOT_RENDER_DIRECTORY="$CAPTURE_RENDER_DIR"',
            self.capture_script,
        )
        self.assertIn('CAPTURE_RENDER_DIR="$CAPTURE_TMPDIR/EaselWall"', self.capture_script)
        self.assertIn('local rendered_dir="$CAPTURE_RENDER_DIR"', self.capture_script)
        screenshot_only_renderer = self.mat_renderer_source[
            self.mat_renderer_source.index("#if SCREENSHOT_CAPTURE") :
            self.mat_renderer_source.index("#endif")
        ]
        self.assertIn("EASELWALL_SCREENSHOT_RENDER_DIRECTORY", screenshot_only_renderer)
        self.assertIn("screenshotRenderDirectory()", self.mat_renderer_source)
        self.assertIn('--manifest "$WALLPAPER_STATE"', self.capture_script)
        self.assertIn("unexpected display IDs", self.inventory_source)
        self.assertIn("duplicate display IDs", self.inventory_source)

    def test_success_is_reported_only_after_verified_cleanup(self):
        explicit_cleanup = self.capture_script.rindex("if ! cleanup_resources; then")
        success = self.capture_script.index(
            'echo "Real App Store screenshots are ready in $OUTPUT_DIR"'
        )
        self.assertLess(explicit_cleanup, success)
        self.assertIn("trap - EXIT", self.capture_script[explicit_cleanup:success])
        cleanup = self.capture_script[
            self.capture_script.index("cleanup_resources()") :
            self.capture_script.index("cleanup_on_exit()")
        ]
        self.assertLess(cleanup.index("restore_wallpapers"), cleanup.index("restore_menu_fallback"))

    def test_cleanup_ignores_followup_signals_before_disabling_exit_trap(self):
        cleanup_on_exit = self.capture_script[
            self.capture_script.index("cleanup_on_exit()") :
            self.capture_script.index("trap 'cleanup_on_exit $?' EXIT")
        ]
        ignore_signals = cleanup_on_exit.index("ignore_cleanup_signals")
        save_status = cleanup_on_exit.index('local status="$1"')
        disable_exit = cleanup_on_exit.index("trap - EXIT")
        invoke_cleanup = cleanup_on_exit.index("cleanup_resources")
        self.assertLess(ignore_signals, save_status)
        self.assertLess(ignore_signals, disable_exit)
        self.assertLess(disable_exit, invoke_cleanup)

        cleanup_resources = self.capture_script[
            self.capture_script.index("cleanup_resources()") :
            self.capture_script.index("report_recovery_state()")
        ]
        self.assertLess(
            cleanup_resources.index("ignore_cleanup_signals"),
            cleanup_resources.index('if [[ "$CLEANUP_DONE" -eq 1 ]]'),
        )

        finalize = self.capture_script[
            self.capture_script.index("finalize_safe_cleanup()") :
            self.capture_script.index("cleanup_on_exit()")
        ]
        self.assertLess(
            finalize.index("ignore_cleanup_signals"),
            finalize.index('if [[ "$CLEANUP_DONE" -ne 1'),
        )

    def test_hup_quit_int_and_term_are_ignored_during_cleanup(self):
        functions = "".join(
            shell_function(self.capture_script, name)
            for name in ("ignore_cleanup_signals",)
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail\n"
                + functions
                + "\nignore_cleanup_signals\n"
                + 'kill -HUP "$$"\nkill -QUIT "$$"\n'
                + 'kill -INT "$$"\nkill -TERM "$$"\n'
                + "printf 'survived\\n'\n",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "survived\n")

    def test_signal_between_capture_spawn_and_pid_assignment_is_deferred(self):
        names = (
            "arm_signal_traps",
            "remember_deferred_signal",
            "defer_signal_traps",
            "resume_signal_traps",
            "ignore_cleanup_signals",
            "capture_safety_checkpoint",
            "terminate_process",
            "stop_capture_app",
            "cleanup_resources",
            "launch_capture_app",
        )
        functions = "".join(shell_function(self.capture_script, name) for name in names)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            derived = root / "derived"
            binary = (
                derived
                / "Build/Products/Screenshot/EaselWall.app/Contents/MacOS/EaselWall"
            )
            binary.parent.mkdir(parents=True)
            child_ready = root / "child-ready"
            events = root / "events"
            binary.write_text(
                "#!/usr/bin/env bash\n"
                + f"trap 'echo child-terminated >>{shlex.quote(str(events))}; exit 0' TERM\n"
                + f"printf ready >{shlex.quote(str(child_ready))}\n"
                + "while :; do :; done\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)

            harness = (
                "set -euo pipefail\n"
                "DEFERRED_SIGNAL_STATUS=0\nCAPTURE_PID=''\nAUTOMATION_PID=''\n"
                "CLEANUP_DONE=0\nCLEANUP_RESULT=0\nWALLPAPERS_SAVED=1\n"
                "MENU_RECOVERY_NEEDED=0\n"
                f"DERIVED_DATA={shlex.quote(str(derived))}\n"
                f"RUN_DIR={shlex.quote(str(root))}\n"
                f"RUN_HOME={shlex.quote(str(root / 'home'))}\n"
                f"CAPTURE_TMPDIR={shlex.quote(str(root / 'tmp'))}\n"
                f"CAPTURE_RENDER_DIR={shlex.quote(str(root / 'render'))}\n"
                + functions
                + "\nstop_automation() { AUTOMATION_PID=''; }\n"
                + f"restore_wallpapers() {{ echo wallpaper-rollback >>{shlex.quote(str(events))}; WALLPAPERS_SAVED=0; }}\n"
                + "restore_menu_fallback() { :; }\n"
                + "capture_safety_checkpoint() {\n"
                + '  [[ "$1" == "after-capture-spawn" ]] || return 0\n'
                + f"  while [[ ! -s {shlex.quote(str(child_ready))} ]]; do sleep 0.01; done\n"
                + '  kill -TERM "$$"\n'
                + "}\n"
                + "test_exit_cleanup() {\n"
                + '  local status="$1"\n  trap - EXIT\n  cleanup_resources\n  exit "$status"\n}\n'
                + "trap 'test_exit_cleanup $?' EXIT\n"
                + f"launch_capture_app {shlex.quote(str(root / 'never-ready'))}\n"
            )
            result = subprocess.run(
                ["bash", "-c", harness],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 143, result.stderr)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                ["child-terminated", "wallpaper-rollback"],
            )

    def test_lock_creation_and_partial_writes_do_not_leak_on_signals(self):
        names = (
            "arm_signal_traps",
            "remember_deferred_signal",
            "defer_signal_traps",
            "resume_signal_traps",
            "ignore_cleanup_signals",
            "capture_safety_checkpoint",
            "write_lock_token",
            "write_lock_owner",
            "acquire_lock",
            "release_lock",
        )
        functions = "".join(shell_function(self.capture_script, name) for name in names)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            lock_dir = root / "mkdir-signal.lock"
            common = (
                "set -euo pipefail\nDEFERRED_SIGNAL_STATUS=0\nLOCK_HELD=0\nLOCK_TOKEN=''\n"
                f"LOCK_DIR={shlex.quote(str(lock_dir))}\n"
                'LOCK_OWNER_FILE="$LOCK_DIR/owner.txt"\n'
                'LOCK_TOKEN_FILE="$LOCK_DIR/token"\n'
                + functions
            )
            mkdir_signal = (
                common
                + "\ncapture_safety_checkpoint() {\n"
                + '  [[ "$1" == "after-lock-mkdir" ]] && kill -HUP "$$"\n'
                + "}\n"
                + "release_on_exit() { local status=\"$1\"; trap - EXIT; "
                + "ignore_cleanup_signals; release_lock; exit \"$status\"; }\n"
                + "trap 'release_on_exit $?' EXIT\nacquire_lock\n"
            )
            result = subprocess.run(
                ["bash", "-c", mkdir_signal],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 129, result.stderr)
            self.assertFalse(lock_dir.exists())

            for writer_name in ("write_lock_token", "write_lock_owner"):
                partial_dir = root / f"partial-{writer_name}.lock"
                partial = common.replace(str(lock_dir), str(partial_dir))
                partial += (
                    f"\n{writer_name}() {{\n"
                    + f"  printf partial >\"${{{'LOCK_TOKEN_FILE' if writer_name == 'write_lock_token' else 'LOCK_OWNER_FILE'}}}\"\n"
                    + '  kill -QUIT "$$"\n  return 1\n}\n'
                    + "acquire_lock\n"
                )
                result = subprocess.run(
                    ["bash", "-c", partial],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 131, result.stderr)
                self.assertFalse(partial_dir.exists())

    def test_atomic_lock_covers_capture_and_compose_and_fails_closed(self):
        self.assertIn('if ! mkdir "$LOCK_DIR"', self.capture_script)
        self.assertIn("a stale safety lock exists", self.capture_script)
        self.assertIn("Failing closed", self.capture_script)
        acquire = self.capture_script.rindex("\nacquire_lock\n")
        compose_branch = self.capture_script.index('if [[ "$RUN_MODE" == "compose" ]]')
        self.assertLess(acquire, compose_branch)
        finalize = self.capture_script[
            self.capture_script.index("finalize_safe_cleanup()") :
            self.capture_script.index("cleanup_on_exit()")
        ]
        self.assertLess(finalize.index("remove_run_directory"), finalize.index("release_lock"))
        self.assertIn('"$WALLPAPERS_SAVED" -ne 0', finalize)

    def test_private_run_data_is_removed_only_after_verified_rollback(self):
        self.assertEqual(self.capture_script.count('rm -rf -- "$RUN_DIR"'), 1)
        self.assertIn("Recovery data was preserved at: $RUN_DIR", self.capture_script)
        self.assertIn("The cross-run safety lock was retained at: $LOCK_DIR", self.capture_script)
        finalize = self.capture_script[
            self.capture_script.index("finalize_safe_cleanup()") :
            self.capture_script.index("cleanup_on_exit()")
        ]
        verified_gate = finalize.index('"$WALLPAPERS_SAVED" -ne 0')
        remove_call = finalize.index("remove_run_directory || return 1")
        self.assertLess(verified_gate, remove_call)

    def test_documented_upload_order_matches_composer(self):
        documented = re.findall(r"^[1-5]\. `([^`]+)`$", self.metadata, re.MULTILINE)
        self.assertEqual(documented, [output[0] for output in COMPOSER.OUTPUTS])
        self.assertEqual(COMPOSER.OUTPUTS[0][1], "settings-appearance.png")
        self.assertEqual(COMPOSER.OUTPUTS[-1][1], "menu.png")

    def test_failed_menu_recapture_restores_last_verified_capture(self):
        self.assertIn("stage_menu_fallback", self.capture_script)
        self.assertIn("restore_menu_fallback", self.capture_script)
        self.assertIn("$MENU_FALLBACK_IMAGE", self.capture_script)
        self.assertIn("$MENU_FALLBACK_READY", self.capture_script)
        self.assertIn("menu.ready.json", self.capture_script)
        self.assertIn("easelwall_capture_provenance.py", self.capture_script)

    def test_compositor_has_no_tracked_mockup_asset_dependency(self):
        composer = COMPOSER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("website/assets", composer)
        self.assertNotIn("display_mockup", composer)
        self.assertIn("wallpaper-*.png", composer)


if __name__ == "__main__":
    unittest.main()
