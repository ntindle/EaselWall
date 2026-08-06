#!/usr/bin/env bash
# Drive App Store screenshot capture for EaselWall.
#
# Quick start:
#   ./scripts/screenshot.sh auto 5      # rotate + capture 5 desktops
#   ./scripts/screenshot.sh window      # interactive: click a window (e.g. Settings)
#   ./scripts/screenshot.sh region      # interactive: drag a region
#   ./scripts/screenshot.sh menubar     # capture the menu bar dropdown (needs Accessibility perm)
#
# Outputs land in screenshots/. App Store needs 1280x800 minimum; we capture at
# native retina (typically 2880x1800 or 3024x1964 on M-series) which downscales
# cleanly for any required size.
set -euo pipefail

cd "$(dirname "$0")/.."

SHOTS_DIR="screenshots"
mkdir -p "$SHOTS_DIR"

usage() {
  cat <<EOF
Usage: $0 <command> [args]

Commands:
  rotate                Trigger app to rotate to next painting
  desktop [LABEL]       Capture every display as desktop-LABEL-display-N.png
  auto [COUNT]          Rotate + capture COUNT times (default 5)
  window                Interactive: click an EaselWall window to capture
  region                Interactive: drag to select a region
  menubar               Click + capture the EaselWall menu bar dropdown
  list                  List displays
  clean                 Delete everything in screenshots/

Notes:
  - 'menubar' and 'auto' may require Terminal/iTerm to have Accessibility
    permission in System Settings → Privacy & Security → Accessibility.
  - 'rotate' uses DistributedNotificationCenter (no entitlements needed).
EOF
}

require_app() {
  if ! pgrep -x EaselWall >/dev/null; then
    echo "EaselWall is not running. Launch it first (e.g. open -b com.ntindle.EaselWall)." >&2
    exit 1
  fi
}

rotate() {
  require_app
  swift -e '
  import Foundation
  DistributedNotificationCenter.default().postNotificationName(
    .init("com.ntindle.EaselWall.nextPainting"),
    object: nil
  )
  RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.5))
  ' >/dev/null
  # Give the wallpaper time to render + apply
  sleep 2
}

count_displays() {
  system_profiler SPDisplaysDataType 2>/dev/null \
    | grep -cE "^\s+Resolution:" || echo 1
}

# Find where the app drops rendered wallpapers
rendered_dir() {
  echo "${TMPDIR%/}/EaselWall"
}

# Copy the freshest rendered wallpaper per screen (one per CGDirectDisplayID).
# These are the actual mat-composited PNGs the OS used as wallpaper — no window
# eclipse, no focus issues, native resolution.
desktop() {
  local label="${1:-$(date +%H%M%S)}"
  local dir
  dir="$(rendered_dir)"
  if [[ ! -d "$dir" ]]; then
    echo "No rendered wallpapers found at $dir — is the app running and has it rotated yet?" >&2
    return 1
  fi
  # For each unique screen ID, pick the file with the largest mtime
  local screens
  screens=$(find "$dir" -maxdepth 1 -type f -name 'wallpaper_*.png' -print \
    | sed -E 's|.*wallpaper_([0-9]+)_[0-9]+\.png|\1|' \
    | sort -un)
  if [[ -z "$screens" ]]; then
    echo "No wallpaper_*.png files in $dir." >&2
    return 1
  fi
  while IFS= read -r screen; do
    local newest
    newest=$(find "$dir" -maxdepth 1 -type f -name "wallpaper_${screen}_*.png" \
      -exec stat -f '%m %N' {} + \
      | sort -rn \
      | sed -n '1s/^[^ ]* //p')
    [[ -z "$newest" ]] && continue
    local out="$SHOTS_DIR/desktop-${label}-screen-${screen}.png"
    cp "$newest" "$out"
    local dims
    dims=$(sips -g pixelWidth -g pixelHeight "$out" 2>/dev/null \
      | awk '/pixel(Width|Height)/{print $2}' | paste -sd x -)
    echo "→ $out ($dims)"
  done <<< "$screens"
}

# Legacy screencap-based desktop (kept for debugging — captures whatever's on
# screen, which usually means the front app, not the wallpaper).
desktop_raw() {
  local label="${1:-$(date +%H%M%S)}"
  local n
  n=$(count_displays)
  for i in $(seq 1 "$n"); do
    local out="$SHOTS_DIR/raw-${label}-display-${i}.png"
    screencapture -D "$i" -t png -x -o "$out"
    echo "→ $out"
  done
}

auto() {
  local count="${1:-5}"
  echo "Will rotate + capture $count times. Quit early with Ctrl-C."
  for i in $(seq 1 "$count"); do
    local n
    n=$(printf "%02d" "$i")
    echo "─── round $n ───"
    rotate
    desktop "$n"
  done
}

window() {
  local out
  out="$SHOTS_DIR/window-$(date +%H%M%S).png"
  echo "Click the window you want to capture (Esc cancels)..."
  screencapture -w -t png -o "$out"
  echo "→ $out"
}

region() {
  local out
  out="$SHOTS_DIR/region-$(date +%H%M%S).png"
  echo "Drag to select a region (Esc cancels)..."
  screencapture -i -t png -o "$out"
  echo "→ $out"
}

menubar() {
  require_app
  echo "Opening EaselWall menu bar dropdown..."
  if ! osascript <<'APPLESCRIPT'
    tell application "System Events"
      tell process "EaselWall"
        try
          click menu bar item 1 of menu bar 2
        on error
          click menu bar item 1 of menu bar 1
        end try
      end tell
    end tell
APPLESCRIPT
  then
    echo "AppleScript failed — make sure Terminal has Accessibility permission." >&2
    echo "System Settings → Privacy & Security → Accessibility → toggle on for your terminal." >&2
    exit 1
  fi
  sleep 0.4
  local out
  out="$SHOTS_DIR/menubar-$(date +%H%M%S).png"
  # Capture top-right region of primary display where MenuBarExtra lives.
  # Tune coords if your display geometry differs.
  screencapture -D 1 -t png -x -o "$out"
  echo "→ $out  (crop in Preview to just the dropdown)"
  echo "Press Esc or click away to dismiss the menu."
}

list() {
  echo "Displays:"
  system_profiler SPDisplaysDataType 2>/dev/null \
    | grep -E "^\s+(Display Type|Resolution|Main Display):" \
    || echo "  (none detected)"
}

clean() {
  rm -f "$SHOTS_DIR"/*.png
  echo "Cleaned $SHOTS_DIR/"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  rotate)   rotate ;;
  desktop)  desktop "$@" ;;
  desktop-raw) desktop_raw "$@" ;;
  auto)     auto "$@" ;;
  window)   window ;;
  region)   region ;;
  menubar)  menubar ;;
  list)     list ;;
  clean)    clean ;;
  ""|help|-h|--help) usage ;;
  *) echo "Unknown command: $cmd" >&2; usage; exit 1 ;;
esac
