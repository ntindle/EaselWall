#if !APPSTORE
import Foundation

/// Manages a launchd agent plist that wakes the app at the scheduled rotation time.
/// This ensures rotation happens even if the app was killed, the Mac was asleep, or after reboot.
struct LaunchdScheduler {
    static let agentLabel = "com.ntindle.EaselWall.rotation"

    static var agentPlistURL: URL {
        let launchAgents = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents", isDirectory: true)
        return launchAgents.appendingPathComponent("\(agentLabel).plist")
    }

    static var appPath: String {
        Bundle.main.bundlePath
    }

    /// Install or update the launchd agent to wake the app at the given hour/minute daily.
    static func install(hour: Int, minute: Int) {
        let launchAgentsDir = agentPlistURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: launchAgentsDir, withIntermediateDirectories: true)

        let plist: [String: Any] = [
            "Label": agentLabel,
            "ProgramArguments": ["open", "-a", appPath],
            "StartCalendarInterval": [
                "Hour": hour,
                "Minute": minute
            ],
            "StandardOutPath": "/tmp/EaselWall-launchd.log",
            "StandardErrorPath": "/tmp/EaselWall-launchd.log",
        ]

        let data = try? PropertyListSerialization.data(
            fromPropertyList: plist,
            format: .xml,
            options: 0
        )

        guard let data else {
            NSLog("[EaselWall] Failed to serialize launchd plist")
            return
        }

        // Unload existing agent first (ignore errors if not loaded)
        unload()

        do {
            try data.write(to: agentPlistURL)
            NSLog("[EaselWall] Wrote launchd plist to \(agentPlistURL.path)")
        } catch {
            NSLog("[EaselWall] Failed to write launchd plist: \(error)")
            return
        }

        load()
    }

    /// Remove the launchd agent.
    static func uninstall() {
        unload()

        if FileManager.default.fileExists(atPath: agentPlistURL.path) {
            try? FileManager.default.removeItem(at: agentPlistURL)
            NSLog("[EaselWall] Removed launchd plist")
        }
    }

    /// Check if the agent plist exists.
    static var isInstalled: Bool {
        FileManager.default.fileExists(atPath: agentPlistURL.path)
    }

    // MARK: - Private

    private static func load() {
        let result = runLaunchctl(["load", agentPlistURL.path])
        NSLog("[EaselWall] launchctl load: \(result)")
    }

    private static func unload() {
        if FileManager.default.fileExists(atPath: agentPlistURL.path) {
            let result = runLaunchctl(["unload", agentPlistURL.path])
            NSLog("[EaselWall] launchctl unload: \(result)")
        }
    }

    private static func runLaunchctl(_ arguments: [String]) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe

        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        } catch {
            return "error: \(error)"
        }
    }
}

#endif
