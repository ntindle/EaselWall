import Foundation

enum MuseumURLRequest {
    static let aicUserAgent = "EaselWall (nick@ntindle.com)"

    static func make(for url: URL) -> URLRequest {
        var request = URLRequest(url: url)

        if isArtInstituteRequest(url) {
            request.setValue(aicUserAgent, forHTTPHeaderField: "AIC-User-Agent")
        }

        return request
    }

    private static func isArtInstituteRequest(_ url: URL) -> Bool {
        switch url.host?.lowercased() {
        case "api.artic.edu":
            return true
        case "www.artic.edu":
            return url.path.hasPrefix("/iiif/")
        default:
            return false
        }
    }
}
