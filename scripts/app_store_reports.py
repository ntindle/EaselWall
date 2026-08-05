#!/usr/bin/env python3
"""Download and summarize App Store Connect Analytics Reports for EaselWall.

Credentials are read only from the three documented process environment
variables. The script never reads dotenv files and never prints credentials or
the generated JWT.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, TextIO


API_BASE_URL = "https://api.appstoreconnect.apple.com"
API_HOST = "api.appstoreconnect.apple.com"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_ID = "com.ntindle.EaselWall"
REQUIRED_ACCESS_TYPES = ("ONE_TIME_SNAPSHOT", "ONGOING")
TARGET_REPORTS = (
    "App Store Discovery and Engagement Detailed",
    "App Store Downloads Detailed",
    "App Store Purchases Standard",
    "App Store Purchases Detailed",
)
PURCHASES_STANDARD_REPORT = "App Store Purchases Standard"
PURCHASES_DETAILED_REPORT = "App Store Purchases Detailed"
# Compatibility name for callers that previously referred to the sole revenue
# report. Totals now come exclusively from Standard.
PURCHASES_REPORT = PURCHASES_STANDARD_REPORT
DEFAULT_REPORT_OUTPUT = ROOT / "marketing" / "reports" / "app-store-connect"
APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
CHECKSUM_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")
CAMPAIGN_GROUPS = ("tt_organic", "tt_creator", "tt_paid", "unattributed", "other")

EXIT_OK = 0
EXIT_CONFIGURATION = 2
EXIT_PERMISSION = 3
EXIT_API = 4
EXIT_DATA = 5


class ConfigurationError(ValueError):
    """Raised when local credentials or command options are invalid."""


class APIError(RuntimeError):
    """A sanitized App Store Connect API failure."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


class DataError(RuntimeError):
    """Raised when an API response does not have the expected shape."""


class _RejectAPIRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from forwarding the App Store bearer token on redirects."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class Credentials:
    key_id: str
    issuer_id: str
    private_key: str


@dataclass(frozen=True)
class AppInfo:
    app_id: str
    name: str
    bundle_id: str


@dataclass(frozen=True)
class ReportRequest:
    request_id: str
    access_type: str
    stopped_due_to_inactivity: bool = False


@dataclass(frozen=True)
class AnalyticsReport:
    report_id: str
    name: str
    category: str


@dataclass(frozen=True)
class ReportInstance:
    instance_id: str
    granularity: str
    processing_date: date


@dataclass(frozen=True)
class ReportSegment:
    segment_id: str
    checksum: str
    size_in_bytes: int
    signed_url: str


@dataclass
class CampaignTotals:
    purchases: int = 0
    proceeds: Decimal = Decimal("0")
    refund_units: int = 0
    rows: int = 0


@dataclass
class PurchaseSummary:
    year: int
    standard_available: bool = False
    campaign_available: bool = False
    files: int = 0
    files_scanned: int = 0
    datasets_considered: int = 0
    rows: int = 0
    superseded_rows: int = 0
    purchases: int = 0
    proceeds: Decimal = Decimal("0")
    refund_units: int = 0
    dates: set[date] = field(default_factory=set)
    campaign_files: int = 0
    campaign_files_scanned: int = 0
    campaign_datasets_considered: int = 0
    campaign_rows: int = 0
    campaign_superseded_rows: int = 0
    campaigns: dict[str, CampaignTotals] = field(
        default_factory=lambda: {name: CampaignTotals() for name in CAMPAIGN_GROUPS}
    )
    canonical_rows: list[PurchaseRow] = field(default_factory=list)
    campaign_canonical_rows: list[PurchaseRow] = field(default_factory=list)


@dataclass(frozen=True)
class PurchaseDataset:
    processing_date: date
    access_type: str
    request_id: str
    report_id: str
    instance_id: str

    @property
    def rank(self) -> tuple[date, int]:
        access_priority = {
            "ONE_TIME_SNAPSHOT": 1,
            "ONGOING": 2,
            "EXPLICIT": 3,
        }.get(self.access_type, 0)
        return self.processing_date, access_priority

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return self.request_id, self.report_id, self.instance_id, self.access_type


@dataclass(frozen=True)
class PurchaseRow:
    event_date: date
    purchases: int
    proceeds: Decimal
    campaign: str
    source_path: Path


