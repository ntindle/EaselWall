#if SCREENSHOT_CAPTURE
import AppKit
import Foundation

enum ScreenshotCaptureMode {
    case settings(SettingsTab)
    case menu
}

struct ScreenshotCaptureOptions {
    enum CaptureError: LocalizedError {
        case conflictingModes
        case invalidTab(String)
        case missingReadyFile
        case nonAbsoluteReadyFile(String)

        var errorDescription: String? {
            switch self {
            case .conflictingModes:
                return "choose exactly one of --screenshot-tab or --screenshot-menu"
            case .invalidTab(let tab):
                return "unknown Settings tab: \(tab)"
            case .missingReadyFile:
                return "--screenshot-ready=/absolute/path.json is required"
            case .nonAbsoluteReadyFile(let path):
                return "screenshot ready path must be absolute: \(path)"
            }
        }
    }

    let mode: ScreenshotCaptureMode
    let readyFile: URL
    let renderWallpaper: Bool

    init?(arguments: [String]) throws {
        let tabPrefix = "--screenshot-tab="
        let readyPrefix = "--screenshot-ready="
        let tabArgument = arguments.first(where: { $0.hasPrefix(tabPrefix) })
        let menuRequested = arguments.contains("--screenshot-menu")

        guard tabArgument != nil || menuRequested else {
            return nil
        }
        guard (tabArgument != nil) != menuRequested else {
            throw CaptureError.conflictingModes
        }

        if let tabArgument {
            let tabName = String(tabArgument.dropFirst(tabPrefix.count)).lowercased()
            guard let tab = SettingsTab.allCases.first(where: {
                $0.rawValue.lowercased() == tabName
            }) else {
                throw CaptureError.invalidTab(tabName)
            }
            mode = .settings(tab)
        } else {
            mode = .menu
        }

        guard let readyArgument = arguments.first(where: { $0.hasPrefix(readyPrefix) }) else {
            throw CaptureError.missingReadyFile
        }
        let readyPath = String(readyArgument.dropFirst(readyPrefix.count))
        guard readyPath.hasPrefix("/") else {
            throw CaptureError.nonAbsoluteReadyFile(readyPath)
        }

        readyFile = URL(fileURLWithPath: readyPath).standardizedFileURL
        renderWallpaper = arguments.contains("--screenshot-render-wallpaper")
    }

    func writeReady(window: NSWindow?) {
        let contentSize = window?.contentView?.bounds.size ?? .zero
        let windowNumber = window?.windowNumber ?? 0
        let payload = ReadyPayload(
            pid: ProcessInfo.processInfo.processIdentifier,
            kind: window == nil ? "menu" : "settings",
            windowID: windowNumber > 0 ? UInt32(windowNumber) : nil,
            widthPoints: contentSize.width,
            heightPoints: contentSize.height,
            scale: window?.backingScaleFactor ?? NSScreen.main?.backingScaleFactor ?? 1
        )

        do {
            try FileManager.default.createDirectory(
                at: readyFile.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let data = try JSONEncoder().encode(payload)
            try data.write(to: readyFile, options: .atomic)
        } catch {
            NSLog("[EaselWall] Could not write screenshot readiness file: \(error)")
            NSApp.terminate(nil)
        }
    }
}

private struct ReadyPayload: Codable {
    let pid: Int32
    let kind: String
    let windowID: UInt32?
    let widthPoints: CGFloat
    let heightPoints: CGFloat
    let scale: CGFloat
}
#endif
