import Foundation

enum PaintingOrientation: String, Codable, CaseIterable {
    case landscape
    case portrait

    init(width: CGFloat, height: CGFloat) {
        self = width >= height ? .landscape : .portrait
    }
}

struct Painting: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let artist: String
    let year: Int?
    let orientation: PaintingOrientation
    let sourceMuseum: String
    let sourceURL: String?
    let localFilename: String?
    let remoteImageURL: String?
    let width: Int
    let height: Int

    var displayTitle: String {
        if let year {
            return "\(title) (\(year))"
        }
        return title
    }

    var attribution: String {
        "\(artist) — \(sourceMuseum)"
    }

    var aspectRatio: CGFloat {
        CGFloat(width) / CGFloat(height)
    }
}

struct PaintingCatalog: Codable {
    let version: Int
    let paintings: [Painting]
}
