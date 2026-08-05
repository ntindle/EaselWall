# EaselWall

A native macOS menu bar app that sets your desktop wallpaper to a different impressionist painting each day, displayed on museum-style mats.

**Repo:** github.com/ntindle/EaselWall
**Website:** easelwall.com (Cloudflare Pages, deploys from `website/` on push to main)
**Homebrew:** `brew install ntindle/easelwall/easelwall` (tap: ntindle/homebrew-easelwall)

## Core Features

- Daily wallpaper rotation of impressionist paintings (53 in catalog, 30 bundled offline)
- Museum-style mats (cream, configurable color/spacing, optional toggle)
- Orientation-aware: landscape paintings → landscape monitors, portrait → portrait
- Unique painting per display option
- Pseudo-random rotation — no repeats within each display orientation pool until that cycle completes
- Midnight swap with in-process scheduling; direct-download/Homebrew builds also install a launchd agent
- Control Center widget (macOS 26+ Tahoe)
- Launch at login (SMAppService)
- Pre-fetches next week's paintings in background
- Localized: EN, ES, FR, DE, JA, ZH-Hans, PT-BR, NL

## Distribution

- **Homebrew cask** — free, `brew install ntindle/easelwall/easelwall`
- **DMG download** — free, from GitHub Releases + easelwall.com
- **Mac App Store** — $2.99
- **License:** BSL 1.1, converts to Apache 2.0 on 2030-04-13

## Architecture

- **Platform**: macOS 14+ (Sonoma), Swift 6, SwiftUI
- **Build system**: XcodeGen (`project.yml` → `.xcodeproj`). The xcodeproj is gitignored.
- **App type**: Menu bar-only (`LSUIElement = true`, `MenuBarExtra`)
- **Settings**: NSWindow + NSHostingView with sidebar layout (SwiftUI `Settings` scene doesn't work for LSUIElement apps)
- **Scheduling**: In-process `Timer` + launchd agent (`~/Library/LaunchAgents/com.ntindle.EaselWall.rotation.plist`). LaunchdScheduler is wrapped in `#if !APPSTORE` (forbidden in sandbox).
- **Wallpaper API**: `NSWorkspace.shared.setDesktopImageURL(_:for:options:)` — works per-screen, no entitlements needed, works in sandbox
- **Mat rendering**: Pure `CGContext` (thread-safe, no lockFocus). Unique filenames per render (timestamp suffix) because macOS caches wallpapers by URL.
- **Build configs**: Debug (ad-hoc), Release (Developer ID), AppStore (sandbox + `APPSTORE` compilation condition)

## Image Sourcing & Legal

The bundled catalogue features artists who died at least 100 years ago. Remote artwork is accepted only when its source record carries CC0 1.0 or Public Domain Mark 1.0. Copyright status can vary by jurisdiction, so rely on each source record's rights statement rather than a blanket worldwide claim.

### Sources (CC0 / Public Domain Mark records)
| Museum | Notes |
|---|---|
| Art Institute of Chicago | IIIF API, no key needed. Add `AIC-User-Agent` header. |
| The Metropolitan Museum of Art | CDN URLs, no key needed. Include www.metmuseum.org in citations. |
| Rijksmuseum | Keyless Search, framed EDM, and IIIF APIs. Accept only CC0/PDM records. Credit as "Rijksmuseum Collection" per image. Must not use "Rijksmuseum" in app branding (settings UI uses "Additional Collections"). |

### Avoid
- Google Arts & Culture (restrictive ToS)
- Wikimedia CC-BY-SA images (share-alike incompatible)
- Italian state museum images (cultural heritage law)

### Attribution
Credits screen in About tab + website provenance section. Rijksmuseum requires "developed using the Rijksmuseum API" attribution (in About pane).

## Project Structure

```
Sources/
  EaselWall/          — Main app (Models, Services, Views, API, App)
  ControlWidget/      — macOS 26+ Control Center widget
  Shared/             — AppIntents (shared between app + widget)
Resources/Paintings/
  catalog.json        — 53 paintings with metadata
  images/             — 30 bundled JPEGs (~30MB)
website/              — Static site for easelwall.com (Cloudflare Pages)
Casks/                — Homebrew formula (also in ntindle/homebrew-easelwall)
.github/workflows/    — CI (pr/push) + Release (tag-triggered: build, sign, notarize, DMG, GitHub Release, update Homebrew tap)
docs/                 — App Store review notes
```

## Development Process

- **Confirm root cause before making changes.** Trace the actual failure (logs, state, file inspection) before rewriting code. Reproducing the bug > guessing at fixes.
- **macOS caches wallpapers by file URL.** `setDesktopImageURL` ignores updates if the URL is unchanged. Use unique filenames (timestamp suffix).
- Run formatters/linters over the whole project, not specific files
- Never rebase — use `git pull --no-rebase`
- Never force push
- Boy Scout Rule — commit formatting/lint fixes even in untouched files
- xcodeproj is gitignored — regenerate with `xcodegen generate` or `make generate-project`

## Release channels

- Tag-triggered GitHub Actions build, sign, notarize, and publish the direct DMG and Homebrew update.
- The same release tag uploads a sandboxed build to App Store Connect; submission for review remains an explicit App Store Connect action.
