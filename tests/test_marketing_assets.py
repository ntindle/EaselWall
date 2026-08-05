import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "website" / "index.html"
REDIRECTS = ROOT / "website" / "_redirects"


class WebsiteAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.redirects = REDIRECTS.read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
