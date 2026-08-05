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

        let request = MuseumURLRequest.make(for: components.url!)
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

    func fetchRijksmuseumPaintings(query: String = "van gogh", limit: Int = 20) async throws -> [Painting] {
        guard limit > 0 else { return [] }

        var components = URLComponents(string: "https://data.rijksmuseum.nl/search/collection")!
        components.queryItems = [
            URLQueryItem(name: "creator", value: query),
            URLQueryItem(name: "type", value: "painting"),
            URLQueryItem(name: "imageAvailable", value: "true"),
        ]

        let searchData = try await fetchData(
            from: components.url!,
            expectedHost: "data.rijksmuseum.nl"
        )
        let response = try JSONDecoder().decode(RijksSearchResponse.self, from: searchData)
        let identifiers = response.orderedItems.compactMap { Self.rijksIdentifier(from: $0.id) }

        guard response.orderedItems.isEmpty || !identifiers.isEmpty else {
            throw MuseumAPIError.invalidRijksmuseumResponse
        }
        guard !identifiers.isEmpty else { return [] }

        var paintings: [Painting] = []
        var failedDetailCount = 0

        // The search endpoint returns identifiers only. Resolve each candidate through
        // the keyless data API's framed EDM representation, then resolve IIIF dimensions.
        for identifier in identifiers.prefix(min(limit, 100)) {
            do {
                if let painting = try await fetchRijksmuseumObject(identifier: identifier) {
                    paintings.append(painting)
                }
            } catch {
                failedDetailCount += 1
                NSLog("[EaselWall] Rijksmuseum detail request failed for \(identifier): \(error)")
            }
        }

        // Do not report a misleading successful zero when every candidate failed to
        // resolve. A real empty search is handled above, and rights-filtered records
        // complete successfully without incrementing this count.
        if paintings.isEmpty, failedDetailCount > 0 {
            throw MuseumAPIError.rijksmuseumDetailsUnavailable
        }

        return paintings
    }

    private func fetchRijksmuseumObject(identifier: String) async throws -> Painting? {
        var components = URLComponents()
        components.scheme = "https"
        components.host = "data.rijksmuseum.nl"
        components.path = "/\(identifier)"
        components.queryItems = [URLQueryItem(name: "_profile", value: "edm-framed")]

        guard let detailURL = components.url else {
            throw MuseumAPIError.invalidRijksmuseumResponse
        }

        let detailData = try await fetchData(
            from: detailURL,
            expectedHost: "data.rijksmuseum.nl"
        )
        let record = try JSONDecoder().decode(RijksEDMRecord.self, from: detailData)

        guard Self.isAcceptedPublicDomainRights(record.edmRights) else { return nil }
        guard let imageIdentifier = Self.rijksIIIFIdentifier(from: record.isShownBy?.id) else {
            return nil
        }
        guard let title = record.aggregatedCHO.title?.preferredValue else { return nil }

        let infoURL = URL(string: "https://iiif.micr.io/\(imageIdentifier)/info.json")!
        let infoData = try await fetchData(from: infoURL, expectedHost: "iiif.micr.io")
        let info = try JSONDecoder().decode(RijksIIIFInfo.self, from: infoData)
        guard info.width > 0, info.height > 0 else {
            throw MuseumAPIError.invalidRijksmuseumResponse
        }

        let imageWidth = min(info.width, 2_000)
        let remoteImageURL = "https://iiif.micr.io/\(imageIdentifier)/full/\(imageWidth),/0/default.jpg"
        let artist = record.aggregatedCHO.creator?.first?.preferredLabel ?? "Unknown"
        let canonicalURL = "https://id.rijksmuseum.nl/\(identifier)"

        return Painting(
            id: "rijks_\(identifier)",
            title: title,
            artist: artist,
            year: record.aggregatedCHO.created?.firstYear,
            orientation: PaintingOrientation(
                width: CGFloat(info.width),
                height: CGFloat(info.height)
            ),
            sourceMuseum: Museum.rijksmuseum.rawValue,
            sourceURL: canonicalURL,
            localFilename: nil,
            remoteImageURL: remoteImageURL,
            width: info.width,
            height: info.height
        )
    }

    private func fetchData(from url: URL, expectedHost: String) async throws -> Data {
        let (data, response) = try await session.data(from: url)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw MuseumAPIError.invalidHTTPResponse
        }
        guard let responseURL = httpResponse.url,
              responseURL.scheme?.lowercased() == "https",
              responseURL.host?.lowercased() == expectedHost.lowercased(),
              responseURL.port == nil,
              responseURL.user == nil,
              responseURL.password == nil else {
            throw MuseumAPIError.invalidHTTPResponse
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            throw MuseumAPIError.httpStatus(httpResponse.statusCode)
        }
        return data
    }

    private static func rijksIdentifier(from value: String) -> String? {
        guard let url = URL(string: value),
              url.scheme?.lowercased() == "https",
              url.host?.lowercased() == "id.rijksmuseum.nl",
              url.port == nil,
              url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil else {
            return nil
        }

        let pathParts = url.path.split(separator: "/")
        guard pathParts.count == 1 else {
            return nil
        }
        let identifier = String(pathParts[0])
        guard !identifier.isEmpty,
              identifier.utf8.allSatisfy({ (48...57).contains($0) }) else {
            return nil
        }
        return identifier
    }

    private static func rijksIIIFIdentifier(from value: String?) -> String? {
        guard let value,
              let url = URL(string: value),
              url.scheme?.lowercased() == "https",
              url.host?.lowercased() == "iiif.micr.io",
              url.port == nil,
              url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil else {
            return nil
        }

        let pathParts = url.path.split(separator: "/")
        guard pathParts.count == 5,
              pathParts[1] == "full",
              pathParts[3] == "0",
              pathParts[4] == "default.jpg" else {
            return nil
        }

        let identifier = String(pathParts[0])
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        guard !identifier.isEmpty,
              identifier.unicodeScalars.allSatisfy(allowed.contains) else {
            return nil
        }
        return identifier
    }

    private static func isAcceptedPublicDomainRights(_ value: String?) -> Bool {
        guard let value,
              let url = URL(string: value),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host?.lowercased() == "creativecommons.org",
              url.port == nil,
              url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil else {
            return false
        }

        let normalizedPath = url.path.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return normalizedPath == "publicdomain/mark/1.0"
            || normalizedPath == "publicdomain/zero/1.0"
    }
}

