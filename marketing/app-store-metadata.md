# Mac App Store product page

This file is the source of truth for the US English product page. Keep every
claim synchronized with the shipping App Store build.

## Metadata

| Field | Copy | Limit |
| --- | --- | ---: |
| Name | `EaselWall` | 9 / 30 characters |
| Subtitle | `Daily Impressionist Art` | 23 / 30 characters |
| Promotional text | `Turn your Mac into a quiet daily exhibition: a new Impressionist painting, thoughtfully matted for each display—no account, ads, or tracking.` | 141 / 170 characters |
| Keywords | `wallpaper,desktop,painting,museum,landscape,portrait,menubar,background,curated,masterpiece,matting` | 99 / 100 UTF-8 bytes |

Description opening:

> Turn your Mac desktop into a daily exhibition.
>
> EaselWall lives in your menu bar and sets a different public-domain
> Impressionist painting as your wallpaper each day, presented on a
> museum-style mat.

Do not repeat the app name or subtitle terms in Keywords. Do not call every
source image CC0: EaselWall also accepts Public Domain Mark 1.0 records.

## Screenshot upload order

The generated `screenshots/for-upload/manifest.json` owns the exact set and
hashes. Upload in this order:

1. `01-daily-masterpiece.png`
2. `02-every-display.png`
3. `03-custom-museum-mats.png`
4. `04-fifty-three-works.png`
5. `05-simple-by-design.png`

Apple requires screenshots to show the app in use. Explanatory text and
overlays are allowed, but each final composite must visibly use genuine
EaselWall output or UI. Slides 3&ndash;5 include reviewed Settings captures for
Appearance, Gallery Summary, and Schedule; the Gallery crop stops before
Additional Collections so it cannot show the obsolete API-key UI. Review the
complete five-image set before upload.

Mac-only apps cannot currently use Custom Product Pages or Product Page
Optimization; those features require an iOS or iPadOS app. Treat the first
three screenshots as the conversion-critical set and compare results using
App Store Analytics before changing them.

## Campaign links

Never guess or copy a provider token from another account. Generate a campaign
link inside EaselWall's App Store Connect Analytics page and copy its `pt`
value. Both `pt` and `ct` must be present for campaign reporting; Apple hides a
campaign until it has run for more than a day and reached at least five
first-time downloads.

Aggregate low-volume organic traffic into stable tokens:

- Website: `web_site`
- TikTok organic: `tt_organic`

Do not create one token per video until volume is high enough to clear Apple's
privacy threshold reliably.

## Apple references

- [Product-page guidance](https://developer.apple.com/app-store/product-page/)
- [Platform metadata limits](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information)
- [Mac screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications/)
- [Campaign links](https://developer.apple.com/help/app-store-connect-analytics/acquisition/campaign-links)
- [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
