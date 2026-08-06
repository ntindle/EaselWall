import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
)
RELEASE_GUARDS = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "release_metadata_guards.sh"
)
APP_STORE_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "appstore.yml"
)
MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"
REPOSITORY_CASK = Path(__file__).resolve().parents[1] / "Casks" / "easelwall.rb"


def marked_shell_block(source, begin, end):
    return textwrap.dedent(source.split(begin, 1)[1].split(end, 1)[0])


class ReleaseWorkflowRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text()
        cls.guards = RELEASE_GUARDS.read_text()

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
        ancestry = self.source.index("git merge-base --is-ancestor")
        github_release = self.source.index("Create GitHub Release")
        homebrew = self.source.index("Update Homebrew tap")
        appcast = self.source.index("Update appcast.xml")
        metadata_update = self.source.index(
            "python3 scripts/update_release_metadata.py"
        )
        self.assertLess(guard, github_release)
        self.assertLess(ancestry, github_release)
        self.assertLess(ancestry, metadata_update)
        self.assertLess(guard, homebrew)
        self.assertLess(guard, appcast)
        self.assertIn(
            "+refs/heads/main:refs/remotes/origin/main", self.source
        )
        self.assertIn(
            '"$CHECKED_OUT_COMMIT" refs/remotes/origin/main', self.source
        )
        self.assertIn("is not contained in origin/main", self.source)
        self.assertIn("git fetch --prune --no-tags origin", self.source)
        self.assertIn("+refs/tags/*:refs/remotes/origin-tags/*", self.source)
        self.assertIn('REMOTE_TAG_REF="refs/remotes/origin-tags/$TAG"', self.source)
        self.assertIn('REMOTE_TAG_COMMIT" != "$CHECKED_OUT_COMMIT', self.source)
        self.assertIn("git for-each-ref --sort=-version:refname", self.source)
        self.assertIn('LATEST_TAG" != "$TAG', self.source)

    def test_channel_preflight_and_asset_guard_precede_publication(self):
        checkout = self.source.index("Check out main for release metadata")
        tap_clone = self.source.index("Clone Homebrew tap for preflight")
        preflight = self.source.index("Preflight mutable release channels")
        asset_guard = self.source.index("Refuse unsafe release asset overwrite")
        github_release = self.source.index("Create GitHub Release")
        homebrew = self.source.index("Update Homebrew tap")
        appcast = self.source.index("Update appcast.xml")

        self.assertLess(checkout, preflight)
        self.assertLess(tap_clone, preflight)
        self.assertLess(preflight, asset_guard)
        self.assertLess(asset_guard, github_release)
        self.assertLess(github_release, homebrew)
        self.assertLess(homebrew, appcast)
        self.assertIn(
            "Homebrew, repository cask, appcast, and website versions disagree",
            self.source,
        )
        self.assertIn('"$PROJECT_VERSION" != "$VERSION"', self.source)
        self.assertNotIn('"$TAP_VERSION" == "$PROJECT_VERSION"', self.source)
        self.assertIn(
            '"$TAP_VERSION" == "$VERSION" && "$TAP_SHA256" != "$SHA256"',
            self.source,
        )
        self.assertIn("refusing public asset replacement", self.source)
        self.assertGreaterEqual(self.source.count("git pull --no-rebase origin main"), 3)

    def test_public_release_asset_can_never_be_overwritten(self):
        self.assertIn("overwrite_files: false", self.source)
        self.assertIn("fail_on_unmatched_files: true", self.source)
        self.assertIn("getReleaseByTag", self.source)
        self.assertIn("listReleaseAssets", self.source)
        self.assertIn("Public asset ${existing.name} already exists", self.source)
        self.assertIn("will not replace bytes while Homebrew or Sparkle", self.source)

    def test_tag_fetch_never_targets_local_tag_namespace(self):
        self.assertNotIn(":refs/tags/", self.source)
        self.assertNotIn("--prune-tags", self.source)

    def test_mutable_channels_parse_fail_closed_and_reject_regressions(self):
        self.assertIn("single_cask_version()", self.guards)
        self.assertIn("single_cask_sha256()", self.guards)
        self.assertIn("declarations != 1", self.guards)
        self.assertIn("exactly one canonical sha256 declaration", self.source)
        self.assertIn("disagree or exceed candidate", self.source)
        self.assertIn("single_appcast_value()", self.guards)
        self.assertIn("Appcast must contain exactly one valid short version", self.source)
        self.assertIn("Appcast must contain exactly one numeric sparkle build", self.source)
        self.assertGreaterEqual(
            self.source.count("release_metadata_guards.sh"), 4
        )

    def test_candidate_project_can_lead_synchronized_published_channels(self):
        self.assertIn(
            "CANDIDATE_PROJECT_VERSION=$(single_project_version project.yml)",
            self.source,
        )
        self.assertIn(
            '"$CANDIDATE_PROJECT_VERSION" != "$VERSION"', self.source
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.yml"
            tap_cask = root / "tap.rb"
            repository_cask = root / "repository.rb"
            appcast = root / "appcast.xml"
            index = root / "index.html"
            project.write_text('MARKETING_VERSION: "1.0.4"\n')
            tap_cask.write_text('version "1.0.3"\n')
            repository_cask.write_text('version "1.0.3"\n')
            appcast.write_text(
                "<sparkle:shortVersionString>1.0.3</sparkle:shortVersionString>\n"
            )
            index.write_text('{"softwareVersion": "1.0.3"}\n')

            script = self.guards + """
            candidate=$(single_project_version "$1")
            published=$(single_current_published_version \
              "$candidate" \
              "$(single_cask_version "$2")" \
              "$(single_cask_version "$3")" \
              "$(single_appcast_value version "$4")" \
              "$(single_structured_version "$5")")
            [[ "$candidate" == "1.0.4" && "$published" == "1.0.3" ]]
            """
            valid_transition = subprocess.run(
                [
                    "bash",
                    "-c",
                    script,
                    "bash",
                    str(project),
                    str(tap_cask),
                    str(repository_cask),
                    str(appcast),
                    str(index),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                valid_transition.returncode, 0, valid_transition.stderr
            )

        for versions in (
            ("1.0.3", "1.0.2", "1.0.3", "1.0.3"),
            ("1.0.5", "1.0.5", "1.0.5", "1.0.5"),
        ):
            with self.subTest(versions=versions):
                invalid_transition = subprocess.run(
                    [
                        "bash",
                        "-c",
                        self.guards
                        + '\nsingle_current_published_version "$@"',
                        "bash",
                        "1.0.4",
                        *versions,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(invalid_transition.returncode, 0)

    def test_immediate_main_recheck_keeps_candidate_out_of_published_set(self):
        appcast_step = self.source.split("- name: Update appcast.xml", 1)[1]
        appcast_step = appcast_step.split("- name: Cleanup keychain", 1)[0]
        self.assertIn(
            '"$CURRENT_PROJECT_VERSION" != "$VERSION"', appcast_step
        )
        self.assertIn(
            '"$VERSION" "$CURRENT_VERSION" "$CURRENT_REPOSITORY_CASK_VERSION"',
            appcast_step,
        )
        self.assertNotIn(
            '"$VERSION" "$CURRENT_PROJECT_VERSION" "$CURRENT_VERSION"',
            appcast_step,
        )

    def test_homebrew_parser_rejects_missing_malformed_and_duplicate_versions(self):
        helpers = self.guards
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
        helpers = self.guards
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

    def test_main_metadata_parsers_reject_ambiguous_or_noncanonical_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.yml"
            index = root / "index.html"
            sitemap = root / "sitemap.xml"
            project.write_text('MARKETING_VERSION: "1.0.3"\n')
            index.write_text('{"softwareVersion": "1.0.3"}\n')
            sitemap.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url>
                    <loc>https://easelwall.com/</loc>
                    <lastmod>2026-08-05</lastmod>
                  </url>
                </urlset>
                """
            )
            script = self.guards + """
            single_project_version "$1"
            single_structured_version "$2"
            single_homepage_lastmod "$3"
            """
            valid = subprocess.run(
                ["bash", "-c", script, "bash", str(project), str(index), str(sitemap)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)

            index.write_text(
                '{"softwareVersion": "1.0.3", "softwareVersion": "1.0.4"}\n'
            )
            invalid = subprocess.run(
                [
                    "bash",
                    "-c",
                    self.guards + '\nsingle_structured_version "$1"',
                    "bash",
                    str(index),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(invalid.returncode, 0)

            sitemap.write_text(
                """<urlset>
                <url>
                  <loc>https://easelwall.com/</loc>
                  <lastmod>2026-02-30</lastmod>
                </url>
                </urlset>
                """
            )
            invalid_date = subprocess.run(
                [
                    "bash",
                    "-c",
                    self.guards + '\nsingle_homepage_lastmod "$1"',
                    "bash",
                    str(sitemap),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(invalid_date.returncode, 0)

    def test_homepage_lastmod_parser_accepts_the_repository_sitemap(self):
        result = subprocess.run(
            [
                "bash",
                "-c",
                self.guards + '\nsingle_homepage_lastmod "$1"',
                "bash",
                str(Path(__file__).resolve().parents[1] / "website" / "sitemap.xml"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), r"^\d{4}-\d{2}-\d{2}$")

    def test_homepage_lastmod_parser_rejects_duplicate_homepages(self):
        with tempfile.TemporaryDirectory() as directory:
            sitemap = Path(directory) / "sitemap.xml"
            sitemap.write_text(
                """<urlset>
                <url><loc>https://easelwall.com/</loc><lastmod>2026-08-05</lastmod></url>
                <url><loc>https://easelwall.com/</loc><lastmod>2026-08-06</lastmod></url>
                </urlset>
                """
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    self.guards + '\nsingle_homepage_lastmod "$1"',
                    "bash",
                    str(sitemap),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_appcast_parser_rejects_build_components_that_can_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            appcast = Path(directory) / "appcast.xml"
            appcast.write_text(
                "<sparkle:version>9223372036854775808</sparkle:version>\n"
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    self.guards + '\nsingle_appcast_value build "$1"',
                    "bash",
                    str(appcast),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_appcast_numeric_comparator_handles_equal_newer_and_older(self):
        helpers = self.guards
        script = helpers + """
        build_greater_or_equal 50000000.1.2 50000000.1.2
        build_greater_or_equal 50000001.1.0 50000000.2.9
        ! build_greater_or_equal 49999999.9.9 50000000.0.0
        """
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_appcast_parser_rejects_missing_malformed_and_duplicate_builds(self):
        helpers = self.guards
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

    def test_appcast_parser_rejects_noncanonical_and_duplicate_versions(self):
        helpers = self.guards
        with tempfile.TemporaryDirectory() as directory:
            appcast = Path(directory) / "appcast.xml"
            for content in (
                "<rss></rss>",
                "<sparkle:shortVersionString>future</sparkle:shortVersionString>",
                "<sparkle:shortVersionString>1.2</sparkle:shortVersionString>",
                "<sparkle:shortVersionString>1.02.3</sparkle:shortVersionString>",
                "<sparkle:shortVersionString>1.2.3</sparkle:shortVersionString>"
                "<sparkle:shortVersionString>1.2.4</sparkle:shortVersionString>",
            ):
                appcast.write_text(content)
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        helpers + '\nsingle_appcast_value version "$1"',
                        "bash",
                        str(appcast),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0, content)

    def test_appcast_semver_comparator_rejects_newer_current_version(self):
        helpers = self.guards
        script = helpers + """
        version_greater_than 1.0.4 1.0.3
        ! version_greater_than 1.0.3 1.0.3
        ! version_greater_than 1.0.2 1.0.3
        """
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("single_current_published_version()", self.guards)
        self.assertIn("versions disagree or exceed candidate", self.source)

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
        self.assertIn(
            'MERGED_RELEASE_METADATA_SHA" != "$EXPECTED_RELEASE_METADATA_SHA',
            self.source,
        )

    def test_release_updates_structured_version_and_sitemap(self):
        release_date = self.source.index(
            'RELEASE_DATE=$(release_tag_date "$REMOTE_TAG_REF" short)'
        )
        main_checkout = self.source.index("Check out main for release metadata")
        self.assertIn("scripts/update_release_metadata.py", self.source)
        self.assertIn('--version "$VERSION"', self.source)
        self.assertIn('--date "$RELEASE_DATE"', self.source)
        self.assertIn(
            'RELEASE_DATE=$(release_tag_date "$REMOTE_TAG_REF" short)', self.source
        )
        self.assertIn(
            'RELEASE_PUB_DATE=$(release_tag_date "$REMOTE_TAG_REF" rfc2822)',
            self.source,
        )
        self.assertNotIn("RELEASE_DATE=$(git show", self.source)
        self.assertLess(release_date, main_checkout)
        self.assertIn(
            "Casks/easelwall.rb \\",
            self.source,
        )
        self.assertIn("website/sitemap.xml", self.source)

    def test_repository_cask_is_synced_and_in_release_integrity_set(self):
        cask = REPOSITORY_CASK.read_text()
        cask_version = re.search(r'^\s*version "([^"]+)"$', cask, re.MULTILINE)
        appcast_version = re.search(
            r"<sparkle:shortVersionString>([^<]+)</sparkle:shortVersionString>",
            (WORKFLOW.parents[2] / "website" / "appcast.xml").read_text(),
        )
        project_version = re.search(
            r'^\s*MARKETING_VERSION:\s*"([^"]+)"$',
            (WORKFLOW.parents[2] / "project.yml").read_text(),
            re.MULTILINE,
        )
        self.assertIsNotNone(cask_version)
        self.assertIsNotNone(appcast_version)
        self.assertIsNotNone(project_version)
        self.assertEqual(cask_version.group(1), appcast_version.group(1))
        self.assertEqual(cask_version.group(1), project_version.group(1))
        self.assertRegex(
            cask,
            re.compile(r'^\s*sha256 "[0-9a-f]{64}"$', re.MULTILINE),
        )
        self.assertNotIn("sha256 :no_check", cask)
        self.assertGreaterEqual(self.source.count("Casks/easelwall.rb project.yml"), 2)
        self.assertIn("Repository cask checksum does not match", self.source)

    def test_release_tag_date_uses_annotated_tagger_and_lightweight_commit_dates(self):
        helpers = marked_shell_block(
            self.source,
            "# BEGIN RELEASE TAG DATE HELPERS",
            "# END RELEASE TAG DATE HELPERS",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(
                ["git", "init", str(repository)], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "test"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "user.email",
                    "test@example.com",
                ],
                check=True,
            )
            (repository / "file").write_text("release\n")
            subprocess.run(
                ["git", "-C", str(repository), "add", "file"], check=True
            )
            commit_environment = {
                "GIT_AUTHOR_DATE": "2026-07-01T12:00:00-05:00",
                "GIT_COMMITTER_DATE": "2026-07-01T12:00:00-05:00",
            }
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    "release",
                ],
                check=True,
                capture_output=True,
                env={**os.environ, **commit_environment},
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "tag.gpgSign=false",
                    "tag",
                    "-a",
                    "v1.0.3",
                    "-m",
                    "release",
                ],
                check=True,
                env={
                    **os.environ,
                    "GIT_COMMITTER_DATE": "2026-08-05T12:00:00-05:00",
                },
            )
            subprocess.run(
                ["git", "-C", str(repository), "tag", "v1.0.3-lightweight"],
                check=True,
            )

            annotated = subprocess.run(
                [
                    "bash",
                    "-c",
                    helpers + '\nrelease_tag_date "$1" short',
                    "bash",
                    "refs/tags/v1.0.3",
                ],
                cwd=repository,
                capture_output=True,
                text=True,
            )
            lightweight = subprocess.run(
                [
                    "bash",
                    "-c",
                    helpers + '\nrelease_tag_date "$1" short',
                    "bash",
                    "refs/tags/v1.0.3-lightweight",
                ],
                cwd=repository,
                capture_output=True,
                text=True,
            )
            self.assertEqual(annotated.returncode, 0, annotated.stderr)
            self.assertEqual(annotated.stdout.strip(), "2026-08-05")
            self.assertEqual(lightweight.returncode, 0, lightweight.stderr)
            self.assertEqual(lightweight.stdout.strip(), "2026-07-01")

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
        self.assertIn("git for-each-ref --sort=-version:refname", self.source)
        self.assertIn('git checkout --detach "$REMOTE_TAG_COMMIT"', self.source)
        self.assertIn('REMOTE_TAG_REF="refs/remotes/origin-tags/$TAG"', self.source)
        self.assertIn('git rev-parse HEAD', self.source)
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
        self.assertIn("git fetch --prune --no-tags origin", self.source)
        self.assertIn("+refs/tags/*:refs/remotes/origin-tags/*", self.source)
        self.assertIn('REMOTE_TAG_COMMIT" != "$CHECKED_OUT_COMMIT', self.source)
        self.assertIn("git for-each-ref --sort=-version:refname", self.source)
        self.assertIn('LATEST_TAG" != "$TAG', self.source)

    def test_annotated_tag_fetch_does_not_clobber_checkout_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote.git"
            producer = root / "producer"
            consumer = root / "consumer"

            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", str(producer)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(producer), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(producer), "config", "user.email", "test@example.com"], check=True)
            (producer / "file").write_text("release\n")
            subprocess.run(["git", "-C", str(producer), "add", "file"], check=True)
            subprocess.run(["git", "-C", str(producer), "-c", "commit.gpgsign=false", "commit", "-m", "release"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(producer), "-c", "tag.gpgSign=false", "tag", "-a", "v1.0.2", "-m", "release"], check=True)
            subprocess.run(["git", "-C", str(producer), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(producer), "push", "origin", "HEAD:main", "refs/tags/v1.0.2"], check=True, capture_output=True)

            subprocess.run(["git", "init", str(consumer)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(consumer), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(consumer), "fetch", "--no-tags", "origin", "main"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(consumer), "checkout", "--detach", "FETCH_HEAD"], check=True, capture_output=True)
            commit = subprocess.check_output(["git", "-C", str(consumer), "rev-parse", "HEAD"], text=True).strip()
            # Reproduce actions/checkout: the annotated local tag name points
            # directly at the peeled commit rather than the remote tag object.
            subprocess.run(["git", "-C", str(consumer), "update-ref", "refs/tags/v1.0.2", commit], check=True)

            result = subprocess.run(
                ["git", "-C", str(consumer), "fetch", "--prune", "--no-tags", "origin", "+refs/tags/*:refs/remotes/origin-tags/*"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            remote_commit = subprocess.check_output(
                ["git", "-C", str(consumer), "rev-parse", "refs/remotes/origin-tags/v1.0.2^{commit}"],
                text=True,
            ).strip()
            self.assertEqual(remote_commit, commit)
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(consumer), "rev-parse", "refs/tags/v1.0.2"], text=True).strip(),
                commit,
            )

            # The isolated namespace must also be pruned from current remote
            # state. Otherwise a deleted release tag could remain eligible as
            # the apparent latest version in a long-lived/manual runner.
            subprocess.run(
                ["git", "-C", str(producer), "push", "origin", ":refs/tags/v1.0.2"],
                check=True,
                capture_output=True,
            )
            prune = subprocess.run(
                ["git", "-C", str(consumer), "fetch", "--prune", "--no-tags", "origin", "+refs/tags/*:refs/remotes/origin-tags/*"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(prune.returncode, 0, prune.stderr)
            self.assertNotEqual(
                subprocess.run(
                    ["git", "-C", str(consumer), "show-ref", "--verify", "refs/remotes/origin-tags/v1.0.2"],
                    capture_output=True,
                    check=False,
                ).returncode,
                0,
            )
            # The checkout-created local tag remains untouched throughout.
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(consumer), "rev-parse", "refs/tags/v1.0.2"], text=True).strip(),
                commit,
            )

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
