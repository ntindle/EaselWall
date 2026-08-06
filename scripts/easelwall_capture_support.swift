import AppKit
import CoreGraphics
import Foundation

private struct WindowRecord {
    let id: CGWindowID
    let layer: Int
    let bounds: CGRect
    let name: String

    var area: CGFloat { bounds.width * bounds.height }
}

private struct SavedWallpaper: Codable {
    let displayID: UInt32
    let url: String
    let backupPath: String
    let desktopImageOptions: Data
}

private struct CaptureSupportError: LocalizedError {
    let message: String

    var errorDescription: String? { message }
}

private func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
    exit(1)
}

private func integer(_ value: Any?) -> Int? {
    if let number = value as? NSNumber {
        return number.intValue
    }
    return value as? Int
}

private func windows(ownedBy pid: pid_t) -> [WindowRecord] {
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let rawWindows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
        return []
    }

    return rawWindows.compactMap { info in
        guard integer(info[kCGWindowOwnerPID as String]) == Int(pid),
              let idValue = integer(info[kCGWindowNumber as String]),
              let layer = integer(info[kCGWindowLayer as String]),
              let rawBounds = info[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: rawBounds as CFDictionary)
        else {
            return nil
        }

        return WindowRecord(
            id: CGWindowID(idValue),
            layer: layer,
            bounds: bounds,
            name: info[kCGWindowName as String] as? String ?? ""
        )
    }
}

private func screenDisplayID(_ screen: NSScreen) -> CGDirectDisplayID? {
    (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)
        .map { CGDirectDisplayID($0.uint32Value) }
}

private let encodedColorTypeKey = "EaselWallCaptureType"
private let encodedColorTypeValue = "NSColor"

private func encodeDesktopImageOptions(
    _ options: [NSWorkspace.DesktopImageOptionKey: Any],
    displayID: CGDirectDisplayID
) throws -> Data {
    var propertyList: [String: Any] = [:]

    for (key, value) in options {
        if key == .fillColor {
            guard let color = value as? NSColor,
                  let calibrated = color.usingColorSpace(.genericRGB)
            else {
                throw CaptureSupportError(
                    message: "display \(displayID) has an unsupported desktop fill color"
                )
            }
            propertyList[key.rawValue] = [
                encodedColorTypeKey: encodedColorTypeValue,
                "red": Double(calibrated.redComponent),
                "green": Double(calibrated.greenComponent),
                "blue": Double(calibrated.blueComponent),
                "alpha": Double(calibrated.alphaComponent),
            ]
        } else {
            propertyList[key.rawValue] = value
        }
    }

    guard PropertyListSerialization.propertyList(propertyList, isValidFor: .binary) else {
        throw CaptureSupportError(
            message: "display \(displayID) has desktop image options that cannot be saved"
        )
    }
    return try PropertyListSerialization.data(
        fromPropertyList: propertyList,
        format: .binary,
        options: 0
    )
}

private func decodeDesktopImageOptions(
    _ data: Data,
    displayID: CGDirectDisplayID
) throws -> [NSWorkspace.DesktopImageOptionKey: Any] {
    let decoded = try PropertyListSerialization.propertyList(
        from: data,
        options: [],
        format: nil
    )
    guard let propertyList = decoded as? [String: Any] else {
        throw CaptureSupportError(
            message: "display \(displayID) has an invalid desktop image options property list"
        )
    }

    var options: [NSWorkspace.DesktopImageOptionKey: Any] = [:]
    for (rawKey, value) in propertyList {
        let key = NSWorkspace.DesktopImageOptionKey(rawValue: rawKey)
        if key == .fillColor {
            guard let color = value as? [String: Any],
                  color[encodedColorTypeKey] as? String == encodedColorTypeValue,
                  let red = color["red"] as? NSNumber,
                  let green = color["green"] as? NSNumber,
                  let blue = color["blue"] as? NSNumber,
                  let alpha = color["alpha"] as? NSNumber
            else {
                throw CaptureSupportError(
                    message: "display \(displayID) has an invalid saved desktop fill color"
                )
            }
            options[key] = NSColor(
                calibratedRed: red.doubleValue,
                green: green.doubleValue,
                blue: blue.doubleValue,
                alpha: alpha.doubleValue
            )
        } else {
            options[key] = value
        }
    }
    return options
}

private func normalizedDesktopImageOptions(
    _ options: [NSWorkspace.DesktopImageOptionKey: Any],
    displayID: CGDirectDisplayID
) throws -> [String: Any] {
    let data = try encodeDesktopImageOptions(options, displayID: displayID)
    let decoded = try PropertyListSerialization.propertyList(
        from: data,
        options: [],
        format: nil
    )
    guard let propertyList = decoded as? [String: Any] else {
        throw CaptureSupportError(
            message: "display \(displayID) desktop image options did not normalize to a dictionary"
        )
    }
    return propertyList
}

