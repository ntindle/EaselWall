# App Store marketing reports

The supported reporting path is the self-contained
`scripts/app_store_reports.py` CLI. It requests Apple's Analytics Reports,
downloads and verifies private source files, canonicalizes corrections, prints
a compact acquisition funnel plus trailing-365-day `$99` proceeds and 55
net-sales benchmarks, compares only verified-complete weeks, and writes a
weekly Markdown report. No hosted analytics service is required.

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

### Weekly automation credentials

The weekly heartbeat must use a dedicated App Store Connect **Sales and
Reports** key only; do not reuse an Admin key. Its private environment file is
exactly:

```text
/Users/ntindle/.config/easelwall/app-store-reporting.env
```

Set that file's mode to `0600`. It must define exactly these three variable
names, with their real values supplied privately outside this repository:

- `APP_STORE_CONNECT_API_KEY_ID`
- `APP_STORE_CONNECT_API_ISSUER_ID`
- `APP_STORE_CONNECT_API_KEY_BASE64`

The heartbeat fails closed until that file exists with the required mode and
all three variables. It must not fall back to an interactive shell, dotenv
file, key file, macOS Keychain, or broader-privilege credential.

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

`report` defaults to the inclusive 365-day window ending at the conservative
data cutoff (`--as-of` minus three days; today is used when `--as-of` is
omitted). This rolling view matches the operating goal of earning at least $99
in proceeds and the conservative operating benchmark of 55 net sales in any
trailing year, even when the window crosses January 1. The CLI exposes the
latter as `--net-sales-goal 55`; override it only when intentionally changing
the operating benchmark. For tax or calendar-year comparison, use either:

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

- App Store Discovery and Engagement Standard
- App Store Discovery and Engagement Detailed
- App Store Downloads Standard
- App Store Downloads Detailed
- App Store Purchases Standard
- App Store Purchases Detailed

The two acquisition Standard reports provide impression events, product page
views, Get engagement taps, first-time downloads, and redownloads. Their
Detailed counterparts are retained only for optional diagnosis; Apple applies
additional privacy safeguards, so Detailed rows may be suppressed, altered, or
absent and never replace missing Standard totals.

Purchases Standard is required and remains the sole source for net purchases,
refunds, estimated proceeds, and `$99` goal progress. Purchases Detailed is
optional campaign attribution and is never added to the Standard total.

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
Sidecars do not contain signed URLs. They bind each segment to EaselWall's App
Store app ID (`6778701883`) and bundle ID (`com.ntindle.EaselWall`) as well as
the request, access type, report, instance, processing date, and segment.
Managed Purchase rows must also carry EaselWall's `App Apple Identifier`. A
wrong or mixed app archive stops the report instead of being added to
EaselWall's totals. For compatibility, an explicitly supplied standalone TSV
may omit that column; if present, it is still validated. Standalone and other
unmanaged TSV rows remain parseable for inspection, but they never prove that
an arbitrary reporting window is complete. The CLI has no trust override: it
refuses to write canonical or Markdown reports from unmanaged Purchases
Standard input.

An instance becomes readable only after the segment listing finishes and the
CLI atomically writes `_instance-complete.json` with the exact verified segment
set. An interrupted multi-segment download therefore cannot become an
authoritative partial total. A missing, extra, or altered segment makes the
archive fail closed until it is fetched and verified again.

Before classifying any report, the offline reader preflights every managed
instance in scope and verifies every segment named by its manifest: sidecar
identity, known report name, app and bundle, gzip checksum and size, normalized
TSV bytes, and the exact sibling set. A missing or altered sibling cannot be
hidden by changing its `reportName` so another sibling certifies completeness.

App identity uses sidecar schema version 2, and instance manifests are also
required. After upgrading from an older reporter, fetch both
`ONE_TIME_SNAPSHOT` and `ONGOING` again before running `report`; legacy
unscoped or manifest-free segments are deliberately not trusted.

Access type and request ID are separate path components. Root
`latest-summary.json` identifies the one request scope fetched most recently.
The offline `report` command writes `combined-summary.json` from every verified
managed instance found under `--input`. After both archives are fetched this
normally spans snapshot and ongoing data, but it never claims a missing scope.
That combined-input artifact is the supported source for recurring charts and
the annual goal meter.
If the complete Standard goal window is still pending, it records
`standardDataAvailable: false` with null top-level totals so an older dollar
value cannot look current. `acquisitionFunnel` follows the same contract: a
missing Standard dataset produces `null`, never a fabricated zero.
`reportingWindow` records exact 365-day, 28-day, and two seven-day ranges plus a
`complete` flag for each. Derived values remain null unless their own full
window is verified. Its goal window includes a `netSalesBenchmark` object with
the annual goal and nullable progress, percentage, remaining, and met fields.
Those four status values are populated only after the exact trailing-365-day
window is verified complete. The offline `report` command is the combined,
correction-aware view across the snapshot and ongoing archives.

`report` is offline and needs no Apple credentials. It writes:

