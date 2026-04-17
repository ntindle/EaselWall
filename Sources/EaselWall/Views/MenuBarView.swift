import SwiftUI

struct MenuBarView: View {
    @ObservedObject var paintingStore: PaintingStore
    @ObservedObject var wallpaperManager: WallpaperManager
    @ObservedObject var screenManager: ScreenManager

    let onOpenSettings: () -> Void
    let onCheckForUpdates: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !paintingStore.currentAssignments.isEmpty {
                currentPaintingsSection
                Divider()
            }

            Button {
                wallpaperManager.rotateWallpapers()
            } label: {
                Label("Next Painting", systemImage: "forward.fill")
            }
            .keyboardShortcut("n")

            Divider()

            Button {
                onOpenSettings()
            } label: {
                Label("Settings...", systemImage: "gear")
            }
            .keyboardShortcut(",")

            #if !APPSTORE
            Button {
                onCheckForUpdates()
            } label: {
                Label("Check for Updates...", systemImage: "arrow.triangle.2.circlepath")
            }
            #endif

            Divider()

            Button("Quit") {
                NSApplication.shared.terminate(nil)
            }
            .keyboardShortcut("q")
        }
        .frame(width: 280)
    }

    @ViewBuilder
    private var currentPaintingsSection: some View {
        let screens = screenManager.screens
        ForEach(screens) { screen in
            if let painting = paintingStore.currentAssignments[screen.id] {
                VStack(alignment: .leading, spacing: 2) {
                    Text(painting.title)
                        .font(.headline)
                        .lineLimit(1)
                    Text(painting.attribution)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if screens.count > 1 {
                        Text(String(localized: "\(screen.orientation.rawValue.capitalized) display"))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
            }
        }
    }
}
