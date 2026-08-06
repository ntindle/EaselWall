import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_STORE_METADATA = ROOT / "marketing" / "app-store-metadata.md"
INDEX = ROOT / "website" / "index.html"
REDIRECTS = ROOT / "website" / "_redirects"
PRIVACY = ROOT / "website" / "privacy.html"
README = ROOT / "README.md"
SUPPORT = ROOT / "website" / "support.html"
PROJECT = ROOT / "project.yml"
TIKTOK = ROOT / "website" / "tiktok" / "index.html"


class WebsiteAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.redirects = REDIRECTS.read_text(encoding="utf-8")
        cls.privacy = PRIVACY.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
        cls.support = SUPPORT.read_text(encoding="utf-8")
        cls.app_store_metadata = APP_STORE_METADATA.read_text(encoding="utf-8")
        cls.tiktok = TIKTOK.read_text(encoding="utf-8")
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

    def test_structured_version_matches_the_project_marketing_version(self):
        project = PROJECT.read_text(encoding="utf-8")
        match = re.search(r'MARKETING_VERSION:\s*"([^"]+)"', project)
        self.assertIsNotNone(match)
        self.assertEqual(self.structured_data["softwareVersion"], match.group(1))

    def test_website_redirect_preserves_its_campaign_token(self):
        self.assertIn(
            "/app-store https://apps.apple.com/app/apple-store/id6778701883?"
            "pt=122660259&ct=web_site&mt=8 302",
            self.redirects,
        )

    def test_tiktok_route_is_a_mobile_handoff_not_a_device_blind_redirect(self):
        self.assertNotIn("/tiktok ", self.redirects)
        self.assertIn("EaselWall works on Mac", self.tiktok)
        self.assertIn("Send this page to your Mac", self.tiktok)
        self.assertIn("navigator.share", self.tiktok)
        self.assertIn("navigator.clipboard.writeText", self.tiktok)

    def test_tiktok_mac_cta_preserves_organic_campaign_attribution(self):
        self.assertIn(
            "https://apps.apple.com/app/apple-store/id6778701883?"
            "pt=122660259&amp;ct=tt_organic&amp;mt=8",
            self.tiktok,
        )

    def test_tiktok_handoff_collects_no_contact_or_tracking_data(self):
        lowered = self.tiktok.casefold()
        self.assertNotIn("<form", lowered)
        self.assertNotIn("google-analytics", lowered)
        self.assertNotIn("gtag(", lowered)
        self.assertNotIn("mailto:", lowered)
        self.assertIn("code asks for no personal information", lowered)
        self.assertIn("host receives standard request metadata", lowered)

    def test_tiktok_handoff_prioritizes_phone_actions_until_mac_is_detected(self):
        self.assertLess(
            self.tiktok.index('class="card phone-card"'),
            self.tiktok.index('class="card mac-card"'),
        )
        self.assertIn(".is-mac .mac-card", self.tiktok)
        self.assertIn("order: -1", self.tiktok)
        self.assertIn("navigator.maxTouchPoints <= 1", self.tiktok)

    def test_paid_app_store_is_the_default_install_step(self):
        self.assertIn(
            "Get EaselWall from the Mac App Store for $2.99 once.", self.index
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

    def test_app_store_description_is_complete_current_and_within_limits(self):
        text_blocks = re.findall(
            r"```text\n(.*?)\n```", self.app_store_metadata, flags=re.DOTALL
        )
        self.assertEqual(len(text_blocks), 2)
        description, whats_new = text_blocks

        self.assertLessEqual(len(description), 4_000)
        self.assertLessEqual(len(whats_new), 4_000)
        self.assertIn("no API key required", description)
        self.assertIn("No tracking", description)
        self.assertIn("macOS 14 Sonoma or later", description)
        self.assertIn("no longer needs a Rijksmuseum API key", whats_new)
        self.assertNotIn("your own API key", description.lower())


if __name__ == "__main__":
    unittest.main()
