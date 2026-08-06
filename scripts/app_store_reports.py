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
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, TextIO


API_BASE_URL = "https://api.appstoreconnect.apple.com"
API_HOST = "api.appstoreconnect.apple.com"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_ID = "com.ntindle.EaselWall"
DEFAULT_APP_ID = "6778701883"
REQUIRED_ACCESS_TYPES = ("ONE_TIME_SNAPSHOT", "ONGOING")
TARGET_REPORTS = (
    "App Store Discovery and Engagement Standard",
    "App Store Discovery and Engagement Detailed",
    "App Store Downloads Standard",
    "App Store Downloads Detailed",
    "App Store Purchases Standard",
    "App Store Purchases Detailed",
)
DISCOVERY_STANDARD_REPORT = "App Store Discovery and Engagement Standard"
DISCOVERY_DETAILED_REPORT = "App Store Discovery and Engagement Detailed"
DOWNLOADS_STANDARD_REPORT = "App Store Downloads Standard"
DOWNLOADS_DETAILED_REPORT = "App Store Downloads Detailed"
PURCHASES_STANDARD_REPORT = "App Store Purchases Standard"
PURCHASES_DETAILED_REPORT = "App Store Purchases Detailed"
# Compatibility name for callers that previously referred to the sole revenue
# report. Totals now come exclusively from Standard.
PURCHASES_REPORT = PURCHASES_STANDARD_REPORT
DEFAULT_REPORT_OUTPUT = ROOT / "marketing" / "reports" / "app-store-connect"
APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
CHECKSUM_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")
INSTANCE_MANIFEST_NAME = "_instance-complete.json"
CAMPAIGN_GROUPS = (
    "tt_organic",
    "tt_creator",
    "tt_paid",
    "web_site",
    "unattributed",
    "other",
)
PERIOD_TRAILING_365 = "trailing-365"
PERIOD_CALENDAR_YEAR = "calendar-year"
DEFAULT_ANNUAL_NET_SALES_GOAL = 55
COMMON_COMPLETENESS_LAG_DAYS = 3
PURCHASES_COMPLETENESS_LAG_DAYS = 2
DOWNLOADS_COMPLETENESS_LAG_DAYS = 2
DISCOVERY_COMPLETENESS_LAG_DAYS = 3

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


@dataclass(frozen=True)
class ReportingPeriod:
    mode: str
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.mode not in (PERIOD_TRAILING_365, PERIOD_CALENDAR_YEAR):
            raise ValueError(f"Unsupported reporting period mode: {self.mode}")
        if self.start_date > self.end_date:
            raise ValueError("Reporting period start must not follow its end")

    @classmethod
    def trailing_365(cls, as_of: date) -> ReportingPeriod:
        return cls(
            mode=PERIOD_TRAILING_365,
            start_date=as_of - timedelta(days=364),
            end_date=as_of,
        )

    @classmethod
    def calendar_year(cls, year: int) -> ReportingPeriod:
        return cls(
            mode=PERIOD_CALENDAR_YEAR,
            start_date=date(year, 1, 1),
            end_date=date(year, 12, 31),
        )

    def contains(self, event_date: date) -> bool:
        return self.start_date <= event_date <= self.end_date

    @property
    def label(self) -> str:
        if self.mode == PERIOD_TRAILING_365:
            return (
                "trailing 365 days "
                f"({self.start_date.isoformat()} through {self.end_date.isoformat()})"
            )
        return f"calendar year {self.start_date.year}"

    @property
    def output_suffix(self) -> str:
        if self.mode == PERIOD_TRAILING_365:
            return f"trailing-365-{self.end_date.isoformat()}"
        return str(self.start_date.year)


@dataclass
class PurchaseSummary:
    year: int | None = None
    period: ReportingPeriod | None = None
    app_id: str = DEFAULT_APP_ID
    bundle_id: str = DEFAULT_BUNDLE_ID
    period_start: date | None = None
    period_end: date | None = None
    standard_available: bool = False
    explicit_standard_period_complete: bool = False
    standard_snapshot_complete_through: date | None = None
    standard_ongoing_complete_dates: set[date] = field(default_factory=set)
    campaign_available: bool = False
    files: int = 0
    files_scanned: int = 0
    unmanaged_standard_files: int = 0
    datasets_considered: int = 0
    rows: int = 0
    superseded_rows: int = 0
    purchases: int = 0
    proceeds: Decimal = Decimal("0")
    refund_units: int = 0
    latest_standard_processing_date: date | None = None
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

    def __post_init__(self) -> None:
        if self.period is None:
            if self.year is None:
                raise ValueError("PurchaseSummary requires a year or reporting period")
            self.period = ReportingPeriod.calendar_year(self.year)
        elif self.year is None and self.period.mode == PERIOD_CALENDAR_YEAR:
            self.year = self.period.start_date.year
        if self.period_start is None:
            self.period_start = self.period.start_date
        if self.period_end is None:
            self.period_end = self.period.end_date

    def standard_complete_for_window(self, start: date, end: date) -> bool:
        if start > end:
            return False
        # Hand-constructed summaries remain useful to callers and tests; managed
        # summaries always carry an explicit period and provenance below.
        if self.period_start is None or self.period_end is None:
            return self.standard_available
        if start < self.period_start or end > self.period_end:
            return False
        if self.explicit_standard_period_complete:
            return True
        snapshot_end = self.standard_snapshot_complete_through
        current = start
        while current <= end:
            if snapshot_end is None or current > snapshot_end:
                if current not in self.standard_ongoing_complete_dates:
                    return False
            current += timedelta(days=1)
        return True


@dataclass(frozen=True)
class PurchaseDataset:
    processing_date: date
    access_type: str
    request_id: str
    report_id: str
    instance_id: str

    @property
    def rank(self) -> date:
        return self.processing_date

    @property
    def preference(self) -> int:
        return {
            "ONE_TIME_SNAPSHOT": 1,
            "ONGOING": 2,
            "EXPLICIT": 3,
        }.get(self.access_type, 0)

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
    content_signature: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AcquisitionDataset:
    processing_date: date
    access_type: str
    request_id: str
    report_id: str
    instance_id: str

    @property
    def rank(self) -> date:
        return self.processing_date

    @property
    def preference(self) -> int:
        return {
            "ONE_TIME_SNAPSHOT": 1,
            "ONGOING": 2,
        }.get(self.access_type, 0)

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return self.request_id, self.report_id, self.instance_id, self.access_type


@dataclass(frozen=True)
class DiscoveryRow:
    event_date: date
    impressions: int
    unique_impressions: int
    product_page_views: int
    unique_product_page_views: int
    buy_or_get_taps: int
    product_page_buy_or_get_taps: int
    source_path: Path


@dataclass(frozen=True)
class DownloadRow:
    event_date: date
    first_time_downloads: int
    redownloads: int
    source_path: Path


@dataclass
class AcquisitionSummary:
    year: int
    discovery_standard_available: bool = False
    downloads_standard_available: bool = False
    discovery_detailed_available: bool = False
    downloads_detailed_available: bool = False
    discovery_files: int = 0
    discovery_files_scanned: int = 0
    discovery_datasets_considered: int = 0
    discovery_rows: int = 0
    discovery_superseded_rows: int = 0
    download_files: int = 0
    download_files_scanned: int = 0
    download_datasets_considered: int = 0
    download_rows: int = 0
    download_superseded_rows: int = 0
    impressions: int = 0
    unique_impressions: int = 0
    product_page_views: int = 0
    unique_product_page_views: int = 0
    buy_or_get_taps: int = 0
    product_page_buy_or_get_taps: int = 0
    first_time_downloads: int = 0
    redownloads: int = 0
    latest_discovery_processing_date: date | None = None
    latest_download_processing_date: date | None = None
    dates: set[date] = field(default_factory=set)
    period_start: date | None = None
    period_end: date | None = None
    discovery_explicit_period_complete: bool = False
    discovery_snapshot_complete_through: date | None = None
    discovery_ongoing_complete_dates: set[date] = field(default_factory=set)
    downloads_explicit_period_complete: bool = False
    downloads_snapshot_complete_through: date | None = None
    downloads_ongoing_complete_dates: set[date] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.period_start is None:
            self.period_start = date(self.year, 1, 1)
        if self.period_end is None:
            self.period_end = date(self.year, 12, 31)
        if self.period_start > self.period_end:
            raise ValueError("AcquisitionSummary period start cannot follow its end")

    @property
    def total_downloads(self) -> int:
        return self.first_time_downloads + self.redownloads

    @staticmethod
    def _rate(
        numerator: int,
        denominator: int,
        *,
        numerator_available: bool,
        denominator_available: bool,
    ) -> Decimal | None:
        if not numerator_available or not denominator_available or denominator <= 0:
            return None
        return Decimal(numerator) / Decimal(denominator) * Decimal("100")

    @property
    def product_page_view_rate(self) -> Decimal | None:
        return self._rate(
            self.product_page_views,
            self.impressions,
            numerator_available=self.discovery_standard_available,
            denominator_available=self.discovery_standard_available,
        )

    @property
    def product_page_buy_or_get_tap_rate(self) -> Decimal | None:
        return self._rate(
            self.product_page_buy_or_get_taps,
            self.product_page_views,
            numerator_available=self.discovery_standard_available,
            denominator_available=self.discovery_standard_available,
        )

    @property
    def first_time_download_to_buy_or_get_tap_rate(self) -> Decimal | None:
        return self._rate(
            self.first_time_downloads,
            self.buy_or_get_taps,
            numerator_available=self.downloads_standard_available,
            denominator_available=self.discovery_standard_available,
        )

    @property
    def first_time_download_rate(self) -> Decimal | None:
        return self._rate(
            self.first_time_downloads,
            self.impressions,
            numerator_available=self.downloads_standard_available,
            denominator_available=self.discovery_standard_available,
        )

    def _stage_complete_for_window(
        self,
        start: date,
        end: date,
        *,
        explicit_period_complete: bool,
        snapshot_complete_through: date | None,
        ongoing_complete_dates: set[date],
    ) -> bool:
        if start > end:
            return False
        assert self.period_start is not None
        assert self.period_end is not None
        if start < self.period_start or end > self.period_end:
            return False
        if explicit_period_complete:
            return True
        current = start
        while current <= end:
            if (
                snapshot_complete_through is None
                or current > snapshot_complete_through
            ) and current not in ongoing_complete_dates:
                return False
            current += timedelta(days=1)
        return True

    def discovery_complete_for_window(self, start: date, end: date) -> bool:
        return self._stage_complete_for_window(
            start,
            end,
            explicit_period_complete=self.discovery_explicit_period_complete,
            snapshot_complete_through=self.discovery_snapshot_complete_through,
            ongoing_complete_dates=self.discovery_ongoing_complete_dates,
        )

    def downloads_complete_for_window(self, start: date, end: date) -> bool:
        return self._stage_complete_for_window(
            start,
            end,
            explicit_period_complete=self.downloads_explicit_period_complete,
            snapshot_complete_through=self.downloads_snapshot_complete_through,
            ongoing_complete_dates=self.downloads_ongoing_complete_dates,
        )

    def discovery_complete_through(self, cutoff: date) -> bool:
        assert self.period_start is not None
        return self.discovery_complete_for_window(self.period_start, cutoff)

    def downloads_complete_through(self, cutoff: date) -> bool:
        assert self.period_start is not None
        return self.downloads_complete_for_window(self.period_start, cutoff)


