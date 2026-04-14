import AppKit
import Combine

struct ScreenInfo: Identifiable, Equatable {
    let id: CGDirectDisplayID
    let frame: NSRect
    let visibleFrame: NSRect
    let orientation: PaintingOrientation
    let scaleFactor: CGFloat

    var pixelWidth: Int {
        Int(frame.width * scaleFactor)
    }

    var pixelHeight: Int {
        Int(frame.height * scaleFactor)
    }
}

@MainActor
final class ScreenManager: ObservableObject {
    @Published private(set) var screens: [ScreenInfo] = []

    private nonisolated(unsafe) var observer: NSObjectProtocol?

    init() {
        refreshScreens()
        observer = NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.refreshScreens()
            }
        }
    }

    deinit {
        if let observer {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    func refreshScreens() {
        screens = NSScreen.screens.compactMap { screen in
            guard let displayID = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID else {
                return nil
            }
            let frame = screen.frame
            let orientation = PaintingOrientation(width: frame.width, height: frame.height)
            return ScreenInfo(
                id: displayID,
                frame: frame,
                visibleFrame: screen.visibleFrame,
                orientation: orientation,
                scaleFactor: screen.backingScaleFactor
            )
        }
    }

    func nsScreen(for info: ScreenInfo) -> NSScreen? {
        NSScreen.screens.first { screen in
            let id = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID
            return id == info.id
        }
    }
}