def load_credentials(environ: Mapping[str, str]) -> Credentials:
    """Load and validate credentials without consulting files or keychains."""

    variable_names = (
        "APP_STORE_CONNECT_API_KEY_ID",
        "APP_STORE_CONNECT_API_ISSUER_ID",
        "APP_STORE_CONNECT_API_KEY_BASE64",
    )
    missing = [name for name in variable_names if not environ.get(name, "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise ConfigurationError(f"Missing required environment variable(s): {joined}")

    encoded_key = "".join(environ["APP_STORE_CONNECT_API_KEY_BASE64"].split())
    try:
        private_key = base64.b64decode(encoded_key, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigurationError(
            "APP_STORE_CONNECT_API_KEY_BASE64 is not valid base64-encoded UTF-8"
        ) from exc

    if "BEGIN PRIVATE KEY" not in private_key or "END PRIVATE KEY" not in private_key:
        raise ConfigurationError(
            "APP_STORE_CONNECT_API_KEY_BASE64 does not contain a PEM private key"
        )

    return Credentials(
        key_id=environ["APP_STORE_CONNECT_API_KEY_ID"].strip(),
        issuer_id=environ["APP_STORE_CONNECT_API_ISSUER_ID"].strip(),
        private_key=private_key,
    )


def make_token(credentials: Credentials, now: int | None = None) -> str:
    """Create a short-lived App Store Connect ES256 bearer token."""

    try:
        import jwt
    except ImportError as exc:
        raise ConfigurationError(
            "PyJWT is required; install it with: python3 -m pip install 'PyJWT[crypto]'"
        ) from exc

    issued_at = int(time.time()) if now is None else now
    try:
        token = jwt.encode(
            {
                "iss": credentials.issuer_id,
                "iat": issued_at,
                "exp": issued_at + 19 * 60,
                "aud": "appstoreconnect-v1",
            },
            credentials.private_key,
            algorithm="ES256",
            headers={"kid": credentials.key_id, "typ": "JWT"},
        )
    except Exception as exc:
        raise ConfigurationError(
            "Could not create an ES256 token from the supplied App Store Connect key"
        ) from exc
    return token


def _sanitized_error_message(body: bytes, fallback: str) -> str:
    """Extract non-secret Apple error fields while avoiding raw response dumps."""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return fallback

    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list) or not errors:
        return fallback

    first = errors[0]
    if not isinstance(first, dict):
        return fallback
    # Apple detail strings can echo caller-supplied values or account context.
    # Codes and titles are enough for this narrow CLI and are safer to print.
    parts = [first.get("code"), first.get("title")]
    clean_parts = [str(part).strip()[:300] for part in parts if part]
    return ": ".join(clean_parts) if clean_parts else fallback


class AppStoreConnectClient:
    """Minimal JSON client for the App Store Connect API."""

    def __init__(
        self,
        token: str,
        *,
        opener: Callable[..., Any] | None = None,
        download_opener: Callable[..., Any] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        if opener is None:
            self._opener = urllib.request.build_opener(_RejectAPIRedirects()).open
            self._download_opener = (
                urllib.request.urlopen
                if download_opener is None
                else download_opener
            )
        else:
            self._opener = opener
            self._download_opener = opener if download_opener is None else download_opener
        self._timeout = timeout

    @staticmethod
    def _url(path_or_url: str) -> str:
        if path_or_url.startswith("/"):
            return f"{API_BASE_URL}{path_or_url}"

        parsed = urllib.parse.urlparse(path_or_url)
        if parsed.scheme != "https" or parsed.netloc != API_HOST:
            raise DataError("Refusing to send credentials to a non-App-Store-Connect URL")
        return path_or_url

    def request_json(
        self,
        method: str,
        path_or_url: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path_or_url)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "EaselWall-AppStore-Reports/1.0",
            },
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw_body = response.read()
        except urllib.error.HTTPError as exc:
            raw_body = exc.read()
            fallback = f"App Store Connect returned HTTP {exc.code}"
            raise APIError(exc.code, _sanitized_error_message(raw_body, fallback)) from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)[:300]
            raise APIError(None, f"Could not reach App Store Connect: {reason}") from exc

        try:
            decoded = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataError("App Store Connect returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise DataError("App Store Connect returned a non-object JSON response")
        return decoded

    def iter_collection_pages(self, path: str) -> Iterator[list[dict[str, Any]]]:
        """Yield JSON:API pages while enforcing trusted pagination URLs."""

        next_url: str | None = path
        page_count = 0
        while next_url:
            page_count += 1
            if page_count > 100:
                raise DataError("App Store Connect pagination exceeded 100 pages")
            response = self.request_json("GET", next_url)
            data = response.get("data")
            if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                raise DataError("App Store Connect collection is missing a data list")
            yield data

            links = response.get("links", {})
            if links is None:
                links = {}
            if not isinstance(links, dict):
                raise DataError("App Store Connect collection has invalid pagination links")
            candidate = links.get("next")
            if candidate is not None and not isinstance(candidate, str):
                raise DataError("App Store Connect returned an invalid next-page URL")
            next_url = candidate

    def get_collection(self, path: str) -> list[dict[str, Any]]:
        """Fetch a JSON:API collection, following only trusted pagination URLs."""

        return [item for page in self.iter_collection_pages(path) for item in page]

    def download_signed_url(self, signed_url: str) -> bytes:
        """Download a short-lived report URL without sending the bearer token."""

        parsed = urllib.parse.urlparse(signed_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise DataError("App Store Connect returned an invalid report download URL")
        request = urllib.request.Request(
            signed_url,
            headers={"User-Agent": "EaselWall-AppStore-Reports/1.0"},
        )
        try:
            with self._download_opener(request, timeout=60.0) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise APIError(exc.code, "Report segment download failed") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)[:300]
            raise APIError(None, f"Report segment download failed: {reason}") from exc


def _parse_app(item: dict[str, Any]) -> AppInfo:
    app_id = item.get("id")
    attributes = item.get("attributes", {})
    if not isinstance(app_id, str) or not isinstance(attributes, dict):
        raise DataError("App Store Connect returned an invalid app record")
    name = attributes.get("name")
    bundle_id = attributes.get("bundleId")
    return AppInfo(
        app_id=app_id,
        name=name if isinstance(name, str) else "EaselWall",
        bundle_id=bundle_id if isinstance(bundle_id, str) else DEFAULT_BUNDLE_ID,
    )


def resolve_app(
    client: AppStoreConnectClient,
    *,
    app_id: str | None,
    bundle_id: str,
) -> AppInfo:
    """Resolve an explicit App Store ID or discover EaselWall by bundle ID."""

    if app_id:
        if not APP_ID_PATTERN.fullmatch(app_id):
            raise ConfigurationError("--app-id may contain only letters, numbers, and hyphens")
        response = client.request_json("GET", f"/v1/apps/{app_id}")
        data = response.get("data")
        if not isinstance(data, dict):
            raise DataError("App Store Connect app lookup is missing a data object")
        return _parse_app(data)

    query = urllib.parse.urlencode({"filter[bundleId]": bundle_id, "limit": "2"})
    items = client.get_collection(f"/v1/apps?{query}")
    if not items:
        raise DataError(f"No App Store Connect app found for bundle ID {bundle_id}")
    if len(items) > 1:
        raise DataError(f"Multiple App Store Connect apps found for bundle ID {bundle_id}")
    return _parse_app(items[0])


def list_report_requests(
    client: AppStoreConnectClient, app_id: str
) -> list[ReportRequest]:
    items = client.get_collection(
        f"/v1/apps/{app_id}/analyticsReportRequests?limit=200"
    )
    requests: list[ReportRequest] = []
    for item in items:
        request_id = item.get("id")
        attributes = item.get("attributes", {})
        access_type = attributes.get("accessType") if isinstance(attributes, dict) else None
        stopped = (
            bool(attributes.get("stoppedDueToInactivity", False))
            if isinstance(attributes, dict)
            else False
        )
        if not isinstance(request_id, str) or not isinstance(access_type, str):
            raise DataError("App Store Connect returned an invalid analytics report request")
        requests.append(
            ReportRequest(
                request_id=request_id,
                access_type=access_type,
                stopped_due_to_inactivity=stopped,
            )
        )
    return requests


def create_report_request(
    client: AppStoreConnectClient, app_id: str, access_type: str
) -> ReportRequest:
    response = client.request_json(
        "POST",
        "/v1/analyticsReportRequests",
        {
            "data": {
                "type": "analyticsReportRequests",
                "attributes": {"accessType": access_type},
                "relationships": {
                    "app": {"data": {"type": "apps", "id": app_id}}
                },
            }
        },
    )
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise DataError("App Store Connect create response is missing a request ID")
    attributes = data.get("attributes", {})
    returned_type = attributes.get("accessType") if isinstance(attributes, dict) else None
    if returned_type is not None and returned_type != access_type:
        raise DataError(
            "App Store Connect created a different analytics request type than requested"
        )
    return ReportRequest(
        request_id=data["id"],
        access_type=returned_type if isinstance(returned_type, str) else access_type,
    )


def grouped_requests(
    requests: Sequence[ReportRequest],
) -> dict[str, list[ReportRequest]]:
    grouped = {access_type: [] for access_type in REQUIRED_ACCESS_TYPES}
    for request in requests:
        if request.access_type in grouped:
            grouped[request.access_type].append(request)
    return grouped


def print_status(
    app: AppInfo, requests: Sequence[ReportRequest], output: TextIO
) -> None:
    print(f"App: {app.name} ({app.app_id}; {app.bundle_id})", file=output)
    print(f"Analytics report requests: {len(requests)}", file=output)
    grouped = grouped_requests(requests)
    for access_type in REQUIRED_ACCESS_TYPES:
        matches = grouped[access_type]
        active = [request for request in matches if not request.stopped_due_to_inactivity]
        stopped = [request for request in matches if request.stopped_due_to_inactivity]
        if not active and not stopped:
            print(f"  {access_type}: missing", file=output)
        elif not active:
            ids = ", ".join(request.request_id for request in stopped)
            print(f"  {access_type}: stopped due to inactivity ({ids})", file=output)
        elif len(active) == 1:
            suffix = f"; {len(stopped)} stopped" if stopped else ""
            print(f"  {access_type}: ready ({active[0].request_id}{suffix})", file=output)
        else:
            ids = ", ".join(request.request_id for request in active)
            print(
                f"  {access_type}: warning - {len(active)} active requests ({ids})",
                file=output,
            )


def bootstrap_requests(
    client: AppStoreConnectClient,
    app: AppInfo,
    existing: Sequence[ReportRequest],
    output: TextIO,
) -> list[ReportRequest]:
    """Create one request for each missing type; never modify existing requests."""

    current = list(existing)
    grouped = grouped_requests(current)
    for access_type in REQUIRED_ACCESS_TYPES:
        matches = grouped[access_type]
        active_matches = [
            request for request in matches if not request.stopped_due_to_inactivity
        ]
        stopped_matches = [
            request for request in matches if request.stopped_due_to_inactivity
        ]
        if active_matches:
            if len(active_matches) == 1:
                print(
                    f"Kept existing {access_type} request "
                    f"{active_matches[0].request_id}.",
                    file=output,
                )
            else:
                print(
                    f"Warning: found {len(active_matches)} active {access_type} requests; "
                    "left them unchanged.",
                    file=output,
                )
            continue

        if stopped_matches:
            print(
                f"Found {len(stopped_matches)} stopped {access_type} request(s); "
                "creating the replacement Apple requires.",
                file=output,
            )

        try:
            created = create_report_request(client, app.app_id, access_type)
        except APIError as exc:
            if exc.status != 409:
                raise
            # A concurrent bootstrap may have won the race. Confirm that the
            # desired request now exists before treating the conflict as safe.
            refreshed = list_report_requests(client, app.app_id)
            refreshed_matches = [
                request
                for request in grouped_requests(refreshed)[access_type]
                if not request.stopped_due_to_inactivity
            ]
            if not refreshed_matches:
                raise
            created = refreshed_matches[0]
            current = refreshed
            grouped = grouped_requests(current)
            print(
                f"Another process created {access_type} request {created.request_id}.",
                file=output,
            )
            continue

        current.append(created)
        grouped[access_type].append(created)
        print(f"Created {access_type} request {created.request_id}.", file=output)
    return current


def select_report_request(
    requests: Sequence[ReportRequest], access_type: str = "AUTO"
) -> ReportRequest:
    """Select one active request, preferring ongoing data in automatic mode."""

    active = [request for request in requests if not request.stopped_due_to_inactivity]
    preferred_types = (
        ("ONGOING", "ONE_TIME_SNAPSHOT")
        if access_type == "AUTO"
        else (access_type,)
    )
    for preferred_type in preferred_types:
        matches = sorted(
            (request for request in active if request.access_type == preferred_type),
            key=lambda request: request.request_id,
        )
        if matches:
            return matches[0]
    if access_type == "AUTO":
        raise DataError(
            "No active ONGOING or ONE_TIME_SNAPSHOT analytics report request is available"
        )
    raise DataError(f"No active {access_type} analytics report request is available")


def list_analytics_reports(
    client: AppStoreConnectClient, request_id: str
) -> list[AnalyticsReport]:
    encoded_request_id = urllib.parse.quote(request_id, safe="")
    items = client.get_collection(
        f"/v1/analyticsReportRequests/{encoded_request_id}/reports?limit=200"
    )
    reports: list[AnalyticsReport] = []
    for item in items:
        report_id = item.get("id")
        attributes = item.get("attributes", {})
        name = attributes.get("name") if isinstance(attributes, dict) else None
        category = attributes.get("category") if isinstance(attributes, dict) else None
        if not all(isinstance(value, str) for value in (report_id, name, category)):
            raise DataError("App Store Connect returned an invalid analytics report")
        reports.append(
            AnalyticsReport(report_id=report_id, name=name, category=category)
        )
    return reports


def list_daily_instances(
    client: AppStoreConnectClient, report_id: str
) -> list[ReportInstance]:
    query = urllib.parse.urlencode(
        {"filter[granularity]": "DAILY", "limit": "200"}
    )
    encoded_report_id = urllib.parse.quote(report_id, safe="")
    items = client.get_collection(
        f"/v1/analyticsReports/{encoded_report_id}/instances?{query}"
    )
    instances: list[ReportInstance] = []
    for item in items:
        instance_id = item.get("id")
        attributes = item.get("attributes", {})
        granularity = (
            attributes.get("granularity") if isinstance(attributes, dict) else None
        )
        raw_date = (
            attributes.get("processingDate") if isinstance(attributes, dict) else None
        )
        if not all(isinstance(value, str) for value in (instance_id, granularity, raw_date)):
            raise DataError("App Store Connect returned an invalid report instance")
        if granularity != "DAILY":
            raise DataError("App Store Connect returned a non-daily filtered report instance")
        try:
            processing_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise DataError(
                f"App Store Connect returned an invalid processing date: {raw_date}"
            ) from exc
        instances.append(
            ReportInstance(
                instance_id=instance_id,
                granularity=granularity,
                processing_date=processing_date,
            )
        )
    return instances


def _parse_segment(item: Mapping[str, Any]) -> ReportSegment:
    segment_id = item.get("id")
    attributes = item.get("attributes", {})
    checksum = attributes.get("checksum") if isinstance(attributes, dict) else None
    size = attributes.get("sizeInBytes") if isinstance(attributes, dict) else None
    signed_url = attributes.get("url") if isinstance(attributes, dict) else None
    if not isinstance(segment_id, str) or not isinstance(checksum, str):
        raise DataError("App Store Connect returned an invalid report segment")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise DataError("App Store Connect returned an invalid report segment size")
    if not isinstance(signed_url, str) or not signed_url:
        raise DataError("App Store Connect returned a report segment without a URL")
    if not CHECKSUM_PATTERN.fullmatch(checksum):
        raise DataError("App Store Connect returned an invalid MD5 segment checksum")
    return ReportSegment(
        segment_id=segment_id,
        checksum=checksum.lower(),
        size_in_bytes=size,
        signed_url=signed_url,
    )


def iter_segments(
    client: AppStoreConnectClient, instance_id: str
) -> Iterator[ReportSegment]:
    """Yield segments page-by-page so each signed URL is consumed immediately."""

    encoded_instance_id = urllib.parse.quote(instance_id, safe="")
    path = f"/v1/analyticsReportInstances/{encoded_instance_id}/segments?limit=200"
    seen: dict[str, tuple[str, int]] = {}
    for page in client.iter_collection_pages(path):
        parsed_segments = (_parse_segment(item) for item in page)
        for segment in sorted(
            parsed_segments, key=lambda item: item.segment_id
        ):
            fingerprint = (segment.checksum, segment.size_in_bytes)
            previous = seen.get(segment.segment_id)
            if previous is not None:
                if previous != fingerprint:
                    raise DataError(
                        "App Store Connect returned conflicting metadata for repeated "
                        f"report segment {segment.segment_id}"
                    )
                continue
            seen[segment.segment_id] = fingerprint
            yield segment


def list_segments(
    client: AppStoreConnectClient, instance_id: str
) -> list[ReportSegment]:
    """Materialize segment metadata for diagnostics and offline callers."""

    return list(iter_segments(client, instance_id))


def safe_component(value: str) -> str:
    """Create a deterministic, collision-resistant filesystem component."""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    if not normalized:
        normalized = "item"
    if normalized != value:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        normalized = f"{normalized}-{digest}"
    if len(normalized) > 180:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        normalized = f"{normalized[:171]}-{digest}"
    return normalized


def report_slug(name: str) -> str:
    return safe_component(name.casefold().replace(" ", "-"))


def atomic_write(path: Path, content: bytes) -> None:
    """Atomically replace a private generated file with mode 0600."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary.name, 0o600)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        os.chmod(path, 0o600)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def normalize_tsv(compressed: bytes) -> bytes:
    try:
        decompressed = gzip.decompress(compressed)
    except (EOFError, OSError) as exc:
        raise DataError("Downloaded report segment is not valid gzip data") from exc
    try:
        text = decompressed.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError("Downloaded report segment is not valid UTF-8") from exc
    lines = text.splitlines()
    if lines and "\t" not in lines[0]:
        raise DataError("Downloaded report segment is not tab-delimited")
    normalized = "\n".join(lines)
    if lines:
        normalized += "\n"
    return normalized.encode("utf-8")


def _metadata_path(tsv_path: Path) -> Path:
    return tsv_path.with_suffix(".metadata.json")


def existing_segment_is_verified(
    tsv_path: Path, expected_metadata: Mapping[str, Any]
) -> bool:
    metadata_path = _metadata_path(tsv_path)
    gzip_path = tsv_path.with_suffix(".txt.gz")
    if (
        not tsv_path.is_file()
        or not metadata_path.is_file()
        or not gzip_path.is_file()
    ):
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        content = tsv_path.read_bytes()
        compressed = gzip_path.read_bytes()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict):
        return False
    for key, value in expected_metadata.items():
        if metadata.get(key) != value:
            return False
    expected_sha256 = metadata.get("decompressedSha256")
    expected_decompressed_size = metadata.get("decompressedSizeInBytes")
    try:
        normalized_from_gzip = normalize_tsv(compressed)
    except DataError:
        return False
    return (
        isinstance(expected_sha256, str)
        and isinstance(expected_decompressed_size, int)
        and expected_decompressed_size == len(normalized_from_gzip)
        and content == normalized_from_gzip
        and hashlib.sha256(normalized_from_gzip).hexdigest() == expected_sha256
        and len(compressed) == expected_metadata["compressedSizeInBytes"]
        and hashlib.md5(compressed, usedforsecurity=False).hexdigest()
        == expected_metadata["compressedMd5"]
    )


def download_segment(
    client: AppStoreConnectClient,
    *,
    request: ReportRequest,
    report: AnalyticsReport,
    instance: ReportInstance,
    segment: ReportSegment,
    output_dir: Path,
    output: TextIO,
) -> tuple[Path, bool]:
    """Download, verify, decompress, and atomically store one segment.

    Returns the TSV path and whether network content was downloaded. Signed URLs
    are intentionally excluded from paths, metadata, and messages.
    """

    destination = (
        output_dir
        / "downloads"
        / request.access_type.casefold()
        / safe_component(request.request_id)
        / report_slug(report.name)
        / instance.processing_date.isoformat()
        / safe_component(instance.instance_id)
        / f"segment-{safe_component(segment.segment_id)}.tsv"
    )
    expected_metadata = {
        "schemaVersion": 1,
        "requestId": request.request_id,
        "accessType": request.access_type,
        "reportId": report.report_id,
        "reportName": report.name,
        "category": report.category,
        "instanceId": instance.instance_id,
        "granularity": instance.granularity,
        "processingDate": instance.processing_date.isoformat(),
        "segmentId": segment.segment_id,
        "compressedSizeInBytes": segment.size_in_bytes,
        "compressedMd5": segment.checksum,
    }
    if existing_segment_is_verified(destination, expected_metadata):
        print(f"  Reused verified {destination.relative_to(output_dir)}", file=output)
        return destination, False

    compressed = client.download_signed_url(segment.signed_url)
    if len(compressed) != segment.size_in_bytes:
        raise DataError(
            f"Report segment {segment.segment_id} size mismatch "
            f"(expected {segment.size_in_bytes}, got {len(compressed)})"
        )
    digest = hashlib.md5(compressed, usedforsecurity=False).hexdigest()
    if digest != segment.checksum:
        raise DataError(f"Report segment {segment.segment_id} checksum mismatch")
    tsv = normalize_tsv(compressed)
    metadata = {
        **expected_metadata,
        "decompressedSizeInBytes": len(tsv),
        "decompressedSha256": hashlib.sha256(tsv).hexdigest(),
    }

    gzip_destination = destination.with_suffix(".txt.gz")
    replaced = (
        destination.exists()
        or gzip_destination.exists()
        or _metadata_path(destination).exists()
    )
    atomic_write(gzip_destination, compressed)
    atomic_write(destination, tsv)
    atomic_write(
        _metadata_path(destination),
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    action = "Replaced" if replaced else "Downloaded"
    print(f"  {action} {destination.relative_to(output_dir)}", file=output)
    return destination, True


def download_target_reports(
    client: AppStoreConnectClient,
    requests: Sequence[ReportRequest],
    *,
    access_type: str,
    output_dir: Path,
    output: TextIO,
) -> list[Path]:
    request = select_report_request(requests, access_type)
    print(
        f"Using active {request.access_type} request {request.request_id}.",
        file=output,
    )
    reports = list_analytics_reports(client, request.request_id)
    print(f"Available analytics reports: {len(reports)}", file=output)

    reports_by_name: dict[str, list[AnalyticsReport]] = {}
    for report in reports:
        reports_by_name.setdefault(report.name, []).append(report)

    output_paths: list[Path] = []
    for target_name in TARGET_REPORTS:
        matches = sorted(
            reports_by_name.get(target_name, []), key=lambda report: report.report_id
        )
        if not matches:
            print(f"{target_name}: not available yet", file=output)
            continue
        if len(matches) > 1:
            print(
                f"{target_name}: warning - {len(matches)} matches; using {matches[0].report_id}",
                file=output,
            )
        report = matches[0]
        instances = sorted(
            list_daily_instances(client, report.report_id),
            key=lambda instance: (instance.processing_date, instance.instance_id),
        )
        if not instances:
            print(f"{target_name}: no DAILY instances yet", file=output)
            continue

        print(
            f"{target_name}: {len(instances)} DAILY instance(s), "
            f"{instances[0].processing_date.isoformat()} through "
            f"{instances[-1].processing_date.isoformat()}",
            file=output,
        )
        for instance in instances:
            # Apple says these signed URLs expire after five minutes, so fetch
            # every returned segment before doing work on another instance.
            segment_count = 0
            for segment in iter_segments(client, instance.instance_id):
                segment_count += 1
                path, _ = download_segment(
                    client,
                    request=request,
                    report=report,
                    instance=instance,
                    segment=segment,
                    output_dir=output_dir,
                    output=output,
                )
                output_paths.append(path)
            if not segment_count:
                print(f"  Instance {instance.instance_id}: no segments", file=output)
    print(f"Verified local report segments: {len(output_paths)}", file=output)
    return output_paths


def coarse_campaign(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    for campaign in ("tt_organic", "tt_creator", "tt_paid"):
        if normalized == campaign or normalized.startswith(f"{campaign}_"):
            return campaign
    if normalized in ("", "null", "unknown", "unavailable"):
        return "unattributed"
    return "other"


def parse_decimal_field(
    raw_value: str | None, *, field_name: str, path: Path, line_number: int
) -> Decimal:
    value = (raw_value or "").strip()
    if not value:
        return Decimal("0")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise DataError(
            f"Invalid {field_name!r} value in {path} at line {line_number}: {value!r}"
        ) from exc
    if not parsed.is_finite():
        raise DataError(
            f"Non-finite {field_name!r} value in {path} at line {line_number}"
        )
    return parsed


def _load_segment_metadata(path: Path) -> dict[str, Any] | None:
    metadata_path = _metadata_path(path)
    if not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError(f"Invalid report metadata file: {metadata_path}") from exc
    if not isinstance(metadata, dict):
        raise DataError(f"Invalid report metadata object: {metadata_path}")
    return metadata


def _tsv_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.exists():
        return []
    if not input_path.is_dir():
        raise ConfigurationError(f"Report input is not a file or directory: {input_path}")
    return sorted(path for path in input_path.rglob("*.tsv") if path.is_file())


def _purchase_report_kind(
    path: Path,
    metadata: Mapping[str, Any] | None,
    fields: set[str],
    *,
    explicit_file: bool,
) -> str | None:
    """Classify purchase files without mixing Standard and Detailed totals."""

    if metadata is not None:
        report_name = metadata.get("reportName")
        if report_name in (PURCHASES_STANDARD_REPORT, PURCHASES_DETAILED_REPORT):
            return str(report_name)
        return None

    normalized_path = path.as_posix().casefold()
    if report_slug(PURCHASES_DETAILED_REPORT) in normalized_path:
        return PURCHASES_DETAILED_REPORT
    if report_slug(PURCHASES_STANDARD_REPORT) in normalized_path:
        return PURCHASES_STANDARD_REPORT
    if explicit_file:
        # Campaign is Detailed-only in Apple's schema. Never let a standalone
        # campaign export become the authoritative revenue source.
        return (
            PURCHASES_DETAILED_REPORT
            if "Campaign" in fields
            else PURCHASES_STANDARD_REPORT
        )
    if {"Purchases", "Proceeds in USD"}.issubset(fields):
        return (
            PURCHASES_DETAILED_REPORT
            if "Campaign" in fields
            else PURCHASES_STANDARD_REPORT
        )
    return None


def _verify_managed_tsv(path: Path, metadata: Mapping[str, Any]) -> None:
    """Verify a managed TSV against its preserved Apple-verified gzip."""

    compressed_md5 = metadata.get("compressedMd5")
    compressed_size = metadata.get("compressedSizeInBytes")
    decompressed_sha256 = metadata.get("decompressedSha256")
    decompressed_size = metadata.get("decompressedSizeInBytes")
    if not isinstance(compressed_md5, str) or not CHECKSUM_PATTERN.fullmatch(
        compressed_md5
    ):
        raise DataError(f"Missing compressedMd5 in {_metadata_path(path)}")
    if not isinstance(compressed_size, int) or isinstance(compressed_size, bool):
        raise DataError(f"Missing compressedSizeInBytes in {_metadata_path(path)}")
    if not isinstance(decompressed_sha256, str) or not re.fullmatch(
        r"[a-fA-F0-9]{64}", decompressed_sha256
    ):
        raise DataError(f"Missing decompressedSha256 in {_metadata_path(path)}")
    if not isinstance(decompressed_size, int) or isinstance(decompressed_size, bool):
        raise DataError(f"Missing decompressedSizeInBytes in {_metadata_path(path)}")

    gzip_path = path.with_suffix(".txt.gz")
    try:
        compressed = gzip_path.read_bytes()
        content = path.read_bytes()
    except OSError as exc:
        raise DataError(f"Missing managed report payload for {path}") from exc
    if len(compressed) != compressed_size:
        raise DataError(f"Compressed size mismatch for {gzip_path}")
    if (
        hashlib.md5(compressed, usedforsecurity=False).hexdigest()
        != compressed_md5.casefold()
    ):
        raise DataError(f"Compressed checksum mismatch for {gzip_path}")
    normalized = normalize_tsv(compressed)
    if normalized != content:
        raise DataError(f"TSV does not match its verified gzip for {path}")
    if len(normalized) != decompressed_size:
        raise DataError(f"TSV size mismatch for {path}")
    if hashlib.sha256(normalized).hexdigest() != decompressed_sha256.casefold():
        raise DataError(f"TSV checksum mismatch for {path}")


def _event_financial_signature(
    rows: Sequence[PurchaseRow], *, detailed: bool
) -> tuple[Any, ...]:
    """Return a segmentation-independent financial signature for one event date."""

    if not detailed:
        return (
            sum((row.purchases for row in rows), 0),
            sum((row.proceeds for row in rows), Decimal("0")),
        )
    grouped: dict[str, tuple[int, Decimal]] = {}
    for row in rows:
        purchases, proceeds = grouped.get(row.campaign, (0, Decimal("0")))
        grouped[row.campaign] = purchases + row.purchases, proceeds + row.proceeds
    return tuple(
        (campaign, purchases, proceeds)
        for campaign, (purchases, proceeds) in sorted(grouped.items())
    )


def _canonical_rows(
    rows_by_dataset: Mapping[PurchaseDataset, list[PurchaseRow]],
    *,
    year: int,
    report_name: str,
) -> tuple[list[PurchaseRow], int, set[Path]]:
    """Select correction-safe rows, rejecting ambiguous same-rank conflicts."""

    datasets_by_event_date: dict[date, set[PurchaseDataset]] = {}
    for dataset, rows in rows_by_dataset.items():
        for row in rows:
            if row.event_date.year == year:
                datasets_by_event_date.setdefault(row.event_date, set()).add(dataset)

    canonical: list[PurchaseRow] = []
    selected_paths: set[Path] = set()
    superseded_rows = 0
    detailed = report_name == PURCHASES_DETAILED_REPORT
    for event_date in sorted(datasets_by_event_date):
        candidates = datasets_by_event_date[event_date]
        winning_rank = max(dataset.rank for dataset in candidates)
        tied = sorted(
            (dataset for dataset in candidates if dataset.rank == winning_rank),
            key=lambda dataset: dataset.identity,
        )
        if len(tied) > 1:
            signatures = {
                _event_financial_signature(
                    [
                        row
                        for row in rows_by_dataset[dataset]
                        if row.event_date == event_date
                    ],
                    detailed=detailed,
                )
                for dataset in tied
            }
            if len(signatures) > 1:
                raise DataError(
                    f"Conflicting {report_name} instances share processingDate "
                    f"{winning_rank[0].isoformat()} for event Date "
                    f"{event_date.isoformat()}"
                )
        selected_dataset = tied[0]
        selected_rows = [
            row
            for row in rows_by_dataset[selected_dataset]
            if row.event_date == event_date
        ]
        superseded_rows += sum(
            1
            for dataset in candidates
            if dataset != selected_dataset
            for row in rows_by_dataset[dataset]
            if row.event_date == event_date
        )
        canonical.extend(selected_rows)
        selected_paths.update(row.source_path for row in selected_rows)
    return canonical, superseded_rows, selected_paths


def summarize_purchases(input_path: Path, *, year: int) -> PurchaseSummary:
    """Build independent Standard totals and Detailed campaign attribution."""

    summary = PurchaseSummary(year=year)
    explicit_file = input_path.is_file()
    base_fields = {"Date", "Purchases", "Proceeds in USD"}
    report_names = (PURCHASES_STANDARD_REPORT, PURCHASES_DETAILED_REPORT)
    rows_by_report: dict[str, dict[PurchaseDataset, list[PurchaseRow]]] = {
        report_name: {} for report_name in report_names
    }
    unmanaged_files: dict[str, list[Path]] = {
        report_name: [] for report_name in report_names
    }
    file_counts = {report_name: 0 for report_name in report_names}
    seen_segments: dict[tuple[str, str, str], tuple[str, Path]] = {}
    dataset_provenance: dict[
        tuple[str, str, str], tuple[str, str, str]
    ] = {}

    for path in _tsv_paths(input_path):
        try:
            handle = path.open("r", encoding="utf-8-sig", newline="")
        except (OSError, UnicodeDecodeError) as exc:
            raise DataError(f"Could not read report file: {path}") from exc
        with handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fields = set(reader.fieldnames or [])
            metadata = _load_segment_metadata(path)
            report_name = _purchase_report_kind(
                path, metadata, fields, explicit_file=explicit_file
            )
            if report_name is None:
                continue
            required_fields = set(base_fields)
            if report_name == PURCHASES_DETAILED_REPORT:
                required_fields.add("Campaign")
            if not required_fields.issubset(fields):
                missing = ", ".join(sorted(required_fields - fields))
                raise DataError(f"Purchase report {path} is missing column(s): {missing}")

            if metadata is not None:
                _verify_managed_tsv(path, metadata)
                raw_processing_date = metadata.get("processingDate")
                access_type = metadata.get("accessType")
                request_id = metadata.get("requestId")
                report_id = metadata.get("reportId")
                instance_id = metadata.get("instanceId")
                segment_id = metadata.get("segmentId")
                if not isinstance(raw_processing_date, str):
                    raise DataError(f"Missing processingDate in {_metadata_path(path)}")
                try:
                    processing_date = date.fromisoformat(raw_processing_date)
                except ValueError as exc:
                    raise DataError(
                        f"Invalid processingDate in {_metadata_path(path)}"
                    ) from exc
                if access_type not in REQUIRED_ACCESS_TYPES:
                    raise DataError(f"Invalid accessType in {_metadata_path(path)}")
                segment_values = (report_id, instance_id, segment_id)
                if not all(isinstance(value, str) and value for value in segment_values):
                    raise DataError(f"Missing dataset identity in {_metadata_path(path)}")
                if not isinstance(request_id, str) or not request_id:
                    raise DataError(f"Missing requestId in {_metadata_path(path)}")
                provenance_key = (report_name, str(report_id), str(instance_id))
                provenance = (
                    raw_processing_date,
                    str(access_type),
                    request_id,
                )
                previous_provenance = dataset_provenance.get(provenance_key)
                if previous_provenance is not None and previous_provenance != provenance:
                    raise DataError(
                        f"Inconsistent metadata for report instance {instance_id}"
                    )
                dataset_provenance[provenance_key] = provenance
                dataset = PurchaseDataset(
                    processing_date=processing_date,
                    access_type=str(access_type),
                    request_id=request_id,
                    report_id=str(report_id),
                    instance_id=str(instance_id),
                )
                segment_key = tuple(str(value) for value in segment_values)
                segment_fingerprint = str(metadata["compressedMd5"]).casefold()
                previous = seen_segments.get(segment_key)
                if previous is not None:
                    if previous[0] != segment_fingerprint:
                        raise DataError(
                            f"Conflicting duplicate segment {metadata['segmentId']}"
                        )
                    continue
                seen_segments[segment_key] = (segment_fingerprint, path)

            file_counts[report_name] += 1
            file_rows: list[PurchaseRow] = []
            for line_number, row in enumerate(reader, start=2):
                raw_date = (row.get("Date") or "").strip()
                try:
                    row_date = date.fromisoformat(raw_date)
                except ValueError as exc:
                    raise DataError(
                        f"Invalid 'Date' value in {path} at line {line_number}: "
                        f"{raw_date!r}"
                    ) from exc
                purchases_decimal = parse_decimal_field(
                    row.get("Purchases"),
                    field_name="Purchases",
                    path=path,
                    line_number=line_number,
                )
                if purchases_decimal != purchases_decimal.to_integral_value():
                    raise DataError(
                        f"Non-integer 'Purchases' value in {path} at line {line_number}"
                    )
                file_rows.append(
                    PurchaseRow(
                        event_date=row_date,
                        purchases=int(purchases_decimal),
                        proceeds=parse_decimal_field(
                            row.get("Proceeds in USD"),
                            field_name="Proceeds in USD",
                            path=path,
                            line_number=line_number,
                        ),
                        campaign=(
                            coarse_campaign(row.get("Campaign"))
                            if report_name == PURCHASES_DETAILED_REPORT
                            else "unattributed"
                        ),
                        source_path=path,
                    )
                )

            if metadata is None:
                unmanaged_files[report_name].append(path)
                processing_date = (
                    max(row.event_date for row in file_rows)
                    if file_rows
                    else date(year, 1, 1)
                )
                dataset = PurchaseDataset(
                    processing_date=processing_date,
                    access_type="EXPLICIT",
                    request_id=path.as_posix(),
                    report_id=report_name,
                    instance_id=path.as_posix(),
                )
            rows_by_report[report_name].setdefault(dataset, []).extend(file_rows)

    for report_name in report_names:
        if unmanaged_files[report_name] and file_counts[report_name] > 1:
            raise DataError(
                f"Cannot safely combine unmetadataed {report_name} TSVs with other "
                "files; pass one TSV or use files produced by the fetch command"
            )

    summary.standard_available = file_counts[PURCHASES_STANDARD_REPORT] > 0
    summary.campaign_available = file_counts[PURCHASES_DETAILED_REPORT] > 0
    summary.files_scanned = file_counts[PURCHASES_STANDARD_REPORT]
    summary.campaign_files_scanned = file_counts[PURCHASES_DETAILED_REPORT]
    summary.datasets_considered = len(rows_by_report[PURCHASES_STANDARD_REPORT])
    summary.campaign_datasets_considered = len(
        rows_by_report[PURCHASES_DETAILED_REPORT]
    )

    standard_rows, summary.superseded_rows, standard_paths = _canonical_rows(
        rows_by_report[PURCHASES_STANDARD_REPORT],
        year=year,
        report_name=PURCHASES_STANDARD_REPORT,
    )
    summary.canonical_rows.extend(standard_rows)
    summary.files = len(standard_paths)
    for row in standard_rows:
        summary.rows += 1
        summary.purchases += row.purchases
        summary.proceeds += row.proceeds
        summary.refund_units += max(-row.purchases, 0)
        summary.dates.add(row.event_date)

    campaign_rows, summary.campaign_superseded_rows, campaign_paths = _canonical_rows(
        rows_by_report[PURCHASES_DETAILED_REPORT],
        year=year,
        report_name=PURCHASES_DETAILED_REPORT,
    )
    summary.campaign_canonical_rows.extend(campaign_rows)
    summary.campaign_files = len(campaign_paths)
    for row in campaign_rows:
        summary.campaign_rows += 1
        campaign = summary.campaigns[row.campaign]
        campaign.rows += 1
        campaign.purchases += row.purchases
        campaign.proceeds += row.proceeds
        campaign.refund_units += max(-row.purchases, 0)
    return summary


def format_money(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    return f"{sign}${abs(rounded):,.2f}"


def goal_progress(value: Decimal, goal: Decimal, *, width: int = 20) -> tuple[str, Decimal]:
    ratio = value / goal
    visible_ratio = min(max(ratio, Decimal("0")), Decimal("1"))
    filled = int(visible_ratio * width)
    bar = "#" * filled + "-" * (width - filled)
    return bar, ratio * Decimal("100")


def _format_percent(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):,.1f}%"


def terminal_report(
    summary: PurchaseSummary, goal: Decimal, output: TextIO
) -> None:
    if summary.standard_available:
        bar, percentage = goal_progress(summary.proceeds, goal)
        print(
            f"Estimated revenue [{bar}] {format_money(summary.proceeds)} / "
            f"{format_money(goal)} ({_format_percent(percentage)})",
            file=output,
        )
        print(
            f"{summary.year}: {summary.purchases} net purchase(s), "
            f"{summary.refund_units} refunded unit(s), {summary.rows} canonical row(s), "
            f"{summary.superseded_rows} superseded row(s) from Standard",
            file=output,
        )
    else:
        print(
            "Estimated revenue: unknown/pending - no verified App Store Purchases "
            "Standard dataset is available.",
            file=output,
        )
    populated = [
        (name, totals)
        for name, totals in summary.campaigns.items()
        if totals.rows or totals.purchases or totals.proceeds
    ]
    if populated:
        campaigns = ", ".join(
            f"{name} {totals.purchases}/{format_money(totals.proceeds)}"
            for name, totals in populated
        )
        print(
            "Campaign attribution (Detailed; privacy-limited): "
            + campaigns,
            file=output,
        )
    elif summary.campaign_available:
        print(
            "Campaign attribution (Detailed; privacy-limited): "
            "no exposed rows",
            file=output,
        )
    else:
        print(
            "Campaign attribution (Detailed; privacy-limited): "
            "unavailable; "
            "rows may be absent at low volume",
            file=output,
        )


def markdown_report(
    summary: PurchaseSummary, *, goal: Decimal, as_of: date
) -> str:
    if not summary.standard_available:
        return "\n".join(
            [
                f"# EaselWall App Store weekly report - {as_of.isoformat()}",
                "",
                "- Revenue status: **unknown/pending**",
                "- App Store Purchases Standard: no verified dataset available",
                "- No zero-dollar result was inferred from missing data.",
                "",
                "Campaign attribution comes from App Store Purchases Detailed and is "
                "privacy-limited; it cannot replace the missing "
                "Standard total.",
                "",
            ]
        )

    bar, percentage = goal_progress(summary.proceeds, goal)
    if summary.dates:
        first_date = min(summary.dates).isoformat()
        last_date = max(summary.dates).isoformat()
        data_period = first_date if first_date == last_date else f"{first_date} to {last_date}"
    else:
        data_period = "No dated purchase rows"

    lines = [
        f"# EaselWall App Store weekly report - {as_of.isoformat()}",
        "",
        f"- Reporting year: {summary.year}",
        f"- Data period: {data_period}",
        f"- Net purchases: {summary.purchases}",
        f"- Refunded units: {summary.refund_units}",
        f"- Estimated net proceeds: {format_money(summary.proceeds)}",
        f"- Developer fee goal: {format_money(goal)}",
        f"- Goal progress: `[{bar}]` {_format_percent(percentage)}",
        f"- Canonical Standard rows: {summary.rows} across {summary.files} selected file(s)",
        f"- Superseded Standard rows excluded: {summary.superseded_rows}",
        f"- Standard source archive: {summary.files_scanned} file(s), "
        f"{summary.datasets_considered} logical dataset(s)",
        "",
        "## Campaign attribution (privacy-limited)",
        "",
        "Campaign rows come only from App Store Purchases Detailed. Apple may "
        "suppress them at low volume, and they are never added to the Standard total.",
        f"Detailed source archive: {summary.campaign_files_scanned} file(s), "
        f"{summary.campaign_datasets_considered} logical dataset(s), "
        f"{summary.campaign_superseded_rows} superseded row(s).",
        "",
    ]
    if summary.campaign_available:
        lines.extend(
            [
                "| Campaign | Net purchases | Refunded units | Net proceeds | Rows |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for name in CAMPAIGN_GROUPS:
            totals = summary.campaigns[name]
            lines.append(
                f"| `{name}` | {totals.purchases} | {totals.refund_units} | "
                f"{format_money(totals.proceeds)} | {totals.rows} |"
            )
    else:
        lines.append(
            "**Status: unavailable or not yet exposed by Apple.** No campaign TSV "
            "was written; do not interpret this as zero attributed purchases."
        )
    lines.extend(
        [
            "",
            "Net purchases and proceeds are signed sums. Apple's negative purchase "
            "rows are refunds; partial refunds can have zero purchases and negative proceeds.",
            "Standard totals and Detailed campaign attribution are canonicalized "
            "independently. For each event Date, the newest Apple processingDate wins. "
            "ONGOING wins over ONE_TIME_SNAPSHOT on an exact tie; conflicting instances "
            "at the same remaining rank stop the report instead of choosing a request ID.",
            "",
        ]
    )
    return "\n".join(lines)


def canonical_purchase_tsv(summary: PurchaseSummary) -> str:
    """Materialize authoritative Standard rows into a deterministic TSV."""

    if not summary.standard_available:
        raise DataError(
            "App Store Purchases Standard is unavailable; revenue is unknown/pending"
        )
    grouped: dict[date, tuple[int, Decimal]] = {}
    for row in summary.canonical_rows:
        purchases, proceeds = grouped.get(row.event_date, (0, Decimal("0")))
        grouped[row.event_date] = purchases + row.purchases, proceeds + row.proceeds
    lines = ["Date\tPurchases\tProceeds in USD"]
    for event_date, (purchases, proceeds) in sorted(grouped.items()):
        lines.append(f"{event_date.isoformat()}\t{purchases}\t{proceeds}")
    return "\n".join(lines) + "\n"


def canonical_campaign_tsv(summary: PurchaseSummary) -> str:
    """Materialize privacy-limited Detailed rows separately."""

    grouped: dict[tuple[date, str], tuple[int, Decimal]] = {}
    for row in summary.campaign_canonical_rows:
        key = (row.event_date, row.campaign)
        purchases, proceeds = grouped.get(key, (0, Decimal("0")))
        grouped[key] = purchases + row.purchases, proceeds + row.proceeds
    lines = ["Date\tCampaign\tPurchases\tProceeds in USD"]
    for (event_date, campaign), (purchases, proceeds) in sorted(grouped.items()):
        lines.append(
            f"{event_date.isoformat()}\t{campaign}\t{purchases}\t{proceeds}"
        )
    return "\n".join(lines) + "\n"


def write_canonical_purchases(path: Path, summary: PurchaseSummary) -> bool:
    content = canonical_purchase_tsv(summary).encode("utf-8")
    if path.is_file():
        try:
            if path.read_bytes() == content:
                return False
        except OSError:
            pass
    atomic_write(path, content)
    return True


def write_canonical_campaigns(path: Path, summary: PurchaseSummary) -> bool:
    content = canonical_campaign_tsv(summary).encode("utf-8")
    if path.is_file():
        try:
            if path.read_bytes() == content:
                return False
        except OSError:
            pass
    atomic_write(path, content)
    return True


def write_weekly_report(path: Path, content: str) -> bool:
    encoded = content.encode("utf-8")
    if path.is_file():
        try:
            if path.read_bytes() == encoded:
                return False
        except OSError:
            pass
    atomic_write(path, encoded)
    return True


def write_latest_summary(
    output_dir: Path,
    summary: PurchaseSummary,
    *,
    request: ReportRequest,
) -> Path:
    """Write current availability and proceeds for one isolated request scope."""

    payload = {
        "generatedOn": date.today().isoformat(),
        "scope": {
            "accessType": request.access_type,
            "requestId": request.request_id,
            "year": summary.year,
        },
        "totalsSource": PURCHASES_STANDARD_REPORT,
        "standardDataAvailable": summary.standard_available,
        "purchaseFiles": summary.files,
        "purchaseRows": summary.rows,
        "supersededRows": summary.superseded_rows,
        "netPurchases": summary.purchases if summary.standard_available else None,
        "refundUnits": summary.refund_units if summary.standard_available else None,
        "proceedsInUSD": str(summary.proceeds) if summary.standard_available else None,
        "campaignSource": PURCHASES_DETAILED_REPORT,
        "campaignDataAvailable": summary.campaign_available,
        "campaignDataPrivacyLimited": True,
        "campaignDataPrivacyThresholded": True,
        "campaignRows": summary.campaign_rows,
        "campaignSupersededRows": summary.campaign_superseded_rows,
        "campaigns": {
            name: {
                "netPurchases": totals.purchases,
                "refundUnits": totals.refund_units,
                "proceedsInUSD": str(totals.proceeds),
                "rows": totals.rows,
            }
            for name, totals in summary.campaigns.items()
        },
    }
    path = output_dir / "latest-summary.json"
    atomic_write(
        path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return path


def parse_iso_date(value: str, option_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError(f"{option_name} must use YYYY-MM-DD format") from exc


def parse_goal(value: str) -> Decimal:
    try:
        goal = Decimal(value)
    except InvalidOperation as exc:
        raise ConfigurationError("--goal must be a decimal number") from exc
    if not goal.is_finite() or goal <= 0:
        raise ConfigurationError("--goal must be greater than zero")
    return goal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and summarize EaselWall App Store analytics reports."
    )
    parser.add_argument(
        "--app-id",
        help="explicit App Store Connect app ID; default: discover by bundle ID",
    )
    parser.add_argument(
        "--bundle-id",
        default=DEFAULT_BUNDLE_ID,
        help=f"bundle ID used for discovery (default: {DEFAULT_BUNDLE_ID})",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show analytics report request state")
    commands.add_parser(
        "bootstrap", help="create each missing snapshot and ongoing report request"
    )

    for command_name in ("fetch", "download"):
        command_help = "fetch every available daily target report instance"
        if command_name == "download":
            command_help = "deprecated alias for fetch"
        download = commands.add_parser(command_name, help=command_help)
        download.add_argument(
            "--access-type",
            choices=("AUTO", "ONGOING", "ONE_TIME_SNAPSHOT"),
            default="AUTO",
            help="active request type (default: AUTO, preferring ONGOING)",
        )
        download.add_argument(
            "--output-dir",
            type=Path,
            default=DEFAULT_REPORT_OUTPUT,
            help=f"private output root (default: {DEFAULT_REPORT_OUTPUT})",
        )

    report = commands.add_parser(
        "report", help="summarize purchases and write a weekly Markdown report"
    )
    report.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_REPORT_OUTPUT / "downloads",
        help="purchase TSV file or report archive directory to scan",
    )
    report.add_argument(
        "--output",
        type=Path,
        help="Markdown output path; default: private weekly directory",
    )
    report.add_argument(
        "--canonical-output",
        type=Path,
        help="canonical purchase TSV output path; default: private canonical directory",
    )
    report.add_argument(
        "--campaign-output",
        type=Path,
        help=(
            "privacy-limited campaign TSV path; default: private "
            "canonical directory"
        ),
    )
    report.add_argument(
        "--as-of",
        help="report date in YYYY-MM-DD format (default: today)",
    )
    report.add_argument(
        "--year",
        type=int,
        help="purchase year to include (default: year from --as-of)",
    )
    report.add_argument(
        "--goal",
        default="99",
        help="annual proceeds goal in USD (default: 99)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    client_factory: Callable[[str], AppStoreConnectClient] = AppStoreConnectClient,
) -> int:
    args = build_parser().parse_args(argv)
    environment = os.environ if environ is None else environ

    try:
        if args.command == "report":
            as_of = (
                date.today()
                if args.as_of is None
                else parse_iso_date(args.as_of, "--as-of")
            )
            year = as_of.year if args.year is None else args.year
            if not 2000 <= year <= 9999:
                raise ConfigurationError("--year must be between 2000 and 9999")
            goal = parse_goal(args.goal)
            summary = summarize_purchases(args.input, year=year)
            terminal_report(summary, goal, stdout)
            if not summary.standard_available:
                raise DataError(
                    "App Store Purchases Standard is not available yet; revenue is "
                    "unknown/pending and no report was written"
                )
            canonical_path = (
                args.canonical_output
                if args.canonical_output is not None
                else DEFAULT_REPORT_OUTPUT
                / "canonical"
                / f"purchases-{year}.tsv"
            )
            canonical_changed = write_canonical_purchases(canonical_path, summary)
            canonical_action = "Wrote" if canonical_changed else "Unchanged"
            print(
                f"{canonical_action} canonical purchase TSV: {canonical_path}",
                file=stdout,
            )
            campaign_path = (
                args.campaign_output
                if args.campaign_output is not None
                else DEFAULT_REPORT_OUTPUT
                / "canonical"
                / f"campaigns-{year}.tsv"
            )
            if summary.campaign_available:
                campaign_changed = write_canonical_campaigns(campaign_path, summary)
                campaign_action = "Wrote" if campaign_changed else "Unchanged"
                print(
                    f"{campaign_action} privacy-limited campaign TSV: "
                    f"{campaign_path}",
                    file=stdout,
                )
            else:
                print(
                    "Skipped campaign TSV: App Store Purchases Detailed is unavailable; "
                    "campaign rows may be absent at low volume.",
                    file=stdout,
                )
            report_path = (
                args.output
                if args.output is not None
                else DEFAULT_REPORT_OUTPUT
                / "weekly"
                / f"easelwall-app-store-{as_of.isoformat()}.md"
            )
            content = markdown_report(summary, goal=goal, as_of=as_of)
            changed = write_weekly_report(report_path, content)
            action = "Wrote" if changed else "Unchanged"
            print(f"{action} Markdown report: {report_path}", file=stdout)
            return EXIT_OK

        credentials = load_credentials(environment)
        token = make_token(credentials)
        client = client_factory(token)
        app = resolve_app(client, app_id=args.app_id, bundle_id=args.bundle_id)
        requests = list_report_requests(client, app.app_id)

        if args.command == "status":
            print_status(app, requests, stdout)
        elif args.command == "bootstrap":
            updated = bootstrap_requests(client, app, requests, stdout)
            print_status(app, updated, stdout)
        elif args.command in ("download", "fetch"):
            print(f"App: {app.name} ({app.app_id}; {app.bundle_id})", file=stdout)
            selected_request = select_report_request(requests, args.access_type)
            download_target_reports(
                client,
                requests,
                access_type=args.access_type,
                output_dir=args.output_dir,
                output=stdout,
            )
            purchase_scope = (
                args.output_dir
                / "downloads"
                / selected_request.access_type.casefold()
                / safe_component(selected_request.request_id)
            )
            purchase_summary = summarize_purchases(
                purchase_scope, year=date.today().year
            )
            terminal_report(purchase_summary, Decimal("99"), stdout)
            summary_path = write_latest_summary(
                args.output_dir,
                purchase_summary,
                request=selected_request,
            )
            print(f"Wrote private summary: {summary_path}", file=stdout)
        return EXIT_OK
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=stderr)
        return EXIT_CONFIGURATION
    except APIError as exc:
        if exc.status == 403:
            if args.command == "bootstrap":
                print(
                    "Permission denied by App Store Connect (HTTP 403). "
                    "Creating Analytics Reports requires an Admin API key; "
                    "no existing request was changed.",
                    file=stderr,
                )
            else:
                print(
                    "Permission denied by App Store Connect (HTTP 403). "
                    "Downloading reports requires an Admin, Sales and Reports, "
                    "or Finance API key.",
                    file=stderr,
                )
            return EXIT_PERMISSION
        print(f"App Store Connect API error: {exc}", file=stderr)
        return EXIT_API
    except DataError as exc:
        print(f"Unexpected App Store Connect response: {exc}", file=stderr)
        return EXIT_DATA
    except OSError as exc:
        print(f"Local report file error: {exc}", file=stderr)
        return EXIT_DATA


if __name__ == "__main__":
    raise SystemExit(main())
