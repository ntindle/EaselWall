# App Store marketing reports

The supported reporting path is the self-contained
`scripts/app_store_reports.py` CLI. It requests Apple's Analytics Reports,
downloads and verifies private source files, canonicalizes corrections, prints
a compact `$99` progress bar for the trailing 365 days, and writes a weekly
Markdown report. No hosted analytics service is required.

## One-time setup

Install the JWT dependency in the Python environment used for reporting:

```sh
python3 -m pip install 'PyJWT[crypto]'
```

Export exactly these credentials into the process environment:

```sh
export APP_STORE_CONNECT_API_KEY_ID='...'
export APP_STORE_CONNECT_API_ISSUER_ID='...'
export APP_STORE_CONNECT_API_KEY_BASE64='...'
```

The last value is the base64 encoding of the App Store Connect `.p8` private
key. Do not commit it. The CLI does not read dotenv files, key files, or the
macOS Keychain, and it never prints the private key, JWT, or signed report URLs.

Inspect request state without changing it:

```sh
python3 scripts/app_store_reports.py status
```

Create one historical snapshot request and one ongoing request when absent:

```sh
python3 scripts/app_store_reports.py bootstrap
```

`bootstrap` is safe to repeat. It preserves active requests and creates a new
request when Apple has stopped the previous one due to inactivity. Creating a
request requires an Admin key. Reading generated reports also works with Sales
and Reports or Finance keys.

When Apple finishes generating the initial reports, fetch the snapshot once.
This step is required: it captures sales from before reporting was enabled.

```sh
python3 scripts/app_store_reports.py fetch --access-type ONE_TIME_SNAPSHOT
```

It is safe to retry while reports are still being generated and safe to rerun;
verified segments are reused.

The default app lookup discovers `com.ntindle.EaselWall`. An explicit ID skips
discovery:

```sh
python3 scripts/app_store_reports.py --app-id 6778701883 status
```

## Weekly loop

Fetch the ongoing feed and rebuild the canonical report at least weekly:

```sh
python3 scripts/app_store_reports.py fetch --access-type ONGOING
python3 scripts/app_store_reports.py report
```

`report` defaults to the inclusive 365-day window ending on `--as-of` (today
when omitted). This rolling view matches the operating goal of earning at least
$99 in proceeds in any trailing year, even when the window crosses January 1.
For tax or calendar-year comparison, use either:

```sh
python3 scripts/app_store_reports.py report --period calendar-year
python3 scripts/app_store_reports.py report --year 2026
```

Without `--access-type`, `fetch` prefers an active `ONGOING` request and
falls back to `ONE_TIME_SNAPSHOT`. The explicit commands above prevent the
initial historical snapshot from being accidentally skipped.
`download` remains available as a deprecated compatibility alias for `fetch`.

For each of these reports, it walks every available `DAILY` instance and
downloads every segment immediately. Local verification makes the full walk
idempotent while ensuring a weekly run cannot miss intervening days:

- App Store Discovery and Engagement Detailed
- App Store Downloads Detailed
- App Store Purchases Standard
- App Store Purchases Detailed

Purchases Standard is required and is the sole source for net purchases,
refunds, estimated proceeds, and `$99` goal progress. Purchases Detailed is
optional campaign attribution: Apple applies additional privacy safeguards, so
its rows may be suppressed, altered, or absent and are never added to the Standard total.

Every compressed segment is checked against Apple's byte count and MD5, then
expanded as UTF-8 TSV. The `.txt.gz`, `.tsv`, and metadata sidecar are written
atomically with private file permissions beneath:

```text
marketing/reports/app-store-connect/downloads/
```

That directory is gitignored. Existing files are reused only when the stored
compressed checksum, size, decompressed SHA-256, and source metadata all match,
and the readable TSV exactly matches normalization of the Apple-MD5-verified
gzip. Otherwise a newly downloaded and verified segment replaces them.
Sidecars do not contain signed URLs. They do contain the resolved App Store app
ID and bundle ID; offline summaries require both to match EaselWall. When a
Purchases row exposes Apple's `App Apple Identifier` field, its value must also
match EaselWall's Apple ID (`6778701883`). A mismatch stops reporting instead
of allowing another app's rows into the total. Access type and request ID are
separate path components. Root `latest-summary.json` records the same app and
rolling-window scope for the request fetched most recently. If Standard is
still pending, it records
`standardDataAvailable: false` with null totals so an older dollar value cannot
look current. The offline `report` command is the combined correction-aware
view across the archive.

App identity was added in sidecar schema version 2. On the first run after this
upgrade, fetch both `ONE_TIME_SNAPSHOT` and `ONGOING` again before reporting;
segments with older unscoped sidecars are re-downloaded and replaced rather
than trusted.

`report` is offline and needs no Apple credentials. It writes:

```text
marketing/reports/app-store-connect/canonical/purchases-trailing-365-YYYY-MM-DD.tsv
marketing/reports/app-store-connect/canonical/campaigns-trailing-365-YYYY-MM-DD.tsv
marketing/reports/app-store-connect/weekly/easelwall-app-store-YYYY-MM-DD.md
```