```text
marketing/reports/app-store-connect/canonical/purchases-trailing-365-YYYY-MM-DD.tsv
marketing/reports/app-store-connect/canonical/campaigns-trailing-365-YYYY-MM-DD.tsv
marketing/reports/app-store-connect/weekly/easelwall-app-store-YYYY-MM-DD.md
marketing/reports/app-store-connect/combined-summary.json
```

Calendar-year mode retains the shorter `purchases-YYYY.tsv` and
`campaigns-YYYY.tsv` names.

The purchases TSV is authoritative Standard data. The campaigns TSV is a
separate, privacy-limited Detailed view and is skipped when Detailed is not
available.

### Exact goal, run-rate, and weekly windows

By default, `report` includes events only through `--as-of` minus three days.
That common cutoff uses the slowest report in the funnel: Apple documents
Discovery and Engagement as complete within three days, while Downloads and
Purchases are complete within two. The terminal and Markdown output always show
the resulting inclusive cutoff. Pass today's/report-generation date to
`--as-of`; do not subtract the lag yourself. Use `--through` only when you want
to supply the already-computed inclusive cutoff explicitly.

Override it only when investigating a known-complete historical period:

```sh
python3 scripts/app_store_reports.py report \
  --as-of 2026-08-08 \
  --through 2026-08-05
```

`--data-lag-days` changes the default subtraction when `--through` is absent.
The `$99` payback window is always the trailing 365 days ending on the inclusive
cutoff; for example, a cutoff of `2027-01-06` uses `2026-01-07` through
`2027-01-06`. Calendar-year mode changes the primary accounting view and its
canonical files, while the report's separate `$99` performance section remains
trailing-365. `--period calendar-year` uses January 1 through the cutoff in the
cutoff's year. `--year YYYY` implies calendar-year mode for that year and writes
`canonical/purchases-YYYY.tsv` and `canonical/campaigns-YYYY.tsv`.

The reporter does not infer complete data merely because a TSV contains a row.
A row proves a lower bound, not that absent dates are zero. A managed historical
snapshot proves Purchases Standard coverage through two days before its
`processingDate`. After that baseline, every remaining date in a requested
window must be represented by a completed ongoing instance whose
`processingDate` is two days later. Any gap leaves that window unknown. A
header-only snapshot can prove a real zero only when its verified coverage
includes the entire requested window.

Completeness is evaluated independently for three exact windows:

- Goal progress: 365 days ending on the cutoff. Both the `$99` proceeds target
  and 55 net-sales benchmark use this exact window. Net-sales progress,
  remaining, and met status stay `unknown`/`null` until all 365 days are
  verified complete; observed rows never become partial goal progress.
- Annualized run rate: 28 days ending on the cutoff, multiplied by `365 / 28`.
- Week over week: the seven days ending on the cutoff and the preceding seven
  days.

The 28-day and weekly values also require a latest Purchases Standard
`processingDate` at least two days after the cutoff. A complete recent feed can
therefore show a run rate while the longer 365-day goal remains unknown; a
single fresh ongoing row cannot. Unknown values are labeled `unknown` in the
terminal and Markdown and serialized as `null` in the JSON summaries, with
their window's `complete` flag set to `false`.

The current comparison window is the seven days ending on the cutoff; the
previous seven days are its baseline. A zero-dollar prior week is reported as
flat or as new proceeds, not as an undefined percentage. The 28-day annualized
run rate is a steering estimate from Analytics proceeds, not a settled
financial result or a prediction that seasonality will remain constant.

Use explicit paths for fixtures or another private workspace:

```sh
python3 scripts/app_store_reports.py report \
  --input /private/path/downloads \
  --canonical-output /private/path/purchases-2026.tsv \
  --campaign-output /private/path/campaigns-2026.tsv \
  --output /private/path/weekly.md \
  --as-of 2026-08-08 \
  --through 2026-08-05 \
  --year 2026
```

A verified, header-only historical snapshot produces a valid zero-dollar report
only for a window it fully covers. An empty archive, a Detailed-only archive, or
a Standard instance without a completion manifest is unknown/pending;
`report` writes no Markdown or canonical TSV when Purchases Standard is absent.
When Standard exists but does not completely cover a calendar partition, the
Markdown report can still expose independently complete recent metrics, but the
canonical purchase TSV for that partition is skipped rather than written as a
false zero.

Acquisition completeness is evaluated independently for Discovery Standard and
Downloads Standard across the exact requested window. A row is an observed
count, not proof that the rest of the window is zero. A verified snapshot covers
history through its lag-adjusted `processingDate` (three days for Discovery,
two for Downloads); every later date requires its corresponding completed
ongoing `DAILY` instance. Header-only daily instances count as zero-valued
coverage, but one missing day keeps that funnel stage unknown. Until the whole
window is verified, its counts and rates are hidden in terminal and Markdown
output and serialized as `null` rather than presented as partial full-window
metrics.

### Acquisition funnel semantics

The dependency-free terminal, Markdown report, and `combined-summary.json`
expose these Standard-only totals:

