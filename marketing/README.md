# EaselWall organic marketing kit

This directory contains the reproducible, no-ad-spend content pipeline for
EaselWall. It creates silent 1080x1920 H.264 masters from real app imagery and
the public-domain painting catalog.

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

- `posting-plan.csv` with captions, disclosure, audio, and link instructions.
- `render-manifest.json` with dimensions, duration, codec, and SHA-256 hashes.

## First organic test

Publish three times a week, leaving at least a day between posts so each result
is readable. Use the rendered order for the first eight posts:

1. `museum-morning`
2. `portrait-monitor`
3. `three-displays`
4. `no-feed-just-art`
5. `fifty-three-works`
6. `monet-week`
7. `van-gogh-without-starry-night`
8. `tiny-museum`

After the eighth post, keep the two strongest hooks and render new proof scenes
for those hooks. Judge a hook by watch-through and profile visits first, then by
attributed downloads and proceeds once Apple has enough data. A high view count
without profile or store movement is not a winner.

## App Store screenshots

Render the five polished 1280x800 Mac App Store screenshots with:

```bash
make appstore-marketing-screenshots
```

The upload-ready PNGs are written to the gitignored
`screenshots/for-upload/` directory. They are built only from tracked website,
icon, and painting assets; the renderer never reads raw desktop captures from
`screenshots/`, which may contain private UI. It writes the five current files,
removes only the two superseded generated names
`05-two-ninety-nine-once.png` and `05-pay-once.png`, and preserves unrelated
captures. Review all five images before uploading them in App Store Connect.

## App Store reporting

The reporting CLI uses Apple's Analytics Reports API to create one historical
snapshot request and one ongoing request, then downloads four reports used for
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

Apple normally takes 24–48 hours to generate the first reports. Fetch writes
the original gzip TSVs, readable `.tsv` copies, and `latest-summary.json` under
the private, gitignored `marketing/reports/app-store-connect/` directory. The
default fetch uses the ongoing request; pass
`fetch --access-type ONE_TIME_SNAPSHOT` for the historical snapshot. Paths and
metadata keep those request scopes separate, and corrections with newer Apple
processing dates supersede older rows instead of being added twice.
Until a verified Purchases Standard segment exists, revenue is shown as
unknown/pending and no false zero-dollar report is written. Detailed campaign
rows may remain absent or altered because of Apple's Detailed-report privacy safeguards.

`report` is offline and needs no Apple credentials. It prints a terminal
progress bar toward $99 and writes a dated Markdown report plus separate
authoritative-purchases and privacy-limited campaign TSVs under
the private report directory. `Proceeds in USD` from Purchases Standard is an
estimated weekly steering metric; reconcile it against settled Payments and
Financial Reports each month and at year-end.

## Publishing checklist

1. Watch the entire rendered master. Reject visual glitches or misleading text.
2. Add a current track from TikTok's Commercial Music Library in TikTok. Do not
   bake trending music into the reusable master.
3. Turn on the commercial content disclosure and choose **Your brand** so the
   post is labeled **Promotional content**.
4. Use `https://easelwall.com/tiktok` in the profile. It redirects through the
   aggregate `tt_organic` App Store campaign. Do not make one Apple campaign
   token per video until volume clears Apple's privacy threshold reliably.
5. Publish from the single official EaselWall account. Do not automate follows,
   comments, direct messages, engagement, or multiple accounts.
6. Cross-post the same approved master to Instagram Reels and YouTube Shorts.
7. Record performance in the weekly report and make the next batch from the
   strongest hook and retention pattern—not raw view count alone.

## Source of truth

`videos.json` owns the hooks, proof lines, captions, and selected assets. All
claims must remain true of the shipping app. In particular:

- 53 paintings are in the catalog; 30 are bundled offline.
- Paintings do not repeat until the current collection cycle completes.
- Rijksmuseum is an optional, keyless collection that accepts only images marked CC0 or Public Domain.
- EaselWall renders a mat, shadow, and painting; it does not render wall-label
  text into the wallpaper.