Calendar-year mode retains the shorter `purchases-YYYY.tsv` and
`campaigns-YYYY.tsv` names.

The purchases TSV is authoritative Standard data. The campaigns TSV is a
separate, privacy-limited Detailed view and is skipped when Detailed is not
available.

Use explicit paths for fixtures or another private workspace:

```sh
python3 scripts/app_store_reports.py report \
  --input /private/path/downloads \
  --canonical-output /private/path/purchases-2026.tsv \
  --campaign-output /private/path/campaigns-2026.tsv \
  --output /private/path/weekly.md \
  --as-of 2026-08-05 \
  --year 2026
```

A verified, header-only Purchases Standard report produces a valid zero-dollar
report. An empty archive, a Detailed-only archive, or a Standard report with no
downloaded segment is unknown/pending; `report` writes no Markdown or canonical
TSV and does not overwrite the last known report with a false zero.

### Correction-safe accounting

Analytics instances are an archive, not additive snapshots. A newer Apple
`processingDate` can replace older rows for the same event `Date`. Standard and
Detailed are canonicalized independently. Within each report, the CLI selects
one logical dataset per event date using report and instance identity:

1. Newest `processingDate` wins.
2. `ONE_TIME_SNAPSHOT` and `ONGOING` have equal authority when their
   `processingDate` values tie and both contain that event `Date`.
3. Exact parsed contents for that shared event `Date` are deduplicated. Any
   difference stops the report for investigation instead of selecting one
   request arbitrarily.

Non-overlapping Date batches coexist. Apple defines each instance as one or
more Date batches, so absence of a Date from an instance does not assert a
zero-value replacement for that Date. This matters when the historical
snapshot and recent ongoing feed are created on the same processing date but
cover different ranges.

Only Purchases Standard contributes signed `Purchases` and `Proceeds in USD` to
the revenue total. Negative purchase rows count as refunds; partial refunds can
have zero purchases and negative proceeds. Purchases Detailed is canonicalized
independently for its Detailed-only `Campaign` field, which Apple may suppress
or perturb under its privacy safeguards. Campaign tokens are deliberately
collapsed into `tt_organic`, `tt_creator`, `tt_paid`, `unattributed`, and
`other`; those rows are never added to the Standard total.

The purchases and campaigns TSVs materialize those two correction-aware views,
so downstream tools must use them instead of summing the raw download archive.

### Estimated versus settled proceeds

Analytics `Proceeds in USD` is an estimate suitable for weekly marketing
steering and the progress bar. It is not final payback authority. Each month,
and again at year end, reconcile the CLI estimate against App Store Connect's
Payments and Financial Reports, including settled proceeds, refunds,
withholding, currency conversion, and other adjustments. The `$99/year` fee is
covered only when settled financial proceeds reach the target.

## Optional inspection tools

The built-in terminal, canonical TSV, and Markdown report are the supported
path. These optional CLIs are useful for manual inspection and are not runtime
dependencies.

Open the default rolling correction-safe canonical view interactively with
VisiData (substitute the report's current end date):

```sh
vd marketing/reports/app-store-connect/canonical/purchases-trailing-365-2026-08-05.tsv
```

Run ad-hoc revenue SQL against that same canonical file with DuckDB:

```sh
duckdb -c "SELECT sum(Purchases) AS purchases, round(sum(\"Proceeds in USD\"), 2) AS proceeds FROM read_csv('marketing/reports/app-store-connect/canonical/purchases-trailing-365-2026-08-05.tsv', delim='\t', header=true)"
```

Inspect the separate, privacy-limited campaign view without
mixing it into revenue totals:

```sh
vd marketing/reports/app-store-connect/canonical/campaigns-trailing-365-2026-08-05.tsv
duckdb -c "SELECT Campaign, sum(Purchases) AS purchases, round(sum(\"Proceeds in USD\"), 2) AS proceeds FROM read_csv('marketing/reports/app-store-connect/canonical/campaigns-trailing-365-2026-08-05.tsv', delim='\t', header=true) GROUP BY Campaign ORDER BY proceeds DESC"
```

Do not point aggregate queries at `downloads/`; it intentionally retains
superseded processing dates for auditability.

## Interpretation and cadence

Apple can take one or two days to prepare the first reports. Ongoing report
instances expire after 35 days if not retrieved, so a weekly fetch prevents
gaps. Campaign reporting can remain hidden until a campaign reaches Apple's
privacy threshold and processing delay.

Track the funnel as TikTok views to profile/link clicks to first-time downloads
to net purchases to estimated proceeds. Do not add paid spend until organic
posts show a repeatable click-to-sale path.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Command completed |
| 2 | Missing or invalid credentials, options, or dependency |
| 3 | API key lacks permission |
| 4 | App Store Connect API or network failure |
| 5 | Invalid API data, download verification, or local report data |
