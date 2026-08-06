import Foundation
import XCTest

#if SCREENSHOT_CAPTURE
final class MatRendererScreenshotOverrideTests: XCTestCase {
    func testExplicitScreenshotRenderDirectoryOverridesFoundationTemporaryDirectory() {
        let expected = URL(
            fileURLWithPath: "/private/tmp/easelwall-isolated-render-root",
            isDirectory: true
        ).standardizedFileURL

        let resolved = MatRenderer.screenshotRenderDirectory(
            environment: [
                MatRenderer.screenshotRenderDirectoryEnvironmentKey: expected.path
            ]
        )

        XCTAssertEqual(resolved, expected)
    }

    func testScreenshotRenderDirectoryRejectsRelativeAndMissingOverrides() {
        XCTAssertNil(
            MatRenderer.screenshotRenderDirectory(
                environment: [
                    MatRenderer.screenshotRenderDirectoryEnvironmentKey: "relative/path"
                ]
            )
        )
        XCTAssertNil(MatRenderer.screenshotRenderDirectory(environment: [:]))
    }
}
#endif
