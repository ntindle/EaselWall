import SwiftUI
import WidgetKit

@available(macOS 26.0, *)
struct EaselWallNextPaintingControl: ControlWidget {
    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(
            kind: "com.ntindle.EaselWall.nextPainting"
        ) {
            ControlWidgetButton(action: NextPaintingIntent()) {
                Label("Next Painting", systemImage: "paintpalette.fill")
            }
        }
        .displayName("Next Painting")
        .description("Switch to the next EaselWall wallpaper")
    }
}

@available(macOS 26.0, *)
struct EaselWallWidgetBundle: WidgetBundle {
    var body: some Widget {
        EaselWallNextPaintingControl()
    }
}
