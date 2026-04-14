import ServiceManagement
import SwiftUI

enum SettingsTab: String, CaseIterable {
    case appearance = "Appearance"
    case schedule = "Schedule"
    case displays = "Displays"
    case gallery = "Gallery"
    case general = "General"
    case about = "About"

    var icon: String {
        switch self {
        case .appearance: return "paintbrush"
        case .schedule: return "clock"
        case .displays: return "display.2"
        case .gallery: return "photo.on.rectangle.angled"
        case .general: return "gear"
        case .about: return "info.circle"
        }
    }
}

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var paintingStore: PaintingStore
    @ObservedObject var wallpaperManager: WallpaperManager
    @State private var selectedTab: SettingsTab = .appearance

    var body: some View {
        HSplitView {
            // Sidebar
            VStack(spacing: 2) {
                ForEach(SettingsTab.allCases, id: \.self) { tab in
                    Button {
                        selectedTab = tab
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: tab.icon)
                                .frame(width: 20)
                            Text(tab.rawValue)
                            Spacer()
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(
                            selectedTab == tab
                                ? Color.accentColor.opacity(0.15)
                                : Color.clear
                        )
                        .cornerRadius(6)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(selectedTab == tab ? .primary : .secondary)
                }
                Spacer()
            }
            .padding(10)
            .frame(width: 170)

            // Content
            ScrollView {
                VStack(alignment: .leading) {
                    switch selectedTab {
                    case .appearance:
                        AppearancePane(settings: settings, wallpaperManager: wallpaperManager)
                    case .schedule:
                        RotationPane(settings: settings)
                    case .displays:
                        DisplayPane(settings: settings)
                    case .gallery:
                        GalleryPane(settings: settings, paintingStore: paintingStore)
                    case .general:
                        GeneralPane(settings: settings)
                    case .about:
                        AboutPane(paintingStore: paintingStore)
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(width: 520, height: 360)
    }
}

// MARK: - Appearance

private struct AppearancePane: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var wallpaperManager: WallpaperManager

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Appearance")
                .font(.title2.bold())

            Toggle("Museum Mat", isOn: $settings.matEnabled)
                .onChange(of: settings.matEnabled) { _, _ in
                    wallpaperManager.refreshCurrentWallpapers()
                }

            if settings.matEnabled {
                LabeledContent("Spacing") {
                    Picker("", selection: $settings.matSpacing) {
                        ForEach(MatSpacing.allCases, id: \.self) { spacing in
                            Text(spacing.displayName).tag(spacing)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 140)
                    .onChange(of: settings.matSpacing) { _, _ in
                        wallpaperManager.refreshCurrentWallpapers()
                    }
                }

                LabeledContent("Mat Color") {
                    ColorPicker("", selection: matColorBinding)
                        .labelsHidden()
                        .onChange(of: settings.matColorHex) { _, _ in
                            wallpaperManager.refreshCurrentWallpapers()
                        }
                }
            }
        }
    }

    private var matColorBinding: Binding<Color> {
        Binding(
            get: { settings.matColor },
            set: { newColor in
                if let components = NSColor(newColor).usingColorSpace(.sRGB) {
                    let r = Int(components.redComponent * 255)
                    let g = Int(components.greenComponent * 255)
                    let b = Int(components.blueComponent * 255)
                    settings.matColorHex = String(format: "%02X%02X%02X", r, g, b)
                }
            }
        )
    }
}

// MARK: - Schedule

private struct RotationPane: View {
    @ObservedObject var settings: AppSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Schedule")
                .font(.title2.bold())

            HStack {
                Text("Change wallpaper at")
                Picker("", selection: $settings.rotationHour) {
                    ForEach(0..<24, id: \.self) { hour in
                        Text(String(format: "%02d", hour)).tag(hour)
                    }
                }
                .labelsHidden()
                .frame(width: 60)
                Text(":")
                Picker("", selection: $settings.rotationMinute) {
                    ForEach([0, 15, 30, 45], id: \.self) { minute in
                        Text(String(format: "%02d", minute)).tag(minute)
                    }
                }
                .labelsHidden()
                .frame(width: 60)
            }

            Text("Wallpaper changes daily at the selected time.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        #if !APPSTORE
        .onChange(of: settings.rotationHour) { _, newHour in
            LaunchdScheduler.install(hour: newHour, minute: settings.rotationMinute)
        }
        .onChange(of: settings.rotationMinute) { _, newMinute in
            LaunchdScheduler.install(hour: settings.rotationHour, minute: newMinute)
        }
        #endif
    }
}

// MARK: - Displays

private struct DisplayPane: View {
    @ObservedObject var settings: AppSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Displays")
                .font(.title2.bold())

            Toggle("Unique painting per display", isOn: $settings.uniquePerDisplay)

            Text("When enabled, each monitor gets a different painting. When disabled, monitors with the same orientation share a painting.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }
}

// MARK: - Gallery

private struct GalleryPane: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var paintingStore: PaintingStore
    @State private var isFetching = false
    @State private var fetchResult: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Gallery")
                .font(.title2.bold())

            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 6) {
                GridRow {
                    Text("Total paintings:").foregroundStyle(.secondary)
                    Text("\(paintingStore.catalog.count)")
                }
                GridRow {
                    Text("Landscape:").foregroundStyle(.secondary)
                    Text("\(paintingStore.paintings(for: .landscape).count)")
                }
                GridRow {
                    Text("Portrait:").foregroundStyle(.secondary)
                    Text("\(paintingStore.paintings(for: .portrait).count)")
                }
            }

            Divider()

            Text("Additional Collections")
                .font(.headline)

            SecureField("API Key", text: $settings.rijksmuseumAPIKey)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 280)

            if settings.hasRijksmuseumKey {
                HStack {
                    Button("Fetch Paintings") {
                        fetchRijksmuseum()
                    }
                    .disabled(isFetching)

                    if isFetching {
                        ProgressView()
                            .controlSize(.small)
                    }

                    if let result = fetchResult {
                        Text(result)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Text("Expand the collection with Dutch masters. Get a free API key at data.europa.eu.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }

    private func fetchRijksmuseum() {
        isFetching = true
        fetchResult = nil

        Task {
            let client = MuseumAPIClient()
            do {
                let paintings = try await client.fetchRijksmuseumPaintings(
                    query: "van gogh",
                    apiKey: settings.rijksmuseumAPIKey,
                    limit: 30
                )
                await MainActor.run {
                    let existing = Set(paintingStore.catalog.map(\.id))
                    let new = paintings.filter { !existing.contains($0.id) }
                    paintingStore.addPaintings(new)
                    fetchResult = String(localized: "Added \(new.count) new paintings")
                    isFetching = false
                }
            } catch {
                await MainActor.run {
                    fetchResult = "Error: \(error.localizedDescription)"
                    isFetching = false
                }
            }
        }
    }
}

// MARK: - General

private struct GeneralPane: View {
    @ObservedObject var settings: AppSettings

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("General")
                .font(.title2.bold())

            Toggle("Launch at login", isOn: $settings.launchAtLogin)
                .onChange(of: settings.launchAtLogin) { _, newValue in
                    do {
                        if newValue {
                            try SMAppService.mainApp.register()
                        } else {
                            try SMAppService.mainApp.unregister()
                        }
                    } catch {
                        NSLog("[EaselWall] Login item error: \(error)")
                    }
                }

            Text("Automatically start EaselWall when you log in.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }
}

// MARK: - About

private struct AboutPane: View {
    @ObservedObject var paintingStore: PaintingStore

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Image(systemName: "paintpalette.fill")
                    .font(.system(size: 48))
                    .foregroundStyle(.tint)

                Text("EaselWall")
                    .font(.title2.bold())

                Text("v1.0")
                    .font(.caption)
                    .foregroundStyle(.tertiary)

                Text("\(paintingStore.catalog.count) paintings in collection")
                    .foregroundStyle(.secondary)

                Divider()
                    .frame(width: 240)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Artwork images in the public domain, sourced under CC0 from:")
                        .font(.callout)
                        .foregroundStyle(.secondary)

                    ForEach(Museum.allCases, id: \.self) { museum in
                        Text(museum.rawValue)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }

                Divider()
                    .frame(width: 240)

                VStack(alignment: .leading, spacing: 4) {
                    Text("This application is not affiliated with, endorsed by, or sponsored by any museum or cultural institution.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)

                    Text("Rijksmuseum collection images obtained via the Rijksmuseum API.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: 300)
            }
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity)
        }
    }
}