- `Event = Impression` counts and unique counts
- `Event = Page view` with `Page Type = Product page`
- `Event = Tap` with `Engagement Type = Get`
- `Download Type = First-time Download`
- `Download Type = Redownload`

Apple defines `Engagement Type = Get` as taps on the Get, Buy, or Pre-order
button. The report schema does not distinguish those actions. The CLI therefore
labels the aggregate `Buy/Get/Pre-order taps` and leaves the dedicated
`buyTaps` JSON field null instead of pretending every tap is a completed paid
purchase.

The displayed directional rates state their numerator and denominator:
product page views per impression event, product-page Get taps per product page
view, first-time downloads per Get tap, and first-time downloads per impression
event. They are not labeled as Apple's UI Conversion Rate because the exported
event aggregates cannot reconstruct unique-user overlap or pre-orders exactly.
A zero denominator produces `n/a`/`null`, not `0%`.

The parser validates required Standard columns, non-negative integer counts,
known event/download types, and EaselWall's App Apple Identifier. An unknown or
ambiguous schema stops the run for inspection. Detailed acquisition data is
verified and archived but never contributes to these totals.

Purchases parsing also treats `Purchases` and `Proceeds in USD` as required
values on every row. Blank values are invalid source data, not zeroes, and stop
the run before any canonical or Markdown output is written.

### Correction-safe accounting

Analytics instances are an archive, not additive snapshots. A newer Apple
`processingDate` can replace older rows for the same event `Date`. Acquisition
Standard, Purchases Standard, and each Detailed report are canonicalized
independently. Within each report, the CLI selects one logical dataset per event
date using managed report and instance identity:

1. Newest `processingDate` wins.
2. Instances sharing that newest date have equal authority and must produce
   identical totals and identical values for every parsed source field for the
   event date. A conflict stops the report even when headline purchases and
   proceeds match.
3. After agreement, `ONGOING` is the deterministic preferred copy and the
   other identical copy is counted as superseded.

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
collapsed into `tt_organic`, `tt_creator`, `tt_paid`, `web_site`,
`unattributed`, and `other`. The `web_site` group keeps the website campaign
separate from unattributed traffic and TikTok campaigns. Those Detailed rows
are never added to the Standard total.

The purchases and campaigns TSVs materialize those two correction-aware views,
so downstream tools must use them instead of summing the raw download archive.

### Estimated versus settled proceeds

Analytics `Proceeds in USD` is an estimate suitable for weekly marketing
steering and the progress bar. It is not final payback authority. Each month,
and again at year end, reconcile the CLI estimate against App Store Connect's
Payments and Financial Reports, including settled proceeds, refunds,
withholding, currency conversion, and other adjustments. The `$99/year` fee is
covered only when settled financial proceeds reach the target.

## Optional visualization and inspection tools

The built-in terminal, Markdown, JSON, and canonical TSV outputs are the
supported dependency-free reporting path. The tools below are optional for
ad-hoc exploration; none is an EaselWall runtime dependency.

For a quick funnel bar chart, [termgraph](https://pypi.org/project/termgraph/)
0.7.6 supports Python 3.9 and later:

```sh
python3 -m pip install 'termgraph==0.7.6'
jq -r '.acquisitionFunnel | [["Impressions", .impressions], ["ProductPages", .productPageViews], ["FirstDownloads", .firstTimeDownloads]][] | select(.[1] != null) | @csv' \
  marketing/reports/app-store-connect/combined-summary.json | termgraph --delim ,
```

For richer stdin plots, install
[YouPlot](https://formulae.brew.sh/formula/youplot) and
[DuckDB](https://formulae.brew.sh/formula/duckdb) with Homebrew:

```sh
brew install youplot duckdb
duckdb -c "COPY (SELECT Date, \"Proceeds in USD\" AS proceeds FROM read_csv('marketing/reports/app-store-connect/canonical/purchases-trailing-365-2026-08-05.tsv', delim='\t', header=true) ORDER BY Date) TO '/dev/stdout' WITH (FORMAT csv, HEADER)" \
  | uplot line -d, -H -t 'Daily App Store proceeds'
```

That pipeline follows DuckDB's
[YouPlot stdout pattern](https://duckdb.org/docs/current/guides/data_viewers/youplot).

Open the default rolling correction-safe canonical view interactively with
[VisiData](https://www.visidata.org/docs/usage/) (substitute the report's
current cutoff date):

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

Apple schema and timing references:

- [App Store Discovery and Engagement](https://developer.apple.com/documentation/analytics-reports/app-store-discovery-and-engagement)
- [App Store Downloads](https://developer.apple.com/documentation/analytics-reports/app-download)
- [App Store Purchases](https://developer.apple.com/documentation/analytics-reports/app-store-purchase)
- [Data Completeness and Corrections](https://developer.apple.com/documentation/analytics-reports/data-completeness-corrections)

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Command completed |
| 2 | Missing or invalid credentials, options, or dependency |
| 3 | API key lacks permission |
| 4 | App Store Connect API or network failure |
| 5 | Invalid API data, download verification, or local report data |