private func numbersAreSemanticallyEqual(_ lhs: NSNumber, _ rhs: NSNumber) -> Bool {
    if CFGetTypeID(lhs) == CFBooleanGetTypeID()
        || CFGetTypeID(rhs) == CFBooleanGetTypeID()
    {
        return CFGetTypeID(lhs) == CFBooleanGetTypeID()
            && CFGetTypeID(rhs) == CFBooleanGetTypeID()
            && lhs.boolValue == rhs.boolValue
    }
    return lhs.compare(rhs) == .orderedSame
}

private func propertyListValuesAreSemanticallyEqual(_ lhs: Any, _ rhs: Any) -> Bool {
    if let lhsDictionary = lhs as? [String: Any],
       let rhsDictionary = rhs as? [String: Any]
    {
        guard Set(lhsDictionary.keys) == Set(rhsDictionary.keys) else {
            return false
        }

        if lhsDictionary[encodedColorTypeKey] as? String == encodedColorTypeValue,
           rhsDictionary[encodedColorTypeKey] as? String == encodedColorTypeValue
        {
            let components = ["red", "green", "blue", "alpha"]
            for component in components {
                guard let lhsNumber = lhsDictionary[component] as? NSNumber,
                      let rhsNumber = rhsDictionary[component] as? NSNumber,
                      abs(lhsNumber.doubleValue - rhsNumber.doubleValue) <= 0.000_001
                else {
                    return false
                }
            }
            return true
        }

        return lhsDictionary.allSatisfy { key, lhsValue in
            guard let rhsValue = rhsDictionary[key] else {
                return false
            }
            return propertyListValuesAreSemanticallyEqual(lhsValue, rhsValue)
        }
    }

    if let lhsArray = lhs as? [Any], let rhsArray = rhs as? [Any] {
        return lhsArray.count == rhsArray.count
            && zip(lhsArray, rhsArray).allSatisfy(propertyListValuesAreSemanticallyEqual)
    }
    if let lhsNumber = lhs as? NSNumber, let rhsNumber = rhs as? NSNumber {
        return numbersAreSemanticallyEqual(lhsNumber, rhsNumber)
    }
    if let lhsString = lhs as? String, let rhsString = rhs as? String {
        return lhsString == rhsString
    }
    if let lhsData = lhs as? Data, let rhsData = rhs as? Data {
        return lhsData == rhsData
    }
    if let lhsDate = lhs as? Date, let rhsDate = rhs as? Date {
        return lhsDate == rhsDate
    }
    return false
}

private func desktopImageOptionsAreSemanticallyEqual(
    _ expected: [NSWorkspace.DesktopImageOptionKey: Any],
    _ actual: [NSWorkspace.DesktopImageOptionKey: Any],
    displayID: CGDirectDisplayID
) throws -> Bool {
    let normalizedExpected = try normalizedDesktopImageOptions(
        expected,
        displayID: displayID
    )
    let normalizedActual = try normalizedDesktopImageOptions(
        actual,
        displayID: displayID
    )
    return propertyListValuesAreSemanticallyEqual(normalizedExpected, normalizedActual)
}

private let wallpaperRestoreVerificationAttempts = 49
private let wallpaperRestoreVerificationInterval: TimeInterval = 0.25

private func wallpaperRestoreVerificationMismatch(
    screen: NSScreen,
    expectedURL: URL,
    expectedOptions: [NSWorkspace.DesktopImageOptionKey: Any],
    expectedBytes: Data,
    displayID: CGDirectDisplayID
) throws -> String? {
    guard let restoredRawURL = NSWorkspace.shared.desktopImageURL(for: screen),
          restoredRawURL.isFileURL
    else {
        return "desktop image URL is not readable"
    }
    let restoredURL = restoredRawURL.standardizedFileURL
    guard restoredURL == expectedURL else {
        return "desktop image URL is \(restoredURL.absoluteString), expected \(expectedURL.absoluteString)"
    }

    guard let restoredOptions = NSWorkspace.shared.desktopImageOptions(for: screen) else {
        return "desktop image options are not readable"
    }
    guard try desktopImageOptionsAreSemanticallyEqual(
        expectedOptions,
        restoredOptions,
        displayID: displayID
    ) else {
        return "desktop image options do not semantically match the saved options"
    }

    let restoredBytes = try Data(contentsOf: expectedURL, options: .mappedIfSafe)
    guard restoredBytes == expectedBytes else {
        return "desktop image bytes do not match the saved backup"
    }
    return nil
}

