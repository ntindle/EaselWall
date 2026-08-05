from pathlib import Path
import unittest


WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
)
APP_STORE_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "appstore.yml"
)


class ReleaseWorkflowRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text()

    def test_sparkle_signature_uses_print_only_mode(self):
        self.assertIn('"$SPARKLE_BIN" --ed-key-file - -p', self.source)
        self.assertIn('^[A-Za-z0-9+/]{86}==$', self.source)
        self.assertNotIn('grep "sparkle:edSignature"', self.source)

    def test_appcast_uses_the_builds_monotonic_version(self):
        self.assertIn("<sparkle:version>$BUILD_NUM</sparkle:version>", self.source)
        self.assertNotIn('BUILD_NUM=$(echo "$VERSION"', self.source)

    def test_appcast_is_pushed_from_main_without_rebase_or_force(self):
        self.assertIn("ref: main", self.source)
        self.assertIn("path: appcast-repo", self.source)
        self.assertIn("working-directory: appcast-repo", self.source)
        self.assertIn('git branch --show-current)" != "main"', self.source)
        self.assertIn("git pull --no-rebase origin main", self.source)
        self.assertIn("git push origin main", self.source)
        self.assertNotIn("git push ||", self.source)
        self.assertNotIn("git push --force", self.source)
        self.assertNotIn("git rebase", self.source)

    def test_release_certificate_is_private_and_removed(self):
        self.assertIn("umask 077", self.source)
        self.assertIn('chmod 600 "$CERT_PATH"', self.source)
        self.assertIn('rm -f "$RUNNER_TEMP/cert.p12"', self.source)


class AppStoreWorkflowRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_STORE_WORKFLOW.read_text()

    def test_uses_bsd_portable_archive_and_package_resolution(self):
        self.assertNotIn("-maxdepth", self.source)
        self.assertIn(
            'APP_PATH="build/$APP_NAME.xcarchive/Products/Applications/$APP_NAME.app"',
            self.source,
        )
        self.assertIn("PACKAGES=(build/export/*.pkg)", self.source)
        self.assertIn("${#PACKAGES[@]} != 1", self.source)

    def test_manual_dispatch_resolves_a_tag_instead_of_branch_name(self):
        self.assertIn("fetch-depth: 0", self.source)
        self.assertIn('GITHUB_REF_TYPE" == "tag', self.source)
        self.assertIn("git tag --list 'v[0-9]*' --sort=-version:refname", self.source)
        self.assertIn('git checkout --detach "refs/tags/$TAG"', self.source)
        self.assertIn('git rev-parse HEAD', self.source)
        self.assertIn('git rev-list -n 1 "$TAG"', self.source)
        self.assertNotIn('VERSION=${GITHUB_REF_NAME#v}', self.source)

    def test_signing_material_is_private_and_removed(self):
        self.assertGreaterEqual(self.source.count("umask 077"), 2)
        self.assertIn('chmod 600 "$CERT_PATH"', self.source)
        self.assertIn('chmod 600 "$KEY_PATH"', self.source)
        self.assertIn('rm -f "$RUNNER_TEMP/cert.p12"', self.source)


if __name__ == "__main__":
    unittest.main()
