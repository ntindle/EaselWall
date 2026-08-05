import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "website" / "index.html"
REDIRECTS = ROOT / "website" / "_redirects"
PRIVACY = ROOT / "website" / "privacy.html"
README = ROOT / "README.md"
SUPPORT = ROOT / "website" / "support.html"


class WebsiteAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.redirects = REDIRECTS.read_text(encoding="utf-8")
        cls.privacy = PRIVACY.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
        cls.support = SUPPORT.read_text(encoding="utf-8")
        match = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            cls.index,
            flags=re.DOTALL,
        )
        if match is None:
            raise AssertionError("website/index.html is missing SoftwareApplication JSON-LD")
        cls.structured_data = json.loads(match.group(1))

    def test_structured_app_links_use_the_attributed_website_route(self):
        self.assertEqual(
            self.structured_data["downloadUrl"], "https://easelwall.com/app-store"
        )
        self.assertEqual(
            self.structured_data["offers"][0]["url"],
            "https://easelwall.com/app-store",
        )

    def test_redirects_preserve_website_and_tiktok_campaign_tokens(self):
        self.assertIn(
            "/app-store https://apps.apple.com/app/apple-store/id6778701883?"
            "pt=122660259&ct=web_site&mt=8 302",
            self.redirects,
        )
        self.assertIn(
            "/tiktok https://apps.apple.com/app/apple-store/id6778701883?"
            "pt=122660259&ct=tt_organic&mt=8 302",
            self.redirects,
        )

    def test_paid_store_cta_is_primary_and_source_build_is_subordinate(self):
        nav = self.index.split('<nav class="topbar-meta"', 1)[1].split("</nav>", 1)[0]
        hero = self.index.split('<section class="hero">', 1)[1].split(
            "<!-- Marquee -->", 1
        )[0]
        closing = self.index.split('<section class="store-close"', 1)[1].split(
            "</section>", 1
        )[0]

        self.assertIn('href="/app-store"', nav)
        self.assertNotIn("github.com", nav)
        self.assertIn('href="/app-store" class="btn btn-primary"', hero)
        self.assertNotIn("github.com", hero)
        self.assertIn("automatic App Store updates", hero)
        self.assertIn("supports ongoing curation", hero)
        self.assertIn("source-available build", closing)
        self.assertIn("github.com/ntindle/EaselWall/releases/latest", closing)

    def test_artwork_views_preserve_natural_proportions(self):
        self.assertRegex(
            self.index,
            r"\.canvas-window\s*\{[^}]*background:\s*var\(--mat\)",
        )
        self.assertRegex(
            self.index,
            r"\.canvas-window img\s*\{[^}]*object-fit:\s*contain",
        )
        self.assertRegex(
            self.index,
            r"\.easel-slide\s*\{[^}]*background:\s*var\(--mat\)",
        )
        self.assertRegex(
            self.index,
            r"\.easel-slide img\s*\{[^}]*object-fit:\s*contain",
        )
        self.assertRegex(
            self.index,
            r"\.monitor-screen img\s*\{[^}]*object-fit:\s*cover",
        )

    def test_navigation_wraps_on_narrow_screens(self):
        for document in (self.index, self.privacy, self.support):
            nav = document.split('<nav class="topbar-meta"', 1)[1].split("</nav>", 1)[0]
            self.assertRegex(
                document,
                r"\.topbar-inner\s*\{[^}]*flex-wrap:\s*wrap",
            )
            self.assertRegex(
                document,
                r"\.topbar-meta\s*\{[^}]*flex-wrap:\s*wrap",
            )
            self.assertIn('href="/app-store"', nav)
            self.assertNotIn("github.com", nav)

    def test_readme_limits_launchd_claim_to_non_app_store_builds(self):
        self.assertIn("In direct-download and Homebrew builds", self.readme)
        self.assertIn("the Mac App Store build uses in-process scheduling", self.readme)

    def test_provenance_uses_cc0_for_aic_and_exact_rijksmuseum_credit(self):
        aic_card = self.index.split('<article class="prov-card">', 2)[1]
        self.assertIn('<div class="stamp">CC0</div>', aic_card)
        credit = "developed using the Rijksmuseum API"
        self.assertIn(credit, self.index)
        self.assertIn(credit, self.readme)


if __name__ == "__main__":
    unittest.main()
