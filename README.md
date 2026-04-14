# EaselWall

A native macOS menu bar app that sets your desktop wallpaper to a different impressionist painting each day, displayed on museum-style mats.

**[easelwall.com](https://easelwall.com)** · Free via Homebrew & direct download · $3 on the Mac App Store

## Install

**Homebrew:**
```
brew install ntindle/easelwall/easelwall
```

**Direct download:** [Latest DMG from GitHub Releases](https://github.com/ntindle/EaselWall/releases/latest)

## Features

- **Museum-style mats** — Each painting is presented on a soft cream mat with gallery-style spacing and a subtle shadow. Configurable color and padding.
- **Orientation aware** — Landscape paintings go on landscape monitors, portrait paintings on portrait monitors. Automatic detection on plug/unplug.
- **Unique per display** — Optionally show a different painting on each monitor.
- **Daily rotation** — Pseudo-random selection with no repeats until the full cycle completes. Swaps at midnight (configurable).
- **Set & forget** — Lives in your menu bar. Launch at login via SMAppService. launchd agent wakes the app even after reboot or sleep.
- **Control Center widget** — macOS Tahoe (26+) gets a native "Next Painting" button in Control Center.
- **53 paintings** from Van Gogh, Monet, Renoir, Cezanne, Degas, Cassatt, and Seurat. 30 bundled for offline use, more fetched at runtime.
- **Expand the collection** — Add Dutch masters via optional Rijksmuseum API key in Settings.
- **Localized** — English, Spanish, French, German, Japanese, Chinese (Simplified), Portuguese (Brazil), Dutch.

## Screenshots

_Coming soon — see the website for a preview of the desk setup with 3 monitors._

## Development

Requirements: macOS 14+, Xcode 16+, XcodeGen

```bash
# Generate the Xcode project from project.yml
brew install xcodegen
xcodegen generate

# Build
make build

# Release build + DMG
make release
make dmg
```

The `.xcodeproj` is gitignored — it's generated from `project.yml`.

## Architecture

```
Sources/
  EaselWall/
    App/            — SwiftUI app entry point, AppDelegate
    Models/         — Painting, AppSettings
    Services/       — WallpaperManager, ScreenManager, MatRenderer,
                      PaintingStore, LaunchdScheduler
    Views/          — MenuBarView, SettingsView (sidebar layout)
    API/            — MuseumAPIClient (AIC, Met, Rijksmuseum)
    Resources/      — Localizable.xcstrings
  ControlWidget/    — macOS 26+ Control Center widget
  Shared/           — AppIntents (shared between app + widget)
Resources/
  Paintings/
    catalog.json    — 53 paintings with metadata + image URLs
    images/         — 30 bundled JPEGs for offline use
```

## Legal & Image Licensing

### Artwork Copyright

All paintings featured in this application are by artists who died over 100 years ago. Their works are in the **public domain worldwide** under all applicable copyright regimes.

Featured artists include Vincent van Gogh (d. 1890), Claude Monet (d. 1926), Pierre-Auguste Renoir (d. 1919), Paul Cezanne (d. 1906), Edgar Degas (d. 1917), Mary Cassatt (d. 1926), and Georges Seurat (d. 1891).

### Photographic Reproductions

Digital reproductions of two-dimensional public domain artworks do not create new copyrights under U.S. law. This principle was established in *Bridgeman Art Library, Ltd. v. Corel Corp.*, 36 F. Supp. 2d 191 (S.D.N.Y. 1999), reinforced in *Meshwerks, Inc. v. Toyota Motor Sales U.S.A., Inc.*, 528 F.3d 1258 (10th Cir. 2008), and codified in Article 14 of EU Directive 2019/790 (DSM Directive).

### Image Sources

All artwork images are obtained under **CC0 1.0 Universal** public domain dedications:

| Institution | License | Attribution Required |
|---|---|---|
| [Art Institute of Chicago](https://www.artic.edu/open-access) | CC0 1.0 | No |
| [The Metropolitan Museum of Art](https://www.metmuseum.org/about-the-met/policies-and-documents/open-access) | CC0 1.0 | No |
| [Rijksmuseum](https://www.rijksmuseum.nl/en/research/conduct-research/data/policy) | CC0 1.0 / PDM | No (but see API terms) |

### Rijksmuseum API Attribution

Per the Rijksmuseum API Terms of Use, this application was developed using the Rijksmuseum API. Rijksmuseum collection images are credited as "Rijksmuseum Collection."

### Disclaimer

This application is not affiliated with, endorsed by, or sponsored by any museum or cultural institution. If you believe any image has been included in error, please open an issue.

## License

[Business Source License 1.1](LICENSE) — Source-available. Converts to Apache 2.0 on 2030-04-13.
