import Foundation
import XCTest

@MainActor
final class PaintingStoreMigrationTests: XCTestCase {
    func testPreGateCachePurgesRijksmuseumStateAndRetainsUnrelatedRecords() throws {
        let context = try TestContext()
        defer { context.cleanup() }

        let legacyRijksmuseum = Self.painting(
            id: "rijks_SK-A-3262",
            sourceMuseum: Museum.rijksmuseum.rawValue
        )
        let secondLegacyRijksmuseum = Self.painting(
            id: "rijks_RP-P-OB-12.363",
            sourceMuseum: Museum.rijksmuseum.rawValue
        )
        let retainedAIC = Self.painting(
            id: "aic_28560",
            sourceMuseum: Museum.artInstituteChicago.rawValue
        )

        try context.writeCatalog(
            version: 1,
            paintings: [legacyRijksmuseum, retainedAIC, secondLegacyRijksmuseum]
        )
        try context.writeCachedImage(for: legacyRijksmuseum.id)
        try context.writeCachedImage(for: secondLegacyRijksmuseum.id)
        try context.writeCachedImage(for: retainedAIC.id)
        context.defaults.set(
            [
                legacyRijksmuseum.id,
                retainedAIC.id,
                secondLegacyRijksmuseum.id,
                "unrelated_history",
            ],
            forKey: "paintingHistory"
        )
        context.defaults.set(
            [
                "100": legacyRijksmuseum.id,
                "200": retainedAIC.id,
                "300": "unrelated_assignment",
                "400": secondLegacyRijksmuseum.id,
            ],
            forKey: "currentAssignments"
        )

        let store = PaintingStore(
            defaults: context.defaults,
            cacheDirectory: context.cacheDirectory,
            resourceBundle: Bundle(for: Self.self)
        )

        XCTAssertEqual(store.catalog.map(\.id), [retainedAIC.id])
        let migratedCatalog = try context.readCatalog()
        XCTAssertEqual(migratedCatalog.version, 2)
        XCTAssertEqual(migratedCatalog.paintings.map(\.id), [retainedAIC.id])

        XCTAssertFalse(context.cachedImageExists(for: legacyRijksmuseum.id))
        XCTAssertFalse(context.cachedImageExists(for: secondLegacyRijksmuseum.id))
        XCTAssertTrue(context.cachedImageExists(for: retainedAIC.id))
        XCTAssertEqual(
            context.defaults.stringArray(forKey: "paintingHistory"),
            [retainedAIC.id, "unrelated_history"]
        )
        XCTAssertEqual(
            context.defaults.dictionary(forKey: "currentAssignments") as? [String: String],
            [
                "200": retainedAIC.id,
                "300": "unrelated_assignment",
            ]
        )
        XCTAssertNil(store.currentAssignments[CGDirectDisplayID(100)])
        XCTAssertEqual(store.currentAssignments[CGDirectDisplayID(200)]?.id, retainedAIC.id)
        XCTAssertNil(store.currentAssignments[CGDirectDisplayID(400)])
    }

    func testSchemaTwoRijksmuseumCacheAndStateAreRetained() throws {
        let context = try TestContext()
        defer { context.cleanup() }

        let verifiedRijksmuseum = Self.painting(
            id: "rijks_200109794",
            sourceMuseum: Museum.rijksmuseum.rawValue,
            sourceURL: "https://id.rijksmuseum.nl/200109794",
            remoteImageURL: "https://iiif.micr.io/vjYfT/full/2000,/0/default.jpg"
        )

        try context.writeCatalog(version: 2, paintings: [verifiedRijksmuseum])
        try context.writeCachedImage(for: verifiedRijksmuseum.id)
        context.defaults.set([verifiedRijksmuseum.id], forKey: "paintingHistory")
        context.defaults.set(
            ["400": verifiedRijksmuseum.id],
            forKey: "currentAssignments"
        )

        let store = PaintingStore(
            defaults: context.defaults,
            cacheDirectory: context.cacheDirectory,
            resourceBundle: Bundle(for: Self.self)
        )

        XCTAssertEqual(store.catalog.map(\.id), [verifiedRijksmuseum.id])
        let cachedCatalog = try context.readCatalog()
        XCTAssertEqual(cachedCatalog.version, 2)
        XCTAssertEqual(cachedCatalog.paintings.map(\.id), [verifiedRijksmuseum.id])
        XCTAssertTrue(context.cachedImageExists(for: verifiedRijksmuseum.id))
        XCTAssertEqual(
            context.defaults.stringArray(forKey: "paintingHistory"),
            [verifiedRijksmuseum.id]
        )
        XCTAssertEqual(
            context.defaults.dictionary(forKey: "currentAssignments") as? [String: String],
            ["400": verifiedRijksmuseum.id]
        )
        XCTAssertEqual(
            store.currentAssignments[CGDirectDisplayID(400)]?.id,
            verifiedRijksmuseum.id
        )
    }

