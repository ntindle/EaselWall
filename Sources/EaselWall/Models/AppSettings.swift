import Foundation
import SwiftUI

@MainActor
final class AppSettings: ObservableObject {
    static let shared = AppSettings()

    @AppStorage("matEnabled") var matEnabled: Bool = true
    @AppStorage("matColorHex") var matColorHex: String = "F5F0EB"
    @AppStorage("matSpacing") var matSpacing: MatSpacing = .gallery
    @AppStorage("uniquePerDisplay") var uniquePerDisplay: Bool = false
    @AppStorage("rotationHour") var rotationHour: Int = 0
    @AppStorage("rotationMinute") var rotationMinute: Int = 0
    @AppStorage("launchAtLogin") var launchAtLogin: Bool = false
    @AppStorage("rijksmuseumAPIKey") var rijksmuseumAPIKey: String = ""

    var matColor: Color {
        Color(hex: matColorHex) ?? Color(red: 0.96, green: 0.94, blue: 0.92)
    }

    var matNSColor: NSColor {
        NSColor(hex: matColorHex) ?? NSColor(red: 0.96, green: 0.94, blue: 0.92, alpha: 1.0)
    }

    var hasRijksmuseumKey: Bool {
        !rijksmuseumAPIKey.trimmingCharacters(in: .whitespaces).isEmpty
    }
}

enum MatSpacing: String, CaseIterable, Codable {
    case compact
    case gallery
    case generous

    var relativePadding: CGFloat {
        switch self {
        case .compact: return 0.04
        case .gallery: return 0.08
        case .generous: return 0.12
        }
    }

    var displayName: String {
        String(localized: String.LocalizationValue(rawValue.capitalized))
    }
}

extension Color {
    init?(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard hex.count == 6 else { return nil }
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255.0
        let g = Double((int >> 8) & 0xFF) / 255.0
        let b = Double(int & 0xFF) / 255.0
        self.init(red: r, green: g, blue: b)
    }
}

extension NSColor {
    convenience init?(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard hex.count == 6 else { return nil }
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r = CGFloat((int >> 16) & 0xFF) / 255.0
        let g = CGFloat((int >> 8) & 0xFF) / 255.0
        let b = CGFloat(int & 0xFF) / 255.0
        self.init(red: r, green: g, blue: b, alpha: 1.0)
    }
}
