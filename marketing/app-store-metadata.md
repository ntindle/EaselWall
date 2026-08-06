# Mac App Store product page

This file is the source of truth for the US English product page. Keep every
claim synchronized with the shipping App Store build.

## Metadata

| Field | Copy | Limit |
| --- | --- | ---: |
| Name | `EaselWall: Art Wallpapers` | 25 / 30 characters |
| Subtitle | `Museum paintings every day` | 26 / 30 characters |
| Promotional text | `Turn every display into a tiny museum: 53 public-domain masterpieces, daily rotation, custom mats, and no accounts, ads, or subscriptions.` | 138 / 170 characters |
| Keywords | `desktop,background,monet,van gogh,mat,gallery,classic,automatic,multi monitor,impressionist,rotation` | 100 / 100 UTF-8 bytes |

## Description

```text
Turn your Mac desktop into a daily exhibition.

EaselWall lives in your menu bar and sets a different public-domain Impressionist painting as your wallpaper each day, presented on a museum-style mat.

A CALMER DESKTOP
• 53 curated works by Monet, Van Gogh, Cassatt, Degas, Renoir, Cézanne, and Seurat
• 30 paintings bundled for offline use
• Automatic daily rotation at a time you choose
• No repeats within each display orientation until that cycle completes

MADE FOR MAC DISPLAYS
• Landscape art for landscape screens; portrait art for portrait screens
• Optionally use a different painting on every display
• Automatically adapts when displays connect or disconnect
• Native menu bar controls and launch at login
• “Next Painting” Control Center action on macOS 26 or later

YOUR OWN MUSEUM PRESENTATION
• Soft museum-style mats with configurable color and spacing
• Optional mat-free wallpapers
• Change to the next painting whenever you like
• Add more public-domain Van Gogh works from the Rijksmuseum Collection—no API key required

SIMPLE BY DESIGN
• One-time purchase
• No account
• No ads
• No tracking
• No in-app purchases

Artwork comes from museum open-access programs. EaselWall accepts remotely fetched works only when the source record marks the image CC0 or Public Domain. Credits and source links are included in the app.

Requires macOS 14 Sonoma or later. Internet access is needed to discover and download works beyond the bundled collection.
```

## What's New in 1.0.3

```text
Additional Collections no longer needs a Rijksmuseum API key. EaselWall now uses the museum’s current keyless data service, accepts only records marked CC0 or Public Domain, and gives clearer feedback when a collection is temporarily unavailable. This update also improves painting-cache migration and reliability.
```

Do not repeat the app name or subtitle terms in Keywords. Do not call every
source image CC0: EaselWall also accepts Public Domain Mark 1.0 records.

## Real screenshot candidate order

Generated layouts are references only. The real capture harness writes a
review manifest under `screenshots/real/for-upload/`. After confirming every
frame comes from the exact submitted build and contains no private UI, use this
order:

1. `01-current-painting.png`
2. `02-customize-mats.png`
3. `03-every-display.png`
4. `04-curated-gallery.png`
5. `05-daily-schedule.png`

Apple requires screenshots to show the app in use. Explanatory text and
overlays are allowed, but each final composite must visibly use genuine
EaselWall output or UI. The Gallery frame must show the current keyless
collection UI. Review the complete five-image set at full size before upload.

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