    func testPreGateMigrationIsIdempotent() throws {
        let context = try TestContext()
        defer { context.cleanup() }

        let legacyRijksmuseum = Self.painting(
            id: "rijks_SK-C-1701",
            sourceMuseum: Museum.rijksmuseum.rawValue
        )
        let retainedMet = Self.painting(
            id: "met_436532",
            sourceMuseum: Museum.metropolitanMuseum.rawValue
        )
        try context.writeCatalog(version: 1, paintings: [legacyRijksmuseum, retainedMet])
        try context.writeCachedImage(for: legacyRijksmuseum.id)
        context.defaults.set(
            [legacyRijksmuseum.id, retainedMet.id],
            forKey: "paintingHistory"
        )
        context.defaults.set(
            ["500": legacyRijksmuseum.id, "600": retainedMet.id],
            forKey: "currentAssignments"
        )

        let firstStore = PaintingStore(
            defaults: context.defaults,
            cacheDirectory: context.cacheDirectory,
            resourceBundle: Bundle(for: Self.self)
        )
        let firstCatalogData = try Data(contentsOf: context.catalogURL)
        let firstHistory = context.defaults.stringArray(forKey: "paintingHistory")
        let firstAssignments = context.defaults.dictionary(forKey: "currentAssignments")

        let secondStore = PaintingStore(
            defaults: context.defaults,
            cacheDirectory: context.cacheDirectory,
            resourceBundle: Bundle(for: Self.self)
        )

        XCTAssertEqual(firstStore.catalog.map(\.id), [retainedMet.id])
        XCTAssertEqual(secondStore.catalog.map(\.id), [retainedMet.id])
        XCTAssertEqual(try Data(contentsOf: context.catalogURL), firstCatalogData)
        XCTAssertEqual(
            context.defaults.stringArray(forKey: "paintingHistory"),
            firstHistory
        )
        XCTAssertEqual(
            context.defaults.dictionary(forKey: "currentAssignments") as NSDictionary?,
            firstAssignments as NSDictionary?
        )
        XCTAssertFalse(context.cachedImageExists(for: legacyRijksmuseum.id))
        XCTAssertEqual(try context.readCatalog().version, 2)
    }

    func testNewCachedPaintingsAreWrittenAsSchemaTwo() throws {
        let context = try TestContext()
        defer { context.cleanup() }

        let verifiedRijksmuseum = Self.painting(
            id: "rijks_200109305",
            sourceMuseum: Museum.rijksmuseum.rawValue,
            sourceURL: "https://id.rijksmuseum.nl/200109305",
            remoteImageURL: "https://iiif.micr.io/egrgo/full/1600,/0/default.jpg"
        )
        let store = PaintingStore(
            defaults: context.defaults,
            cacheDirectory: context.cacheDirectory,
            resourceBundle: Bundle(for: Self.self)
        )

        store.addPaintings([verifiedRijksmuseum])

        let cachedCatalog = try context.readCatalog()
        XCTAssertEqual(cachedCatalog.version, 2)
        XCTAssertEqual(cachedCatalog.paintings.map(\.id), [verifiedRijksmuseum.id])
    }

    private static func painting(
        id: String,
        sourceMuseum: String,
        sourceURL: String? = "https://example.com/artwork",
        remoteImageURL: String? = "https://example.com/image.jpg"
    ) -> Painting {
        Painting(
            id: id,
            title: "Test Painting",
            artist: "Test Artist",
            year: 1887,
            orientation: .landscape,
            sourceMuseum: sourceMuseum,
            sourceURL: sourceURL,
            localFilename: nil,
            remoteImageURL: remoteImageURL,
            width: 1_600,
            height: 1_200
        )
    }
}

private struct TestContext {
    let suiteName: String
    let defaults: UserDefaults
    let cacheDirectory: URL

    init() throws {
        suiteName = "PaintingStoreMigrationTests.\(UUID().uuidString)"
        defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        cacheDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent(suiteName, isDirectory: true)
        try FileManager.default.createDirectory(
            at: cacheDirectory,
            withIntermediateDirectories: true
        )
    }

    var catalogURL: URL {
        cacheDirectory.appendingPathComponent("cached_catalog.json")
    }

    private var imagesDirectory: URL {
        cacheDirectory.appendingPathComponent("images", isDirectory: true)
    }

    func writeCatalog(version: Int, paintings: [Painting]) throws {
        let data = try JSONEncoder().encode(
            PaintingCatalog(version: version, paintings: paintings)
        )
        try data.write(to: catalogURL, options: .atomic)
    }

    func readCatalog() throws -> PaintingCatalog {
        let data = try Data(contentsOf: catalogURL)
        return try JSONDecoder().decode(PaintingCatalog.self, from: data)
    }

    func writeCachedImage(for paintingID: String) throws {
        try FileManager.default.createDirectory(
            at: imagesDirectory,
            withIntermediateDirectories: true
        )
        try Data("cached image".utf8).write(
            to: imagesDirectory.appendingPathComponent(paintingID + ".jpg"),
            options: .atomic
        )
    }

    func cachedImageExists(for paintingID: String) -> Bool {
        FileManager.default.fileExists(
            atPath: imagesDirectory.appendingPathComponent(paintingID + ".jpg").path
        )
    }

    func cleanup() {
        defaults.removePersistentDomain(forName: suiteName)
        try? FileManager.default.removeItem(at: cacheDirectory)
    }
}
