from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.response
from datetime import date, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "app_store_reports.py"
SPEC = importlib.util.spec_from_file_location("app_store_reports", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
reports = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reports
SPEC.loader.exec_module(reports)


class FakeClient:
    def __init__(
        self, collections=None, responses=None, errors=None, pages=None, downloads=None
    ):
        self.collections = collections or {}
        self.responses = responses or {}
        self.errors = errors or {}
        self.pages = pages or {}
        self.downloads = downloads or {}
        self.calls = []

    def get_collection(self, path):
        self.calls.append(("COLLECTION", path, None))
        return list(self.collections.get(path, []))

    def request_json(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        error = self.errors.get((method, path))
        if error:
            raise error
        return self.responses[(method, path)]

    def iter_collection_pages(self, path):
        self.calls.append(("PAGES", path, None))
        for page in self.pages.get(path, []):
            yield list(page)

    def download_signed_url(self, signed_url):
        self.calls.append(("DOWNLOAD", signed_url, None))
        return self.downloads[signed_url]


def app_item(app_id="6778701883"):
    return {
        "type": "apps",
        "id": app_id,
        "attributes": {"name": "EaselWall", "bundleId": "com.ntindle.EaselWall"},
    }


def request_item(request_id, access_type):
    return {
        "type": "analyticsReportRequests",
        "id": request_id,
        "attributes": {"accessType": access_type},
    }


EASELWALL_APP = reports.AppInfo(
    app_id=reports.DEFAULT_APP_ID,
    name="EaselWall",
    bundle_id=reports.DEFAULT_BUNDLE_ID,
)


class CredentialTests(unittest.TestCase):
    def test_missing_environment_variable_names_are_reported_without_values(self):
        with self.assertRaises(reports.ConfigurationError) as raised:
            reports.load_credentials({"APP_STORE_CONNECT_API_KEY_ID": "abc"})
        message = str(raised.exception)
        self.assertIn("APP_STORE_CONNECT_API_ISSUER_ID", message)
        self.assertIn("APP_STORE_CONNECT_API_KEY_BASE64", message)
        self.assertNotIn("abc", message)

    def test_private_key_is_decoded_from_base64(self):
        pem = "-----BEGIN PRIVATE KEY-----\nsecret-material\n-----END PRIVATE KEY-----\n"
        credentials = reports.load_credentials(
            {
                "APP_STORE_CONNECT_API_KEY_ID": "key-id",
                "APP_STORE_CONNECT_API_ISSUER_ID": "issuer-id",
                "APP_STORE_CONNECT_API_KEY_BASE64": base64.b64encode(pem.encode()).decode(),
            }
        )
        self.assertEqual(credentials.private_key, pem)


class ResolutionTests(unittest.TestCase):
    def test_default_resolution_filters_by_bundle_id(self):
        expected_path = "/v1/apps?filter%5BbundleId%5D=com.ntindle.EaselWall&limit=2"
        client = FakeClient(collections={expected_path: [app_item()]})

        app = reports.resolve_app(
            client, app_id=None, bundle_id="com.ntindle.EaselWall"
        )

        self.assertEqual(app.app_id, "6778701883")
        self.assertEqual(client.calls, [("COLLECTION", expected_path, None)])

    def test_explicit_app_id_is_looked_up(self):
        client = FakeClient(
            responses={("GET", "/v1/apps/6778701883"): {"data": app_item()}}
        )

        app = reports.resolve_app(
            client, app_id="6778701883", bundle_id="com.ntindle.EaselWall"
        )

        self.assertEqual(app.name, "EaselWall")

    def test_explicit_app_id_must_resolve_to_expected_bundle(self):
        client = FakeClient(
            responses={
                ("GET", "/v1/apps/6778701883"): {
                    "data": {
                        **app_item(),
                        "attributes": {
                            "name": "Another App",
                            "bundleId": "example.other",
                        },
                    }
                }
            }
        )

        with self.assertRaisesRegex(reports.DataError, "not expected EaselWall"):
            reports.resolve_app(
                client,
                app_id="6778701883",
                bundle_id="com.ntindle.EaselWall",
            )


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.app = reports.AppInfo(
            app_id="6778701883", name="EaselWall", bundle_id="com.ntindle.EaselWall"
        )

    def test_creates_exactly_one_of_each_missing_type(self):
        client = FakeClient(
            responses={
                ("POST", "/v1/analyticsReportRequests"): {
                    "data": request_item("created", "ONE_TIME_SNAPSHOT")
                }
            }
        )
        created_ids = iter(("snapshot-id", "ongoing-id"))

        def create_side_effect(_, __, access_type):
            return reports.ReportRequest(next(created_ids), access_type)

        output = io.StringIO()
        with patch.object(
            reports, "create_report_request", side_effect=create_side_effect
        ) as create:
            result = reports.bootstrap_requests(client, self.app, [], output)

        self.assertEqual(
            [request.access_type for request in result],
            ["ONE_TIME_SNAPSHOT", "ONGOING"],
        )
        self.assertEqual(create.call_count, 2)

    def test_existing_type_is_kept_and_only_missing_type_is_created(self):
        existing = [reports.ReportRequest("snapshot-id", "ONE_TIME_SNAPSHOT")]
        client = FakeClient()
        output = io.StringIO()
        with patch.object(
            reports,
            "create_report_request",
            return_value=reports.ReportRequest("ongoing-id", "ONGOING"),
        ) as create:
            reports.bootstrap_requests(client, self.app, existing, output)

        create.assert_called_once_with(client, "6778701883", "ONGOING")
        self.assertIn("Kept existing ONE_TIME_SNAPSHOT", output.getvalue())

    def test_duplicates_are_reported_and_never_modified(self):
        existing = [
            reports.ReportRequest("snapshot-1", "ONE_TIME_SNAPSHOT"),
            reports.ReportRequest("snapshot-2", "ONE_TIME_SNAPSHOT"),
            reports.ReportRequest("ongoing-1", "ONGOING"),
        ]
        client = FakeClient()
        output = io.StringIO()
        with patch.object(reports, "create_report_request") as create:
            result = reports.bootstrap_requests(client, self.app, existing, output)

        create.assert_not_called()
        self.assertEqual(result, existing)
        self.assertIn("left them unchanged", output.getvalue())

    def test_conflict_is_accepted_only_after_refresh_confirms_request(self):
        conflict = reports.APIError(409, "conflict")
        client = FakeClient()
        output = io.StringIO()
        refreshed = [
            reports.ReportRequest("snapshot-id", "ONE_TIME_SNAPSHOT"),
            reports.ReportRequest("ongoing-id", "ONGOING"),
        ]
        with (
            patch.object(reports, "create_report_request", side_effect=conflict),
            patch.object(reports, "list_report_requests", return_value=refreshed),
        ):
            result = reports.bootstrap_requests(client, self.app, [], output)

        self.assertEqual(result, refreshed)
        self.assertIn("Another process created ONE_TIME_SNAPSHOT", output.getvalue())

    def test_stopped_ongoing_request_is_replaced(self):
        existing = [
            reports.ReportRequest("snapshot-id", "ONE_TIME_SNAPSHOT"),
            reports.ReportRequest(
                "stopped-id", "ONGOING", stopped_due_to_inactivity=True
            ),
        ]
        client = FakeClient()
        output = io.StringIO()
        with patch.object(
            reports,
            "create_report_request",
            return_value=reports.ReportRequest("replacement-id", "ONGOING"),
        ) as create:
            result = reports.bootstrap_requests(client, self.app, existing, output)

        create.assert_called_once_with(client, "6778701883", "ONGOING")
        self.assertIn("replacement Apple requires", output.getvalue())
        self.assertIn(reports.ReportRequest("replacement-id", "ONGOING"), result)


class RequestSelectionTests(unittest.TestCase):
    def test_auto_prefers_active_ongoing_and_ignores_stopped_requests(self):
        requests = [
            reports.ReportRequest("snapshot", "ONE_TIME_SNAPSHOT"),
            reports.ReportRequest("stopped", "ONGOING", True),
            reports.ReportRequest("ongoing", "ONGOING"),
        ]

        selected = reports.select_report_request(requests)

        self.assertEqual(selected.request_id, "ongoing")

    def test_auto_falls_back_to_snapshot(self):
        selected = reports.select_report_request(
            [reports.ReportRequest("snapshot", "ONE_TIME_SNAPSHOT")]
        )
        self.assertEqual(selected.access_type, "ONE_TIME_SNAPSHOT")


class AnalyticsTraversalTests(unittest.TestCase):
    def test_download_walks_all_targets_and_all_daily_instances(self):
        request = reports.ReportRequest("ongoing-id", "ONGOING")
        report_items = []
        collections = {
            "/v1/analyticsReportRequests/ongoing-id/reports?limit=200": report_items
        }
        pages = {}
        downloads = {}
        expected_urls = []

        for index, report_name in enumerate(reports.TARGET_REPORTS, start=1):
            report_id = f"report-{index}"
            report_items.append(
                {
                    "id": report_id,
                    "attributes": {"name": report_name, "category": "COMMERCE"},
                }
            )
            instances_path = (
                f"/v1/analyticsReports/{report_id}/instances?"
                "filter%5Bgranularity%5D=DAILY&limit=200"
            )
            collections[instances_path] = [
                {
                    "id": f"old-{index}",
                    "attributes": {
                        "granularity": "DAILY",
                        "processingDate": "2026-08-04",
                    },
                },
                {
                    "id": f"new-{index}",
                    "attributes": {
                        "granularity": "DAILY",
                        "processingDate": "2026-08-05",
                    },
                },
            ]
            source = f"Date\tMetric\n2026-08-04\t{index}\n".encode()
            compressed = gzip.compress(source, mtime=0)
            signed_url = f"https://reports.example.com/{index}?private=signature"
            expected_urls.append(signed_url)
            downloads[signed_url] = compressed
            segments_path = (
                f"/v1/analyticsReportInstances/new-{index}/segments?limit=200"
            )
            pages[segments_path] = [
                [
                    {
                        "id": f"segment-{index}",
                        "attributes": {
                            "checksum": hashlib.md5(
                                compressed, usedforsecurity=False
                            ).hexdigest(),
                            "sizeInBytes": len(compressed),
                            "url": signed_url,
                        },
                    }
                ]
            ]

        client = FakeClient(
            collections=collections, pages=pages, downloads=downloads
        )
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = reports.download_target_reports(
                client,
                [request],
                app=EASELWALL_APP,
                access_type="AUTO",
                output_dir=Path(temporary_directory),
                output=output,
            )

        self.assertEqual(len(paths), len(reports.TARGET_REPORTS))
        self.assertEqual(
            [call[1] for call in client.calls if call[0] == "DOWNLOAD"],
            expected_urls,
        )
        self.assertNotIn("private=signature", output.getvalue())
        self.assertTrue(
            all(
                any(
                    call
                    == (
                        "PAGES",
                        f"/v1/analyticsReportInstances/old-{index}/segments?limit=200",
                        None,
                    )
                    for call in client.calls
                )
                for index in range(1, len(reports.TARGET_REPORTS) + 1)
            )
        )

        for index, signed_url in enumerate(expected_urls, start=1):
            segments_path = (
                f"/v1/analyticsReportInstances/new-{index}/segments?limit=200"
            )
            page_index = next(
                call_index
                for call_index, call in enumerate(client.calls)
                if call == ("PAGES", segments_path, None)
            )
            download_index = next(
                call_index
                for call_index, call in enumerate(client.calls)
                if call[0] == "DOWNLOAD" and call[1] == signed_url
            )
            self.assertLess(page_index, download_index)

    def test_repeated_segment_ids_are_downloaded_once(self):
        segment = {
            "id": "segment-1",
            "attributes": {
                "checksum": "a" * 32,
                "sizeInBytes": 123,
                "url": "https://reports.example.com/first",
            },
        }
        repeated = {
            **segment,
            "attributes": {
                **segment["attributes"],
                "url": "https://reports.example.com/refreshed-signature",
            },
        }
        path = "/v1/analyticsReportInstances/instance/segments?limit=200"
        client = FakeClient(pages={path: [[segment], [repeated]]})

        result = list(reports.iter_segments(client, "instance"))

        self.assertEqual([item.segment_id for item in result], ["segment-1"])

    def test_repeated_segment_id_with_conflicting_checksum_fails(self):
        path = "/v1/analyticsReportInstances/instance/segments?limit=200"
        first = {
            "id": "segment-1",
            "attributes": {
                "checksum": "a" * 32,
                "sizeInBytes": 123,
                "url": "https://reports.example.com/first",
            },
        }
        second = {
            "id": "segment-1",
            "attributes": {
                "checksum": "b" * 32,
                "sizeInBytes": 123,
                "url": "https://reports.example.com/second",
            },
        }
        client = FakeClient(pages={path: [[first], [second]]})

        with self.assertRaisesRegex(reports.DataError, "conflicting metadata"):
            list(reports.iter_segments(client, "instance"))


class HttpClientTests(unittest.TestCase):
    def test_api_redirect_to_evil_host_is_not_followed(self):
        class RedirectingHTTPSHandler(reports.urllib.request.BaseHandler):
            handler_order = 100

            def __init__(self):
                self.requests = []

            def https_open(self, request):
                self.requests.append(request)
                headers = Message()
                headers["Location"] = "https://evil.example/steal"
                response = urllib.response.addinfourl(
                    io.BytesIO(b""), headers, request.full_url, code=302
                )
                response.msg = "Found"
                return response

        transport = RedirectingHTTPSHandler()
        opener = reports.urllib.request.build_opener(
            reports._RejectAPIRedirects(), transport
        )
        client = reports.AppStoreConnectClient(
            "private-token", opener=opener.open
        )

        with self.assertRaises(reports.APIError) as raised:
            client.request_json("GET", "/v1/apps")

        self.assertEqual(raised.exception.status, 302)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(
            transport.requests[0].host, reports.API_HOST
        )
        self.assertEqual(
            transport.requests[0].get_header("Authorization"),
            "Bearer private-token",
        )
        self.assertNotEqual(transport.requests[0].host, "evil.example")

    def test_default_api_transport_installs_redirect_rejection(self):
        fake_opener = unittest.mock.Mock()
        with patch.object(
            reports.urllib.request, "build_opener", return_value=fake_opener
        ) as build_opener:
            reports.AppStoreConnectClient("private-token")

        self.assertIsInstance(
            build_opener.call_args.args[0], reports._RejectAPIRedirects
        )

    def test_pagination_rejects_untrusted_host_before_sending_token(self):
        first_response = unittest.mock.MagicMock()
        first_response.__enter__.return_value.read.return_value = json.dumps(
            {"data": [], "links": {"next": "https://example.com/steal"}}
        ).encode()
        opener = unittest.mock.Mock(return_value=first_response)
        client = reports.AppStoreConnectClient("private-token", opener=opener)

        with self.assertRaises(reports.DataError):
            client.get_collection("/v1/apps")

        self.assertEqual(opener.call_count, 1)

    def test_http_error_message_does_not_include_authorization_header(self):
        body = json.dumps(
            {"errors": [{"code": "FORBIDDEN", "title": "Forbidden"}]}
        ).encode()
        error = urllib.error.HTTPError(
            "https://api.appstoreconnect.apple.com/v1/apps",
            403,
            "Forbidden",
            {},
            io.BytesIO(body),
        )
        client = reports.AppStoreConnectClient(
            "private-token", opener=unittest.mock.Mock(side_effect=error)
        )

        with self.assertRaises(reports.APIError) as raised:
            client.request_json("GET", "/v1/apps")

        self.assertEqual(raised.exception.status, 403)
        self.assertNotIn("private-token", str(raised.exception))

    def test_signed_download_never_receives_bearer_token(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"report-bytes"
        opener = unittest.mock.Mock(return_value=response)
        client = reports.AppStoreConnectClient("private-token", opener=opener)

        result = client.download_signed_url("https://reports.example.com/signed?token=x")

        self.assertEqual(result, b"report-bytes")
        request = opener.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertNotIn("private-token", str(request.header_items()))


class DownloadAndReportTests(unittest.TestCase):
    def _download_purchase_fixture(
        self,
        output_dir,
        *,
        request_id,
        access_type,
        processing_date,
        segment_id,
        rows,
        report_name=None,
        report_id=None,
        instance_id=None,
        extra_header=None,
    ):
        report_name = report_name or reports.PURCHASES_STANDARD_REPORT
        report_id = report_id or f"report-{reports.report_slug(report_name)}"
        instance_id = instance_id or f"instance-{segment_id}"
        header = "Date\tPurchases\tProceeds in USD"
        if report_name == reports.PURCHASES_DETAILED_REPORT:
            header += "\tCampaign"
        if extra_header is not None:
            header += f"\t{extra_header}"
        source = (header + "\n" + "".join(rows)).encode()
        compressed = gzip.compress(source, mtime=0)
        checksum = hashlib.md5(compressed, usedforsecurity=False).hexdigest()
        signed_url = f"https://reports.example.com/{segment_id}?signature=private"
        client = FakeClient(downloads={signed_url: compressed})
        path, _ = reports.download_segment(
            client,
            app=EASELWALL_APP,
            request=reports.ReportRequest(request_id, access_type),
            report=reports.AnalyticsReport(
                report_id, report_name, "COMMERCE"
            ),
            instance=reports.ReportInstance(
                instance_id, "DAILY", processing_date
            ),
            segment=reports.ReportSegment(
                segment_id, checksum, len(compressed), signed_url
            ),
            output_dir=output_dir,
            output=io.StringIO(),
        )
        return path

    def test_download_segment_preserves_verified_gzip_and_tsv(self):
        source = (
            "Date\tPurchases\tProceeds in USD\tCampaign\n"
            "2026-08-01\t1\t2.55\ttt_organic\n"
        ).encode()
        compressed = gzip.compress(source)
        checksum = hashlib.md5(compressed, usedforsecurity=False).hexdigest()
        signed_url = "https://reports.example.com/segment?signature=private"
        client = FakeClient(downloads={signed_url: compressed})
        request = reports.ReportRequest("request-id", "ONGOING")
        report = reports.AnalyticsReport(
            "report-id", "App Store Purchases Detailed", "COMMERCE"
        )
        instance = reports.ReportInstance("instance-id", "DAILY", date(2026, 8, 5))
        segment = reports.ReportSegment(
            "segment-id", checksum, len(compressed), signed_url
        )
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            tsv_path, downloaded = reports.download_segment(
                client,
                app=EASELWALL_APP,
                request=request,
                report=report,
                instance=instance,
                segment=segment,
                output_dir=output_dir,
                output=output,
            )
            gzip_path = tsv_path.with_suffix(".txt.gz")
            metadata_path = tsv_path.with_suffix(".metadata.json")

            self.assertTrue(downloaded)
            self.assertEqual(gzip_path.read_bytes(), compressed)
            self.assertEqual(tsv_path.read_bytes(), source)
            self.assertNotIn("signature=private", metadata_path.read_text())
            metadata = json.loads(metadata_path.read_text())
            self.assertEqual(metadata["appId"], reports.DEFAULT_APP_ID)
            self.assertEqual(metadata["bundleId"], reports.DEFAULT_BUNDLE_ID)
            self.assertEqual(metadata["schemaVersion"], 2)
            self.assertEqual(gzip_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(tsv_path.stat().st_mode & 0o777, 0o600)

            _, downloaded_again = reports.download_segment(
                client,
                app=EASELWALL_APP,
                request=request,
                report=report,
                instance=instance,
                segment=segment,
                output_dir=output_dir,
                output=output,
            )
            self.assertFalse(downloaded_again)

        self.assertEqual(
            len([call for call in client.calls if call[0] == "DOWNLOAD"]), 1
        )

    def test_tampered_tsv_and_sidecar_are_redownloaded_from_verified_gzip(self):
        source = (
            "Date\tPurchases\tProceeds in USD\n"
            "2026-08-01\t1\t2.55\n"
        ).encode()
        compressed = gzip.compress(source, mtime=0)
        checksum = hashlib.md5(compressed, usedforsecurity=False).hexdigest()
        signed_url = "https://reports.example.com/reuse-integrity"
        client = FakeClient(downloads={signed_url: compressed})
        request = reports.ReportRequest("request-id", "ONGOING")
        report = reports.AnalyticsReport(
            "report-id", reports.PURCHASES_STANDARD_REPORT, "COMMERCE"
        )
        instance = reports.ReportInstance("instance-id", "DAILY", date(2026, 8, 5))
        segment = reports.ReportSegment(
            "segment-id", checksum, len(compressed), signed_url
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            tsv_path, _ = reports.download_segment(
                client,
                app=EASELWALL_APP,
                request=request,
                report=report,
                instance=instance,
                segment=segment,
                output_dir=output_dir,
                output=io.StringIO(),
            )
            tampered = source.replace(b"1\t2.55", b"9\t99.00")
            tsv_path.write_bytes(tampered)
            metadata_path = tsv_path.with_suffix(".metadata.json")
            metadata = json.loads(metadata_path.read_text())
            metadata["decompressedSha256"] = hashlib.sha256(tampered).hexdigest()
            metadata["decompressedSizeInBytes"] = len(tampered)
            metadata_path.write_text(json.dumps(metadata))

            _, downloaded = reports.download_segment(
                client,
                app=EASELWALL_APP,
                request=request,
                report=report,
                instance=instance,
                segment=segment,
                output_dir=output_dir,
                output=io.StringIO(),
            )

            self.assertTrue(downloaded)
            self.assertEqual(tsv_path.read_bytes(), source)
            self.assertEqual(
                reports.normalize_tsv(tsv_path.with_suffix(".txt.gz").read_bytes()),
                source,
            )
        self.assertEqual(
            len([call for call in client.calls if call[0] == "DOWNLOAD"]), 2
        )

    def test_report_command_is_offline_and_writes_markdown(self):
        source = (
            "Date\tPurchases\tProceeds in USD\n"
            "2026-08-01\t2\t5.10\n"
            "2026-08-02\t1\t2.55\n"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "purchases.tsv"
            report_path = root / "weekly.md"
            canonical_path = root / "canonical.tsv"
            input_path.write_text(source)

            exit_code = reports.main(
                [
                    "report",
                    "--input",
                    str(input_path),
                    "--output",
                    str(report_path),
                    "--canonical-output",
                    str(canonical_path),
                    "--as-of",
                    "2026-08-05",
                    "--year",
                    "2026",
                ],
                environ={},
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, reports.EXIT_OK)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("$7.65 / $99.00", stdout.getvalue())
            self.assertEqual(stdout.getvalue().count("Estimated revenue ["), 1)
            self.assertIn("3 net purchase(s)", stdout.getvalue())
            markdown = report_path.read_text()
            self.assertIn("Estimated net proceeds: $7.65", markdown)
            self.assertIn("privacy-limited", markdown)
            self.assertIn("privacy-limited", markdown)
            self.assertIn("Detailed source archive: 0 file(s)", markdown)
            canonical = canonical_path.read_text()
            self.assertEqual(
                canonical.splitlines()[0], "Date\tPurchases\tProceeds in USD"
            )
            self.assertIn("2026-08-01\t2\t5.10", canonical)
            self.assertNotIn("Campaign", canonical)

    def test_report_defaults_to_rolling_365_days_across_calendar_years(self):
        as_of = date(2026, 2, 1)
        period = reports.ReportingPeriod.trailing_365(as_of)
        source = (
            "Date\tPurchases\tProceeds in USD\n"
            f"{period.start_date.isoformat()}\t1\t2.55\n"
            "2026-01-31\t2\t5.10\n"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "purchases.tsv"
            report_path = root / "weekly.md"
            canonical_path = root / "canonical.tsv"
            input_path.write_text(source)

            exit_code = reports.main(
                [
                    "report",
                    "--input",
                    str(input_path),
                    "--output",
                    str(report_path),
                    "--canonical-output",
                    str(canonical_path),
                    "--as-of",
                    as_of.isoformat(),
                ],
                environ={},
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, reports.EXIT_OK)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("3 net purchase(s)", stdout.getvalue())
            self.assertIn("trailing 365 days", stdout.getvalue())
            self.assertIn(period.start_date.isoformat(), report_path.read_text())
            self.assertIn(period.start_date.isoformat(), canonical_path.read_text())

    def test_year_retains_calendar_year_mode(self):
        source = (
            "Date\tPurchases\tProceeds in USD\n"
            "2025-12-31\t10\t25.50\n"
            "2026-01-01\t1\t2.55\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "purchases.tsv"
            input_path.write_text(source)
            summary = reports.summarize_purchases(input_path, year=2026)

        self.assertEqual(summary.purchases, 1)
        self.assertEqual(summary.proceeds, reports.Decimal("2.55"))
        self.assertEqual(summary.period.mode, reports.PERIOD_CALENDAR_YEAR)

    def test_purchase_summary_treats_negative_purchases_as_refunds(self):
        source = (
            "Date\tPurchases\tProceeds in USD\n"
            "2026-08-01\t2\t5.10\n"
            "2026-08-02\t-1\t-2.55\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "purchases.tsv"
            input_path.write_text(source)
            summary = reports.summarize_purchases(input_path, year=2026)

        self.assertEqual(summary.purchases, 1)
        self.assertEqual(summary.refund_units, 1)
        self.assertEqual(str(summary.proceeds), "2.55")

    def test_trailing_365_period_includes_both_boundaries_only(self):
        as_of = date(2026, 8, 5)
        period = reports.ReportingPeriod.trailing_365(as_of)
        source = (
            "Date\tPurchases\tProceeds in USD\n"
            f"{(period.start_date - timedelta(days=1)).isoformat()}\t10\t25.50\n"
            f"{period.start_date.isoformat()}\t1\t2.55\n"
            f"{period.end_date.isoformat()}\t2\t5.10\n"
            f"{(period.end_date + timedelta(days=1)).isoformat()}\t20\t51.00\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "purchases.tsv"
            input_path.write_text(source)
            summary = reports.summarize_purchases(input_path, period=period)

        self.assertEqual((period.end_date - period.start_date).days + 1, 365)
        self.assertEqual(summary.purchases, 3)
        self.assertEqual(summary.proceeds, reports.Decimal("7.65"))
        self.assertEqual(summary.dates, {period.start_date, period.end_date})

    def test_app_apple_identifier_is_validated_when_column_is_present(self):
        source = (
            "Date\tApp Apple Identifier\tPurchases\tProceeds in USD\n"
            "2026-08-01\t6778701883\t1\t2.55\n"
            "2026-08-02\t9999999999\t1\t2.55\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "purchases.tsv"
            input_path.write_text(source)
            with self.assertRaisesRegex(
                reports.DataError, "Unexpected 'App Apple Identifier'"
            ):
                reports.summarize_purchases(input_path, year=2026)

    def test_managed_report_app_id_and_bundle_id_are_enforced(self):
        for metadata_field, wrong_value, expected_message in (
            ("appId", "9999999999", "not EaselWall app"),
            ("bundleId", "example.other", "not EaselWall bundle"),
        ):
            with self.subTest(metadata_field=metadata_field):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    path = self._download_purchase_fixture(
                        root,
                        request_id="ongoing",
                        access_type="ONGOING",
                        processing_date=date(2026, 8, 5),
                        segment_id=f"wrong-{metadata_field}",
                        rows=["2026-08-01\t1\t2.55\n"],
                    )
                    metadata_path = path.with_suffix(".metadata.json")
                    metadata = json.loads(metadata_path.read_text())
                    metadata[metadata_field] = wrong_value
                    metadata_path.write_text(json.dumps(metadata))

                    with self.assertRaisesRegex(
                        reports.DataError, expected_message
                    ):
                        reports.summarize_purchases(
                            root / "downloads", year=2026
                        )

    def test_explicit_campaign_tsv_cannot_become_authoritative_revenue(self):
        source = (
            "Date\tCampaign\tPurchases\tProceeds in USD\n"
            "2026-08-01\ttt_organic\t1\t2.55\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "campaigns-2026.tsv"
            input_path.write_text(source)
            summary = reports.summarize_purchases(input_path, year=2026)

        self.assertFalse(summary.standard_available)
        self.assertTrue(summary.campaign_available)
        self.assertEqual(summary.purchases, 0)
        self.assertEqual(summary.proceeds, reports.Decimal("0"))
        self.assertEqual(summary.campaigns["tt_organic"].purchases, 1)
        with self.assertRaises(reports.DataError):
            reports.canonical_purchase_tsv(summary)

    def test_newer_processing_date_replaces_overlapping_event_date(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            # For August 1, the newer snapshot replaces the older ongoing data,
            # even though ongoing is normally preferred on an exact tie.
            self._download_purchase_fixture(
                output_dir,
                request_id="ongoing-old",
                access_type="ONGOING",
                processing_date=date(2026, 8, 4),
                segment_id="old-aug-1",
                rows=["2026-08-01\t2\t5.10\n"],
            )
            self._download_purchase_fixture(
                output_dir,
                request_id="snapshot-new",
                access_type="ONE_TIME_SNAPSHOT",
                processing_date=date(2026, 8, 6),
                segment_id="new-aug-1",
                rows=["2026-08-01\t1\t2.55\n"],
            )
            # For August 2, equal-date snapshot and ongoing contents deduplicate.
            self._download_purchase_fixture(
                output_dir,
                request_id="snapshot-tie",
                access_type="ONE_TIME_SNAPSHOT",
                processing_date=date(2026, 8, 6),
                segment_id="snapshot-aug-2",
                rows=["2026-08-02\t1\t2.55\n"],
            )
            self._download_purchase_fixture(
                output_dir,
                request_id="ongoing-tie",
                access_type="ONGOING",
                processing_date=date(2026, 8, 6),
                segment_id="ongoing-aug-2",
                rows=["2026-08-02\t1\t2.55\n"],
            )
            self._download_purchase_fixture(
                output_dir,
                request_id="detailed-old",
                access_type="ONGOING",
                processing_date=date(2026, 8, 4),
                segment_id="detailed-old-aug-1",
                rows=["2026-08-01\t2\t5.10\ttt_organic\n"],
                report_name=reports.PURCHASES_DETAILED_REPORT,
            )
            self._download_purchase_fixture(
                output_dir,
                request_id="detailed-new",
                access_type="ONE_TIME_SNAPSHOT",
                processing_date=date(2026, 8, 6),
                segment_id="detailed-new-aug-1",
                rows=["2026-08-01\t1\t2.55\ttt_creator\n"],
                report_name=reports.PURCHASES_DETAILED_REPORT,
            )

            summary = reports.summarize_purchases(
                output_dir / "downloads", year=2026
            )

        self.assertEqual(summary.purchases, 2)
        self.assertEqual(summary.proceeds, reports.Decimal("5.10"))
        self.assertEqual(summary.superseded_rows, 2)
        self.assertEqual(summary.files_scanned, 4)
        self.assertEqual(summary.files, 2)
        self.assertEqual(summary.campaigns["tt_creator"].purchases, 1)
        self.assertEqual(summary.campaigns["tt_organic"].purchases, 0)
        self.assertEqual(summary.campaign_superseded_rows, 1)
        canonical = reports.canonical_purchase_tsv(summary)
        self.assertIn("2026-08-01\t1\t2.55", canonical)
        self.assertIn("2026-08-02\t1\t2.55", canonical)
        self.assertNotIn("Campaign", canonical)
        campaign_canonical = reports.canonical_campaign_tsv(summary)
        self.assertIn("2026-08-01\ttt_creator\t1\t2.55", campaign_canonical)
        self.assertNotIn("\ttt_organic\t", campaign_canonical)

    def test_standard_and_detailed_rows_are_never_double_counted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="standard-sale",
                rows=["2026-08-01\t1\t2.55\n"],
            )
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="detailed-sale",
                rows=["2026-08-01\t1\t2.55\ttt_organic_video-1\n"],
                report_name=reports.PURCHASES_DETAILED_REPORT,
            )

            summary = reports.summarize_purchases(root / "downloads", year=2026)

        self.assertEqual(summary.purchases, 1)
        self.assertEqual(summary.proceeds, reports.Decimal("2.55"))
        self.assertEqual(summary.rows, 1)
        self.assertEqual(summary.campaign_rows, 1)
        self.assertEqual(summary.campaigns["tt_organic"].purchases, 1)

    def test_standard_sale_survives_header_only_detailed_report(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="standard-sale",
                rows=["2026-08-01\t1\t2.55\n"],
            )
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="detailed-header",
                rows=[],
                report_name=reports.PURCHASES_DETAILED_REPORT,
            )
            summary = reports.summarize_purchases(root / "downloads", year=2026)
            markdown = reports.markdown_report(
                summary, goal=reports.Decimal("99"), as_of=date(2026, 8, 5)
            )

        self.assertEqual(summary.purchases, 1)
        self.assertEqual(summary.proceeds, reports.Decimal("2.55"))
        self.assertTrue(summary.campaign_available)
        self.assertEqual(summary.campaign_rows, 0)
        self.assertIn("privacy-limited", markdown)
        self.assertIn("privacy-limited", markdown)
        self.assertIn("never added to the Standard total", markdown)

    def test_multiple_segments_in_one_standard_instance_are_aggregated(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for segment_id, purchases, proceeds in (
                ("segment-a", 1, "2.55"),
                ("segment-b", 2, "5.10"),
            ):
                self._download_purchase_fixture(
                    root,
                    request_id="ongoing",
                    access_type="ONGOING",
                    processing_date=date(2026, 8, 5),
                    segment_id=segment_id,
                    rows=[f"2026-08-01\t{purchases}\t{proceeds}\n"],
                    report_id="standard-report",
                    instance_id="shared-instance",
                )
            summary = reports.summarize_purchases(root / "downloads", year=2026)

        self.assertEqual(summary.datasets_considered, 1)
        self.assertEqual(summary.files_scanned, 2)
        self.assertEqual(summary.files, 2)
        self.assertEqual(summary.purchases, 3)
        self.assertEqual(summary.proceeds, reports.Decimal("7.65"))

    def test_missing_standard_is_unknown_and_writes_no_report(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "downloads"
            input_path.mkdir()
            markdown_path = root / "weekly.md"
            canonical_path = root / "purchases.tsv"
            campaign_path = root / "campaigns.tsv"

            exit_code = reports.main(
                [
                    "report",
                    "--input",
                    str(input_path),
                    "--output",
                    str(markdown_path),
                    "--canonical-output",
                    str(canonical_path),
                    "--campaign-output",
                    str(campaign_path),
                    "--as-of",
                    "2026-08-05",
                ],
                environ={},
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, reports.EXIT_DATA)
            self.assertFalse(markdown_path.exists())
            self.assertFalse(canonical_path.exists())
            self.assertFalse(campaign_path.exists())
        self.assertIn("unknown/pending", stdout.getvalue())
        self.assertIn("unknown/pending", stderr.getvalue())

    def test_header_only_purchase_report_yields_valid_zero_report(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="header-only-standard",
                rows=[],
            )
            summary = reports.summarize_purchases(root / "downloads", year=2026)
            markdown = reports.markdown_report(
                summary, goal=reports.Decimal("99"), as_of=date(2026, 8, 5)
            )

        self.assertTrue(summary.standard_available)
        self.assertEqual(summary.rows, 0)
        self.assertEqual(summary.purchases, 0)
        self.assertEqual(summary.proceeds, reports.Decimal("0"))
        self.assertIn("Estimated net proceeds: $0.00", markdown)
        self.assertIn("Goal progress: `[--------------------]` 0.0%", markdown)

    def test_checksum_mismatch_does_not_write_segment(self):
        source = b"Date\tPurchases\tProceeds in USD\n"
        compressed = gzip.compress(source, mtime=0)
        signed_url = "https://reports.example.com/bad"
        client = FakeClient(downloads={signed_url: compressed})
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            with self.assertRaises(reports.DataError):
                reports.download_segment(
                    client,
                    app=EASELWALL_APP,
                    request=reports.ReportRequest("request", "ONGOING"),
                    report=reports.AnalyticsReport(
                        "report", reports.PURCHASES_REPORT, "COMMERCE"
                    ),
                    instance=reports.ReportInstance(
                        "instance", "DAILY", date(2026, 8, 5)
                    ),
                    segment=reports.ReportSegment(
                        "segment", "0" * 32, len(compressed), signed_url
                    ),
                    output_dir=output_dir,
                    output=io.StringIO(),
                )
            self.assertEqual(list(output_dir.rglob("*.tsv")), [])

    def test_conflicting_same_processing_date_instances_fail_visibly(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for segment_id, proceeds in (("instance-a", "2.55"), ("instance-b", "5.10")):
                self._download_purchase_fixture(
                    root,
                    request_id="ongoing",
                    access_type="ONGOING",
                    processing_date=date(2026, 8, 5),
                    segment_id=segment_id,
                    instance_id=segment_id,
                    rows=[f"2026-08-01\t1\t{proceeds}\n"],
                )

            with self.assertRaisesRegex(
                reports.DataError, "Conflicting App Store Purchases Standard instances"
            ):
                reports.summarize_purchases(root / "downloads", year=2026)

    def test_cross_access_equal_processing_date_conflict_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._download_purchase_fixture(
                root,
                request_id="snapshot",
                access_type="ONE_TIME_SNAPSHOT",
                processing_date=date(2026, 8, 5),
                segment_id="snapshot",
                rows=["2026-08-01\t1\t2.55\n"],
            )
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="ongoing",
                rows=["2026-08-01\t2\t5.10\n"],
            )

            with self.assertRaisesRegex(
                reports.DataError,
                "Conflicting App Store Purchases Standard instances",
            ):
                reports.summarize_purchases(root / "downloads", year=2026)

    def test_cross_access_tie_compares_complete_rows_not_only_totals(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._download_purchase_fixture(
                root,
                request_id="snapshot",
                access_type="ONE_TIME_SNAPSHOT",
                processing_date=date(2026, 8, 5),
                segment_id="snapshot-app-name",
                extra_header="App Name",
                rows=["2026-08-01\t1\t2.55\tEaselWall\n"],
            )
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="ongoing-app-name",
                extra_header="App Name",
                rows=["2026-08-01\t1\t2.55\tOther Name\n"],
            )

            with self.assertRaisesRegex(
                reports.DataError,
                "Conflicting App Store Purchases Standard instances",
            ):
                reports.summarize_purchases(root / "downloads", year=2026)

    def test_cross_access_equal_processing_date_identical_contents_deduplicate(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for request_id, access_type in (
                ("snapshot", "ONE_TIME_SNAPSHOT"),
                ("ongoing", "ONGOING"),
            ):
                self._download_purchase_fixture(
                    root,
                    request_id=request_id,
                    access_type=access_type,
                    processing_date=date(2026, 8, 5),
                    segment_id="shared-segment",
                    report_id="shared-report",
                    instance_id="shared-instance",
                    rows=["2026-08-01\t1\t2.55\n"],
                )

            summary = reports.summarize_purchases(
                root / "downloads", year=2026
            )

        self.assertEqual(summary.purchases, 1)
        self.assertEqual(summary.proceeds, reports.Decimal("2.55"))
        self.assertEqual(summary.files_scanned, 2)
        self.assertEqual(summary.files, 1)
        self.assertEqual(summary.superseded_rows, 1)

    def test_cross_access_equal_processing_date_nonoverlapping_batches_coexist(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._download_purchase_fixture(
                root,
                request_id="snapshot",
                access_type="ONE_TIME_SNAPSHOT",
                processing_date=date(2026, 8, 5),
                segment_id="snapshot-history",
                rows=["2026-07-01\t1\t2.55\n"],
            )
            self._download_purchase_fixture(
                root,
                request_id="ongoing",
                access_type="ONGOING",
                processing_date=date(2026, 8, 5),
                segment_id="ongoing-recent",
                rows=["2026-08-01\t2\t5.10\n"],
            )

            summary = reports.summarize_purchases(
                root / "downloads", year=2026
            )

        self.assertEqual(summary.purchases, 3)
        self.assertEqual(summary.proceeds, reports.Decimal("7.65"))
        self.assertEqual(summary.files, 2)
        self.assertEqual(summary.superseded_rows, 0)

    def test_distinct_segments_of_one_report_instance_form_one_dataset(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for segment_id, purchases, proceeds in (
                ("segment-a", 1, "2.55"),
                ("segment-b", 2, "5.10"),
            ):
                self._download_purchase_fixture(
                    root,
                    request_id="ongoing",
                    access_type="ONGOING",
                    processing_date=date(2026, 8, 5),
                    segment_id=segment_id,
                    instance_id="shared-instance",
                    rows=[f"2026-08-01\t{purchases}\t{proceeds}\n"],
                )

            summary = reports.summarize_purchases(root / "downloads", year=2026)

        self.assertEqual(summary.datasets_considered, 1)
        self.assertEqual(summary.purchases, 3)
        self.assertEqual(summary.proceeds, reports.Decimal("7.65"))

    def test_fetch_downloads_every_daily_processing_date_oldest_first(self):
        request = reports.ReportRequest("ongoing-request", "ONGOING")
        report_id = "purchases-report"
        reports_path = (
            f"/v1/analyticsReportRequests/{request.request_id}/reports?limit=200"
        )
        instances_query = reports.urllib.parse.urlencode(
            {"filter[granularity]": "DAILY", "limit": "200"}
        )
        instances_path = f"/v1/analyticsReports/{report_id}/instances?{instances_query}"
        collections = {
            reports_path: [
                {
                    "id": report_id,
                    "attributes": {
                        "name": reports.PURCHASES_REPORT,
                        "category": "COMMERCE",
                    },
                }
            ],
            instances_path: [
                {
                    "id": "new-instance",
                    "attributes": {
                        "granularity": "DAILY",
                        "processingDate": "2026-08-05",
                    },
                },
                {
                    "id": "old-instance",
                    "attributes": {
                        "granularity": "DAILY",
                        "processingDate": "2026-08-03",
                    },
                },
            ],
        }
        pages = {}
        downloads = {}
        signed_urls = []
        for instance_id, event_date in (
            ("old-instance", "2026-08-01"),
            ("new-instance", "2026-08-02"),
        ):
            source = (
                "Date\tPurchases\tProceeds in USD\tCampaign\n"
                f"{event_date}\t1\t2.55\ttt_organic\n"
            ).encode()
            compressed = gzip.compress(source, mtime=0)
            signed_url = f"https://reports.example.com/{instance_id}"
            signed_urls.append(signed_url)
            pages[
                f"/v1/analyticsReportInstances/{instance_id}/segments?limit=200"
            ] = [
                [
                    {
                        "id": f"segment-{instance_id}",
                        "attributes": {
                            "url": signed_url,
                            "checksum": hashlib.md5(
                                compressed, usedforsecurity=False
                            ).hexdigest(),
                            "sizeInBytes": len(compressed),
                        },
                    }
                ]
            ]
            downloads[signed_url] = compressed

        client = FakeClient(
            collections=collections, pages=pages, downloads=downloads
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = reports.download_target_reports(
                client,
                [request],
                app=EASELWALL_APP,
                access_type="AUTO",
                output_dir=Path(temporary_directory),
                output=io.StringIO(),
            )

        self.assertEqual(len(paths), 2)
        download_calls = [
            call[1] for call in client.calls if call[0] == "DOWNLOAD"
        ]
        self.assertEqual(download_calls, signed_urls)

    def test_latest_json_summary_has_one_request_scope(self):
        summary = reports.PurchaseSummary(
            year=2026, standard_available=True, purchases=1
        )
        summary.proceeds = reports.Decimal("2.55")
        request = reports.ReportRequest("ongoing-request", "ONGOING")
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            path = reports.write_latest_summary(
                output_dir, summary, request=request
            )
            payload = json.loads(path.read_text())

        self.assertEqual(payload["scope"]["accessType"], "ONGOING")
        self.assertEqual(payload["scope"]["requestId"], "ongoing-request")
        self.assertEqual(payload["scope"]["appId"], reports.DEFAULT_APP_ID)
        self.assertEqual(payload["scope"]["bundleId"], reports.DEFAULT_BUNDLE_ID)
        self.assertEqual(
            payload["scope"]["reportingPeriod"]["mode"],
            reports.PERIOD_CALENDAR_YEAR,
        )
        self.assertTrue(payload["standardDataAvailable"])
        self.assertEqual(payload["proceedsInUSD"], "2.55")

    def test_latest_json_marks_missing_standard_as_pending(self):
        summary = reports.PurchaseSummary(year=2026)
        request = reports.ReportRequest("ongoing-request", "ONGOING")
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = reports.write_latest_summary(
                Path(temporary_directory), summary, request=request
            )
            payload = json.loads(path.read_text())

        self.assertFalse(payload["standardDataAvailable"])
        self.assertIsNone(payload["netPurchases"])
        self.assertIsNone(payload["refundUnits"])
        self.assertIsNone(payload["proceedsInUSD"])

    def test_fetch_is_canonical_and_download_is_deprecated_alias(self):
        parser = reports.build_parser()
        self.assertEqual(parser.parse_args(["download"]).command, "download")
        self.assertEqual(parser.parse_args(["fetch"]).command, "fetch")
        self.assertEqual(parser.parse_args(["report"]).command, "report")
        help_text = parser.format_help()
        self.assertIn("fetch every available daily", help_text)
        self.assertIn("deprecated alias for fetch", help_text)


class CommandTests(unittest.TestCase):
    def test_bootstrap_403_has_safe_exit_code_and_no_secret_output(self):
        app_path = "/v1/apps/6778701883"
        requests_path = "/v1/apps/6778701883/analyticsReportRequests?limit=200"
        client = FakeClient(
            collections={requests_path: []},
            responses={("GET", app_path): {"data": app_item()}},
            errors={
                ("POST", "/v1/analyticsReportRequests"): reports.APIError(
                    403, "server detail"
                )
            },
        )
        pem = "-----BEGIN PRIVATE KEY-----\nsecret-material\n-----END PRIVATE KEY-----\n"
        environ = {
            "APP_STORE_CONNECT_API_KEY_ID": "secret-key-id",
            "APP_STORE_CONNECT_API_ISSUER_ID": "secret-issuer-id",
            "APP_STORE_CONNECT_API_KEY_BASE64": base64.b64encode(pem.encode()).decode(),
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(reports, "make_token", return_value="secret-jwt"):
            exit_code = reports.main(
                ["--app-id", "6778701883", "bootstrap"],
                environ=environ,
                stdout=stdout,
                stderr=stderr,
                client_factory=lambda _: client,
            )

        self.assertEqual(exit_code, reports.EXIT_PERMISSION)
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertIn("requires an Admin API key", combined)
        for secret in ("secret-key-id", "secret-issuer-id", "secret-material", "secret-jwt"):
            self.assertNotIn(secret, combined)


if __name__ == "__main__":
    unittest.main()
