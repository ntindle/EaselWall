import json
import itertools
from pathlib import Path
import re
import unittest

from scripts import render_social_videos as renderer


ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "marketing" / "videos.json"
EXPERIMENT = ROOT / "marketing" / "tiktok-experiment.md"
APP_STORE_METADATA = ROOT / "marketing" / "app-store-metadata.md"


class SocialMarketingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = json.loads(CONTENT.read_text(encoding="utf-8"))
        cls.experiment = EXPERIMENT.read_text(encoding="utf-8")
        cls.app_store_metadata = APP_STORE_METADATA.read_text(encoding="utf-8")

    def test_master_duration_meets_tiktok_baseline(self):
        total = sum(renderer.SCENE_DURATIONS) - 2 * renderer.CROSSFADE
        self.assertGreaterEqual(total, 10)
        self.assertLessEqual(total, 15)

    def test_default_cta_does_not_assume_a_profile_link(self):
        destination = self.content["defaultDestinationCta"].casefold()
        self.assertIn("search easelwall", destination)
        self.assertNotIn("link in bio", destination)

    def test_hashtag_sets_are_small_and_avoid_fyp(self):
        for hashtag_set in self.content["hashtagSets"]:
            hashtags = hashtag_set.split()
            self.assertGreaterEqual(len(hashtags), 2)
            self.assertLessEqual(len(hashtags), 4)
            self.assertNotIn("#fyp", hashtag_set.casefold())

    def test_every_storyboard_has_true_claim_fields_and_assets(self):
        for video in self.content["videos"]:
            with self.subTest(video=video["id"]):
                self.assertTrue(video["hook"].strip())
                self.assertTrue(video["proof"].strip())
                self.assertTrue(video["caption"].strip())
                self.assertEqual(len(video["assets"]), 2)

    def test_rotation_and_crop_claims_are_qualified(self):
        by_id = {video["id"]: video for video in self.content["videos"]}
        self.assertIn("orientation cycle", by_id["fifty-three-works"]["hook"])
        self.assertIn(
            "mat enabled", by_id["museum-morning"]["caption"].casefold()
        )
        self.assertIn("mat enabled", by_id["tiny-museum"]["caption"].casefold())

    def test_fee_story_calls_55_sales_a_goal_not_literal_break_even(self):
        self.assertIn("My goal: 55 sales", self.experiment)
        self.assertNotIn("needs 55 sales", self.experiment)

    def test_matched_schedule_covers_every_experiment_cell_once(self):
        rows = []
        for line in self.experiment.splitlines():
            if not re.match(r"^\| [0-9]+ \|", line):
                continue
            columns = [
                column.strip().strip("`")
                for column in line.strip("|").split("|")
            ]
            rows.append(columns)

        self.assertEqual(len(rows), 12)
        self.assertEqual(len({row[2] for row in rows}), 12)
        observed = {(row[3], row[4], row[5]) for row in rows}
        expected = set(
            itertools.product(
                ("composition", "every_display", "honest_utility"),
                ("direct_demo", "founder_desk"),
                ("A", "B"),
            )
        )
        self.assertEqual(observed, expected)
        self.assertIn("Freeze the App Store listing", self.experiment)

    def test_app_store_aso_copy_and_limits_are_frozen(self):
        name = "EaselWall: Art Wallpapers"
        subtitle = "Museum paintings every day"
        promotional_text = (
            "Turn every display into a tiny museum: 53 public-domain masterpieces, "
            "daily rotation, custom mats, and no accounts, ads, or subscriptions."
        )
        keywords = (
            "desktop,background,monet,van gogh,mat,gallery,classic,automatic,"
            "multi monitor,impressionist,rotation"
        )
        expected_rows = {
            "Name": (name, "25 / 30 characters"),
            "Subtitle": (subtitle, "26 / 30 characters"),
            "Promotional text": (promotional_text, "138 / 170 characters"),
            "Keywords": (keywords, "100 / 100 UTF-8 bytes"),
        }
        for field, (value, limit_text) in expected_rows.items():
            self.assertIn(
                f"| {field} | `{value}` | {limit_text} |",
                self.app_store_metadata,
            )
        self.assertEqual(len(name), 25)
        self.assertLessEqual(len(name), 30)
        self.assertEqual(len(subtitle), 26)
        self.assertLessEqual(len(subtitle), 30)
        self.assertEqual(len(promotional_text), 138)
        self.assertLessEqual(len(promotional_text), 170)
        self.assertEqual(len(keywords.encode("utf-8")), 100)


if __name__ == "__main__":
    unittest.main()
