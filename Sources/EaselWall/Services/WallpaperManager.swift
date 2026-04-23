import AppKit

@MainActor
final class WallpaperManager: ObservableObject {
    private let screenManager: ScreenManager
    private let paintingStore: PaintingStore
    private let settings: AppSettings

    @Published private(set) var lastError: String?
    @Published private(set) var isWorking: Bool = false

    init(screenManager: ScreenManager, paintingStore: PaintingStore, settings: AppSettings) {
        self.screenManager = screenManager
        self.paintingStore = paintingStore
        self.settings = settings
    }

    func rotateWallpapers() {
        NSLog("[EaselWall] rotateWallpapers called")
        let screens = screenManager.screens
        guard !screens.isEmpty else {
            lastError = "No screens detected"
            return
        }

        var usedPaintingIDs: Set<String> = []

        for screen in screens {
            guard let painting = paintingStore.nextPainting(
                for: screen.orientation,
                excluding: settings.uniquePerDisplay ? usedPaintingIDs : []
            ) else {
                NSLog("[EaselWall] No painting for \(screen.orientation)")
                continue
            }

            if settings.uniquePerDisplay {
                usedPaintingIDs.insert(painting.id)
            }

            // Assign immediately so the menu updates
            paintingStore.assign(painting, to: screen.id)

            let paintingImage = paintingStore.loadImage(for: painting)
            renderAndApply(
                paintingImage: paintingImage,
                painting: painting,
                screen: screen
            )
        }

        paintingStore.recordRotation()
        lastError = nil
    }

    func refreshCurrentWallpapers() {
        let screens = screenManager.screens
        var usedPaintingIDs = Set(paintingStore.currentAssignments.values.map(\.id))

        for screen in screens {
            if let painting = paintingStore.currentAssignments[screen.id],
               let image = paintingStore.loadImage(for: painting) {
                // Existing assignment — re-render with current mat settings
                renderAndApply(
                    paintingImage: image,
                    painting: painting,
                    screen: screen
                )
            } else {
                // New/unassigned screen — pick a painting for it
                guard let painting = paintingStore.nextPainting(
                    for: screen.orientation,
                    excluding: settings.uniquePerDisplay ? usedPaintingIDs : []
                ) else { continue }

                usedPaintingIDs.insert(painting.id)
                let image = paintingStore.loadImage(for: painting)
                renderAndApply(
                    paintingImage: image,
                    painting: painting,
                    screen: screen
                )
            }
        }
    }

    private func renderAndApply(
        paintingImage: NSImage?,
        painting: Painting,
        screen: ScreenInfo
    ) {
        let screenID = screen.id
        let pixelWidth = screen.pixelWidth
        let pixelHeight = screen.pixelHeight
        let matCGColor = settings.matNSColor.cgColor
        let spacing = settings.matSpacing
        let matEnabled = settings.matEnabled
        let paintingID = painting.id
        let paintingTitle = painting.title
        let remoteURL = painting.remoteImageURL

        isWorking = true

        Task {
            // Step 1: Get or fetch the image (can do async)
            let image: NSImage
            if let existing = paintingImage {
                image = existing
            } else {
                guard let urlString = remoteURL,
                      let url = URL(string: urlString) else {
                    NSLog("[EaselWall] No URL for '\(paintingTitle)'")
                    isWorking = false
                    return
                }
                do {
                    let (data, response) = try await URLSession.shared.data(from: url)
                    if let httpResp = response as? HTTPURLResponse, httpResp.statusCode != 200 {
                        NSLog("[EaselWall] HTTP \(httpResp.statusCode) for '\(paintingTitle)'")
                        isWorking = false
                        return
                    }
                    guard let fetched = NSImage(data: data) else {
                        NSLog("[EaselWall] Bad image data for '\(paintingTitle)'")
                        isWorking = false
                        return
                    }
                    paintingStore.cacheImage(data, for: painting)
                    image = fetched
                } catch {
                    NSLog("[EaselWall] Fetch error for '\(paintingTitle)': \(error)")
                    isWorking = false
                    return
                }
            }

            // Step 2: Render (pure CGContext, thread-safe)
            let matConfig = MatRenderer.Configuration(
                matColor: matCGColor,
                spacing: spacing,
                matEnabled: matEnabled
            )

            guard let rendered = MatRenderer.render(
                painting: image,
                screenWidth: pixelWidth,
                screenHeight: pixelHeight,
                configuration: matConfig
            ) else {
                NSLog("[EaselWall] Render failed for '\(paintingTitle)'")
                isWorking = false
                return
            }

            guard let fileURL = MatRenderer.saveToTemporaryFile(rendered, screenID: screenID) else {
                NSLog("[EaselWall] Save failed for '\(paintingTitle)'")
                isWorking = false
                return
            }

            NSLog("[EaselWall] Rendered '\(paintingTitle)' -> \(fileURL.lastPathComponent)")

            // Step 3: Apply wallpaper (must use NSScreen on main thread)
            guard let nsScreen = screenManager.nsScreen(for: screen) else {
                lastError = "Screen not available"
                isWorking = false
                return
            }

            do {
                try NSWorkspace.shared.setDesktopImageURL(fileURL, for: nsScreen, options: [:])
                paintingStore.markAsShown(painting)
                NSLog("[EaselWall] Applied wallpaper: '\(paintingTitle)' on screen \(screenID)")
            } catch {
                lastError = "Failed: \(error.localizedDescription)"
                NSLog("[EaselWall] setDesktopImageURL error: \(error)")
            }
            isWorking = false
        }
    }
}
