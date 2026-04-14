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
- Pseudo-random rotation — no repeats until full cycle completes
- Midnight swap with launchd agent (survives sleep/reboot/app termination)
- Control Center widget (macOS 26+ Tahoe)
- Launch at login (SMAppService)
- Pre-fetches next week's paintings in background
- Localized: EN, ES, FR, DE, JA, ZH-Hans, PT-BR, NL

## Distribution

- **Homebrew cask** — free, `brew install ntindle/easelwall/easelwall`
- **DMG download** — free, from GitHub Releases + easelwall.com
- **Mac App Store** — $3 (pending Apple Developer enrollment)
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

All artists died 100+ years ago — public domain worldwide. *Bridgeman v. Corel* (1999) confirms faithful photo reproductions of 2D public domain works aren't copyrightable.

### Sources (all CC0)
| Museum | Notes |
|---|---|
| Art Institute of Chicago | IIIF API, no key needed. Add `AIC-User-Agent` header. |
| The Metropolitan Museum of Art | CDN URLs, no key needed. Include www.metmuseum.org in citations. |
| Rijksmuseum | Requires free API key. Must credit as "Rijksmuseum Collection" per image. Must not use "Rijksmuseum" in app branding (settings UI uses "Additional Collections"). |

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

## Pending (blocked on Apple Developer enrollment)

- Code signing (Developer ID Application cert)
- Notarization (removes Gatekeeper "unidentified developer" warning)
- Mac App Store submission ($3)
- GitHub Actions secrets (P12, Apple ID, team ID, app-specific password)
