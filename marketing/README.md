# EaselWall organic marketing kit

This directory contains the reproducible, no-ad-spend content pipeline for
EaselWall. It creates silent 1080x1920 H.264 storyboard/fallback masters from
real app output and the public-domain painting catalog. The first organic test
uses real macOS interaction or desk footage; the generated still-scene masters
are not substitutes for native product proof.

## Payback target

The operating target is **$99 in settled App Store proceeds per rolling year**.
Use Analytics `Proceeds in USD` as the weekly estimate because units and the
listed price do not account for commission, taxes, or refunds. Reconcile that
estimate against Apple's Payments and Financial Reports monthly and at
year-end; those settled statements are the final payback authority. Plan
against 55 net sales until the real proceeds-per-sale rate is established.
That is about one sale a week; the reporting CLI shows estimated progress and
pace between reconciliations.

Do not buy ads during the baseline. Paid promotion needs separate approval and
should wait until one organic creative repeatedly drives profile visits and the
`tt_organic` campaign clears Apple's attribution threshold.

## Render

Requirements: Python 3.9 or newer, Pillow, FFmpeg, and FFprobe.

```bash
make marketing-videos
# or one concept
make marketing-video VIDEO=museum-morning
```

Generated files are written to `marketing/renders/` and intentionally ignored
by Git. The renderer also writes:

- `posting-plan.csv` with captions, rotating hashtag sets, disclosure, audio,
  safe store-discovery copy, and conditional profile-link instructions.
- `render-manifest.json` with dimensions, duration, codec, and SHA-256 hashes.

Rendered masters are roughly 11.7 seconds. They default to **Search EaselWall
on the Mac App Store**, because TikTok does not expose a clickable website field
to every new account. Only use the conditional profile link after it is visibly
available on the live EaselWall profile.

## First organic test

Use the 12-post real-footage matched-pair plan in
[`tiktok-experiment.md`](tiktok-experiment.md). Publish two or three times a
week, leaving at least a day between posts. Record 2-hour, 24-hour, and 72-hour
observations from TikTok Studio in a private copy of
[`experiment-template.csv`](experiment-template.csv).

Judge hooks by retained viewing, profile visits, and native outbound clicks
first, then by attributed downloads and proceeds once Apple clears its privacy
threshold. A high view count without downstream movement is not a winner.

## App Store screenshots

The screenshot renderer produces 1280x800 layout references with:

```bash
make appstore-marketing-screenshots
```

The PNGs are written to the gitignored `screenshots/for-upload/` directory.
They are built only from tracked website, icon, and painting assets; the
renderer never reads raw desktop captures from `screenshots/`, which may
contain private UI. **Do not upload these generated references as the App Store
set.** Apple screenshots must show EaselWall in use. Capture the real menu,
Settings panes, and resulting desktops, then review every frame for private UI
before upload.

The exact US English metadata and screenshot ordering live in
[`app-store-metadata.md`](app-store-metadata.md). Campaign links must use the
provider token copied from EaselWall's own App Store Connect campaign-link
generator; never infer that account-specific value.

## App Store reporting

The reporting CLI uses Apple's Analytics Reports API to create one historical
snapshot request and one ongoing request, then downloads six reports used for
weekly acquisition and revenue review. It never writes or prints the API
private key. Purchases Standard is the only source for the $99 proceeds meter;
Purchases Detailed is kept separately for privacy-limited campaign attribution
that may be incomplete, so the two financial datasets can never be
added together.

Requirements: Python 3, PyJWT, and cryptography. Export these values from a
gitignored environment file or your shell:

- `APP_STORE_CONNECT_API_KEY_ID`
- `APP_STORE_CONNECT_API_ISSUER_ID`
- `APP_STORE_CONNECT_API_KEY_BASE64` (the base64-encoded `.p8` contents)

Bootstrap once with an Admin-role key, then inspect or fetch reports:

```bash
make app-store-report-bootstrap
make app-store-report-status
make app-store-report-snapshot
make app-store-report-fetch
make app-store-report
```

The equivalent direct CLI commands and all options are documented in
[`reporting.md`](reporting.md). Run the snapshot command once after Apple
finishes preparing the historical feed, then use fetch plus report weekly.
The default report window is the rolling 365 days ending today, so its `$99`
meter matches the rolling-year operating target. Pass `--year YYYY` for a
separate calendar-year view.

Apple normally takes 24–48 hours to generate the first reports. Fetch writes
the original gzip TSVs, readable `.tsv` copies, and `latest-summary.json` under
the private, gitignored `marketing/reports/app-store-connect/` directory. The
default fetch uses the ongoing request; pass
`fetch --access-type ONE_TIME_SNAPSHOT` for the historical snapshot. Paths and
metadata keep those request scopes separate and bind every managed segment to
EaselWall's App Store ID and bundle ID. Purchase rows with an Apple app
identifier are validated too. Corrections with newer Apple processing dates
supersede older rows instead of being added twice. When snapshot and ongoing
instances both contain the same event Date at the same processing date, their
rows must be identical or reporting stops for investigation; non-overlapping
Date batches coexist.
Until a verified Purchases Standard segment exists, revenue is shown as
unknown/pending and no false zero-dollar report is written. Detailed campaign
rows may remain absent or altered because of Apple's Detailed-report privacy safeguards.

`report` is offline and needs no Apple credentials. It prints a terminal
progress bar toward $99 and writes a dated Markdown report, a combined JSON
summary of every verified managed request under `--input`, and separate
authoritative-purchases and privacy-limited campaign TSVs under the private
report directory. `Proceeds in USD` from Purchases Standard is an
estimated weekly steering metric; reconcile it against settled Payments and
Financial Reports each month and at year-end.

## Publishing checklist

1. Watch the entire rendered master. Reject visual glitches or misleading text.
2. Add voiceover and/or a current track from TikTok's Commercial Music Library
   in TikTok. Do not bake trending music into the reusable master.
3. Turn on the commercial content disclosure and choose **Your brand** so the
   post is labeled **Promotional content**.
4. Default the spoken/on-screen CTA to **Search EaselWall on the Mac App
   Store**. If the live profile visibly exposes a clickable website field, use
   `https://easelwall.com/tiktok/`; it provides a mobile-to-Mac handoff and the
   aggregate `tt_organic` App Store campaign. Do not make one Apple campaign
   token per video until volume clears Apple's privacy threshold reliably.
5. Publish from the single official EaselWall account. Do not automate follows,
   comments, direct messages, engagement, or multiple accounts.
6. Schedule approved posts with TikTok Studio or Web Business Suite. Do not
   build a custom API autoposter or automate engagement.
7. Do not send Reels or Shorts traffic through the TikTok route. Record each
   platform separately before adding platform-specific aggregate routes.
8. Record performance in the experiment ledger and weekly report; make the next batch from the
   strongest hook and retention pattern—not raw view count alone.

## Source of truth

`videos.json` owns the hooks, proof lines, captions, and selected assets. All
claims must remain true of the shipping app. In particular:

- 53 paintings are in the catalog; 30 are bundled offline.
- Paintings do not repeat within each display orientation pool until that cycle completes.
- Rijksmuseum is an optional, keyless collection that accepts only images marked CC0 or Public Domain.
- Claims about preserving the whole composition must say Museum Mat is enabled;
  disabling it uses an edge-to-edge aspect-fill crop.
- EaselWall renders a mat, shadow, and painting; it does not render wall-label
  text into the wallpaper.
