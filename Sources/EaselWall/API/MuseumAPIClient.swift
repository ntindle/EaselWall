import Foundation

enum Museum: String, CaseIterable, Codable {
    case rijksmuseum = "Rijksmuseum Collection"
    case artInstituteChicago = "Art Institute of Chicago"
    case metropolitanMuseum = "The Metropolitan Museum of Art"
}

actor MuseumAPIClient {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    // MARK: - Art Institute of Chicago

    func fetchAICPaintings(query: String = "impressionism", limit: Int = 20) async throws -> [Painting] {
        var components = URLComponents(string: "https://api.artic.edu/api/v1/artworks/search")!
        components.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "limit", value: String(limit)),
            URLQueryItem(name: "fields", value: "id,title,artist_title,date_start,image_id,thumbnail"),
            URLQueryItem(name: "query[term][is_public_domain]", value: "true"),
            URLQueryItem(name: "query[term][classification_titles]", value: "painting"),
        ]

        var request = URLRequest(url: components.url!)
        request.setValue("EaselWall (nick@ntindle.com)", forHTTPHeaderField: "AIC-User-Agent")
        let (data, _) = try await session.data(for: request)
        let response = try JSONDecoder().decode(AICSearchResponse.self, from: data)

        return response.data.compactMap { item -> Painting? in
            guard let imageID = item.imageID else { return nil }
            let w = item.thumbnail?.width ?? 1000
            let h = item.thumbnail?.height ?? 800
            let orientation = PaintingOrientation(width: CGFloat(w), height: CGFloat(h))

            return Painting(
                id: "aic_\(item.id)",
                title: item.title,
                artist: item.artistTitle ?? "Unknown",
                year: item.dateStart,
                orientation: orientation,
                sourceMuseum: Museum.artInstituteChicago.rawValue,
                sourceURL: "https://www.artic.edu/artworks/\(item.id)",
                localFilename: nil,
                remoteImageURL: "https://www.artic.edu/iiif/2/\(imageID)/full/1686,/0/default.jpg",
                width: w,
                height: h
            )
        }
    }

    // MARK: - Metropolitan Museum of Art

    func fetchMetPaintings(query: String = "van gogh", limit: Int = 20) async throws -> [Painting] {
        // Search for object IDs
        var searchComponents = URLComponents(string: "https://collectionapi.metmuseum.org/public/collection/v1/search")!
        searchComponents.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "hasImages", value: "true"),
            URLQueryItem(name: "isPublicDomain", value: "true"),
            URLQueryItem(name: "medium", value: "Paintings"),
        ]

        let (searchData, _) = try await session.data(from: searchComponents.url!)
        let searchResponse = try JSONDecoder().decode(MetSearchResponse.self, from: searchData)

        guard let objectIDs = searchResponse.objectIDs else { return [] }

        // Fetch details for each object (limited)
        let ids = Array(objectIDs.prefix(limit))
        var paintings: [Painting] = []

        for id in ids {
            guard let painting = try? await fetchMetObject(id: id) else { continue }
            paintings.append(painting)
        }

        return paintings
    }

    private func fetchMetObject(id: Int) async throws -> Painting? {
        let url = URL(string: "https://collectionapi.metmuseum.org/public/collection/v1/objects/\(id)")!
        let (data, _) = try await session.data(from: url)
        let object = try JSONDecoder().decode(MetObject.self, from: data)

        guard let imageURL = object.primaryImage, !imageURL.isEmpty else { return nil }

        // Estimate orientation from image (default to landscape)
        let orientation: PaintingOrientation = .landscape

        return Painting(
            id: "met_\(object.objectID)",
            title: object.title,
            artist: object.artistDisplayName ?? "Unknown",
            year: object.objectBeginDate,
            orientation: orientation,
            sourceMuseum: Museum.metropolitanMuseum.rawValue,
            sourceURL: object.objectURL,
            localFilename: nil,
            remoteImageURL: imageURL,
            width: 1600,
            height: 1200
        )
    }

    // MARK: - Rijksmuseum

    func fetchRijksmuseumPaintings(query: String = "van gogh", apiKey: String, limit: Int = 20) async throws -> [Painting] {
        var components = URLComponents(string: "https://www.rijksmuseum.nl/api/en/collection")!
        components.queryItems = [
            URLQueryItem(name: "key", value: apiKey),
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "type", value: "painting"),
            URLQueryItem(name: "imgonly", value: "true"),
            URLQueryItem(name: "ps", value: String(limit)),
        ]

        let (data, _) = try await session.data(from: components.url!)
        let response = try JSONDecoder().decode(RijksResponse.self, from: data)

        return response.artObjects.compactMap { obj -> Painting? in
            guard let webImage = obj.webImage else { return nil }
            let w = webImage.width ?? 1600
            let h = webImage.height ?? 1200
            let orientation = PaintingOrientation(width: CGFloat(w), height: CGFloat(h))

            return Painting(
                id: "rijks_\(obj.objectNumber)",
                title: obj.title,
                artist: obj.principalOrFirstMaker,
                year: nil,
                orientation: orientation,
                sourceMuseum: Museum.rijksmuseum.rawValue,
                sourceURL: obj.links?.web,
                localFilename: nil,
                remoteImageURL: webImage.url,
                width: w,
                height: h
            )
        }
    }
}

// MARK: - Art Institute of Chicago Models

private struct AICSearchResponse: Decodable {
    let data: [AICArtwork]
}

private struct AICArtwork: Decodable {
    let id: Int
    let title: String
    let artistTitle: String?
    let dateStart: Int?
    let imageID: String?
    let thumbnail: AICThumbnail?

    enum CodingKeys: String, CodingKey {
        case id, title, thumbnail
        case artistTitle = "artist_title"
        case dateStart = "date_start"
        case imageID = "image_id"
    }
}

private struct AICThumbnail: Decodable {
    let width: Int?
    let height: Int?
}

// MARK: - Metropolitan Museum Models

private struct MetSearchResponse: Decodable {
    let total: Int
    let objectIDs: [Int]?
}

private struct MetObject: Decodable {
    let objectID: Int
    let title: String
    let artistDisplayName: String?
    let objectBeginDate: Int?
    let primaryImage: String?
    let objectURL: String?
}

// MARK: - Rijksmuseum Models

private struct RijksResponse: Decodable {
    let artObjects: [RijksArtObject]
}

private struct RijksArtObject: Decodable {
    let objectNumber: String
    let title: String
    let principalOrFirstMaker: String
    let webImage: RijksImage?
    let links: RijksLinks?
}

private struct RijksImage: Decodable {
    let url: String?
    let width: Int?
    let height: Int?
}

private struct RijksLinks: Decodable {
    let web: String?
}
