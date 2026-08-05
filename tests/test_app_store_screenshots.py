import importlib.util
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "render_app_store_screenshots.py"
ASSETS = ROOT / "website" / "assets"

SAFE_UI_CAPTURE_HASHES = {
    "settings-appearance.png": "c2c6d8844942ca730123a308af7665144cacde6967c85e8bb9b40e68e44c18a2",
    "settings-schedule.png": "b2be41c687ec2d7d99d207dda3ea3f41e847731dbe98ca2535a7ea4b9c84e36d",
    "settings-gallery-summary.png": "a96635e11f63cd4142ebbb0bbc252fc6395f0cacbc1900972b2691c1530d74cb",
}


def load_renderer():
    spec = importlib.util.spec_from_file_location("render_app_store_screenshots", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScreenshotOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = load_renderer()

    def test_unrecognized_pngs_are_archived_and_current_outputs_stay_put(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir = root / "for-upload"
            archive_dir = root / "archive"
            output_dir.mkdir()
            current = output_dir / self.renderer.OUTPUTS[0]
            stale = output_dir / "03-curated-from-museums.png"
            note = output_dir / "review-notes.txt"
            current.write_bytes(b"current")
            stale.write_bytes(b"stale")
            note.write_text("keep me here", encoding="utf-8")

            with (
                patch.object(self.renderer, "OUTPUT_DIR", output_dir),
                patch.object(self.renderer, "ARCHIVE_DIR", archive_dir),
            ):
                archived = self.renderer.archive_stale_uploads()

            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0][0], stale)
            self.assertFalse(stale.exists())
            self.assertEqual(archived[0][1].read_bytes(), b"stale")
            self.assertTrue(current.exists())
            self.assertTrue(note.exists())

    def test_manifest_lists_only_the_explicit_upload_set(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            manifest = output_dir / "manifest.json"
            rendered = [
                (output_dir / name, f"digest-{index}")
                for index, name in enumerate(self.renderer.OUTPUTS)
            ]

            with patch.object(self.renderer, "MANIFEST", manifest):
                self.renderer.write_manifest(rendered)

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["schemaVersion"], 1)
            self.assertEqual(
                [item["filename"] for item in payload["uploadFiles"]],
                list(self.renderer.OUTPUTS),
            )
            self.assertTrue(all(item["width"] == 1280 for item in payload["uploadFiles"]))
            self.assertTrue(all(item["height"] == 800 for item in payload["uploadFiles"]))

    def test_archive_suffix_preserves_two_batches_created_in_one_second(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir = root / "for-upload"
            archive_dir = root / "archive"
            output_dir.mkdir()
            fixed_time = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)

            with (
                patch.object(self.renderer, "OUTPUT_DIR", output_dir),
                patch.object(self.renderer, "ARCHIVE_DIR", archive_dir),
                patch.object(self.renderer, "datetime") as renderer_datetime,
            ):
                renderer_datetime.now.return_value = fixed_time
                (output_dir / "old-first.png").write_bytes(b"first")
                first_batch = self.renderer.archive_stale_uploads()
                (output_dir / "old-second.png").write_bytes(b"second")
                second_batch = self.renderer.archive_stale_uploads()

            self.assertEqual(first_batch[0][1].parent.name, "20260805T180000Z")
            self.assertEqual(second_batch[0][1].parent.name, "20260805T180000Z-2")
            self.assertEqual(first_batch[0][1].read_bytes(), b"first")
            self.assertEqual(second_batch[0][1].read_bytes(), b"second")

    def test_reviewed_settings_captures_anchor_slides_three_through_five(self):
        self.renderer.require_assets()
        source = MODULE_PATH.read_text(encoding="utf-8")

        for name, expected_hash in SAFE_UI_CAPTURE_HASHES.items():
            path = ASSETS / name
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)

        with Image.open(ASSETS / "settings-gallery-summary.png") as gallery:
            self.assertEqual(gallery.size, (1040, 350))

        mats = source.split("def screenshot_mats", 1)[1].split(
            "def screenshot_simple_by_design", 1
        )[0]
        catalog = source.split("def screenshot_catalog", 1)[1].split(
            "def screenshot_mats", 1
        )[0]
        simple = source.split("def screenshot_simple_by_design", 1)[1].split(
            "def save_and_validate", 1
        )[0]
        self.assertIn("settings_capture(canvas, APPEARANCE_UI", mats)
        self.assertIn("settings_capture(canvas, GALLERY_SUMMARY_UI", catalog)
        self.assertIn("settings_capture(canvas, SCHEDULE_UI", simple)
        self.assertIn("picture = contain(", source)
        self.assertNotIn("API Key", source)


if __name__ == "__main__":
    unittest.main()
