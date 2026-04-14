import AppIntents
import Foundation

struct NextPaintingIntent: AppIntent {
    static let title: LocalizedStringResource = "Next Painting"
    static let description: IntentDescription = "Switch to the next wallpaper painting"

    func perform() async throws -> some IntentResult {
        DistributedNotificationCenter.default().postNotificationName(
            .init("com.ntindle.EaselWall.nextPainting"),
            object: nil
        )
        return .result()
    }
}

struct OpenSettingsIntent: AppIntent {
    static let title: LocalizedStringResource = "EaselWall Settings"
    static let description: IntentDescription = "Open EaselWall settings"
    static let openAppWhenRun: Bool = true

    func perform() async throws -> some IntentResult {
        return .result()
    }
}