enum MuseumAPIError: Error, Equatable, LocalizedError {
    case invalidHTTPResponse
    case httpStatus(Int)
    case invalidRijksmuseumResponse
    case rijksmuseumDetailsUnavailable

    var errorDescription: String? {
        switch self {
        case .invalidHTTPResponse, .invalidRijksmuseumResponse:
            return "The museum returned an invalid response."
        case let .httpStatus(statusCode):
            return "The museum request failed (HTTP \(statusCode))."
        case .rijksmuseumDetailsUnavailable:
            return "Rijksmuseum is temporarily unavailable. Try again later."
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

private struct RijksSearchResponse: Decodable {
    let orderedItems: [RijksSearchItem]
}

private struct RijksSearchItem: Decodable {
    let id: String
}

private struct RijksEDMRecord: Decodable {
    let aggregatedCHO: RijksProvidedCHO
    let isShownBy: RijksWebResource?
    let edmRights: String?
}

private struct RijksProvidedCHO: Decodable {
    let creator: [RijksAgent]?
    let title: RijksLocalizedTitle?
    let created: RijksLanguageValues?
}

private struct RijksAgent: Decodable {
    let labels: RijksLanguageValues?

    var preferredLabel: String? {
        labels?.preferredValue
    }

    enum CodingKeys: String, CodingKey {
        case labels = "http://www.w3.org/2004/02/skos/core#prefLabel"
    }
}

private struct RijksWebResource: Decodable {
    let id: String
}

private struct RijksIIIFInfo: Decodable {
    let width: Int
    let height: Int
}

private struct RijksLanguageValue: Decodable {
    let language: String?
    let value: String

    enum CodingKeys: String, CodingKey {
        case language = "@language"
        case value = "@value"
    }
}

private struct RijksLanguageValues: Decodable {
    let values: [RijksLanguageValue]

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let many = try? container.decode([RijksLanguageValue].self) {
            values = many
        } else if let one = try? container.decode(RijksLanguageValue.self) {
            values = [one]
        } else if let scalar = try? container.decode(String.self) {
            values = [RijksLanguageValue(language: nil, value: scalar)]
        } else {
            throw DecodingError.typeMismatch(
                RijksLanguageValues.self,
                DecodingError.Context(
                    codingPath: decoder.codingPath,
                    debugDescription: "Expected a Rijksmuseum localized string or list"
                )
            )
        }
    }

    var preferredValue: String? {
        let preferred = values.first { $0.language == "en" }
            ?? values.first { $0.language == "nl" }
            ?? values.first
        return preferred?.value.nilIfBlank
    }

    var firstYear: Int? {
        for value in values {
            for component in value.value.split(whereSeparator: { !$0.isNumber }) where component.count == 4 {
                if let year = Int(component), (1_000...2_100).contains(year) {
                    return year
                }
            }
        }
        return nil
    }
}

private struct RijksLocalizedTitle: Decodable {
    private let valuesByLanguage: [String: [String]]

    init(from decoder: Decoder) throws {
        if let scalar = try? decoder.singleValueContainer().decode(String.self) {
            valuesByLanguage = ["": [scalar]]
            return
        }

        let container = try decoder.container(keyedBy: RijksLanguageKey.self)
        var decoded: [String: [String]] = [:]
        for key in container.allKeys {
            if let many = try? container.decode([String].self, forKey: key) {
                decoded[key.stringValue] = many
            } else if let one = try? container.decode(String.self, forKey: key) {
                decoded[key.stringValue] = [one]
            }
        }
        valuesByLanguage = decoded
    }

    var preferredValue: String? {
        // The framed EDM response places the English display title last when it
        // provides both Dutch and English variants inside the `en` field.
        for language in ["en", "nl", ""] {
            if let value = valuesByLanguage[language]?.reversed().compactMap(\.nilIfBlank).first {
                return value
            }
        }
        return valuesByLanguage
            .sorted { $0.key < $1.key }
            .lazy
            .flatMap { $0.value }
            .compactMap(\.nilIfBlank)
            .first
    }
}

private struct RijksLanguageKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil

    init?(stringValue: String) {
        self.stringValue = stringValue
    }

    init?(intValue: Int) {
        return nil
    }
}

private extension String {
    var nilIfBlank: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