@dataclass(frozen=True)
class PerformanceSummary:
    as_of: date
    data_cutoff: date
    completeness_lag_days: int
    annual_goal: Decimal
    annual_net_sales_goal: int
    goal_window_complete: bool
    purchase_data_complete: bool
    weekly_data_complete: bool
    goal_window_start: date
    goal_window_end: date
    goal_window_purchases: int | None
    goal_window_proceeds: Decimal | None
    goal_remaining: Decimal | None
    goal_met: bool | None
    net_sales_progress: int | None
    net_sales_progress_percent: Decimal | None
    net_sales_remaining: int | None
    net_sales_goal_met: bool | None
    run_rate_window_start: date
    run_rate_window_end: date
    run_rate_purchases: int | None
    run_rate_proceeds: Decimal | None
    annualized_run_rate_proceeds: Decimal | None
    run_rate_on_pace: bool | None
    current_week_start: date
    current_week_end: date
    prior_week_start: date
    prior_week_end: date
    current_week_purchases: int | None
    current_week_proceeds: Decimal | None
    prior_week_purchases: int | None
    prior_week_proceeds: Decimal | None
    week_over_week_proceeds_percent: Decimal | None


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
    if not isinstance(bundle_id, str) or not bundle_id:
        raise DataError("App Store Connect app record is missing its bundle ID")
    return AppInfo(
        app_id=app_id,
        name=name if isinstance(name, str) else "EaselWall",
        bundle_id=bundle_id,
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
            raise ConfigurationError(
                "--app-id may contain only letters, numbers, and hyphens"
            )
        response = client.request_json("GET", f"/v1/apps/{app_id}")
        data = response.get("data")
        if not isinstance(data, dict):
            raise DataError("App Store Connect app lookup is missing a data object")
        app = _parse_app(data)
        if app.app_id != app_id:
            raise DataError("App Store Connect returned a different app ID than requested")
        if app.bundle_id != bundle_id:
            raise DataError(
                f"App Store Connect app {app.app_id} has bundle ID {app.bundle_id}, "
                f"not expected EaselWall bundle ID {bundle_id}"
            )
        return app

    query = urllib.parse.urlencode({"filter[bundleId]": bundle_id, "limit": "2"})
    items = client.get_collection(f"/v1/apps?{query}")
    if not items:
        raise DataError(f"No App Store Connect app found for bundle ID {bundle_id}")
    if len(items) > 1:
        raise DataError(f"Multiple App Store Connect apps found for bundle ID {bundle_id}")
    app = _parse_app(items[0])
    if app.bundle_id != bundle_id:
        raise DataError(
            f"App Store Connect lookup returned bundle ID {app.bundle_id}, "
            f"not requested bundle ID {bundle_id}"
        )
    return app


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
    app: AppInfo,
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
        "schemaVersion": 2,
        "appId": app.app_id,
        "bundleId": app.bundle_id,
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


def write_instance_completion_manifest(
    *,
    app: AppInfo,
    request: ReportRequest,
    report: AnalyticsReport,
    instance: ReportInstance,
    tsv_paths: Sequence[Path],
) -> Path:
    """Mark one report instance complete only after every segment is verified."""

    if not tsv_paths:
        raise DataError(f"Cannot complete empty report instance {instance.instance_id}")
    parents = {path.parent for path in tsv_paths}
    if len(parents) != 1:
        raise DataError("Report instance segments do not share one destination directory")

    entries: list[dict[str, Any]] = []
    seen_segment_ids: set[str] = set()
    seen_files: set[str] = set()
    expected_identity = {
        "schemaVersion": 2,
        "appId": app.app_id,
        "bundleId": app.bundle_id,
        "requestId": request.request_id,
        "accessType": request.access_type,
        "reportId": report.report_id,
        "reportName": report.name,
        "instanceId": instance.instance_id,
        "granularity": instance.granularity,
        "processingDate": instance.processing_date.isoformat(),
    }
    for path in sorted(tsv_paths, key=lambda item: item.name):
        metadata = _load_segment_metadata(path)
        if metadata is None:
            raise DataError(f"Missing report metadata file: {_metadata_path(path)}")
        for key, value in expected_identity.items():
            if metadata.get(key) != value:
                raise DataError(
                    f"Segment metadata identity mismatch for {path}: {key}"
                )
        segment_id = metadata.get("segmentId")
        compressed_md5 = metadata.get("compressedMd5")
        compressed_size = metadata.get("compressedSizeInBytes")
        if not isinstance(segment_id, str) or not segment_id:
            raise DataError(f"Missing segmentId in {_metadata_path(path)}")
        if segment_id in seen_segment_ids or path.name in seen_files:
            raise DataError(f"Duplicate segment in report instance {instance.instance_id}")
        seen_segment_ids.add(segment_id)
        seen_files.add(path.name)
        entries.append(
            {
                "segmentId": segment_id,
                "compressedMd5": compressed_md5,
                "compressedSizeInBytes": compressed_size,
                "tsvFile": path.name,
            }
        )

    instance_directory = next(iter(parents))
    actual_files = {candidate.name for candidate in instance_directory.glob("*.tsv")}
    if actual_files != seen_files:
        raise DataError(
            "Incomplete or unexpected segment set while completing report "
            f"instance: {instance_directory}"
        )

    payload = {
        **expected_identity,
        "manifestSchemaVersion": 1,
        "segmentCount": len(entries),
        "segments": entries,
    }
    manifest_path = instance_directory / INSTANCE_MANIFEST_NAME
    atomic_write(
        manifest_path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return manifest_path


def download_target_reports(
    client: AppStoreConnectClient,
    requests: Sequence[ReportRequest],
    *,
    app: AppInfo,
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
            instance_paths: list[Path] = []
            for segment in iter_segments(client, instance.instance_id):
                segment_count += 1
                path, _ = download_segment(
                    client,
                    app=app,
                    request=request,
                    report=report,
                    instance=instance,
                    segment=segment,
                    output_dir=output_dir,
                    output=output,
                )
                output_paths.append(path)
                instance_paths.append(path)
            if not segment_count:
                print(f"  Instance {instance.instance_id}: no segments", file=output)
            else:
                manifest_path = write_instance_completion_manifest(
                    app=app,
                    request=request,
                    report=report,
                    instance=instance,
                    tsv_paths=instance_paths,
                )
                print(
                    f"  Verified complete {manifest_path.parent.relative_to(output_dir)} "
                    f"({segment_count} segment(s))",
                    file=output,
                )
    print(f"Verified local report segments: {len(output_paths)}", file=output)
    return output_paths


def coarse_campaign(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    for campaign in ("tt_organic", "tt_creator", "tt_paid", "web_site"):
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
        raise DataError(
            f"Missing {field_name!r} value in {path} at line {line_number}"
        )
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


def _verify_managed_instance_directory(
    instance_directory: Path,
    *,
    expected_app_id: str,
    expected_bundle_id: str,
) -> dict[Path, dict[str, Any]]:
    """Verify every segment in one managed instance before classification."""

    manifest_path = instance_directory / INSTANCE_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError(
            "Missing or invalid completion manifest for report instance: "
            f"{instance_directory}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("manifestSchemaVersion") != 1:
        raise DataError(f"Invalid report instance manifest: {manifest_path}")
    if manifest.get("schemaVersion") != 2:
        raise DataError(
            f"Unsupported report metadata schema in {manifest_path}; "
            "refetch this report archive"
        )
    if manifest.get("appId") != expected_app_id:
        raise DataError(f"Unexpected app ID in {manifest_path}")
    if manifest.get("bundleId") != expected_bundle_id:
        raise DataError(f"Unexpected bundle ID in {manifest_path}")
    if manifest.get("reportName") not in TARGET_REPORTS:
        raise DataError(f"Unknown managed reportName in {manifest_path}")
    if manifest.get("accessType") not in REQUIRED_ACCESS_TYPES:
        raise DataError(f"Invalid accessType in {manifest_path}")
    if manifest.get("granularity") != "DAILY":
        raise DataError(f"Invalid granularity in {manifest_path}")
    for key in ("requestId", "reportId", "instanceId"):
        if not isinstance(manifest.get(key), str) or not manifest.get(key):
            raise DataError(f"Missing {key} in {manifest_path}")
    raw_processing_date = manifest.get("processingDate")
    if not isinstance(raw_processing_date, str):
        raise DataError(f"Missing processingDate in {manifest_path}")
    try:
        date.fromisoformat(raw_processing_date)
    except ValueError as exc:
        raise DataError(f"Invalid processingDate in {manifest_path}") from exc

    identity_keys = (
        "schemaVersion",
        "appId",
        "bundleId",
        "requestId",
        "accessType",
        "reportId",
        "reportName",
        "instanceId",
        "granularity",
        "processingDate",
    )

    raw_segments = manifest.get("segments")
    segment_count = manifest.get("segmentCount")
    if (
        not isinstance(raw_segments, list)
        or not isinstance(segment_count, int)
        or isinstance(segment_count, bool)
        or segment_count <= 0
        or segment_count != len(raw_segments)
    ):
        raise DataError(f"Invalid segment set in {manifest_path}")

    entries_by_file: dict[str, Mapping[str, Any]] = {}
    segment_ids: set[str] = set()
    for entry in raw_segments:
        if not isinstance(entry, dict):
            raise DataError(f"Invalid segment entry in {manifest_path}")
        filename = entry.get("tsvFile")
        segment_id = entry.get("segmentId")
        checksum = entry.get("compressedMd5")
        size = entry.get("compressedSizeInBytes")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".tsv")
            or not isinstance(segment_id, str)
            or not segment_id
            or not isinstance(checksum, str)
            or not CHECKSUM_PATTERN.fullmatch(checksum)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise DataError(f"Invalid segment entry in {manifest_path}")
        if filename in entries_by_file or segment_id in segment_ids:
            raise DataError(f"Duplicate segment entry in {manifest_path}")
        entries_by_file[filename] = entry
        segment_ids.add(segment_id)

    actual_files = {
        candidate.name for candidate in instance_directory.glob("*.tsv")
    }
    if actual_files != set(entries_by_file):
        raise DataError(
            "Incomplete or unexpected segment set for report instance: "
            f"{instance_directory}"
        )

    verified: dict[Path, dict[str, Any]] = {}
    for filename, entry in sorted(entries_by_file.items()):
        path = instance_directory / filename
        metadata = _load_segment_metadata(path)
        if metadata is None:
            raise DataError(f"Missing report metadata file: {_metadata_path(path)}")
        if metadata.get("schemaVersion") != 2:
            raise DataError(
                f"Unsupported report metadata schema in {_metadata_path(path)}; "
                "refetch this report archive"
            )
        if metadata.get("appId") != expected_app_id:
            raise DataError(f"Unexpected app ID in {_metadata_path(path)}")
        if metadata.get("bundleId") != expected_bundle_id:
            raise DataError(f"Unexpected bundle ID in {_metadata_path(path)}")
        if metadata.get("reportName") not in TARGET_REPORTS:
            raise DataError(
                f"Unknown managed reportName in {_metadata_path(path)}"
            )
        for key in identity_keys:
            if manifest.get(key) != metadata.get(key):
                raise DataError(
                    f"Instance manifest identity mismatch for {path}: {key}"
                )
        if (
            entry.get("segmentId") != metadata.get("segmentId")
            or str(entry.get("compressedMd5")).casefold()
            != str(metadata.get("compressedMd5")).casefold()
            or entry.get("compressedSizeInBytes")
            != metadata.get("compressedSizeInBytes")
        ):
            raise DataError(f"Instance manifest segment mismatch for {path}")
        _verify_managed_tsv(path, metadata)
        verified[path] = metadata
    return verified


def _preflight_managed_instances(
    input_path: Path,
    paths: Sequence[Path],
    *,
    expected_app_id: str,
    expected_bundle_id: str,
) -> dict[Path, dict[str, Any]]:
    """Verify the complete managed archive before any report-name filtering."""

    instance_directories: set[Path] = set()
    for path in paths:
        if (
            _metadata_path(path).is_file()
            or (path.parent / INSTANCE_MANIFEST_NAME).is_file()
        ):
            instance_directories.add(path.parent)
    if input_path.is_dir():
        instance_directories.update(
            manifest_path.parent
            for manifest_path in input_path.rglob(INSTANCE_MANIFEST_NAME)
            if manifest_path.is_file()
        )
        instance_directories.update(
            metadata_path.parent
            for metadata_path in input_path.rglob("*.metadata.json")
            if metadata_path.is_file()
        )

    verified: dict[Path, dict[str, Any]] = {}
    for instance_directory in sorted(instance_directories):
        instance_files = _verify_managed_instance_directory(
            instance_directory,
            expected_app_id=expected_app_id,
            expected_bundle_id=expected_bundle_id,
        )
        overlap = set(verified) & set(instance_files)
        if overlap:
            raise DataError(
                f"Managed report segment belongs to multiple instances: "
                f"{sorted(overlap)[0]}"
            )
        verified.update(instance_files)
    return verified


def _event_content_signature(rows: Sequence[PurchaseRow]) -> tuple[Any, ...]:
    """Return an order-independent signature of every source field in the rows."""

    return tuple(sorted(row.content_signature for row in rows))


def _canonical_rows(
    rows_by_dataset: Mapping[PurchaseDataset, list[PurchaseRow]],
    *,
    period: ReportingPeriod,
    report_name: str,
) -> tuple[list[PurchaseRow], int, set[Path]]:
    """Select rows per event Date, rejecting ambiguous same-date conflicts."""

    datasets_by_event_date: dict[date, set[PurchaseDataset]] = {}
    for dataset, rows in rows_by_dataset.items():
        for row in rows:
            if period.contains(row.event_date):
                datasets_by_event_date.setdefault(row.event_date, set()).add(dataset)

    canonical: list[PurchaseRow] = []
    selected_paths: set[Path] = set()
    superseded_rows = 0
    for event_date in sorted(datasets_by_event_date):
        candidates = datasets_by_event_date[event_date]
        winning_rank = max(dataset.rank for dataset in candidates)
        tied = sorted(
            (dataset for dataset in candidates if dataset.rank == winning_rank),
            key=lambda dataset: (-dataset.preference, dataset.identity),
        )
        if len(tied) > 1:
            signatures = {
                _event_content_signature(
                    [
                        row
                        for row in rows_by_dataset[dataset]
                        if row.event_date == event_date
                    ]
                )
                for dataset in tied
            }
            if len(signatures) > 1:
                raise DataError(
                    f"Conflicting {report_name} instances share processingDate "
                    f"{winning_rank.isoformat()} for event Date "
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


def summarize_purchases(
    input_path: Path,
    *,
    year: int | None = None,
    period: ReportingPeriod | None = None,
    expected_app_id: str = DEFAULT_APP_ID,
    expected_bundle_id: str = DEFAULT_BUNDLE_ID,
    start_date: date | None = None,
    end_date: date | None = None,
) -> PurchaseSummary:
    """Build independent Standard totals and Detailed campaign attribution."""

    if period is not None and (
        year is not None or start_date is not None or end_date is not None
    ):
        raise ConfigurationError(
            "Specify a reporting period or calendar year/date range, not both"
        )
    if period is None and (start_date is not None or end_date is not None):
        reference_year = year
        if reference_year is None:
            reference_date = end_date if end_date is not None else start_date
            assert reference_date is not None
            reference_year = reference_date.year
        period_start = start_date or date(reference_year, 1, 1)
        period_end = end_date or date(reference_year, 12, 31)
        if period_start > period_end:
            raise ConfigurationError("Report period start cannot be after its end")
        period_mode = (
            PERIOD_TRAILING_365
            if (period_end - period_start).days == 364
            else PERIOD_CALENDAR_YEAR
        )
        period = ReportingPeriod(period_mode, period_start, period_end)
    elif period is None:
        if year is None:
            raise ConfigurationError("A reporting period or calendar year is required")
        period = ReportingPeriod.calendar_year(year)
    summary = PurchaseSummary(
        year=year,
        period=period,
        app_id=expected_app_id,
        bundle_id=expected_bundle_id,
        period_start=period.start_date,
        period_end=period.end_date,
    )
    explicit_file = input_path.is_file()
    period_start = period.start_date
    period_end = period.end_date
    base_fields = {"Date", "Purchases", "Proceeds in USD"}
    report_names = (PURCHASES_STANDARD_REPORT, PURCHASES_DETAILED_REPORT)
    rows_by_report: dict[str, dict[PurchaseDataset, list[PurchaseRow]]] = {
        report_name: {} for report_name in report_names
    }
    unmanaged_files: dict[str, list[Path]] = {
        report_name: [] for report_name in report_names
    }
    file_counts = {report_name: 0 for report_name in report_names}
    seen_segments: dict[tuple[str, str, str, str, str, str], tuple[str, Path]] = {}
    dataset_provenance: dict[tuple[str, str, str, str, str], str] = {}

    paths = _tsv_paths(input_path)
    managed_metadata = _preflight_managed_instances(
        input_path,
        paths,
        expected_app_id=expected_app_id,
        expected_bundle_id=expected_bundle_id,
    )
    paths = sorted(set(paths) | set(managed_metadata))
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8-sig", newline="")
        except (OSError, UnicodeDecodeError) as exc:
            raise DataError(f"Could not read report file: {path}") from exc
        with handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fieldnames = reader.fieldnames or []
            if len(fieldnames) != len(set(fieldnames)):
                raise DataError(f"Ambiguous duplicate columns in {path}")
            fields = set(fieldnames)
            metadata = managed_metadata.get(path)
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
                provenance_key = (
                    report_name,
                    request_id,
                    str(access_type),
                    str(report_id),
                    str(instance_id),
                )
                previous_provenance = dataset_provenance.get(provenance_key)
                if (
                    previous_provenance is not None
                    and previous_provenance != raw_processing_date
                ):
                    raise DataError(
                        f"Inconsistent metadata for report instance {instance_id}"
                    )
                dataset_provenance[provenance_key] = raw_processing_date
                dataset = PurchaseDataset(
                    processing_date=processing_date,
                    access_type=str(access_type),
                    request_id=request_id,
                    report_id=str(report_id),
                    instance_id=str(instance_id),
                )
                if report_name == PURCHASES_STANDARD_REPORT:
                    if (
                        summary.latest_standard_processing_date is None
                        or processing_date > summary.latest_standard_processing_date
                    ):
                        summary.latest_standard_processing_date = processing_date
                segment_key = (
                    report_name,
                    request_id,
                    str(access_type),
                    str(report_id),
                    str(instance_id),
                    str(segment_id),
                )
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
                if None in row:
                    raise DataError(
                        f"Ambiguous extra column value in {path} at line {line_number}"
                    )
                if "App Apple Identifier" in fields:
                    row_app_id = (row.get("App Apple Identifier") or "").strip()
                    if row_app_id != expected_app_id:
                        raise DataError(
                            f"Unexpected 'App Apple Identifier' in {path} at line "
                            f"{line_number}: expected {expected_app_id}, got "
                            f"{row_app_id or '<blank>'}"
                        )
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
                        content_signature=tuple(
                            (field, row.get(field) or "") for field in sorted(fields)
                        ),
                    )
                )

            if metadata is None:
                if not explicit_file and "App Apple Identifier" not in fields:
                    raise DataError(
                        f"Cannot verify EaselWall app identity for unmetadataed {path}; "
                        "pass one explicit TSV or use files produced by the fetch command"
                    )
                unmanaged_files[report_name].append(path)
                processing_date = (
                    max(row.event_date for row in file_rows)
                    if file_rows
                    else period.start_date
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

    summary.campaign_available = file_counts[PURCHASES_DETAILED_REPORT] > 0
    summary.files_scanned = file_counts[PURCHASES_STANDARD_REPORT]
    summary.unmanaged_standard_files = len(
        unmanaged_files[PURCHASES_STANDARD_REPORT]
    )
    summary.campaign_files_scanned = file_counts[PURCHASES_DETAILED_REPORT]
    summary.datasets_considered = len(rows_by_report[PURCHASES_STANDARD_REPORT])
    summary.campaign_datasets_considered = len(
        rows_by_report[PURCHASES_DETAILED_REPORT]
    )

    standard_rows, summary.superseded_rows, standard_paths = _canonical_rows(
        rows_by_report[PURCHASES_STANDARD_REPORT],
        period=period,
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
        period=period,
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
    standard_datasets = rows_by_report[PURCHASES_STANDARD_REPORT]
    snapshot_completion_dates = [
        dataset.processing_date
        - timedelta(days=PURCHASES_COMPLETENESS_LAG_DAYS)
        for dataset in standard_datasets
        if dataset.access_type == "ONE_TIME_SNAPSHOT"
    ]
    if snapshot_completion_dates:
        summary.standard_snapshot_complete_through = max(
            snapshot_completion_dates
        )
    summary.standard_ongoing_complete_dates = {
        dataset.processing_date
        - timedelta(days=PURCHASES_COMPLETENESS_LAG_DAYS)
        for dataset in standard_datasets
        if dataset.access_type == "ONGOING"
    }
    # Unmanaged TSVs remain parseable for inspection, but without managed
    # provenance they cannot prove that absent dates are complete zeros.
    summary.explicit_standard_period_complete = False
    summary.standard_available = summary.standard_complete_for_window(
        period_start, period_end
    )
    return summary


def _parse_nonnegative_int_field(
    raw_value: str | None, *, field_name: str, path: Path, line_number: int
) -> int:
    value = (raw_value or "").strip()
    if not value:
        raise DataError(
            f"Missing {field_name!r} value in {path} at line {line_number}"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise DataError(
            f"Invalid {field_name!r} value in {path} at line {line_number}: "
            f"{value!r}"
        ) from exc
    if (
        not parsed.is_finite()
        or parsed < 0
        or parsed != parsed.to_integral_value()
    ):
        raise DataError(
            f"Expected a non-negative integer {field_name!r} value in {path} "
            f"at line {line_number}"
        )
    return int(parsed)


def _normalized_dimension(value: str | None) -> str:
    normalized_dashes = (value or "").translate(
        str.maketrans({"‑": "-", "–": "-", "—": "-", "−": "-"})
    )
    return " ".join(normalized_dashes.split()).casefold()


def _managed_acquisition_dataset(
    path: Path,
    metadata: Mapping[str, Any],
    *,
    report_name: str,
    dataset_provenance: dict[tuple[str, str, str, str, str], str],
) -> tuple[AcquisitionDataset, tuple[str, str, str, str, str, str], str]:
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
        raise DataError(f"Invalid processingDate in {_metadata_path(path)}") from exc
    if access_type not in REQUIRED_ACCESS_TYPES:
        raise DataError(f"Invalid accessType in {_metadata_path(path)}")
    if metadata.get("granularity") != "DAILY":
        raise DataError(f"Invalid granularity in {_metadata_path(path)}")
    identity_values = (request_id, report_id, instance_id, segment_id)
    if not all(isinstance(value, str) and value for value in identity_values):
        raise DataError(f"Missing dataset identity in {_metadata_path(path)}")
    provenance_key = (
        report_name,
        str(request_id),
        str(access_type),
        str(report_id),
        str(instance_id),
    )
    previous_provenance = dataset_provenance.get(provenance_key)
    if (
        previous_provenance is not None
        and previous_provenance != raw_processing_date
    ):
        raise DataError(f"Inconsistent metadata for report instance {instance_id}")
    dataset_provenance[provenance_key] = raw_processing_date
    dataset = AcquisitionDataset(
        processing_date=processing_date,
        access_type=str(access_type),
        request_id=str(request_id),
        report_id=str(report_id),
        instance_id=str(instance_id),
    )
    segment_key = (
        report_name,
        str(request_id),
        str(access_type),
        str(report_id),
        str(instance_id),
        str(segment_id),
    )
    return dataset, segment_key, str(metadata["compressedMd5"]).casefold()


def _acquisition_signature(
    rows: Sequence[DiscoveryRow] | Sequence[DownloadRow],
    *,
    report_name: str,
) -> tuple[int, ...]:
    if report_name == DISCOVERY_STANDARD_REPORT:
        discovery_rows = rows
        return (
            sum(row.impressions for row in discovery_rows),
            sum(row.unique_impressions for row in discovery_rows),
            sum(row.product_page_views for row in discovery_rows),
            sum(row.unique_product_page_views for row in discovery_rows),
            sum(row.buy_or_get_taps for row in discovery_rows),
            sum(row.product_page_buy_or_get_taps for row in discovery_rows),
        )
    download_rows = rows
    return (
        sum(row.first_time_downloads for row in download_rows),
        sum(row.redownloads for row in download_rows),
    )


def _canonical_acquisition_rows(
    rows_by_dataset: Mapping[
        AcquisitionDataset, list[DiscoveryRow] | list[DownloadRow]
    ],
    *,
    year: int,
    report_name: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[DiscoveryRow] | list[DownloadRow], int, set[Path]]:
    """Select one correction-safe Standard dataset for each event date."""

    period_start = start_date or date(year, 1, 1)
    period_end = end_date or date(year, 12, 31)
    if period_start > period_end:
        raise ConfigurationError("Report period start cannot be after its end")
    datasets_by_event_date: dict[date, set[AcquisitionDataset]] = {}
    for dataset, rows in rows_by_dataset.items():
        for row in rows:
            if period_start <= row.event_date <= period_end:
                datasets_by_event_date.setdefault(row.event_date, set()).add(dataset)

    canonical: list[DiscoveryRow] | list[DownloadRow] = []
    selected_paths: set[Path] = set()
    superseded_rows = 0
    for event_date in sorted(datasets_by_event_date):
        candidates = datasets_by_event_date[event_date]
        winning_rank = max(dataset.rank for dataset in candidates)
        tied = sorted(
            (dataset for dataset in candidates if dataset.rank == winning_rank),
            key=lambda dataset: (-dataset.preference, dataset.identity),
        )
        tied_rows = [
            [
                row
                for row in rows_by_dataset[dataset]
                if row.event_date == event_date
            ]
            for dataset in tied
        ]
        signatures = {
            _acquisition_signature(rows, report_name=report_name)
            for rows in tied_rows
        }
        if len(signatures) > 1:
            raise DataError(
                f"Conflicting {report_name} instances share processingDate "
                f"{winning_rank.isoformat()} for event Date "
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


def summarize_acquisition(
    input_path: Path,
    *,
    year: int,
    expected_app_id: str = DEFAULT_APP_ID,
    expected_bundle_id: str = DEFAULT_BUNDLE_ID,
    start_date: date | None = None,
    end_date: date | None = None,
) -> AcquisitionSummary:
    """Build Standard acquisition totals without using privacy-limited Detailed rows."""

    period_start = start_date or date(year, 1, 1)
    period_end = end_date or date(year, 12, 31)
    if period_start > period_end:
        raise ConfigurationError("Report period start cannot be after its end")
    summary = AcquisitionSummary(
        year=year,
        period_start=period_start,
        period_end=period_end,
    )
    standard_names = (DISCOVERY_STANDARD_REPORT, DOWNLOADS_STANDARD_REPORT)
    detailed_names = (DISCOVERY_DETAILED_REPORT, DOWNLOADS_DETAILED_REPORT)
    acquisition_names = set(standard_names + detailed_names)
    rows_by_report: dict[
        str,
        dict[AcquisitionDataset, list[DiscoveryRow] | list[DownloadRow]],
    ] = {report_name: {} for report_name in standard_names}
    file_counts = {report_name: 0 for report_name in acquisition_names}
    seen_segments: dict[
        tuple[str, str, str, str, str, str], tuple[str, Path]
    ] = {}
    dataset_provenance: dict[tuple[str, str, str, str, str], str] = {}

    paths = _tsv_paths(input_path)
    managed_metadata = _preflight_managed_instances(
        input_path,
        paths,
        expected_app_id=expected_app_id,
        expected_bundle_id=expected_bundle_id,
    )
    paths = sorted(set(paths) | set(managed_metadata))
    for path in paths:
        metadata = managed_metadata.get(path)
        if metadata is None:
            continue
        report_name = metadata.get("reportName")
        if not isinstance(report_name, str) or report_name not in acquisition_names:
            continue
        dataset, segment_key, segment_fingerprint = _managed_acquisition_dataset(
            path,
            metadata,
            report_name=report_name,
            dataset_provenance=dataset_provenance,
        )
        previous_segment = seen_segments.get(segment_key)
        if previous_segment is not None:
            if previous_segment[0] != segment_fingerprint:
                raise DataError(
                    f"Conflicting duplicate segment {metadata['segmentId']}"
                )
            continue
        seen_segments[segment_key] = (segment_fingerprint, path)
        file_counts[report_name] += 1

        if report_name == DISCOVERY_STANDARD_REPORT:
            if (
                summary.latest_discovery_processing_date is None
                or dataset.processing_date
                > summary.latest_discovery_processing_date
            ):
                summary.latest_discovery_processing_date = dataset.processing_date
        elif report_name == DOWNLOADS_STANDARD_REPORT:
            if (
                summary.latest_download_processing_date is None
                or dataset.processing_date > summary.latest_download_processing_date
            ):
                summary.latest_download_processing_date = dataset.processing_date

        if report_name in detailed_names:
            continue

        try:
            handle = path.open("r", encoding="utf-8-sig", newline="")
        except (OSError, UnicodeDecodeError) as exc:
            raise DataError(f"Could not read report file: {path}") from exc
        with handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fieldnames = reader.fieldnames or []
            if len(fieldnames) != len(set(fieldnames)):
                raise DataError(f"Ambiguous duplicate columns in {path}")
            fields = set(fieldnames)
            if report_name == DISCOVERY_STANDARD_REPORT:
                required_fields = {
                    "Date",
                    "App Apple Identifier",
                    "Event",
                    "Page Type",
                    "Engagement Type",
                    "Counts",
                    "Unique Counts",
                }
                detailed_only_fields = {"Campaign", "Page Title", "Source Info"}
            else:
                required_fields = {
                    "Date",
                    "App Apple Identifier",
                    "Download Type",
                    "Counts",
                }
                detailed_only_fields = {"Campaign", "Page Title", "Source Info"}
            if not required_fields.issubset(fields):
                missing = ", ".join(sorted(required_fields - fields))
                raise DataError(
                    f"{report_name} report {path} is missing column(s): {missing}"
                )
            ambiguous_fields = fields & detailed_only_fields
            if ambiguous_fields:
                joined = ", ".join(sorted(ambiguous_fields))
                raise DataError(
                    f"{report_name} report {path} contains Detailed-only "
                    f"column(s): {joined}"
                )

            parsed_rows = rows_by_report[report_name].setdefault(dataset, [])
            for line_number, row in enumerate(reader, start=2):
                if None in row:
                    raise DataError(
                        f"Ambiguous extra column value in {path} at line {line_number}"
                    )
                raw_date = (row.get("Date") or "").strip()
                try:
                    event_date = date.fromisoformat(raw_date)
                except ValueError as exc:
                    raise DataError(
                        f"Invalid 'Date' value in {path} at line {line_number}: "
                        f"{raw_date!r}"
                    ) from exc
                app_id = (row.get("App Apple Identifier") or "").strip()
                if app_id != expected_app_id:
                    raise DataError(
                        f"Unexpected App Apple Identifier in {path} at line "
                        f"{line_number}"
                    )
                counts = _parse_nonnegative_int_field(
                    row.get("Counts"),
                    field_name="Counts",
                    path=path,
                    line_number=line_number,
                )

                if report_name == DISCOVERY_STANDARD_REPORT:
                    unique_counts = _parse_nonnegative_int_field(
                        row.get("Unique Counts"),
                        field_name="Unique Counts",
                        path=path,
                        line_number=line_number,
                    )
                    event = _normalized_dimension(row.get("Event"))
                    if event not in {"impression", "page view", "tap"}:
                        raise DataError(
                            f"Unknown 'Event' value in {path} at line {line_number}"
                        )
                    page_type = _normalized_dimension(row.get("Page Type"))
                    engagement_type = _normalized_dimension(
                        row.get("Engagement Type")
                    )
                    is_product_page = page_type == "product page"
                    is_buy_or_get = event == "tap" and engagement_type == "get"
                    parsed_rows.append(
                        DiscoveryRow(
                            event_date=event_date,
                            impressions=counts if event == "impression" else 0,
                            unique_impressions=(
                                unique_counts if event == "impression" else 0
                            ),
                            product_page_views=(
                                counts
                                if event == "page view" and is_product_page
                                else 0
                            ),
                            unique_product_page_views=(
                                unique_counts
                                if event == "page view" and is_product_page
                                else 0
                            ),
                            buy_or_get_taps=counts if is_buy_or_get else 0,
                            product_page_buy_or_get_taps=(
                                counts if is_buy_or_get and is_product_page else 0
                            ),
                            source_path=path,
                        )
                    )
                else:
                    download_type = _normalized_dimension(row.get("Download Type"))
                    known_download_types = {
                        "first-time download",
                        "redownload",
                        "manual update",
                        "auto-update",
                        "restore",
                    }
                    if download_type not in known_download_types:
                        raise DataError(
                            f"Unknown 'Download Type' value in {path} at line "
                            f"{line_number}"
                        )
                    parsed_rows.append(
                        DownloadRow(
                            event_date=event_date,
                            first_time_downloads=(
                                counts
                                if download_type == "first-time download"
                                else 0
                            ),
                            redownloads=(
                                counts if download_type == "redownload" else 0
                            ),
                            source_path=path,
                        )
                    )

    summary.discovery_detailed_available = (
        file_counts[DISCOVERY_DETAILED_REPORT] > 0
    )
    summary.downloads_detailed_available = file_counts[DOWNLOADS_DETAILED_REPORT] > 0
    summary.discovery_files_scanned = file_counts[DISCOVERY_STANDARD_REPORT]
    summary.download_files_scanned = file_counts[DOWNLOADS_STANDARD_REPORT]
    summary.discovery_datasets_considered = len(
        rows_by_report[DISCOVERY_STANDARD_REPORT]
    )
    summary.download_datasets_considered = len(rows_by_report[DOWNLOADS_STANDARD_REPORT])

    discovery_rows, summary.discovery_superseded_rows, discovery_paths = (
        _canonical_acquisition_rows(
            rows_by_report[DISCOVERY_STANDARD_REPORT],
            year=year,
            report_name=DISCOVERY_STANDARD_REPORT,
            start_date=period_start,
            end_date=period_end,
        )
    )
    summary.discovery_files = len(discovery_paths)
    for row in discovery_rows:
        summary.discovery_rows += 1
        summary.impressions += row.impressions
        summary.unique_impressions += row.unique_impressions
        summary.product_page_views += row.product_page_views
        summary.unique_product_page_views += row.unique_product_page_views
        summary.buy_or_get_taps += row.buy_or_get_taps
        summary.product_page_buy_or_get_taps += row.product_page_buy_or_get_taps
        summary.dates.add(row.event_date)

    download_rows, summary.download_superseded_rows, download_paths = (
        _canonical_acquisition_rows(
            rows_by_report[DOWNLOADS_STANDARD_REPORT],
            year=year,
            report_name=DOWNLOADS_STANDARD_REPORT,
            start_date=period_start,
            end_date=period_end,
        )
    )
    summary.download_files = len(download_paths)
    for row in download_rows:
        summary.download_rows += 1
        summary.first_time_downloads += row.first_time_downloads
        summary.redownloads += row.redownloads
        summary.dates.add(row.event_date)

    # A row proves an observed count, not that every absent date is zero. A
    # snapshot covers history through its lag-adjusted processing date. Each
    # completed ongoing DAILY instance covers exactly its own lag-adjusted date,
    # including header-only instances. Every remaining date must be present.
    discovery_datasets = rows_by_report[DISCOVERY_STANDARD_REPORT]
    discovery_snapshot_completion_dates = [
        dataset.processing_date
        - timedelta(days=DISCOVERY_COMPLETENESS_LAG_DAYS)
        for dataset in discovery_datasets
        if dataset.access_type == "ONE_TIME_SNAPSHOT"
    ]
    if discovery_snapshot_completion_dates:
        summary.discovery_snapshot_complete_through = max(
            discovery_snapshot_completion_dates
        )
    summary.discovery_ongoing_complete_dates = {
        dataset.processing_date
        - timedelta(days=DISCOVERY_COMPLETENESS_LAG_DAYS)
        for dataset in discovery_datasets
        if dataset.access_type == "ONGOING"
    }

    download_datasets = rows_by_report[DOWNLOADS_STANDARD_REPORT]
    download_snapshot_completion_dates = [
        dataset.processing_date
        - timedelta(days=DOWNLOADS_COMPLETENESS_LAG_DAYS)
        for dataset in download_datasets
        if dataset.access_type == "ONE_TIME_SNAPSHOT"
    ]
    if download_snapshot_completion_dates:
        summary.downloads_snapshot_complete_through = max(
            download_snapshot_completion_dates
        )
    summary.downloads_ongoing_complete_dates = {
        dataset.processing_date
        - timedelta(days=DOWNLOADS_COMPLETENESS_LAG_DAYS)
        for dataset in download_datasets
        if dataset.access_type == "ONGOING"
    }

    summary.discovery_standard_available = summary.discovery_complete_for_window(
        period_start, period_end
    )
    summary.downloads_standard_available = summary.downloads_complete_for_window(
        period_start, period_end
    )
    return summary


def build_performance_summary(
    summary: PurchaseSummary,
    *,
    as_of: date,
    data_cutoff: date,
    completeness_lag_days: int,
    goal: Decimal,
    net_sales_goal: int = DEFAULT_ANNUAL_NET_SALES_GOAL,
) -> PerformanceSummary:
    """Calculate rolling fee coverage and a complete 28-day proceeds run rate."""

    if net_sales_goal <= 0:
        raise ConfigurationError("The annual net-sales goal must be greater than zero")

    goal_window_end = data_cutoff
    goal_window_start = goal_window_end - timedelta(days=364)
    run_rate_window_end = data_cutoff
    run_rate_window_start = run_rate_window_end - timedelta(days=27)
    current_week_end = data_cutoff
    current_week_start = current_week_end - timedelta(days=6)
    prior_week_end = current_week_start - timedelta(days=1)
    prior_week_start = prior_week_end - timedelta(days=6)
    required_processing_date = data_cutoff + timedelta(
        days=PURCHASES_COMPLETENESS_LAG_DAYS
    )
    current_data_complete = (
        summary.latest_standard_processing_date is not None
        and summary.latest_standard_processing_date >= required_processing_date
    )
    run_rate_complete = summary.standard_complete_for_window(
        run_rate_window_start, run_rate_window_end
    )
    comparison_complete = summary.standard_complete_for_window(
        prior_week_start, current_week_end
    )
    purchase_data_complete = current_data_complete and run_rate_complete
    weekly_data_complete = current_data_complete and comparison_complete

    goal_rows = [
        row
        for row in summary.canonical_rows
        if goal_window_start <= row.event_date <= goal_window_end
    ]
    goal_window_purchases: int | None = None
    goal_window_proceeds: Decimal | None = None
    goal_remaining: Decimal | None = None
    goal_met: bool | None = None
    net_sales_progress: int | None = None
    net_sales_progress_percent: Decimal | None = None
    net_sales_remaining: int | None = None
    net_sales_goal_met: bool | None = None
    goal_window_complete = summary.standard_complete_for_window(
        goal_window_start, goal_window_end
    )
    if goal_window_complete:
        goal_window_purchases = sum(row.purchases for row in goal_rows)
        goal_window_proceeds = sum(
            (row.proceeds for row in goal_rows), Decimal("0")
        )
        goal_remaining = max(goal - goal_window_proceeds, Decimal("0"))
        goal_met = goal_window_proceeds >= goal
        net_sales_progress = goal_window_purchases
        net_sales_progress_percent = (
            Decimal(net_sales_progress)
            / Decimal(net_sales_goal)
            * Decimal("100")
        )
        net_sales_remaining = max(net_sales_goal - net_sales_progress, 0)
        net_sales_goal_met = net_sales_progress >= net_sales_goal

    run_rate_purchases: int | None = None
    run_rate_proceeds: Decimal | None = None
    annualized_run_rate_proceeds: Decimal | None = None
    run_rate_on_pace: bool | None = None
    if purchase_data_complete:
        run_rate_rows = [
            row
            for row in summary.canonical_rows
            if run_rate_window_start <= row.event_date <= run_rate_window_end
        ]
        run_rate_purchases = sum(row.purchases for row in run_rate_rows)
        run_rate_proceeds = sum(
            (row.proceeds for row in run_rate_rows), Decimal("0")
        )
        annualized_run_rate_proceeds = (
            run_rate_proceeds * Decimal("365") / Decimal("28")
        )
        run_rate_on_pace = annualized_run_rate_proceeds >= goal

    current_week_purchases: int | None = None
    current_week_proceeds: Decimal | None = None
    prior_week_purchases: int | None = None
    prior_week_proceeds: Decimal | None = None
    week_over_week_proceeds_percent: Decimal | None = None
    if weekly_data_complete:
        current_rows = [
            row
            for row in summary.canonical_rows
            if current_week_start <= row.event_date <= current_week_end
        ]
        prior_rows = [
            row
            for row in summary.canonical_rows
            if prior_week_start <= row.event_date <= prior_week_end
        ]
        current_week_purchases = sum(row.purchases for row in current_rows)
        current_week_proceeds = sum(
            (row.proceeds for row in current_rows), Decimal("0")
        )
        prior_week_purchases = sum(row.purchases for row in prior_rows)
        prior_week_proceeds = sum(
            (row.proceeds for row in prior_rows), Decimal("0")
        )
        if prior_week_proceeds != 0:
            week_over_week_proceeds_percent = (
                (current_week_proceeds - prior_week_proceeds)
                / abs(prior_week_proceeds)
                * Decimal("100")
            )

    return PerformanceSummary(
        as_of=as_of,
        data_cutoff=data_cutoff,
        completeness_lag_days=completeness_lag_days,
        annual_goal=goal,
        annual_net_sales_goal=net_sales_goal,
        goal_window_complete=goal_window_complete,
        purchase_data_complete=purchase_data_complete,
        weekly_data_complete=weekly_data_complete,
        goal_window_start=goal_window_start,
        goal_window_end=goal_window_end,
        goal_window_purchases=goal_window_purchases,
        goal_window_proceeds=goal_window_proceeds,
        goal_remaining=goal_remaining,
        goal_met=goal_met,
        net_sales_progress=net_sales_progress,
        net_sales_progress_percent=net_sales_progress_percent,
        net_sales_remaining=net_sales_remaining,
        net_sales_goal_met=net_sales_goal_met,
        run_rate_window_start=run_rate_window_start,
        run_rate_window_end=run_rate_window_end,
        run_rate_purchases=run_rate_purchases,
        run_rate_proceeds=run_rate_proceeds,
        annualized_run_rate_proceeds=annualized_run_rate_proceeds,
        run_rate_on_pace=run_rate_on_pace,
        current_week_start=current_week_start,
        current_week_end=current_week_end,
        prior_week_start=prior_week_start,
        prior_week_end=prior_week_end,
        current_week_purchases=current_week_purchases,
        current_week_proceeds=current_week_proceeds,
        prior_week_purchases=prior_week_purchases,
        prior_week_proceeds=prior_week_proceeds,
        week_over_week_proceeds_percent=week_over_week_proceeds_percent,
    )


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


def _summary_period(summary: PurchaseSummary) -> ReportingPeriod:
    if summary.period is None:
        raise DataError("Purchase summary is missing its reporting period")
    return summary.period


def _rate_text(value: Decimal | None) -> str:
    return "n/a" if value is None else _format_percent(value)


def _terminal_acquisition_report(
    summary: AcquisitionSummary, output: TextIO
) -> None:
    print("Acquisition funnel (Standard reports):", file=output)
    if summary.discovery_standard_available:
        print(
            f"  Impression events: {summary.impressions} "
            f"({summary.unique_impressions} unique counts)",
            file=output,
        )
        print(
            f"  Product page views: {summary.product_page_views} "
            f"({summary.unique_product_page_views} unique counts; "
            f"{_rate_text(summary.product_page_view_rate)} per impression event)",
            file=output,
        )
        print(
            f"  Buy/Get/Pre-order taps: {summary.buy_or_get_taps}; "
            f"product-page taps: {summary.product_page_buy_or_get_taps} "
            f"({_rate_text(summary.product_page_buy_or_get_tap_rate)} of product "
            "page views)",
            file=output,
        )
    else:
        print(
            "  Discovery: unknown/pending - no verified App Store Discovery and "
            "Engagement Standard dataset.",
            file=output,
        )
    if summary.downloads_standard_available:
        print(
            f"  First-time downloads: {summary.first_time_downloads}; "
            f"redownloads: {summary.redownloads}; total downloads: "
            f"{summary.total_downloads}",
            file=output,
        )
        if summary.discovery_standard_available:
            print(
                "  First-time download rates: "
                f"{_rate_text(summary.first_time_download_to_buy_or_get_tap_rate)} "
                "per Buy/Get/Pre-order tap; "
                f"{_rate_text(summary.first_time_download_rate)} per impression event",
                file=output,
            )
    else:
        print(
            "  Downloads: unknown/pending - no verified App Store Downloads "
            "Standard dataset.",
            file=output,
        )
    print(
        "  Detailed acquisition reports are privacy-limited supplements and are "
        "not used for funnel totals.",
        file=output,
    )


def _terminal_performance_report(
    performance: PerformanceSummary, output: TextIO
) -> None:
    print(
        f"Trailing 365-day goal window: {performance.goal_window_start.isoformat()} "
        f"through {performance.goal_window_end.isoformat()}",
        file=output,
    )
    print(
        f"Common data cutoff: {performance.data_cutoff.isoformat()} "
        f"(as of {performance.as_of.isoformat()}, "
        f"{performance.completeness_lag_days}-day lag)",
        file=output,
    )
    if not performance.goal_window_complete:
        print(
            "Annual net-sales benchmark "
            f"({performance.annual_net_sales_goal} net sales): unknown - "
            "Purchases Standard is not verified complete for the exact trailing "
            "365-day goal window.",
            file=output,
        )
    else:
        assert performance.net_sales_progress is not None
        assert performance.net_sales_progress_percent is not None
        assert performance.net_sales_remaining is not None
        assert performance.net_sales_goal_met is not None
        sales_bar, _ = goal_progress(
            Decimal(performance.net_sales_progress),
            Decimal(performance.annual_net_sales_goal),
        )
        print(
            f"Annual net-sales benchmark [{sales_bar}] "
            f"{performance.net_sales_progress} / "
            f"{performance.annual_net_sales_goal} "
            f"({_format_percent(performance.net_sales_progress_percent)}); "
            f"{performance.net_sales_remaining} remaining; met: "
            f"{'yes' if performance.net_sales_goal_met else 'no'}",
            file=output,
        )
    if not performance.weekly_data_complete:
        print(
            "Weekly comparison: unknown - Purchases Standard is not verified "
            "complete for both exact seven-day windows through the cutoff.",
            file=output,
        )
    else:
        if performance.week_over_week_proceeds_percent is None:
            if performance.current_week_proceeds == 0:
                comparison = "flat at $0.00"
            else:
                comparison = "new proceeds from a $0.00 prior week"
        else:
            comparison = (
                f"{_format_percent(performance.week_over_week_proceeds_percent)} WoW"
            )
        print(
            f"Last 7 days: {performance.current_week_purchases} net purchase(s), "
            f"{format_money(performance.current_week_proceeds)}; prior 7 days: "
            f"{performance.prior_week_purchases} net purchase(s), "
            f"{format_money(performance.prior_week_proceeds)} ({comparison})",
            file=output,
        )
    if not performance.purchase_data_complete:
        print(
            f"Trailing 28 days ({performance.run_rate_window_start.isoformat()} "
            f"through {performance.run_rate_window_end.isoformat()}): unknown - "
            "Purchases Standard is not verified complete for the full window "
            "through the cutoff, so incomplete proceeds were not annualized.",
            file=output,
        )
        return
    print(
        f"Trailing 28 days ({performance.run_rate_window_start.isoformat()} through "
        f"{performance.run_rate_window_end.isoformat()}): "
        f"{performance.run_rate_purchases} net purchase(s), "
        f"{format_money(performance.run_rate_proceeds)}; annualized proceeds run "
        f"rate {format_money(performance.annualized_run_rate_proceeds)} "
        f"({'on target' if performance.run_rate_on_pace else 'below target'} for "
        f"{format_money(performance.annual_goal)}/365 days)",
        file=output,
    )


def terminal_report(
    summary: PurchaseSummary,
    goal: Decimal,
    output: TextIO,
    *,
    acquisition: AcquisitionSummary | None = None,
    performance: PerformanceSummary | None = None,
) -> None:
    period = _summary_period(summary)
    if summary.standard_available:
        bar, percentage = goal_progress(summary.proceeds, goal)
        label = (
            "Trailing 365-day proceeds"
            if period.mode == PERIOD_TRAILING_365
            else "Calendar-year proceeds"
        )
        print(
            f"{label} [{bar}] {format_money(summary.proceeds)} / "
            f"{format_money(goal)} ({_format_percent(percentage)})",
            file=output,
        )
        print(
            f"{period.label}: {summary.purchases} net purchase(s), "
            f"{summary.refund_units} refunded unit(s), {summary.rows} canonical row(s), "
            f"{summary.superseded_rows} superseded row(s) from Standard",
            file=output,
        )
    else:
        print(
            f"{period.label} proceeds: unknown/pending - App Store Purchases "
            "Standard is not verified complete for the exact reporting window.",
            file=output,
        )
    if performance is not None:
        _terminal_performance_report(performance, output)
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
    if acquisition is not None:
        _terminal_acquisition_report(acquisition, output)


def markdown_report(
    summary: PurchaseSummary,
    *,
    goal: Decimal,
    as_of: date,
    acquisition: AcquisitionSummary | None = None,
    performance: PerformanceSummary | None = None,
) -> str:
    period = _summary_period(summary)
    if summary.standard_available:
        bar, percentage = goal_progress(summary.proceeds, goal)
        if summary.dates:
            first_date = min(summary.dates).isoformat()
            last_date = max(summary.dates).isoformat()
            data_period = (
                first_date if first_date == last_date else f"{first_date} to {last_date}"
            )
        else:
            data_period = "No dated purchase rows"

        lines = [
            f"# EaselWall App Store weekly report - {as_of.isoformat()}",
            "",
            f"- Reporting period: {period.label}",
            f"- Data period: {data_period}",
            f"- Net purchases: {summary.purchases}",
            f"- Refunded units: {summary.refund_units}",
            f"- Estimated net proceeds: {format_money(summary.proceeds)}",
            f"- Developer fee goal: {format_money(goal)}",
            f"- Goal progress: `[{bar}]` {_format_percent(percentage)}",
            f"- Canonical Standard rows: {summary.rows} across "
            f"{summary.files} selected file(s)",
            f"- Superseded Standard rows excluded: {summary.superseded_rows}",
            f"- Standard source archive: {summary.files_scanned} file(s), "
            f"{summary.datasets_considered} logical dataset(s)",
            "",
        ]
    else:
        lines = [
            f"# EaselWall App Store weekly report - {as_of.isoformat()}",
            "",
            f"- Reporting period: {period.label}",
            "- Proceeds status: **unknown/pending**",
            "- App Store Purchases Standard is not verified complete for the "
            "exact reporting window.",
            "- No zero-dollar result was inferred from incomplete or missing data.",
        ]
        if performance is not None:
            lines.extend(
                [
                    "- Trailing 365-day proceeds status: **unknown/pending**",
                    f"- Trailing 365-day goal window: "
                    f"{performance.goal_window_start.isoformat()} to "
                    f"{performance.goal_window_end.isoformat()}",
                    f"- Common data cutoff: {performance.data_cutoff.isoformat()}",
                ]
            )
        lines.extend(
            [
                f"- Standard source archive: {summary.files_scanned} file(s), "
                f"{summary.datasets_considered} logical dataset(s)",
                "",
                "Campaign attribution comes from App Store Purchases Detailed and is "
                "privacy-limited; it cannot replace an incomplete Standard goal "
                "window.",
                "",
            ]
        )
    if performance is not None:
        lines.extend(
            [
                "## Reporting cutoff, annual benchmarks, and run rate",
                "",
                f"- Report generated as of: {performance.as_of.isoformat()}",
                f"- Common data cutoff: {performance.data_cutoff.isoformat()}",
                f"- Conservative completeness lag: "
                f"{performance.completeness_lag_days} days",
                f"- Trailing 365-day benchmark window: "
                f"{performance.goal_window_start.isoformat()} to "
                f"{performance.goal_window_end.isoformat()}",
            ]
        )
        if not performance.goal_window_complete:
            lines.append(
                f"- Annual net-sales benchmark "
                f"({performance.annual_net_sales_goal} net sales): **unknown** - "
                "Purchases Standard is not verified complete for the exact "
                "trailing 365-day goal window."
            )
        else:
            assert performance.net_sales_progress is not None
            assert performance.net_sales_progress_percent is not None
            assert performance.net_sales_remaining is not None
            assert performance.net_sales_goal_met is not None
            lines.extend(
                [
                    "- Annual net-sales benchmark progress: "
                    f"{performance.net_sales_progress} / "
                    f"{performance.annual_net_sales_goal} "
                    f"({_format_percent(performance.net_sales_progress_percent)})",
                    "- Annual net-sales benchmark remaining: "
                    f"{performance.net_sales_remaining}",
                    "- Annual net-sales benchmark met: "
                    f"{'yes' if performance.net_sales_goal_met else 'no'}",
                ]
            )
        if not performance.weekly_data_complete:
            lines.extend(
                [
                    "- Weekly comparison: **unknown** - Purchases Standard is not "
                    "verified complete for both exact seven-day windows through "
                    "the cutoff.",
                ]
            )
        else:
            lines.extend(
                [
                    f"- Current week ({performance.current_week_start.isoformat()} "
                    f"to {performance.current_week_end.isoformat()}): "
                    f"{performance.current_week_purchases} net purchase(s), "
                    f"{format_money(performance.current_week_proceeds)}",
                    f"- Prior week ({performance.prior_week_start.isoformat()} "
                    f"to {performance.prior_week_end.isoformat()}): "
                    f"{performance.prior_week_purchases} net purchase(s), "
                    f"{format_money(performance.prior_week_proceeds)}",
                ]
            )
            if performance.week_over_week_proceeds_percent is None:
                if performance.current_week_proceeds == 0:
                    lines.append("- Proceeds week over week: flat at $0.00")
                else:
                    lines.append(
                        "- Proceeds week over week: new proceeds from a $0.00 "
                        "prior week"
                    )
            else:
                lines.append(
                    "- Proceeds week over week: "
                    f"{_format_percent(performance.week_over_week_proceeds_percent)}"
                )
        if not performance.purchase_data_complete:
            lines.extend(
                [
                    f"- Trailing 28-day run-rate window: "
                    f"{performance.run_rate_window_start.isoformat()} to "
                    f"{performance.run_rate_window_end.isoformat()}",
                    "- Trailing 28-day annualized proceeds run rate: **unknown** - "
                    "Purchases Standard is not verified complete for the full "
                    "window, so incomplete proceeds were not annualized.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"- Trailing 28-day run-rate window: "
                    f"{performance.run_rate_window_start.isoformat()} to "
                    f"{performance.run_rate_window_end.isoformat()}",
                    f"- Trailing 28-day net purchases: "
                    f"{performance.run_rate_purchases}",
                    f"- Trailing 28-day proceeds: "
                    f"{format_money(performance.run_rate_proceeds)}",
                    "- Annualized proceeds run rate: "
                    f"{format_money(performance.annualized_run_rate_proceeds)}",
                    f"- Run-rate target: "
                    f"{'on target' if performance.run_rate_on_pace else 'below target'} "
                    f"for {format_money(performance.annual_goal)} per 365 days",
                    "",
                ]
            )
    if acquisition is not None:
        lines.extend(
            [
                "## Acquisition funnel",
                "",
                "Funnel totals use only Apple's Standard reports. Detailed reports "
                "remain privacy-limited supplements and are not substituted when a "
                "Standard dataset is missing.",
                "",
            ]
        )
        if acquisition.discovery_standard_available:
            lines.extend(
                [
                    "| Discovery metric | Count | Directional rate |",
                    "| --- | ---: | ---: |",
                    f"| Impression events | {acquisition.impressions} | - |",
                    f"| Unique impression counts | "
                    f"{acquisition.unique_impressions} | - |",
                    f"| Product page views | {acquisition.product_page_views} | "
                    f"{_rate_text(acquisition.product_page_view_rate)} per "
                    "impression event |",
                    f"| Unique product page view counts | "
                    f"{acquisition.unique_product_page_views} | - |",
                    f"| Buy/Get/Pre-order taps | {acquisition.buy_or_get_taps} | - |",
                    f"| Product-page Buy/Get/Pre-order taps | "
                    f"{acquisition.product_page_buy_or_get_taps} | "
                    f"{_rate_text(acquisition.product_page_buy_or_get_tap_rate)} "
                    "of product page views |",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "- Discovery status: **unknown/pending** - no verified App Store "
                    "Discovery and Engagement Standard dataset.",
                    "",
                ]
            )
        if acquisition.downloads_standard_available:
            lines.extend(
                [
                    f"- First-time downloads: {acquisition.first_time_downloads}",
                    f"- Redownloads: {acquisition.redownloads}",
                    f"- Total downloads: {acquisition.total_downloads}",
                ]
            )
            if acquisition.discovery_standard_available:
                lines.extend(
                    [
                        "- First-time downloads / Buy/Get/Pre-order taps: "
                        f"{_rate_text(acquisition.first_time_download_to_buy_or_get_tap_rate)}",
                        "- First-time downloads / impression events: "
                        f"{_rate_text(acquisition.first_time_download_rate)}",
                    ]
                )
            lines.append("")
        else:
            lines.extend(
                [
                    "- Downloads status: **unknown/pending** - no verified App Store "
                    "Downloads Standard dataset.",
                    "",
                ]
            )
        lines.extend(
            [
                "Apple's Discovery schema reports `Engagement Type = Get` for taps "
                "on Get, Buy, or Pre-order; it does not expose those three actions "
                "separately. Rates above are explicitly named ratios of report event "
                "counts, not Apple's UI Conversion Rate.",
                "",
                f"Discovery source archive: {acquisition.discovery_files_scanned} "
                "Standard file(s), "
                f"{acquisition.discovery_datasets_considered} logical dataset(s), "
                f"{acquisition.discovery_superseded_rows} superseded row(s).",
                f"Download source archive: {acquisition.download_files_scanned} "
                "Standard file(s), "
                f"{acquisition.download_datasets_considered} logical dataset(s), "
                f"{acquisition.download_superseded_rows} superseded row(s).",
                "",
            ]
        )
    lines.extend(
        [
            "## Campaign attribution (privacy-limited)",
            "",
            "Campaign rows come only from App Store Purchases Detailed. Apple may "
            "suppress them at low volume, and they are never added to the Standard total.",
            f"Detailed source archive: {summary.campaign_files_scanned} file(s), "
            f"{summary.campaign_datasets_considered} logical dataset(s), "
            f"{summary.campaign_superseded_rows} superseded row(s).",
            "",
        ]
    )
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
            "Instances sharing that newest processingDate must have identical values "
            "for every parsed source field; conflicts stop the report even when totals "
            "match. After agreement, ONGOING is the deterministic preferred copy. "
            "Non-overlapping Date batches coexist.",
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
    request: ReportRequest | None,
    app: AppInfo | None = None,
    acquisition: AcquisitionSummary | None = None,
    performance: PerformanceSummary | None = None,
    output_path: Path | None = None,
) -> Path:
    """Write current availability and proceeds for one request or combined scope."""

    period = _summary_period(summary)
    if app is not None and (
        app.app_id != summary.app_id or app.bundle_id != summary.bundle_id
    ):
        raise DataError("Summary app identity does not match the resolved app")

    def serialized_decimal(
        value: Decimal | None, quantum: Decimal = Decimal("0.01")
    ) -> str | None:
        if value is None:
            return None
        return str(value.quantize(quantum, rounding=ROUND_HALF_UP))

    scope = {
        "appId": summary.app_id,
        "bundleId": summary.bundle_id,
        "accessType": request.access_type if request is not None else "COMBINED",
        "requestId": request.request_id if request is not None else None,
        "calendarPartitionYear": summary.year,
        "reportingPeriod": {
            "mode": period.mode,
            "startDate": period.start_date.isoformat(),
            "endDate": period.end_date.isoformat(),
        },
    }
    if request is None:
        scope["managedRequestScope"] = "ALL_VERIFIED_MANAGED_REQUESTS_IN_INPUT"

    payload = {
        "generatedOn": date.today().isoformat(),
        "scope": scope,
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
    if acquisition is not None:
        discovery_available = acquisition.discovery_standard_available
        downloads_available = acquisition.downloads_standard_available

        payload["acquisitionFunnel"] = {
            "discoverySource": DISCOVERY_STANDARD_REPORT,
            "downloadsSource": DOWNLOADS_STANDARD_REPORT,
            "discoveryStandardDataAvailable": discovery_available,
            "downloadsStandardDataAvailable": downloads_available,
            "detailedReportsSupplemental": True,
            "detailedReportsPrivacyLimited": True,
            "discoveryCompleteThroughCutoff": (
                acquisition.discovery_complete_through(performance.data_cutoff)
                if performance is not None
                else None
            ),
            "downloadsCompleteThroughCutoff": (
                acquisition.downloads_complete_through(performance.data_cutoff)
                if performance is not None
                else None
            ),
            "latestDiscoveryProcessingDate": (
                acquisition.latest_discovery_processing_date.isoformat()
                if acquisition.latest_discovery_processing_date is not None
                else None
            ),
            "latestDownloadsProcessingDate": (
                acquisition.latest_download_processing_date.isoformat()
                if acquisition.latest_download_processing_date is not None
                else None
            ),
            "discoveryDetailedDataAvailable": (
                acquisition.discovery_detailed_available
            ),
            "downloadsDetailedDataAvailable": (
                acquisition.downloads_detailed_available
            ),
            "impressions": (
                acquisition.impressions if discovery_available else None
            ),
            "uniqueImpressionCounts": (
                acquisition.unique_impressions if discovery_available else None
            ),
            "productPageViews": (
                acquisition.product_page_views if discovery_available else None
            ),
            "uniqueProductPageViewCounts": (
                acquisition.unique_product_page_views
                if discovery_available
                else None
            ),
            "buyTaps": None,
            "buyTapsUnavailableReason": (
                "Apple combines Get, Buy, and Pre-order as Engagement Type Get"
            ),
            "buyOrGetOrPreOrderTaps": (
                acquisition.buy_or_get_taps if discovery_available else None
            ),
            "productPageBuyOrGetOrPreOrderTaps": (
                acquisition.product_page_buy_or_get_taps
                if discovery_available
                else None
            ),
            "firstTimeDownloads": (
                acquisition.first_time_downloads if downloads_available else None
            ),
            "redownloads": (
                acquisition.redownloads if downloads_available else None
            ),
            "totalDownloads": (
                acquisition.total_downloads if downloads_available else None
            ),
            "ratesPercent": {
                "productPageViewsPerImpressionEvent": serialized_decimal(
                    acquisition.product_page_view_rate
                ),
                "productPageBuyOrGetOrPreOrderTapsPerProductPageView": (
                    serialized_decimal(acquisition.product_page_buy_or_get_tap_rate)
                ),
                "firstTimeDownloadsPerBuyOrGetOrPreOrderTap": serialized_decimal(
                    acquisition.first_time_download_to_buy_or_get_tap_rate
                ),
                "firstTimeDownloadsPerImpressionEvent": serialized_decimal(
                    acquisition.first_time_download_rate
                ),
            },
            "discoveryRows": acquisition.discovery_rows,
            "discoverySupersededRows": acquisition.discovery_superseded_rows,
            "downloadRows": acquisition.download_rows,
            "downloadSupersededRows": acquisition.download_superseded_rows,
        }
    if performance is not None:
        payload["reportingWindow"] = {
            "asOf": performance.as_of.isoformat(),
            "dataCutoff": performance.data_cutoff.isoformat(),
            "completenessLagDays": performance.completeness_lag_days,
            "purchasesCompleteThroughCutoff": performance.purchase_data_complete,
            "goalWindow": {
                "start": performance.goal_window_start.isoformat(),
                "end": performance.goal_window_end.isoformat(),
                "days": 365,
                "complete": performance.goal_window_complete,
                "annualGoalInUSD": serialized_decimal(performance.annual_goal),
                "netPurchases": performance.goal_window_purchases,
                "proceedsInUSD": serialized_decimal(
                    performance.goal_window_proceeds
                ),
                "remainingToGoalInUSD": serialized_decimal(
                    performance.goal_remaining
                ),
                "goalMet": performance.goal_met,
                "netSalesBenchmark": {
                    "annualGoal": performance.annual_net_sales_goal,
                    "progress": performance.net_sales_progress,
                    "progressPercent": serialized_decimal(
                        performance.net_sales_progress_percent
                    ),
                    "remaining": performance.net_sales_remaining,
                    "met": performance.net_sales_goal_met,
                },
            },
            "currentWeek": {
                "start": performance.current_week_start.isoformat(),
                "end": performance.current_week_end.isoformat(),
                "complete": performance.weekly_data_complete,
                "netPurchases": performance.current_week_purchases,
                "proceedsInUSD": serialized_decimal(
                    performance.current_week_proceeds
                ),
            },
            "priorWeek": {
                "start": performance.prior_week_start.isoformat(),
                "end": performance.prior_week_end.isoformat(),
                "complete": performance.weekly_data_complete,
                "netPurchases": performance.prior_week_purchases,
                "proceedsInUSD": serialized_decimal(performance.prior_week_proceeds),
            },
            "weekOverWeekProceedsPercent": serialized_decimal(
                performance.week_over_week_proceeds_percent
            ),
            "runRateWindow": {
                "start": performance.run_rate_window_start.isoformat(),
                "end": performance.run_rate_window_end.isoformat(),
                "days": 28,
                "complete": performance.purchase_data_complete,
                "netPurchases": performance.run_rate_purchases,
                "proceedsInUSD": serialized_decimal(performance.run_rate_proceeds),
                "annualizedProceedsInUSD": serialized_decimal(
                    performance.annualized_run_rate_proceeds
                ),
                "annualGoalInUSD": serialized_decimal(performance.annual_goal),
                "onPace": performance.run_rate_on_pace,
            },
        }
    path = output_dir / "latest-summary.json" if output_path is None else output_path
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


def parse_net_sales_goal(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise ConfigurationError(
            "--net-sales-goal must be a positive whole number"
        )
    return int(value)


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
        help="managed report archive or single purchase TSV to scan",
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
        "--summary-output",
        type=Path,
        help=(
            "combined managed-input JSON output path; default: beside a custom "
            "Markdown output or in the private report root"
        ),
    )
    report.add_argument(
        "--as-of",
        help="report date in YYYY-MM-DD format (default: today)",
    )
    report.add_argument(
        "--period",
        choices=(PERIOD_TRAILING_365, PERIOD_CALENDAR_YEAR),
        help=(
            "accounting window (default: trailing-365; --year selects "
            "calendar-year)"
        ),
    )
    report.add_argument(
        "--through",
        help=(
            "explicit inclusive data cutoff in YYYY-MM-DD format; default: "
            "--as-of minus --data-lag-days"
        ),
    )
    report.add_argument(
        "--data-lag-days",
        type=int,
        default=COMMON_COMPLETENESS_LAG_DAYS,
        help=(
            "conservative common Apple completeness lag (default: "
            f"{COMMON_COMPLETENESS_LAG_DAYS})"
        ),
    )
    report.add_argument(
        "--year",
        type=int,
        help="calendar year to include; implies --period calendar-year",
    )
    report.add_argument(
        "--goal",
        default="99",
        help="annual proceeds goal in USD (default: 99)",
    )
    report.add_argument(
        "--net-sales-goal",
        default=str(DEFAULT_ANNUAL_NET_SALES_GOAL),
        help=(
            "annual net-sales benchmark for the exact trailing-365 window "
            f"(default: {DEFAULT_ANNUAL_NET_SALES_GOAL})"
        ),
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
            if not 0 <= args.data_lag_days <= 30:
                raise ConfigurationError("--data-lag-days must be between 0 and 30")
            requested_cutoff = (
                as_of - timedelta(days=args.data_lag_days)
                if args.through is None
                else parse_iso_date(args.through, "--through")
            )
            if requested_cutoff > as_of:
                raise ConfigurationError("--through cannot be after --as-of")
            if args.year is not None and not 2000 <= args.year <= 9999:
                raise ConfigurationError("--year must be between 2000 and 9999")
            if args.period == PERIOD_TRAILING_365 and args.year is not None:
                raise ConfigurationError(
                    "--year cannot be combined with --period trailing-365"
                )
            data_cutoff = requested_cutoff
            expected_app_id = args.app_id or DEFAULT_APP_ID
            if not APP_ID_PATTERN.fullmatch(expected_app_id):
                raise ConfigurationError(
                    "--app-id may contain only letters, numbers, and hyphens"
                )
            period_mode = args.period
            if period_mode is None:
                period_mode = (
                    PERIOD_CALENDAR_YEAR
                    if args.year is not None
                    else PERIOD_TRAILING_365
                )
            if period_mode == PERIOD_CALENDAR_YEAR:
                calendar_year = (
                    data_cutoff.year if args.year is None else args.year
                )
                calendar_start = date(calendar_year, 1, 1)
                if data_cutoff < calendar_start:
                    raise ConfigurationError(
                        "The data cutoff cannot be before the requested calendar year"
                    )
                period = ReportingPeriod(
                    PERIOD_CALENDAR_YEAR,
                    calendar_start,
                    min(data_cutoff, date(calendar_year, 12, 31)),
                )
            else:
                period = ReportingPeriod.trailing_365(data_cutoff)
            goal = parse_goal(args.goal)
            net_sales_goal = parse_net_sales_goal(args.net_sales_goal)
            summary = summarize_purchases(
                args.input,
                period=period,
                expected_app_id=expected_app_id,
                expected_bundle_id=args.bundle_id,
            )
            acquisition = summarize_acquisition(
                args.input,
                year=period.end_date.year,
                expected_app_id=expected_app_id,
                expected_bundle_id=args.bundle_id,
                start_date=period.start_date,
                end_date=period.end_date,
            )
            goal_period = ReportingPeriod.trailing_365(data_cutoff)
            goal_summary = (
                summary
                if period == goal_period
                else summarize_purchases(
                    args.input,
                    period=goal_period,
                    expected_app_id=expected_app_id,
                    expected_bundle_id=args.bundle_id,
                )
            )
            performance = build_performance_summary(
                goal_summary,
                as_of=as_of,
                data_cutoff=data_cutoff,
                completeness_lag_days=args.data_lag_days,
                goal=goal,
                net_sales_goal=net_sales_goal,
            )
            terminal_report(
                summary,
                goal,
                stdout,
                acquisition=acquisition,
                performance=performance,
            )
            if summary.unmanaged_standard_files:
                raise DataError(
                    "Standalone or unmanaged Purchases Standard TSVs do not "
                    "verify complete reporting-window coverage; parsed rows are "
                    "inspection-only and no report was written"
                )
            if summary.files_scanned == 0:
                raise DataError(
                    "App Store Purchases Standard is not available yet; revenue is "
                    "unknown/pending and no report was written"
                )
            canonical_path = (
                args.canonical_output
                if args.canonical_output is not None
                else DEFAULT_REPORT_OUTPUT
                / "canonical"
                / f"purchases-{period.output_suffix}.tsv"
            )
            if summary.standard_available:
                canonical_changed = write_canonical_purchases(
                    canonical_path, summary
                )
                canonical_action = "Wrote" if canonical_changed else "Unchanged"
                print(
                    f"{canonical_action} canonical purchase TSV: {canonical_path}",
                    file=stdout,
                )
            else:
                print(
                    "Skipped canonical purchase TSV: App Store Purchases Standard "
                    f"is not verified complete for {period.start_date.isoformat()} "
                    f"through {period.end_date.isoformat()}.",
                    file=stdout,
                )
            campaign_path = (
                args.campaign_output
                if args.campaign_output is not None
                else DEFAULT_REPORT_OUTPUT
                / "canonical"
                / f"campaigns-{period.output_suffix}.tsv"
            )
            if summary.campaign_available:
                campaign_changed = write_canonical_campaigns(
                    campaign_path, summary
                )
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
            content = markdown_report(
                summary,
                goal=goal,
                as_of=as_of,
                acquisition=acquisition,
                performance=performance,
            )
            changed = write_weekly_report(report_path, content)
            action = "Wrote" if changed else "Unchanged"
            print(f"{action} Markdown report: {report_path}", file=stdout)
            summary_json_path = (
                args.summary_output
                if args.summary_output is not None
                else (
                    args.output.parent / "combined-summary.json"
                    if args.output is not None
                    else DEFAULT_REPORT_OUTPUT / "combined-summary.json"
                )
            )
            write_latest_summary(
                summary_json_path.parent,
                summary,
                request=None,
                acquisition=acquisition,
                performance=performance,
                output_path=summary_json_path,
            )
            print(f"Wrote combined JSON summary: {summary_json_path}", file=stdout)
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
                app=app,
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
            fetch_as_of = date.today()
            fetch_cutoff = fetch_as_of - timedelta(
                days=COMMON_COMPLETENESS_LAG_DAYS
            )
            fetch_period = ReportingPeriod.trailing_365(fetch_cutoff)
            purchase_summary = summarize_purchases(
                purchase_scope,
                period=fetch_period,
                expected_app_id=app.app_id,
                expected_bundle_id=app.bundle_id,
            )
            acquisition_summary = summarize_acquisition(
                purchase_scope,
                year=fetch_cutoff.year,
                expected_app_id=app.app_id,
                expected_bundle_id=app.bundle_id,
                start_date=fetch_period.start_date,
                end_date=fetch_period.end_date,
            )
            performance_summary = build_performance_summary(
                purchase_summary,
                as_of=fetch_as_of,
                data_cutoff=fetch_cutoff,
                completeness_lag_days=COMMON_COMPLETENESS_LAG_DAYS,
                goal=Decimal("99"),
                net_sales_goal=DEFAULT_ANNUAL_NET_SALES_GOAL,
            )
            terminal_report(
                purchase_summary,
                Decimal("99"),
                stdout,
                acquisition=acquisition_summary,
                performance=performance_summary,
            )
            summary_path = write_latest_summary(
                args.output_dir,
                purchase_summary,
                app=app,
                request=selected_request,
                acquisition=acquisition_summary,
                performance=performance_summary,
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