private func waitForWallpaperRestoreVerification(
    screen: NSScreen,
    expectedURL: URL,
    expectedOptions: [NSWorkspace.DesktopImageOptionKey: Any],
    expectedBytes: Data,
    displayID: CGDirectDisplayID
) throws {
    var lastMismatch = "desktop state was not readable"
    for attempt in 1 ... wallpaperRestoreVerificationAttempts {
        do {
            if let mismatch = try wallpaperRestoreVerificationMismatch(
                screen: screen,
                expectedURL: expectedURL,
                expectedOptions: expectedOptions,
                expectedBytes: expectedBytes,
                displayID: displayID
            ) {
                lastMismatch = mismatch
            } else {
                return
            }
        } catch {
            lastMismatch = error.localizedDescription
        }

        if attempt < wallpaperRestoreVerificationAttempts {
            Thread.sleep(forTimeInterval: wallpaperRestoreVerificationInterval)
        }
    }

    let timeout = Double(wallpaperRestoreVerificationAttempts - 1)
        * wallpaperRestoreVerificationInterval
    throw CaptureSupportError(
        message: "timed out after \(String(format: "%.0f", timeout)) seconds waiting for desktop restore readback: \(lastMismatch)"
    )
}

private func saveWallpapers(to path: String) throws {
    let outputURL = URL(fileURLWithPath: path).standardizedFileURL
    let backupDirectory = outputURL
        .deletingLastPathComponent()
        .appendingPathComponent("wallpaper-backups", isDirectory: true)
    try FileManager.default.createDirectory(
        at: backupDirectory,
        withIntermediateDirectories: true
    )

    let screens = NSScreen.screens
    guard !screens.isEmpty else {
        throw CaptureSupportError(message: "no screens are available to back up")
    }

    var seenDisplayIDs = Set<CGDirectDisplayID>()
    let saved = try screens.enumerated().map { index, screen -> SavedWallpaper in
        guard let displayID = screenDisplayID(screen) else {
            throw CaptureSupportError(message: "screen \(index + 1) has no display ID")
        }
        guard seenDisplayIDs.insert(displayID).inserted else {
            throw CaptureSupportError(message: "display ID \(displayID) appears more than once")
        }
        guard let rawURL = NSWorkspace.shared.desktopImageURL(for: screen) else {
            throw CaptureSupportError(message: "display \(displayID) has no desktop image URL")
        }
        guard rawURL.isFileURL else {
            throw CaptureSupportError(
                message: "display \(displayID) desktop image is not a file URL: \(rawURL)"
            )
        }
        let url = rawURL.standardizedFileURL
        let originalBytes: Data
        do {
            originalBytes = try Data(contentsOf: url, options: .mappedIfSafe)
        } catch {
            throw CaptureSupportError(
                message: "display \(displayID) desktop image bytes are unreadable: \(error.localizedDescription)"
            )
        }
        guard !originalBytes.isEmpty else {
            throw CaptureSupportError(message: "display \(displayID) desktop image file is empty")
        }
        guard let options = NSWorkspace.shared.desktopImageOptions(for: screen) else {
            throw CaptureSupportError(
                message: "display \(displayID) has no readable desktop image options"
            )
        }
        let encodedOptions = try encodeDesktopImageOptions(options, displayID: displayID)

        let extensionSuffix = url.pathExtension.isEmpty ? "" : ".\(url.pathExtension)"
        let backupURL = backupDirectory
            .appendingPathComponent("display-\(displayID)\(extensionSuffix)")
        if FileManager.default.fileExists(atPath: backupURL.path) {
            try FileManager.default.removeItem(at: backupURL)
        }
        try originalBytes.write(to: backupURL, options: .atomic)
        return SavedWallpaper(
            displayID: displayID,
            url: url.absoluteString,
            backupPath: backupURL.path,
            desktopImageOptions: encodedOptions
        )
    }

    guard saved.count == screens.count else {
        throw CaptureSupportError(message: "wallpaper backup did not include every screen")
    }

    let data = try JSONEncoder().encode(saved)
    try data.write(to: outputURL, options: .atomic)
}

