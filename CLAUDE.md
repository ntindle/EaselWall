# EaselWall

A native macOS menu bar app that sets your desktop wallpaper to a different impressionist painting each day. Starting with Van Gogh, expanding to other impressionists (Monet, Renoir, Cezanne, Degas, etc.).

## Core Concept

- Daily wallpaper rotation of impressionist paintings
- Paintings displayed on museum-style mats (cream/white, gallery spacing, no frame line)
- Mat is optional (user toggle) but on by default — serves the practical purpose of accommodating varied painting aspect ratios to screen dimensions
- Orientation-aware: landscape paintings go on landscape monitors, portrait paintings on portrait monitors
- Option for unique painting per display (vs same painting on all monitors of the same orientation)
- Pseudo-random rotation — no repeats until full cycle completes
- Swaps at midnight; handles sleep/wake via launchd
- Localized UI — String Catalog with translations for EN, ES, FR, DE, JA, ZH-Hans, PT-BR, NL

## Architecture Decisions

- **Platform**: macOS 14+ (Sonoma), Swift/SwiftUI
- **App type**: Menu bar-only app (no dock icon). Use `MenuBarExtra` + `LSUIElement = true`
- **Control Center**: Use the Control Widget API on macOS Tahoe (26+) for native Control Center integration. Fall back to menu bar on macOS 14-15. Goal is to be as macOS-native as possible — adopt platform features on the OSes that support them.
- **Settings**: NSWindow with SwiftUI `NSHostingView`, sidebar-style layout. Opened from menu bar dropdown. (SwiftUI `Settings` scene doesn't work for LSUIElement apps.)
- **UX goal**: As invisible as possible — set and forget
- **Distribution**: Homebrew cask + website download (DMG format). NOT App Store.
- **Scheduling**: Dual approach — in-process `Timer` for precise scheduling while running, plus a `launchd` agent (`~/Library/LaunchAgents/com.ntindle.EaselWall.rotation.plist`) with `StartCalendarInterval` to wake the app if it was killed or after sleep/reboot. Agent is installed/updated on launch and when the user changes the rotation time in settings.
- **Wallpaper API**: `NSWorkspace.shared.setDesktopImageURL(_:for:options:)` — stable, works per-screen, no special entitlements needed, works sandboxed
- **Orientation detection**: Compare `NSScreen.frame` width vs height. Listen for `NSApplication.didChangeScreenParametersNotification` for hotplug/rotation changes.
- **Mat compositing**: Core Graphics — render painting centered on a mat-colored canvas at the exact resolution of each target display

## Image Sourcing & Licensing

All target artists died 100+ years ago — their works are public domain worldwide. Under *Bridgeman Art Library v. Corel Corp* (1999), faithful photographic reproductions of 2D public domain works are not independently copyrightable in the U.S.

### Approved CC0 Sources (no attribution legally required)
1. **Rijksmuseum** — CC0, best API + quality, images 4000-5000px, excellent Van Gogh collection. API requires free key.
2. **Art Institute of Chicago** — CC0, well-documented API with IIIF, strong impressionist holdings. No API key needed.
3. **The Metropolitan Museum of Art** — CC0, largest collection, images 3000-6000px. No API key needed.
4. **National Gallery of Art (DC)** — CC0, strong impressionist collection. No bulk API, website downloads.

### Avoid
- Google Arts & Culture (restrictive ToS, no redistribution)
- Wikimedia CC-BY-SA images (share-alike incompatible with app distribution)
- Italian state museum images (cultural heritage law complications)

### Attribution
Include a credits screen listing museum sources as courtesy, even though CC0 does not require it.

## Image Strategy — Hybrid Approach

- **Bundle a starter set** (~30-50 curated paintings, ~150-300 MB) for immediate use and offline capability
- **Fetch additional paintings** at runtime from museum APIs, caching locally
- **Pre-fetch** the next week's wallpapers in the background
- **Format**: HEIC preferred (native macOS support, ~40% smaller than JPEG). Fallback to JPEG Q85 at 4K+ resolution.
- **Full bundle estimate**: 365 paintings at 4K HEIC = ~500-800 MB. Acceptable for Homebrew cask distribution.

## Reference Projects to Study

- `sindresorhus/macos-wallpaper` — Swift package for wallpaper management
- `axeII/WallpDesk` — Menu bar wallpaper app with time-based switching
- `jacklandrin/OnlySwitch` — Menu bar app architecture patterns

## Development Process

- **Confirm root cause before making changes.** When something doesn't work, trace the actual failure (logs, state, file inspection) before rewriting code. Multiple times we chased the wrong layer (button action, rendering thread) when the real issue was elsewhere (macOS URL caching). Reproducing the bug > guessing at fixes.
- **macOS caches wallpapers by file URL.** `setDesktopImageURL` will not reload if the URL is the same as what's already set, even if the file contents changed. Use unique filenames (e.g., with a timestamp or UUID) each time.

## Development Notes

- Run formatters/linters over the whole project, not specific files
- Never rebase — use `git pull --no-rebase`
- Never force push
- Boy Scout Rule — commit formatting/lint fixes even in untouched files
