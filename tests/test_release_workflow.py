from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
)
APP_STORE_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "appstore.yml"
)
MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def marked_shell_block(source, begin, end):
    return textwrap.dedent(source.split(begin, 1)[1].split(end, 1)[0])


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

    def test_release_build_number_is_retry_safe_compact_and_above_floor(self):
        self.assertIn("CURRENT_EPOCH=$(date -u +%s)", self.source)
        self.assertIn("CURRENT_EPOCH - 1735689600", self.source)
        self.assertIn("BUILD_RUN <= 837975", self.source)
        self.assertIn("BUILD_CHANNEL=1", self.source)
        self.assertIn('BUILD_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"', self.source)
        self.assertIn('BUILD_NUM="$BUILD_RUN.$BUILD_CHANNEL.$BUILD_ATTEMPT"', self.source)
        self.assertIn("${#BUILD_NUM} -gt 18", self.source)
        self.assertIn("at most 18 characters", self.source)
        self.assertIn('CURRENT_PROJECT_VERSION="$BUILD_NUM"', self.source)

    def test_release_runs_are_serialized_across_versions(self):
        self.assertIn("concurrency:", self.source)
        self.assertIn("group: easelwall-release-publishing", self.source)
        self.assertIn("cancel-in-progress: false", self.source)

    def test_latest_tag_guard_precedes_all_publication(self):
        guard = self.source.index("Verify tag is latest release before publishing")
        github_release = self.source.index("Create GitHub Release")
        homebrew = self.source.index("Update Homebrew tap")
        appcast = self.source.index("Update appcast.xml")
        self.assertLess(guard, github_release)
        self.assertLess(guard, homebrew)
        self.assertLess(guard, appcast)
        self.assertIn("git fetch --prune --prune-tags --tags origin", self.source)
        self.assertIn("git ls-remote --exit-code origin", self.source)
        self.assertIn('DIRECT_REF="refs/tags/$TAG"', self.source)
        self.assertIn('REMOTE_TAG_COMMIT" != "$CHECKED_OUT_COMMIT', self.source)
        self.assertIn("git tag --list 'v*' --sort=-version:refname", self.source)
        self.assertIn('LATEST_TAG" != "$TAG', self.source)

    def test_mutable_channels_parse_fail_closed_and_reject_regressions(self):
        self.assertIn("single_cask_version()", self.source)
        self.assertIn("single_cask_sha256()", self.source)
        self.assertIn("declarations != 1", self.source)
        self.assertIn("exactly one canonical sha256 declaration", self.source)
        self.assertIn("Homebrew already publishes newer version", self.source)
        self.assertIn("single_appcast_value()", self.source)
        self.assertIn("Appcast must contain exactly one valid short version", self.source)
        self.assertIn("Appcast must contain exactly one numeric sparkle build", self.source)

    def test_homebrew_parser_rejects_missing_malformed_and_duplicate_versions(self):
        helpers = marked_shell_block(
            self.source,
            "# BEGIN HOMEBREW VERSION HELPERS",
            "# END HOMEBREW VERSION HELPERS",
        )
        with tempfile.TemporaryDirectory() as directory:
            cask = Path(directory) / "easelwall.rb"
            for content in (
                'cask "easelwall" do\nend\n',
                'version "latest"\n',
                'version "1.0.1"\nversion "1.0.2"\n',
            ):
                cask.write_text(content)
                result = subprocess.run(
                    ["bash", "-c", helpers + '\nsingle_cask_version "$1"', "bash", str(cask)],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0, content)

    def test_homebrew_parser_rejects_missing_malformed_and_duplicate_sha256(self):
        helpers = marked_shell_block(
            self.source,
            "# BEGIN HOMEBREW VERSION HELPERS",
            "# END HOMEBREW VERSION HELPERS",
        )
        valid_sha = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            cask = Path(directory) / "easelwall.rb"
            for content in (
                'version "1.0.1"\n',
                'sha256 :no_check\n',
                f'sha256 "{valid_sha}"\nsha256 "{valid_sha}"\n',
            ):
                cask.write_text(content)
                result = subprocess.run(
                    ["bash", "-c", helpers + '\nsingle_cask_sha256 "$1"', "bash", str(cask)],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0, content)

    def test_appcast_numeric_comparator_handles_equal_newer_and_older(self):
        helpers = marked_shell_block(
            self.source,
            "# BEGIN APPCAST VERSION HELPERS",
            "# END APPCAST VERSION HELPERS",
        )
        script = helpers + """
        build_greater_or_equal 50000000.1.2 50000000.1.2
        build_greater_or_equal 50000001.1.0 50000000.2.9
        ! build_greater_or_equal 49999999.9.9 50000000.0.0
        """
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_appcast_parser_rejects_missing_malformed_and_duplicate_builds(self):
        helpers = marked_shell_block(
            self.source,
            "# BEGIN APPCAST VERSION HELPERS",
            "# END APPCAST VERSION HELPERS",
        )
        with tempfile.TemporaryDirectory() as directory:
            appcast = Path(directory) / "appcast.xml"
            for content in (
                "<rss></rss>",
                "<sparkle:version>not-a-build</sparkle:version>",
                "<sparkle:version>1</sparkle:version><sparkle:version>2</sparkle:version>",
            ):
                appcast.write_text(content)
                result = subprocess.run(
                    ["bash", "-c", helpers + '\nsingle_appcast_value build "$1"', "bash", str(appcast)],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0, content)

    def test_equal_appcast_only_noops_when_content_is_exact(self):
        exact = self.source.index("cmp -s website/appcast.xml website/appcast.xml.next")
        reject_equal = self.source.index('build_greater_or_equal "$CURRENT_BUILD" "$BUILD_NUM"')
        self.assertLess(exact, reject_equal)
        self.assertIn("newer than or equal to candidate", self.source)

    def test_appcast_push_retry_is_bounded_merge_only(self):
        self.assertIn("for PUSH_ATTEMPT in 1 2 3", self.source)
        self.assertIn("git fetch origin main", self.source)
        self.assertIn("git merge --no-edit origin/main", self.source)
        self.assertIn("Unable to push appcast after 3 merge-only attempts", self.source)
        self.assertIn('MERGED_APPCAST_SHA" != "$EXPECTED_APPCAST_SHA', self.source)

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

    def test_app_store_build_number_matches_retry_safe_compact_format(self):
        self.assertIn("CURRENT_EPOCH=$(date -u +%s)", self.source)
        self.assertIn("CURRENT_EPOCH - 1735689600", self.source)
        self.assertIn("BUILD_RUN <= 837975", self.source)
        self.assertIn("BUILD_CHANNEL=2", self.source)
        self.assertIn('BUILD_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"', self.source)
        self.assertIn('BUILD_NUM="$BUILD_RUN.$BUILD_CHANNEL.$BUILD_ATTEMPT"', self.source)
        self.assertIn("${#BUILD_NUM} -gt 18", self.source)
        self.assertIn('CURRENT_PROJECT_VERSION="$BUILD_NUM"', self.source)

    def test_app_store_uploads_are_serialized(self):
        self.assertIn("concurrency:", self.source)
        self.assertIn("group: easelwall-app-store-upload", self.source)
        self.assertIn("cancel-in-progress: false", self.source)

    def test_latest_tag_guard_is_immediately_before_upload(self):
        guard = self.source.index("Verify tag is latest release before upload")
        upload = self.source.index("Upload to App Store Connect")
        self.assertLess(guard, upload)
        self.assertIn("git fetch --prune --prune-tags --tags origin", self.source)
        self.assertIn("git ls-remote --exit-code origin", self.source)
        self.assertIn('REMOTE_TAG_COMMIT" != "$CHECKED_OUT_COMMIT', self.source)
        self.assertIn("git tag --list 'v*' --sort=-version:refname", self.source)
        self.assertIn('LATEST_TAG" != "$TAG', self.source)

    def test_signing_material_is_private_and_removed(self):
        self.assertGreaterEqual(self.source.count("umask 077"), 2)
        self.assertIn('chmod 600 "$CERT_PATH"', self.source)
        self.assertIn('chmod 600 "$KEY_PATH"', self.source)
        self.assertIn('rm -f "$RUNNER_TEMP/cert.p12"', self.source)


class MakefileReleaseVersionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAKEFILE.read_text()

    def test_local_release_and_archive_share_semver_build_number(self):
        self.assertIn("override BUILD_NUM :=", self.source)
        self.assertIn("current_epoch - 1735689600", self.source)
        self.assertIn("BUILD_CHANNEL ?= 0", self.source)
        self.assertIn("BUILD_ATTEMPT ?= 0", self.source)
        self.assertIn("$(BUILD_RUN).$(BUILD_CHANNEL).$(BUILD_ATTEMPT)", self.source)
        self.assertEqual(
            self.source.count('CURRENT_PROJECT_VERSION="$(BUILD_NUM)"'), 2
        )
        self.assertIn("release: validate-release-version", self.source)
        self.assertIn("archive: validate-release-version", self.source)

    def test_local_version_must_be_three_numeric_components(self):
        self.assertIn("validate-release-version:", self.source)
        self.assertIn("VERSION must use canonical MAJOR.MINOR.PATCH", self.source)
        self.assertIn("grep -Eq '^(0|[1-9][0-9]{0,2})", self.source)

    def test_local_validation_accepts_canonical_semver(self):
        result = subprocess.run(
            ["make", "-s", "validate-release-version", "VERSION=1.2.10"],
            cwd=MAKEFILE.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_build_number_uses_compact_three_component_encoding(self):
        result = subprocess.run(
            ["make", "-n", "release", "VERSION=1.2.10", "BUILD_RUN=50000000", "BUILD_CHANNEL=0", "BUILD_ATTEMPT=2"],
            cwd=MAKEFILE.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CURRENT_PROJECT_VERSION="50000000.0.2"', result.stdout)

    def test_retry_and_later_epoch_builds_are_unique_and_ordered(self):
        def build(run, attempt):
            result = subprocess.run(
                ["make", "-n", "release", "VERSION=1.0.2", f"BUILD_RUN={run}", "BUILD_CHANNEL=0", f"BUILD_ATTEMPT={attempt}"],
                cwd=MAKEFILE.parent,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return tuple(map(int, re.search(r'CURRENT_PROJECT_VERSION="([0-9.]+)"', result.stdout).group(1).split(".")))

        first = build(50000000, 1)
        retry = build(50000000, 2)
        later = build(50000001, 0)
        self.assertLess(first, retry)
        self.assertLess(retry, later)

    def test_historical_build_floor_rejects_regression(self):
        result = subprocess.run(
            ["make", "-s", "validate-release-version", "VERSION=1.0.2", "BUILD_RUN=837975"],
            cwd=MAKEFILE.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must exceed shipped build 837975", result.stderr)

    def test_same_second_channels_are_distinct_and_ordered(self):
        builds = []
        for channel in (1, 2):
            result = subprocess.run(
                ["make", "-n", "release", "VERSION=1.0.2", "BUILD_RUN=50000000", f"BUILD_CHANNEL={channel}", "BUILD_ATTEMPT=1"],
                cwd=MAKEFILE.parent,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = re.search(r'CURRENT_PROJECT_VERSION="([0-9.]+)"', result.stdout).group(1)
            self.assertLessEqual(len(value), 18)
            builds.append(tuple(map(int, value.split("."))))
        self.assertLess(builds[0], builds[1])

    def test_local_validation_rejects_build_over_18_characters(self):
        boundary = subprocess.run(
            ["make", "-s", "validate-release-version", "VERSION=1.0.2", "BUILD_RUN=99999999999999", "BUILD_CHANNEL=0", "BUILD_ATTEMPT=0"],
            cwd=MAKEFILE.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(boundary.returncode, 0, boundary.stderr)
        self.assertEqual(len("99999999999999.0.0"), 18)

        result = subprocess.run(
            ["make", "-s", "validate-release-version", "VERSION=1.0.2", "BUILD_RUN=100000000000000", "BUILD_CHANNEL=0", "BUILD_ATTEMPT=0"],
            cwd=MAKEFILE.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at most 18 characters", result.stderr)

    def test_local_validation_rejects_colliding_leading_zero_version(self):
        result = subprocess.run(
            ["make", "-s", "validate-release-version", "VERSION=1.02.3"],
            cwd=MAKEFILE.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("VERSION must use canonical MAJOR.MINOR.PATCH", result.stderr)

    def test_local_validation_rejects_component_too_large_for_encoding(self):
        result = subprocess.run(
            ["make", "-s", "validate-release-version", "VERSION=1.1000.0"],
            cwd=MAKEFILE.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_digit_truncation_collision_is_removed(self):
        self.assertNotIn("tr -dc '0-9'", self.source)
        self.assertNotIn("head -c 4", self.source)


if __name__ == "__main__":
    unittest.main()
