import AppKit
import CoreGraphics

struct MatRenderer {
    struct Configuration: Sendable {
        let matColor: CGColor
        let spacing: MatSpacing
        let matEnabled: Bool

        static var `default`: Configuration {
            Configuration(
                matColor: CGColor(red: 0.96, green: 0.94, blue: 0.92, alpha: 1.0),
                spacing: .gallery,
                matEnabled: true
            )
        }
    }

    /// Thread-safe rendering using CGContext (no NSGraphicsContext / lockFocus dependency)
    static func render(
        painting paintingImage: NSImage,
        screenWidth: Int,
        screenHeight: Int,
        configuration: Configuration
    ) -> CGImage? {
        guard screenWidth > 0, screenHeight > 0 else { return nil }

        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let context = CGContext(
            data: nil,
            width: screenWidth,
            height: screenHeight,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }

        let screenRect = CGRect(x: 0, y: 0, width: screenWidth, height: screenHeight)

        // Get CGImage from NSImage
        guard let paintingCG = paintingImage.cgImage(
            forProposedRect: nil,
            context: nil,
            hints: nil
        ) else { return nil }

        let paintingSize = CGSize(
            width: CGFloat(paintingCG.width),
            height: CGFloat(paintingCG.height)
        )

        if configuration.matEnabled {
            renderWithMat(
                context: context,
                painting: paintingCG,
                paintingSize: paintingSize,
                screenRect: screenRect,
                configuration: configuration
            )
        } else {
            renderFillScreen(
                context: context,
                painting: paintingCG,
                paintingSize: paintingSize,
                screenRect: screenRect
            )
        }

        return context.makeImage()
    }

    private static func renderWithMat(
        context: CGContext,
        painting: CGImage,
        paintingSize: CGSize,
        screenRect: CGRect,
        configuration: Configuration
    ) {
        let screenSize = screenRect.size

        // Fill background with mat color
        context.setFillColor(configuration.matColor)
        context.fill(screenRect)

        // Calculate padding
        let shortSide = min(screenSize.width, screenSize.height)
        let padding = shortSide * configuration.spacing.relativePadding

        let availableWidth = screenSize.width - (padding * 2)
        let availableHeight = screenSize.height - (padding * 2)

        guard availableWidth > 0, availableHeight > 0 else { return }

        // Scale painting to fit, maintaining aspect ratio
        let paintingAspect = paintingSize.width / paintingSize.height
        let availableAspect = availableWidth / availableHeight

        let drawSize: CGSize
        if paintingAspect > availableAspect {
            drawSize = CGSize(width: availableWidth, height: availableWidth / paintingAspect)
        } else {
            drawSize = CGSize(width: availableHeight * paintingAspect, height: availableHeight)
        }

        let drawOrigin = CGPoint(
            x: (screenSize.width - drawSize.width) / 2,
            y: (screenSize.height - drawSize.height) / 2
        )
        let drawRect = CGRect(origin: drawOrigin, size: drawSize)

        // Subtle shadow behind the painting
        context.saveGState()
        context.setShadow(
            offset: CGSize(width: 0, height: -4),
            blur: 20,
            color: CGColor(gray: 0, alpha: 0.15)
        )
        context.setFillColor(CGColor.white)
        context.fill(drawRect)
        context.restoreGState()

        // Draw the painting
        context.draw(painting, in: drawRect)
    }

    private static func renderFillScreen(
        context: CGContext,
        painting: CGImage,
        paintingSize: CGSize,
        screenRect: CGRect
    ) {
        let screenSize = screenRect.size

        // Fill with black
        context.setFillColor(CGColor(gray: 0, alpha: 1))
        context.fill(screenRect)

        // Aspect fill
        let paintingAspect = paintingSize.width / paintingSize.height
        let screenAspect = screenSize.width / screenSize.height

        let drawSize: CGSize
        if paintingAspect > screenAspect {
            drawSize = CGSize(width: screenSize.height * paintingAspect, height: screenSize.height)
        } else {
            drawSize = CGSize(width: screenSize.width, height: screenSize.width / paintingAspect)
        }

        let drawOrigin = CGPoint(
            x: (screenSize.width - drawSize.width) / 2,
            y: (screenSize.height - drawSize.height) / 2
        )

        context.draw(painting, in: CGRect(origin: drawOrigin, size: drawSize))
    }

    static func saveToTemporaryFile(_ image: CGImage, screenID: CGDirectDisplayID) -> URL? {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("EaselWall", isDirectory: true)

        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)

        // macOS caches wallpaper by URL — must use a unique filename each time
        let timestamp = Int(Date().timeIntervalSince1970)
        let fileURL = tempDir.appendingPathComponent("wallpaper_\(screenID)_\(timestamp).png")

        // Clean up old wallpapers for this screen
        if let files = try? FileManager.default.contentsOfDirectory(at: tempDir, includingPropertiesForKeys: nil) {
            for file in files where file.lastPathComponent.hasPrefix("wallpaper_\(screenID)_") && file != fileURL {
                try? FileManager.default.removeItem(at: file)
            }
        }

        guard let destination = CGImageDestinationCreateWithURL(
            fileURL as CFURL,
            "public.png" as CFString,
            1,
            nil
        ) else { return nil }

        CGImageDestinationAddImage(destination, image, nil)

        guard CGImageDestinationFinalize(destination) else { return nil }

        return fileURL
    }
}