private func restoreWallpapers(from path: String) throws {
    let data = try Data(contentsOf: URL(fileURLWithPath: path))
    let saved = try JSONDecoder().decode([SavedWallpaper].self, from: data)
    guard !saved.isEmpty else {
        throw CaptureSupportError(message: "wallpaper backup manifest is empty")
    }

    var savedDisplayIDs = Set<CGDirectDisplayID>()
    for wallpaper in saved {
        guard savedDisplayIDs.insert(wallpaper.displayID).inserted else {
            throw CaptureSupportError(
                message: "wallpaper backup contains duplicate display ID \(wallpaper.displayID)"
            )
        }
    }

    var restoreFailures: [String] = []
    var screensByDisplay: [CGDirectDisplayID: NSScreen] = [:]
    for (index, screen) in NSScreen.screens.enumerated() {
        guard let displayID = screenDisplayID(screen) else {
            restoreFailures.append("current screen \(index + 1) has no display ID")
            continue
        }
        guard screensByDisplay[displayID] == nil else {
            restoreFailures.append("current display ID \(displayID) appears more than once")
            continue
        }
        screensByDisplay[displayID] = screen
        if !savedDisplayIDs.contains(displayID) {
            restoreFailures.append("current display \(displayID) was not present in the backup")
        }
    }

    for wallpaper in saved.sorted(by: { $0.displayID < $1.displayID }) {
        let displayID = wallpaper.displayID
        guard let screen = screensByDisplay[displayID] else {
            restoreFailures.append("saved display \(displayID) is no longer connected")
            continue
        }

        do {
            guard let rawURL = URL(string: wallpaper.url), rawURL.isFileURL else {
                throw CaptureSupportError(
                    message: "saved desktop image URL is invalid: \(wallpaper.url)"
                )
            }
            let url = rawURL.standardizedFileURL
            let backupURL = URL(fileURLWithPath: wallpaper.backupPath).standardizedFileURL
            let backupBytes = try Data(contentsOf: backupURL, options: .mappedIfSafe)
            guard !backupBytes.isEmpty else {
                throw CaptureSupportError(message: "saved wallpaper byte backup is empty")
            }
            let options = try decodeDesktopImageOptions(
                wallpaper.desktopImageOptions,
                displayID: displayID
            )

            if !FileManager.default.fileExists(atPath: url.path) {
                try FileManager.default.createDirectory(
                    at: url.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                try backupBytes.write(to: url, options: .atomic)
            } else {
                let currentBytes = try Data(contentsOf: url, options: .mappedIfSafe)
                if currentBytes != backupBytes {
                    try backupBytes.write(to: url, options: .atomic)
                }
            }
            try NSWorkspace.shared.setDesktopImageURL(url, for: screen, options: options)
            try waitForWallpaperRestoreVerification(
                screen: screen,
                expectedURL: url,
                expectedOptions: options,
                expectedBytes: backupBytes,
                displayID: displayID
            )
        } catch {
            restoreFailures.append("display \(displayID): \(error.localizedDescription)")
        }
    }

    if !restoreFailures.isEmpty {
        let details = restoreFailures.map { "- \($0)" }.joined(separator: "\n")
        throw CaptureSupportError(message: "one or more wallpapers could not be restored:\n\(details)")
    }
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let command = arguments.first else {
    fail("usage: capture-support <settings-window|window-ids|new-window|save-wallpapers|restore-wallpapers> ...")
}

switch command {
case "settings-window":
    guard arguments.count == 2, let pid = pid_t(arguments[1]) else {
        fail("usage: capture-support settings-window PID")
    }
    guard let match = windows(ownedBy: pid)
        .filter({ $0.layer == 0 && $0.bounds.width >= 400 && $0.bounds.height >= 300 })
        .max(by: { $0.area < $1.area })
    else {
        exit(2)
    }
    print(match.id)

case "window-ids":
    guard arguments.count == 2, let pid = pid_t(arguments[1]) else {
        fail("usage: capture-support window-ids PID")
    }
    for window in windows(ownedBy: pid).sorted(by: { $0.id < $1.id }) {
        print(window.id)
    }

case "new-window":
    guard arguments.count >= 2, let pid = pid_t(arguments[1]) else {
        fail("usage: capture-support new-window PID [EXCLUDED_ID ...]")
    }
    let excluded = Set(arguments.dropFirst(2).compactMap(CGWindowID.init))
    guard let match = windows(ownedBy: pid)
        .filter({
            !excluded.contains($0.id)
                && $0.bounds.width >= 180
                && $0.bounds.height >= 100
        })
        .max(by: { $0.area < $1.area })
    else {
        exit(2)
    }
    print(match.id)

case "save-wallpapers":
    guard arguments.count == 2 else {
        fail("usage: capture-support save-wallpapers OUTPUT_JSON")
    }
    do {
        try saveWallpapers(to: arguments[1])
    } catch {
        fail("could not save wallpapers: \(error.localizedDescription)")
    }

case "restore-wallpapers":
    guard arguments.count == 2 else {
        fail("usage: capture-support restore-wallpapers INPUT_JSON")
    }
    do {
        try restoreWallpapers(from: arguments[1])
    } catch {
        fail("could not restore wallpapers: \(error.localizedDescription)")
    }

default:
    fail("unknown command: \(command)")
}
