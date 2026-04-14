import AppKit
import Foundation

@MainActor
final class PaintingStore: ObservableObject {
    @Published private(set) var catalog: [Painting] = []
    @Published private(set) var currentAssignments: [CGDirectDisplayID: Painting] = [:]

    private let historyKey = "paintingHistory"
    private let assignmentsKey = "currentAssignments"
    private let lastRotationDateKey = "lastRotationDate"

    private var history: [String] {
        get { UserDefaults.standard.stringArray(forKey: historyKey) ?? [] }
        set { UserDefaults.standard.set(newValue, forKey: historyKey) }
    }

    private var lastRotationDate: Date? {
        get { UserDefaults.standard.object(forKey: lastRotationDateKey) as? Date }
        set { UserDefaults.standard.set(newValue, forKey: lastRotationDateKey) }
    }

    init() {
        loadBundledCatalog()
        loadCachedCatalog()
        restoreAssignments()
    }

    // MARK: - Catalog Loading

    private func loadBundledCatalog() {
        guard let url = Bundle.main.url(forResource: "catalog", withExtension: "json", subdirectory: "Paintings") else {
            NSLog("[EaselWall] catalog.json not found in bundle")
            return
        }
        NSLog("[EaselWall] Found catalog at: \(url.path)")

        guard let data = try? Data(contentsOf: url) else {
            NSLog("[EaselWall] Failed to read catalog data")
            return
        }

        do {
            let decoded = try JSONDecoder().decode(PaintingCatalog.self, from: data)
            catalog = decoded.paintings
            NSLog("[EaselWall] Loaded \(catalog.count) paintings from catalog")
        } catch {
            NSLog("[EaselWall] Failed to decode catalog: \(error)")
        }
    }

    private func loadCachedCatalog() {
        let cacheURL = Self.cacheDirectory.appendingPathComponent("cached_catalog.json")
        guard let data = try? Data(contentsOf: cacheURL),
              let decoded = try? JSONDecoder().decode(PaintingCatalog.self, from: data) else {
            return
        }
        // Merge cached paintings with bundled, avoiding duplicates
        let existingIDs = Set(catalog.map(\.id))
        let newPaintings = decoded.paintings.filter { !existingIDs.contains($0.id) }
        catalog.append(contentsOf: newPaintings)
    }

    func addPaintings(_ paintings: [Painting]) {
        let existingIDs = Set(catalog.map(\.id))
        let newPaintings = paintings.filter { !existingIDs.contains($0.id) }
        catalog.append(contentsOf: newPaintings)
        saveCachedCatalog()
    }

    private func saveCachedCatalog() {
        let cacheURL = Self.cacheDirectory.appendingPathComponent("cached_catalog.json")
        // Save only non-bundled paintings
        let bundledIDs = loadBundledIDs()
        let cachedPaintings = catalog.filter { !bundledIDs.contains($0.id) }
        let cacheCatalog = PaintingCatalog(version: 1, paintings: cachedPaintings)
        if let data = try? JSONEncoder().encode(cacheCatalog) {
            try? data.write(to: cacheURL)
        }
    }

    private func loadBundledIDs() -> Set<String> {
        guard let url = Bundle.main.url(forResource: "catalog", withExtension: "json", subdirectory: "Paintings"),
              let data = try? Data(contentsOf: url),
              let decoded = try? JSONDecoder().decode(PaintingCatalog.self, from: data) else {
            return []
        }
        return Set(decoded.paintings.map(\.id))
    }

    // MARK: - Painting Selection

    func paintings(for orientation: PaintingOrientation) -> [Painting] {
        catalog.filter { $0.orientation == orientation }
    }

    func nextPainting(for orientation: PaintingOrientation, excluding: Set<String> = []) -> Painting? {
        let available = paintings(for: orientation)
        let unseen = available.filter { !history.contains($0.id) && !excluding.contains($0.id) }

        // If all paintings have been seen, reset history for this orientation
        if unseen.isEmpty {
            let orientationIDs = Set(available.map(\.id))
            history.removeAll { orientationIDs.contains($0) }
            return available.filter { !excluding.contains($0.id) }.randomElement() ?? available.first
        }

        return unseen.randomElement()
    }

    func markAsShown(_ painting: Painting) {
        var h = history
        h.append(painting.id)
        history = h
    }

    // MARK: - Assignments

    func assign(_ painting: Painting, to screenID: CGDirectDisplayID) {
        currentAssignments[screenID] = painting
        saveAssignments()
    }

    private func saveAssignments() {
        let serializable = currentAssignments.map { (key: UInt32, value: Painting) in
            [String(key): value.id]
        }
        let flat = serializable.reduce(into: [String: String]()) { result, dict in
            result.merge(dict) { _, new in new }
        }
        UserDefaults.standard.set(flat, forKey: assignmentsKey)
    }

    private func restoreAssignments() {
        guard let saved = UserDefaults.standard.dictionary(forKey: assignmentsKey) as? [String: String] else {
            return
        }
        let paintingsByID = Dictionary(uniqueKeysWithValues: catalog.map { ($0.id, $0) })
        for (screenIDStr, paintingID) in saved {
            if let screenID = UInt32(screenIDStr), let painting = paintingsByID[paintingID] {
                currentAssignments[CGDirectDisplayID(screenID)] = painting
            }
        }
    }

    // MARK: - Rotation

    func needsRotation() -> Bool {
        guard let lastDate = lastRotationDate else { return true }
        return !Calendar.current.isDateInToday(lastDate)
    }

    func recordRotation() {
        lastRotationDate = Date()
    }

    // MARK: - Image Loading

    func loadImage(for painting: Painting) -> NSImage? {
        // Try bundled image first
        if let filename = painting.localFilename,
           let url = Bundle.main.url(forResource: filename, withExtension: nil, subdirectory: "Paintings"),
           let image = NSImage(contentsOf: url) {
            return image
        }

        // Try cached image
        let cachedURL = Self.cacheDirectory
            .appendingPathComponent("images")
            .appendingPathComponent(painting.id + ".jpg")
        if let image = NSImage(contentsOf: cachedURL) {
            return image
        }

        return nil
    }

    func cacheImage(_ data: Data, for painting: Painting) {
        let imagesDir = Self.cacheDirectory.appendingPathComponent("images")
        try? FileManager.default.createDirectory(at: imagesDir, withIntermediateDirectories: true)
        let url = imagesDir.appendingPathComponent(painting.id + ".jpg")
        try? data.write(to: url)
    }

    // MARK: - Paths

    static var cacheDirectory: URL {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = appSupport.appendingPathComponent("EaselWall", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
}
