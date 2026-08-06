import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scripts.update_release_metadata as release_metadata
from scripts.update_release_metadata import update_release_metadata


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "website").mkdir()
        (self.root / "project.yml").write_text(
            'settings:\n  MARKETING_VERSION: "1.0.0"\n', encoding="utf-8"
        )
        (self.root / "website" / "index.html").write_text(
            '<script>{"softwareVersion": "1.0.0"}</script>\n', encoding="utf-8"
        )
        (self.root / "website" / "sitemap.xml").write_text(
            "<urlset>\n"
            "  <url><loc>https://easelwall.com/</loc><lastmod>2026-01-01</lastmod></url>\n"
            "  <url><loc>https://easelwall.com/privacy</loc><lastmod>2026-02-02</lastmod></url>\n"
            "</urlset>\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_updates_project_structured_data_and_only_homepage_lastmod(self):
        update_release_metadata(self.root, "1.2.3", "2026-08-05")

        self.assertIn(
            'MARKETING_VERSION: "1.2.3"',
            (self.root / "project.yml").read_text(encoding="utf-8"),
        )
        self.assertIn(
            '"softwareVersion": "1.2.3"',
            (self.root / "website" / "index.html").read_text(encoding="utf-8"),
        )
        sitemap = (self.root / "website" / "sitemap.xml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "<loc>https://easelwall.com/</loc><lastmod>2026-08-05</lastmod>",
            sitemap,
        )
        self.assertIn(
            "<loc>https://easelwall.com/privacy</loc><lastmod>2026-02-02</lastmod>",
            sitemap,
        )

    def test_invalid_version_writes_nothing(self):
        project_before = (self.root / "project.yml").read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "canonical MAJOR.MINOR.PATCH"):
            update_release_metadata(self.root, "1.2", "2026-08-05")

        self.assertEqual(
            (self.root / "project.yml").read_text(encoding="utf-8"), project_before
        )

    def test_rejects_ambiguous_or_oversized_version_components(self):
        for version in ("1.02.3", "1.1000.0"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(
                    ValueError, "canonical MAJOR.MINOR.PATCH"
                ):
                    update_release_metadata(self.root, version, "2026-08-05")

    def test_missing_marker_writes_nothing(self):
        project_path = self.root / "project.yml"
        index_path = self.root / "website" / "index.html"
        project_before = project_path.read_text(encoding="utf-8")
        index_path.write_text("<html></html>\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "softwareVersion"):
            update_release_metadata(self.root, "1.2.3", "2026-08-05")

        self.assertEqual(project_path.read_text(encoding="utf-8"), project_before)

    def test_date_requires_exact_calendar_yyyy_mm_dd(self):
        original = {
            path: path.read_bytes()
            for path in (
                self.root / "project.yml",
                self.root / "website" / "index.html",
                self.root / "website" / "sitemap.xml",
            )
        }

        for release_date in (
            "20260805",
            "2026-W32-3",
            "2026-8-5",
            "2026-02-30",
        ):
            with self.subTest(release_date=release_date):
                with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                    update_release_metadata(self.root, "1.2.3", release_date)
                self.assertEqual(
                    {path: path.read_bytes() for path in original}, original
                )

    def test_staging_failure_leaves_every_target_unchanged_and_cleans_temps(self):
        targets = (
            self.root / "project.yml",
            self.root / "website" / "index.html",
            self.root / "website" / "sitemap.xml",
        )
        original = {path: path.read_bytes() for path in targets}
        real_stage_update = release_metadata._stage_update
        calls = 0

        def fail_second_stage(target, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated staged write failure")
            return real_stage_update(target, content)

        with patch.object(
            release_metadata, "_stage_update", side_effect=fail_second_stage
        ):
            with self.assertRaisesRegex(OSError, "staged write failure"):
                update_release_metadata(self.root, "1.2.3", "2026-08-05")

        self.assertEqual({path: path.read_bytes() for path in targets}, original)
        self.assertEqual(list(self.root.rglob(".*.tmp")), [])
        self.assertEqual(list(self.root.rglob(".*.backup")), [])

    def test_replace_failures_roll_back_every_target_and_clean_temps(self):
        targets = (
            self.root / "project.yml",
            self.root / "website" / "index.html",
            self.root / "website" / "sitemap.xml",
        )
        original = {path: path.read_bytes() for path in targets}
        real_replace = os.replace

        for failure_call in (2, 3):
            with self.subTest(failure_call=failure_call):
                for path, content in original.items():
                    path.write_bytes(content)
                calls = 0

                def fail_selected_replace(source, destination):
                    nonlocal calls
                    calls += 1
                    if calls == failure_call:
                        raise OSError(f"simulated replace failure {failure_call}")
                    return real_replace(source, destination)

                with patch.object(
                    release_metadata.os,
                    "replace",
                    side_effect=fail_selected_replace,
                ):
                    with self.assertRaisesRegex(
                        OSError, f"replace failure {failure_call}"
                    ):
                        update_release_metadata(
                            self.root, "1.2.3", "2026-08-05"
                        )

                self.assertEqual(
                    {path: path.read_bytes() for path in targets}, original
                )
                self.assertEqual(list(self.root.rglob(".*.tmp")), [])
                self.assertEqual(list(self.root.rglob(".*.backup")), [])


if __name__ == "__main__":
    unittest.main()
