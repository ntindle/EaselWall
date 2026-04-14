import ServiceManagement
import SwiftUI

@main
struct EaselWallApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(
                paintingStore: appDelegate.paintingStore,
                wallpaperManager: appDelegate.wallpaperManager,
                screenManager: appDelegate.screenManager,
                onOpenSettings: { appDelegate.showSettings() }
            )
        } label: {
            Image(systemName: "paintpalette.fill")
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let settings = AppSettings.shared
    let screenManager = ScreenManager()
    let paintingStore = PaintingStore()
    lazy var wallpaperManager = WallpaperManager(
        screenManager: screenManager,
        paintingStore: paintingStore,
        settings: settings
    )

    private var rotationTimer: Timer?
    private var settingsWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSLog("[EaselWall] App launched, \(paintingStore.catalog.count) paintings loaded")

        if settings.launchAtLogin {
            try? SMAppService.mainApp.register()
        }

        if paintingStore.needsRotation() {
            NSLog("[EaselWall] Rotation needed, rotating...")
            wallpaperManager.rotateWallpapers()
        }

        scheduleMidnightRotation()

        #if !APPSTORE
        // Install/update launchd agent so the app wakes even if killed or after sleep
        LaunchdScheduler.install(hour: settings.rotationHour, minute: settings.rotationMinute)
        #endif

        Task {
            await prefetchUpcomingPaintings()
        }

        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.wallpaperManager.refreshCurrentWallpapers()
            }
        }

        DistributedNotificationCenter.default().addObserver(
            forName: .init("com.ntindle.EaselWall.nextPainting"),
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.wallpaperManager.rotateWallpapers()
            }
        }
    }

    // MARK: - Settings Window

    func showSettings() {
        if let existing = settingsWindow, existing.isVisible {
            existing.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let settingsView = SettingsView(
            settings: settings,
            paintingStore: paintingStore,
            wallpaperManager: wallpaperManager
        )

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 360),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "EaselWall"
        window.contentView = NSHostingView(rootView: settingsView)
        window.center()
        window.isReleasedWhenClosed = false
        settingsWindow = window

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - Scheduling

    private func scheduleMidnightRotation() {
        rotationTimer?.invalidate()

        let calendar = Calendar.current
        var targetComponents = DateComponents()
        targetComponents.hour = settings.rotationHour
        targetComponents.minute = settings.rotationMinute

        guard let nextRotation = calendar.nextDate(
            after: Date(),
            matching: targetComponents,
            matchingPolicy: .nextTime
        ) else { return }

        let interval = nextRotation.timeIntervalSinceNow
        NSLog("[EaselWall] Next rotation scheduled in \(Int(interval)) seconds")

        rotationTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: false) { [weak self] _ in
            Task { @MainActor in
                self?.wallpaperManager.rotateWallpapers()
                self?.scheduleMidnightRotation()

                Task {
                    await self?.prefetchUpcomingPaintings()
                }
            }
        }
    }

    // MARK: - Pre-fetching

    private func prefetchUpcomingPaintings() async {
        let screens = screenManager.screens
        let orientationsNeeded = Set(screens.map(\.orientation))
        let paintingsPerOrientation = settings.uniquePerDisplay ? screens.count : 1
        let daysToPreFetch = 7

        for orientation in orientationsNeeded {
            let available = paintingStore.paintings(for: orientation)
            let uncached = available.filter { painting in
                paintingStore.loadImage(for: painting) == nil && painting.remoteImageURL != nil
            }

            let toFetch = Array(uncached.prefix(daysToPreFetch * paintingsPerOrientation))
            for painting in toFetch {
                guard let urlString = painting.remoteImageURL,
                      let url = URL(string: urlString) else { continue }
                do {
                    let (data, response) = try await URLSession.shared.data(from: url)
                    if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200,
                       let contentType = httpResponse.value(forHTTPHeaderField: "Content-Type"),
                       contentType.contains("image") {
                        paintingStore.cacheImage(data, for: painting)
                        NSLog("[EaselWall] Pre-fetched: \(painting.title)")
                    }
                } catch {
                    // Non-critical
                }
            }
        }
    }
}
