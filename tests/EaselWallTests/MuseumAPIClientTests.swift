import Foundation
import XCTest

@MainActor
final class MuseumAPIClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.handler = nil
        super.tearDown()
    }

    func testRijksmuseumUsesKeylessSearchAndFramedEDMDetails() async throws {
        let search = Self.searchResponse(ids: ["200109794"])
        let detail = Self.detailResponse(
            rights: "http://creativecommons.org/publicdomain/mark/1.0/",
            imageURL: "https://iiif.micr.io/vjYfT/full/max/0/default.jpg"
        )
        let info = Data(#"{"width": 3_000, "height": 4_000}"#.replacingOccurrences(of: "_", with: "").utf8)
        let requests = RequestRecorder()

        MockURLProtocol.handler = { request in
            guard let url = request.url else { throw TestFailure.missingURL }
            requests.append(url)
            switch (url.host, url.path) {
            case ("data.rijksmuseum.nl", "/search/collection"):
                return Self.response(url: url, data: search)
            case ("data.rijksmuseum.nl", "/200109794"):
                return Self.response(url: url, data: detail)
            case ("iiif.micr.io", "/vjYfT/info.json"):
                return Self.response(url: url, data: info)
            default:
                throw TestFailure.unexpectedURL(url)
            }
        }

        let client = MuseumAPIClient(session: Self.mockSession())
        let paintings = try await client.fetchRijksmuseumPaintings(limit: 10)

        let painting = try XCTUnwrap(paintings.first)
        XCTAssertEqual(paintings.count, 1)
        XCTAssertEqual(painting.id, "rijks_200109794")
        XCTAssertEqual(painting.title, "Self-portrait")
        XCTAssertEqual(painting.artist, "Vincent van Gogh")
        XCTAssertEqual(painting.year, 1887)
        XCTAssertEqual(painting.orientation, .portrait)
        XCTAssertEqual(painting.sourceURL, "https://id.rijksmuseum.nl/200109794")
        XCTAssertEqual(
            painting.remoteImageURL,
            "https://iiif.micr.io/vjYfT/full/2000,/0/default.jpg"
        )
        XCTAssertEqual(painting.width, 3_000)
        XCTAssertEqual(painting.height, 4_000)

        let searchURL = try XCTUnwrap(requests.urls.first { $0.path == "/search/collection" })
        let queryItems = URLComponents(url: searchURL, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let query = Dictionary(uniqueKeysWithValues: queryItems.map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(query["creator"], "van gogh")
        XCTAssertEqual(query["type"], "painting")
        XCTAssertEqual(query["imageAvailable"], "true")
        XCTAssertNil(query["key"])

        let detailURL = try XCTUnwrap(requests.urls.first { $0.path == "/200109794" })
        XCTAssertEqual(
            URLComponents(url: detailURL, resolvingAgainstBaseURL: false)?.queryItems?.first?.value,
            "edm-framed"
        )
    }

    func testCC0RightsAreAccepted() async throws {
        let search = Self.searchResponse(ids: ["200109305"])
        let detail = Self.detailResponse(
            rights: "https://creativecommons.org/publicdomain/zero/1.0/",
            imageURL: "https://iiif.micr.io/egrgo/full/max/0/default.jpg"
        )
        let info = Data(#"{"width": 1600, "height": 1200}"#.utf8)

        MockURLProtocol.handler = { request in
            guard let url = request.url else { throw TestFailure.missingURL }
            switch url.path {
            case "/search/collection": return Self.response(url: url, data: search)
            case "/200109305": return Self.response(url: url, data: detail)
            case "/egrgo/info.json": return Self.response(url: url, data: info)
            default: throw TestFailure.unexpectedURL(url)
            }
        }

        let client = MuseumAPIClient(session: Self.mockSession())
        let paintings = try await client.fetchRijksmuseumPaintings()
        XCTAssertEqual(paintings.map(\.id), ["rijks_200109305"])
    }

    func testNonPublicDomainRightsAreRejectedWithoutFetchingImageMetadata() async throws {
        let search = Self.searchResponse(ids: ["200109794"])
        let detail = Self.detailResponse(
            rights: "https://creativecommons.org/licenses/by/4.0/",
            imageURL: "https://iiif.micr.io/vjYfT/full/max/0/default.jpg"
        )

        MockURLProtocol.handler = { request in
            guard let url = request.url else { throw TestFailure.missingURL }
            switch url.path {
            case "/search/collection": return Self.response(url: url, data: search)
            case "/200109794": return Self.response(url: url, data: detail)
            default: throw TestFailure.unexpectedURL(url)
            }
        }

        let client = MuseumAPIClient(session: Self.mockSession())
        let paintings = try await client.fetchRijksmuseumPaintings()
        XCTAssertTrue(paintings.isEmpty)
    }

    func testUntrustedIIIFHostIsRejected() async throws {
        let search = Self.searchResponse(ids: ["200109794"])
        let detail = Self.detailResponse(
            rights: "http://creativecommons.org/publicdomain/mark/1.0/",
            imageURL: "https://example.com/vjYfT/full/max/0/default.jpg"
        )

        MockURLProtocol.handler = { request in
            guard let url = request.url else { throw TestFailure.missingURL }
            switch url.path {
            case "/search/collection": return Self.response(url: url, data: search)
            case "/200109794": return Self.response(url: url, data: detail)
            default: throw TestFailure.unexpectedURL(url)
            }
        }

        let client = MuseumAPIClient(session: Self.mockSession())
        let paintings = try await client.fetchRijksmuseumPaintings()
        XCTAssertTrue(paintings.isEmpty)
    }

    func testAllDetailRequestsFailAsAnOutageInsteadOfReturningZero() async throws {
        let search = Self.searchResponse(ids: ["200109305", "200109794"])

        MockURLProtocol.handler = { request in
            guard let url = request.url else { throw TestFailure.missingURL }
            if url.path == "/search/collection" {
                return Self.response(url: url, data: search)
            }
            return Self.response(url: url, status: 503, data: Data())
        }

        let client = MuseumAPIClient(session: Self.mockSession())
        do {
            _ = try await client.fetchRijksmuseumPaintings()
            XCTFail("Expected the detail outage to be surfaced")
        } catch let error as MuseumAPIError {
            XCTAssertEqual(error, .rijksmuseumDetailsUnavailable)
        }
    }

    func testEmptySearchIsARealEmptyResult() async throws {
        let search = Self.searchResponse(ids: [])
        MockURLProtocol.handler = { request in
            guard let url = request.url else { throw TestFailure.missingURL }
            return Self.response(url: url, data: search)
        }

        let client = MuseumAPIClient(session: Self.mockSession())
        let paintings = try await client.fetchRijksmuseumPaintings()
        XCTAssertTrue(paintings.isEmpty)
    }

    func testAppSettingsDeletesLegacyRijksmuseumAPIKey() throws {
        let suiteName = "MuseumAPIClientTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        defaults.set("obsolete-secret", forKey: "rijksmuseumAPIKey")

        _ = AppSettings(defaults: defaults)

        XCTAssertNil(defaults.object(forKey: "rijksmuseumAPIKey"))
    }

    private static func mockSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    private static func searchResponse(ids: [String]) -> Data {
        let items = ids.map { #"{"id":"https://id.rijksmuseum.nl/\#($0)","type":"HumanMadeObject"}"# }
        return Data(#"{"orderedItems":[\#(items.joined(separator: ","))]}"#.utf8)
    }

    private static func detailResponse(rights: String, imageURL: String) -> Data {
        Data(
            #"""
            {
              "aggregatedCHO": {
                "creator": [{
                  "http://www.w3.org/2004/02/skos/core#prefLabel": [
                    {"@language":"nl","@value":"Vincent van Gogh"},
                    {"@language":"en","@value":"Vincent van Gogh"}
                  ]
                }],
                "title": {"en":["Zelfportret","Self-portrait"]},
                "created": [{"@language":"en","@value":"1887"}]
              },
              "isShownBy": {"id":"\#(imageURL)"},
              "edmRights": "\#(rights)"
            }
            """#.utf8
        )
    }

    private static func response(
        url: URL,
        status: Int = 200,
        data: Data
    ) -> (HTTPURLResponse, Data) {
        let response = HTTPURLResponse(
            url: url,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        return (response, data)
    }
}

private enum TestFailure: Error {
    case missingURL
    case unexpectedURL(URL)
}

private final class RequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [URL] = []

    func append(_ url: URL) {
        lock.lock()
        storage.append(url)
        lock.unlock()
    }

    var urls: [URL] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

private final class MockURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: TestFailure.unexpectedURL(request.url!))
            return
        }

        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
