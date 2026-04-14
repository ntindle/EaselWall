# App Store Review Notes

## Image Licensing Authorization

EaselWall displays public-domain impressionist paintings sourced under CC0 (Creative Commons Zero) from the following museum open-access programs:

### Art Institute of Chicago
- **Open Access Policy:** https://www.artic.edu/open-access
- **License:** CC0 1.0 Universal
- **API Documentation:** https://api.artic.edu/docs/
- **Quote:** Images designated CC0 may be used "for any purpose, including commercial and noncommercial uses, free of charge and without additional permission from the museum."

### The Metropolitan Museum of Art
- **Open Access Policy:** https://www.metmuseum.org/about-the-met/policies-and-documents/open-access
- **License:** CC0 1.0 Universal
- **API Documentation:** https://metmuseum.github.io/
- **Quote:** "You are free to copy, modify, and distribute the works, even for commercial purposes, all without asking permission."

### Rijksmuseum (optional — requires user-provided API key)
- **Data Policy:** https://data.rijksmuseum.nl/policy
- **License:** CC0 1.0 / Public Domain Mark
- **API Terms:** https://data.rijksmuseum.nl/policy
- **Note:** Rijksmuseum images are only fetched if the user provides their own free API key in Settings. No Rijksmuseum images are bundled with the app.

## Technical Notes

- The app sets desktop wallpapers using `NSWorkspace.shared.setDesktopImageURL(_:for:options:)`, a public AppKit API that does not require special entitlements.
- A curated starter collection of ~30 paintings is bundled with the app for offline use.
- Additional paintings are fetched at runtime from the above museum APIs.
- The app requires an internet connection to discover new paintings beyond the bundled set.

## Content Rating

- No user-generated content
- No social features
- No in-app purchases
- All artwork is museum-curated, public-domain fine art (19th century impressionism)
