import AppKit
import Foundation

@MainActor
final class PaintingStore: ObservableObject {
    @Published private(set) var catalog: [Painting] = []
    @Published private(set) var currentAssignments: [CGDirectDisplayID: Painting] = [:]

    private static let cachedCatalogVersion = 2

    private let historyKey = "paintingHistory"
    private let assignmentsKey = "currentAssignments"
    private let lastRotationDateKey = "lastRotationDate"
    private let defaults: UserDefaults
    private let cacheDirectory: URL
    private let resourceBundle: Bundle
    private let fileManager: FileManager

    private var history: [String] {
        get { defaults.stringArray(forKey: historyKey) ?? [] }
        set { defaults.set(newValue, forKey: historyKey) }
    }

    private var lastRotationDate: Date? {
        get { defaults.object(forKey: lastRotationDateKey) as? Date }
        set { defaults.set(newValue, forKey: lastRotationDateKey) }
    }

    init(
        defaults: UserDefaults = .standard,
        cacheDirectory: URL? = nil,
        resourceBundle: Bundle = .main,
        fileManager: FileManager = .default
    ) {
        self.defaults = defaults
        self.resourceBundle = resourceBundle
        self.fileManager = fileManager
        self.cacheDirectory = cacheDirectory ?? Self.defaultCacheDirectory(fileManager: fileManager)

        loadBundledCatalog()
        loadCachedCatalog()
        restoreAssignments()
    }

    // MARK: - Catalog Loading

    private func loadBundledCatalog() {
        guard let url = resourceBundle.url(
            forResource: "catalog",
            withExtension: "json",
            subdirectory: "Paintings"
        ) else {
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
        guard let data = try? Data(contentsOf: cachedCatalogURL),
              let decoded = try? JSONDecoder().decode(PaintingCatalog.self, from: data) else {
            return
        }

        let cachedPaintings: [Painting]
        if decoded.version < Self.cachedCatalogVersion {
            cachedPaintings = migratePreRightsGateCache(decoded.paintings)
        } else {
            cachedPaintings = decoded.paintings
        }

        // Merge cached paintings with bundled, avoiding duplicates
        let existingIDs = Set(catalog.map(\.id))
        let newPaintings = cachedPaintings.filter { !existingIDs.contains($0.id) }
        catalog.append(contentsOf: newPaintings)
    }

    /// Cache schema 1 predates the Rijksmuseum rights gate. Remove only those
    /// unverifiable Rijksmuseum records and their related state, then rewrite the
    /// remaining cache at schema 2 so the migration is safe to run repeatedly.
    private func migratePreRightsGateCache(_ paintings: [Painting]) -> [Painting] {
        let legacyRijksmuseumPaintings = paintings.filter {
            $0.sourceMuseum == Museum.rijksmuseum.rawValue
        }
        let legacyRijksmuseumIDs = Set(
            legacyRijksmuseumPaintings.map(\.id)
        )
        let retainedPaintings = paintings.filter {
            $0.sourceMuseum != Museum.rijksmuseum.rawValue
        }

        purgeCachedImages(for: legacyRijksmuseumIDs)
        purgeHistory(for: legacyRijksmuseumIDs)
        purgeAssignments(for: legacyRijksmuseumIDs)
        writeCachedCatalog(retainedPaintings)

        return retainedPaintings
    }

    private func purgeCachedImages(for paintingIDs: Set<String>) {
        for paintingID in paintingIDs {
            guard let imageURL = cachedImageURL(for: paintingID),
                  fileManager.fileExists(atPath: imageURL.path) else {
                continue
            }

            do {
                try fileManager.removeItem(at: imageURL)
            } catch {
                NSLog("[EaselWall] Failed to remove legacy cached image: \(error)")
            }
        }
    }

    private func purgeHistory(for paintingIDs: Set<String>) {
        guard !paintingIDs.isEmpty else { return }
        let savedHistory = history
        let retainedHistory = savedHistory.filter { !paintingIDs.contains($0) }
        if retainedHistory != savedHistory {
            history = retainedHistory
        }
    }

    private func purgeAssignments(for paintingIDs: Set<String>) {
        guard !paintingIDs.isEmpty,
              let saved = defaults.dictionary(forKey: assignmentsKey) as? [String: String] else {
            return
        }

        let retainedAssignments = saved.filter { !paintingIDs.contains($0.value) }
        if retainedAssignments != saved {
            defaults.set(retainedAssignments, forKey: assignmentsKey)
        }
    }

    func addPaintings(_ paintings: [Painting]) {
        let existingIDs = Set(catalog.map(\.id))
        let newPaintings = paintings.filter { !existingIDs.contains($0.id) }
        catalog.append(contentsOf: newPaintings)
        saveCachedCatalog()
    }

    private func saveCachedCatalog() {
        // Save only non-bundled paintings
        let bundledIDs = loadBundledIDs()
        let cachedPaintings = catalog.filter { !bundledIDs.contains($0.id) }
        writeCachedCatalog(cachedPaintings)
    }

    private func writeCachedCatalog(_ paintings: [Painting]) {
        let cacheCatalog = PaintingCatalog(
            version: Self.cachedCatalogVersion,
            paintings: paintings
        )

        do {
            try fileManager.createDirectory(at: cacheDirectory, withIntermediateDirectories: true)
            let data = try JSONEncoder().encode(cacheCatalog)
            try data.write(to: cachedCatalogURL, options: .atomic)
        } catch {
            NSLog("[EaselWall] Failed to save cached catalog: \(error)")
        }
    }

    private func loadBundledIDs() -> Set<String> {
        guard let url = resourceBundle.url(
            forResource: "catalog",
            withExtension: "json",
            subdirectory: "Paintings"
        ),
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
        defaults.set(flat, forKey: assignmentsKey)
    }

    private func restoreAssignments() {
        guard let saved = defaults.dictionary(forKey: assignmentsKey) as? [String: String] else {
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
           let url = resourceBundle.url(
               forResource: filename,
               withExtension: nil,
               subdirectory: "Paintings"
           ),
           let image = NSImage(contentsOf: url) {
            return image
        }

        // Try cached image
        guard let cachedURL = cachedImageURL(for: painting.id) else { return nil }
        if let image = NSImage(contentsOf: cachedURL) {
            return image
        }

        return nil
    }

    func cacheImage(_ data: Data, for painting: Painting) {
        guard let url = cachedImageURL(for: painting.id) else { return }
        do {
            try fileManager.createDirectory(at: imagesDirectory, withIntermediateDirectories: true)
            try data.write(to: url, options: .atomic)
        } catch {
            NSLog("[EaselWall] Failed to cache image: \(error)")
        }
    }

    // MARK: - Paths

    private var cachedCatalogURL: URL {
        cacheDirectory.appendingPathComponent("cached_catalog.json")
    }

    private var imagesDirectory: URL {
        cacheDirectory.appendingPathComponent("images", isDirectory: true)
    }

    private func cachedImageURL(for paintingID: String) -> URL? {
        guard !paintingID.isEmpty,
              !paintingID.contains("/"),
              paintingID != ".",
              paintingID != ".." else {
            return nil
        }
        return imagesDirectory.appendingPathComponent(paintingID + ".jpg")
    }

    private static func defaultCacheDirectory(fileManager: FileManager) -> URL {
        let appSupport = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = appSupport.appendingPathComponent("EaselWall", isDirectory: true)
        try? fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
}
