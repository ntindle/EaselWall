#!/usr/bin/env bash
# Capture privacy-safe App Store inputs from the real EaselWall process.
#
# This script never captures a display or arbitrary region. It captures only a
# CGWindowID owned by the screenshot-harness process and copies wallpaper PNGs
# written by EaselWall's own MatRenderer.
set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE_DIR="screenshots/real/source"
OUTPUT_DIR="screenshots/real/for-upload"
DERIVED_DATA="build/ScreenshotDerivedData"
SCREENSHOT_PROJECT_SPEC="build/project-screenshot.yml"
CAPTURE_BUNDLE_ID="com.ntindle.EaselWall.ScreenshotHarness"
CAPTURE_TABS=(appearance displays gallery schedule)
LOCK_DIR="$PWD/screenshots/real/.capture.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner.txt"
LOCK_TOKEN_FILE="$LOCK_DIR/token"

RUN_DIR=""
RUN_HOME=""
CAPTURE_TMPDIR=""
CAPTURE_RENDER_DIR=""
SUPPORT_TOOL=""
WALLPAPER_STATE=""
MENU_FALLBACK_DIR=""
MENU_FALLBACK_IMAGE=""
MENU_FALLBACK_READY=""
CAPTURE_PID=""
AUTOMATION_PID=""
WALLPAPERS_SAVED=0
CLEANUP_DONE=0
CLEANUP_RESULT=0
LOCK_HELD=0
LOCK_TOKEN=""
DEFERRED_SIGNAL_STATUS=0
MENU_RECOVERY_NEEDED=0
RUN_MODE="capture"
CUA_MENU_MODE=0

case "${1:-}" in
  "") ;;
  --compose-only)
    RUN_MODE="compose"
    ;;
  --cua-menu)
    CUA_MENU_MODE=1
    ;;
  *)
    echo "usage: $0 [--compose-only|--cua-menu]" >&2
    exit 2
    ;;
esac
if [[ $# -gt 1 ]]; then
  echo "usage: $0 [--compose-only|--cua-menu]" >&2
  exit 2
fi

required_commands=(python3)
if [[ "$RUN_MODE" == "capture" ]]; then
  required_commands+=(xcodegen xcodebuild xcrun screencapture osascript defaults jq)
fi
for command in "${required_commands[@]}"; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command not found: $command" >&2
    exit 1
  fi
done

arm_signal_traps() {
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 131' QUIT
  trap 'exit 143' TERM
}

remember_deferred_signal() {
  if [[ "$DEFERRED_SIGNAL_STATUS" -eq 0 ]]; then
    DEFERRED_SIGNAL_STATUS="$1"
  fi
}

defer_signal_traps() {
  DEFERRED_SIGNAL_STATUS=0
  trap 'remember_deferred_signal 129' HUP
  trap 'remember_deferred_signal 130' INT
  trap 'remember_deferred_signal 131' QUIT
  trap 'remember_deferred_signal 143' TERM
}

resume_signal_traps() {
  local deferred_status
  # Rearm first: any signal from this point exits immediately, after the caller
  # has recorded ownership of the resource created in its critical section.
  arm_signal_traps
  deferred_status="$DEFERRED_SIGNAL_STATUS"
  DEFERRED_SIGNAL_STATUS=0
  if [[ "$deferred_status" -ne 0 ]]; then
    exit "$deferred_status"
  fi
}

ignore_cleanup_signals() {
  trap '' HUP INT QUIT TERM
}

# A no-op checkpoint keeps the safety-critical boundaries deterministic in
# regression harnesses without changing production behavior.
capture_safety_checkpoint() {
  :
}

write_lock_token() {
  printf '%s\n' "$LOCK_TOKEN" >"$LOCK_TOKEN_FILE"
}

write_lock_owner() {
  printf 'pid=%s\nstarted_utc=%s\nworking_directory=%s\n' \
    "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PWD" >"$LOCK_OWNER_FILE"
}

acquire_lock() {
  # A signal cannot exit between creating the lock and recording its ownership.
  # It is remembered and delivered only after LOCK_HELD is authoritative.
  defer_signal_traps
  if ! mkdir -p "$(dirname "$LOCK_DIR")"; then
    echo "Could not create screenshot safety-lock parent directory." >&2
    resume_signal_traps
    return 1
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another screenshot capture/compose run is active, or a stale safety lock exists:" >&2
    echo "  $LOCK_DIR" >&2
    if [[ -s "$LOCK_OWNER_FILE" ]]; then
      sed -n '1,20p' "$LOCK_OWNER_FILE" >&2
    fi
    echo "Failing closed. Verify no screenshot run is active and that wallpapers were restored." >&2
    echo "Only then remove $LOCK_OWNER_FILE and $LOCK_TOKEN_FILE, then run: rmdir '$LOCK_DIR'" >&2
    resume_signal_traps
    return 1
  fi
  capture_safety_checkpoint "after-lock-mkdir" || true

  LOCK_TOKEN="$$-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
  if ! write_lock_token || ! write_lock_owner; then
    rm -f -- "$LOCK_TOKEN_FILE" "$LOCK_OWNER_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    LOCK_TOKEN=""
    echo "Could not record ownership for screenshot safety lock $LOCK_DIR" >&2
    resume_signal_traps
    return 1
  fi
  LOCK_HELD=1
  resume_signal_traps
}

release_lock() {
  if [[ "$LOCK_HELD" -eq 0 ]]; then
    return 0
  fi
  if [[ ! -s "$LOCK_TOKEN_FILE" || "$(<"$LOCK_TOKEN_FILE")" != "$LOCK_TOKEN" ]]; then
    echo "Refusing to release screenshot lock because its ownership token changed: $LOCK_DIR" >&2
    return 1
  fi
  if ! rm -f -- "$LOCK_OWNER_FILE" "$LOCK_TOKEN_FILE" || ! rmdir "$LOCK_DIR"; then
    echo "Could not release screenshot safety lock: $LOCK_DIR" >&2
    return 1
  fi
  LOCK_HELD=0
}

terminate_process() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    return 0
  fi

  kill -TERM "$pid" 2>/dev/null || true
  local attempts=40
  while [[ $attempts -gt 0 ]] && kill -0 "$pid" 2>/dev/null; do
    sleep 0.1
    attempts=$((attempts - 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
}

stop_capture_app() {
  if [[ -n "$CAPTURE_PID" ]]; then
    terminate_process "$CAPTURE_PID"
  fi
  CAPTURE_PID=""
}

stop_automation() {
  if [[ -n "$AUTOMATION_PID" ]]; then
    terminate_process "$AUTOMATION_PID"
  fi
  AUTOMATION_PID=""
}

restore_wallpapers() {
  if [[ "$WALLPAPERS_SAVED" -eq 0 ]]; then
    return 0
  fi
  if [[ ! -x "$SUPPORT_TOOL" || ! -s "$WALLPAPER_STATE" ]]; then
    echo "Could not restore wallpapers because the backup helper or manifest is missing." >&2
    return 1
  fi
  if ! "$SUPPORT_TOOL" restore-wallpapers "$WALLPAPER_STATE"; then
    echo "Could not restore every pre-capture wallpaper; capture is not successful." >&2
    return 1
  fi
  WALLPAPERS_SAVED=0
}

restore_menu_fallback() {
  local current_image="$SOURCE_DIR/menu.png"
  local current_ready="$SOURCE_DIR/menu.ready.json"
  if [[ -s "$current_image" && -s "$current_ready" ]]; then
    return 0
  fi

  if [[ -e "$current_image" || -e "$current_ready" ]]; then
    local failed_dir="$RUN_DIR/incomplete-menu-source"
    mkdir -p "$failed_dir"
    [[ -e "$current_image" ]] && mv "$current_image" "$failed_dir/"
    [[ -e "$current_ready" ]] && mv "$current_ready" "$failed_dir/"
  fi

  if [[ -s "$MENU_FALLBACK_IMAGE" && -s "$MENU_FALLBACK_READY" ]]; then
    local pending_image="$SOURCE_DIR/.menu.png.restore.$$"
    local pending_ready="$SOURCE_DIR/.menu.ready.json.restore.$$"
    if ! cp "$MENU_FALLBACK_IMAGE" "$pending_image" ||
      ! cp "$MENU_FALLBACK_READY" "$pending_ready" ||
      ! mv "$pending_image" "$current_image" ||
      ! mv "$pending_ready" "$current_ready"; then
      rm -f -- "$pending_image" "$pending_ready"
      echo "Could not restore the last verified native menu capture and readiness pair." >&2
      return 1
    fi

    shopt -s nullglob
    local current_wallpapers=("$SOURCE_DIR"/menu-wallpaper-*.png)
    local fallback_wallpapers=("$MENU_FALLBACK_DIR"/menu-wallpaper-*.png)
    shopt -u nullglob
    if [[ ${#current_wallpapers[@]} -gt 0 ]]; then
      rm -f -- "${current_wallpapers[@]}"
    fi
    if [[ ${#fallback_wallpapers[@]} -gt 0 ]]; then
      cp "${fallback_wallpapers[@]}" "$SOURCE_DIR/"
    fi
    echo "Restored the last verified native menu capture and readiness pair after recapture failed."
  else
    echo "No verified menu capture/readiness pair is available; composition will remain blocked." >&2
  fi
  return 0
}

cleanup_resources() {
  # Cleanup is deliberately uninterruptible: a second signal must never stop rollback.
  ignore_cleanup_signals
  if [[ "$CLEANUP_DONE" -eq 1 ]]; then
    return "$CLEANUP_RESULT"
  fi
  if [[ -z "$RUN_DIR" ]]; then
    CLEANUP_RESULT=0
    CLEANUP_DONE=1
    return 0
  fi

  local failed=0
  stop_automation
  stop_capture_app
  if ! restore_wallpapers; then
    failed=1
  fi
  # Menu recovery remains independent of wallpaper rollback so both are attempted.
  if [[ "$MENU_RECOVERY_NEEDED" -eq 1 ]]; then
    if ! restore_menu_fallback; then
      failed=1
    fi
  fi
  CLEANUP_RESULT="$failed"
  CLEANUP_DONE=1
  return "$failed"
}

report_recovery_state() {
  echo "Capture rollback/recovery failed; no capture result is safe to report." >&2
  echo "Recovery data was preserved at: $RUN_DIR" >&2
  echo "The cross-run safety lock was retained at: $LOCK_DIR" >&2
  echo "Use $WALLPAPER_STATE and $RUN_DIR/wallpaper-backups to recover every display." >&2
  echo "After verified manual recovery, remove $LOCK_OWNER_FILE and $LOCK_TOKEN_FILE, then run: rmdir '$LOCK_DIR'" >&2
}

remove_run_directory() {
  if [[ -z "$RUN_DIR" ]]; then
    return 0
  fi
  if [[ "$RUN_DIR" != "${TMPDIR%/}/easelwall-screenshot-capture."* ]]; then
    echo "Refusing to remove unexpected screenshot run directory: $RUN_DIR" >&2
    return 1
  fi
  if ! rm -rf -- "$RUN_DIR" || [[ -e "$RUN_DIR" ]]; then
    echo "Could not remove private screenshot run directory: $RUN_DIR" >&2
    return 1
  fi
  RUN_DIR=""
}

finalize_safe_cleanup() {
  ignore_cleanup_signals
  if [[ "$CLEANUP_DONE" -ne 1 || "$CLEANUP_RESULT" -ne 0 || "$WALLPAPERS_SAVED" -ne 0 ]]; then
    echo "Refusing to remove recovery data or release the lock before verified rollback." >&2
    return 1
  fi
  remove_run_directory || return 1
  release_lock
}

cleanup_on_exit() {
  # This ordering is safety-critical: ignore follow-up signals before removing EXIT.
  ignore_cleanup_signals
  local status="$1"
  trap - EXIT
  local cleanup_status=0
  cleanup_resources || cleanup_status=$?
  if [[ "$cleanup_status" -eq 0 ]]; then
    finalize_safe_cleanup || cleanup_status=$?
  else
    report_recovery_state
  fi
  if [[ "$cleanup_status" -ne 0 && "$status" -eq 0 ]]; then
    status="$cleanup_status"
  fi
  exit "$status"
}
trap 'cleanup_on_exit $?' EXIT
arm_signal_traps

acquire_lock
RUN_DIR="$(mktemp -d "${TMPDIR%/}/easelwall-screenshot-capture.XXXXXX")"
chmod 700 "$RUN_DIR"
RUN_HOME="$RUN_DIR/home"
CAPTURE_TMPDIR="$RUN_DIR/tmp"
CAPTURE_RENDER_DIR="$CAPTURE_TMPDIR/EaselWall"
SUPPORT_TOOL="$RUN_DIR/capture-support"
WALLPAPER_STATE="$RUN_DIR/wallpapers-before.json"
MENU_FALLBACK_DIR="$RUN_DIR/menu-fallback"
MENU_FALLBACK_IMAGE="$MENU_FALLBACK_DIR/menu.png"
MENU_FALLBACK_READY="$MENU_FALLBACK_DIR/menu.ready.json"
mkdir -p \
  "$RUN_HOME" \
  "$CAPTURE_TMPDIR" \
  "$CAPTURE_RENDER_DIR" \
  "$MENU_FALLBACK_DIR" \
  "$SOURCE_DIR" \
  "$OUTPUT_DIR"

stage_menu_fallback() {
  local candidate_dir=""
  local candidate_mtime newest_mtime=0
  if [[ -s "$SOURCE_DIR/menu.png" && -s "$SOURCE_DIR/menu.ready.json" ]]; then
    candidate_dir="$SOURCE_DIR"
    newest_mtime="$(stat -f '%m' "$SOURCE_DIR/menu.ready.json")"
  fi

  local candidate
  while IFS= read -r candidate; do
    local archive_dir
    archive_dir="$(dirname "$candidate")"
    [[ -s "$archive_dir/menu.png" ]] || continue
    candidate_mtime="$(stat -f '%m' "$candidate")"
    if [[ "$candidate_mtime" -gt "$newest_mtime" ]]; then
      candidate_dir="$archive_dir"
      newest_mtime="$candidate_mtime"
    fi
  done < <(find screenshots/real/archive/source -type f -name 'menu.ready.json' -print 2>/dev/null)

  if [[ -z "$candidate_dir" ]]; then
    return
  fi

  cp "$candidate_dir/menu.png" "$MENU_FALLBACK_IMAGE"
  cp "$candidate_dir/menu.ready.json" "$MENU_FALLBACK_READY"
  shopt -s nullglob
  local paired_wallpapers=("$candidate_dir"/menu-wallpaper-*.png)
  shopt -u nullglob
  if [[ ${#paired_wallpapers[@]} -gt 0 ]]; then
    cp "${paired_wallpapers[@]}" "$MENU_FALLBACK_DIR/"
  fi
}

archive_existing_sources() {
  shopt -s nullglob
  local existing=("$SOURCE_DIR"/*.png "$SOURCE_DIR"/*.json)
  shopt -u nullglob
  if [[ ${#existing[@]} -eq 0 ]]; then
    return
  fi

  local timestamp archive suffix
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  archive="screenshots/real/archive/source/$timestamp"
  suffix=2
  while [[ -e "$archive" ]]; do
    archive="screenshots/real/archive/source/${timestamp}-${suffix}"
    suffix=$((suffix + 1))
  done
  mkdir -p "$archive"
  mv "${existing[@]}" "$archive/"
  echo "Archived previous native capture sources in $archive"
}

wait_for_file() {
  local path="$1"
  local attempts=80
  while [[ $attempts -gt 0 ]]; do
    if [[ -s "$path" ]]; then
      return 0
    fi
    if [[ -n "$CAPTURE_PID" ]] && ! kill -0 "$CAPTURE_PID" 2>/dev/null; then
      echo "EaselWall screenshot process exited before writing $path" >&2
      return 1
    fi
    sleep 0.25
    attempts=$((attempts - 1))
  done
  echo "Timed out waiting for $path" >&2
  return 1
}

read_json_field() {
  local path="$1"
  local field="$2"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' \
    "$path" "$field"
}

launch_capture_app() {
  local ready_file="$1"
  shift
  local binary="$DERIVED_DATA/Build/Products/Screenshot/EaselWall.app/Contents/MacOS/EaselWall"
  if [[ ! -x "$binary" ]]; then
    echo "Screenshot app binary not found: $binary" >&2
    exit 1
  fi

  defer_signal_traps
  CFFIXED_USER_HOME="$RUN_HOME" \
    TMPDIR="$CAPTURE_TMPDIR/" \
    EASELWALL_SCREENSHOT_RENDER_DIRECTORY="$CAPTURE_RENDER_DIR" \
    "$binary" "$@" "--screenshot-ready=$ready_file" \
    >"$RUN_DIR/easelwall.log" 2>&1 &
  capture_safety_checkpoint "after-capture-spawn" || true
  CAPTURE_PID=$!
  resume_signal_traps
  wait_for_file "$ready_file"

  local reported_pid
  reported_pid="$(read_json_field "$ready_file" pid)"
  if [[ "$reported_pid" != "$CAPTURE_PID" ]]; then
    echo "Readiness PID $reported_pid does not match launched PID $CAPTURE_PID" >&2
    exit 1
  fi
}

capture_settings_tab() {
  local tab="$1"
  local render_flag="${2:-}"
  local ready_file="$RUN_DIR/settings-${tab}.json"
  local args=("--screenshot-tab=$tab")
  if [[ "$render_flag" == "render" ]]; then
    args+=("--screenshot-render-wallpaper")
  fi

  launch_capture_app "$ready_file" "${args[@]}"

  local ready_kind ready_window owner_window width height
  ready_kind="$(read_json_field "$ready_file" kind)"
  if [[ "$ready_kind" != "settings" ]]; then
    echo "Readiness kind $ready_kind does not match Settings/$tab" >&2
    exit 1
  fi
  ready_window="$(read_json_field "$ready_file" windowID)"
  owner_window="$("$SUPPORT_TOOL" settings-window "$CAPTURE_PID")"
  if [[ "$ready_window" != "$owner_window" ]]; then
    echo "Window ownership validation failed for Settings/$tab" >&2
    exit 1
  fi

  width="$(read_json_field "$ready_file" widthPoints)"
  height="$(read_json_field "$ready_file" heightPoints)"
  python3 -c 'import sys; w=float(sys.argv[1]); h=float(sys.argv[2]); assert abs(w-520)<1 and abs(h-360)<1, (w,h)' \
    "$width" "$height"

  screencapture -x -o -t png -l "$ready_window" "$SOURCE_DIR/settings-${tab}.png"
  if [[ ! -s "$SOURCE_DIR/settings-${tab}.png" ]]; then
    echo "Settings/$tab capture is empty; grant Screen Recording permission to the terminal." >&2
    exit 1
  fi
  python3 scripts/easelwall_capture_provenance.py \
    --ready "$ready_file" \
    --capture "$SOURCE_DIR/settings-${tab}.png" \
    --kind settings \
    --pid "$CAPTURE_PID" \
    --window-id "$ready_window"
  cp "$ready_file" "$SOURCE_DIR/settings-${tab}.ready.json"
  if [[ "$render_flag" == "render" ]]; then
    copy_fresh_wallpapers "$WALLPAPER_MARKER"
  fi
  stop_capture_app
}

copy_fresh_wallpapers() {
  local marker="$1"
  local destination_prefix="${2:-wallpaper}"
  local rendered_dir="$CAPTURE_RENDER_DIR"
  local inventory="$RUN_DIR/${destination_prefix}-inventory.tsv"
  local candidate_inventory="${inventory}.candidate"
  local inventory_error="${inventory}.error"
  local attempts=80 stable_rounds=0 previous_inventory=""

  while [[ $attempts -gt 0 ]]; do
    local inventory_status=0 current_inventory=""
    python3 scripts/easelwall_capture_inventory.py \
      --manifest "$WALLPAPER_STATE" \
      --render-dir "$rendered_dir" \
      --marker "$marker" \
      >"$candidate_inventory" 2>"$inventory_error" || inventory_status=$?

    if [[ "$inventory_status" -eq 0 ]]; then
      current_inventory="$(<"$candidate_inventory")"
      if [[ -n "$current_inventory" && "$current_inventory" == "$previous_inventory" ]]; then
        stable_rounds=$((stable_rounds + 1))
        if [[ $stable_rounds -ge 4 ]]; then
          break
        fi
      else
        stable_rounds=0
      fi
      previous_inventory="$current_inventory"
    elif [[ "$inventory_status" -eq 2 ]]; then
      stable_rounds=0
      previous_inventory=""
    else
      sed -n '1,20p' "$inventory_error" >&2
      return 1
    fi

    attempts=$((attempts - 1))
    sleep 0.25
  done

  if [[ $stable_rounds -lt 4 ]]; then
    sed -n '1,20p' "$inventory_error" >&2
    echo "No exact, stable MatRenderer output set appeared within 20 seconds." >&2
    return 1
  fi
  mv "$candidate_inventory" "$inventory"

  local copied=0
  while IFS=$'\t' read -r screen_id size modified_ns path; do
    if [[ ! "$screen_id" =~ ^[0-9]+$ || ! "$size" =~ ^[1-9][0-9]*$ || \
      ! "$modified_ns" =~ ^[0-9]+$ || ! -s "$path" ]]; then
      echo "Invalid validated MatRenderer inventory record for display $screen_id" >&2
      return 1
    fi
    cp "$path" "$SOURCE_DIR/${destination_prefix}-screen-${screen_id}.png"
    copied=$((copied + 1))
  done <"$inventory"

  local expected_count
  expected_count="$(jq 'length' "$WALLPAPER_STATE")"
  if [[ "$copied" -ne "$expected_count" ]]; then
    echo "Copied $copied wallpapers, but the backup manifest requires $expected_count." >&2
    return 1
  fi
}

capture_menu() {
  local ready_file="$RUN_DIR/menu.json"
  local menu_capture="$RUN_DIR/menu.png"
  local menu_wallpaper_marker="$RUN_DIR/menu-wallpaper-start"
  touch "$menu_wallpaper_marker"
  launch_capture_app "$ready_file" --screenshot-menu --screenshot-render-wallpaper

  local before=()
  while IFS= read -r window_id; do
    [[ -n "$window_id" ]] && before+=("$window_id")
  done < <("$SUPPORT_TOOL" window-ids "$CAPTURE_PID")

  local menu_window=""
  if [[ "$CUA_MENU_MODE" -eq 1 ]]; then
    local cua_marker="$RUN_DIR/cua-menu-ready.txt"
    local pending_marker="${cua_marker}.pending"
    {
      printf 'state=ready\n'
      printf 'pid=%s\n' "$CAPTURE_PID"
      printf 'bundle_id=%s\n' "$CAPTURE_BUNDLE_ID"
      printf 'preexisting_window_count=%s\n' "${#before[@]}"
      local baseline_id
      for baseline_id in "${before[@]}"; do
        printf 'preexisting_window_id=%s\n' "$baseline_id"
      done
    } >"$pending_marker"
    mv "$pending_marker" "$cua_marker"
    echo "CUA_MENU_READY"
    echo "  marker: $cua_marker"
    echo "  target PID: $CAPTURE_PID"
    echo "  target bundle ID: $CAPTURE_BUNDLE_ID"
    echo "Use the external CUA driver to click 'Open EaselWall Menu' in this exact-PID harness within 180 seconds."

    local cua_attempts=720
    while [[ $cua_attempts -gt 0 ]]; do
      menu_window="$("$SUPPORT_TOOL" new-window "$CAPTURE_PID" "${before[@]}" 2>/dev/null || true)"
      [[ -n "$menu_window" ]] && break
      if ! kill -0 "$CAPTURE_PID" 2>/dev/null; then
        echo "EaselWall screenshot process exited while waiting for external CUA." >&2
        exit 1
      fi
      cua_attempts=$((cua_attempts - 1))
      sleep 0.25
    done
    if [[ -z "$menu_window" ]]; then
      echo "External CUA did not open a new EaselWall-owned menu window within 180 seconds." >&2
      exit 1
    fi
  else
    local automation_log="$RUN_DIR/menu-automation.log"
    defer_signal_traps
    osascript - "$CAPTURE_PID" >"$automation_log" 2>&1 <<'APPLESCRIPT' &
on run argv
  set targetPID to (item 1 of argv) as integer
  tell application "System Events"
    set targetProcess to first process whose unix id is targetPID
    set clickedItem to false
    repeat with targetBar in menu bars of targetProcess
      repeat with targetItem in menu bar items of targetBar
        try
          if (name of targetItem is "EaselWall") or (description of targetItem is "EaselWall") then
            click targetItem
            set clickedItem to true
            exit repeat
          end if
        end try
      end repeat
      if clickedItem then exit repeat
    end repeat
    if not clickedItem then error "EaselWall menu bar item was not found"
  end tell
end run
APPLESCRIPT
    capture_safety_checkpoint "after-automation-spawn" || true
    AUTOMATION_PID=$!
    resume_signal_traps

    local automation_attempts=40
    while [[ $automation_attempts -gt 0 ]] && kill -0 "$AUTOMATION_PID" 2>/dev/null; do
      sleep 0.25
      automation_attempts=$((automation_attempts - 1))
    done
    if kill -0 "$AUTOMATION_PID" 2>/dev/null; then
      stop_automation
      echo "Timed out opening the EaselWall menu. Grant Accessibility permission to the terminal." >&2
      exit 1
    fi
    if ! wait "$AUTOMATION_PID"; then
      AUTOMATION_PID=""
      sed -n '1,20p' "$automation_log" >&2
      echo "Could not open the EaselWall menu. Grant Accessibility permission to the terminal." >&2
      exit 1
    fi
    AUTOMATION_PID=""

    local attempts=40
    while [[ $attempts -gt 0 ]]; do
      menu_window="$("$SUPPORT_TOOL" new-window "$CAPTURE_PID" "${before[@]}" 2>/dev/null || true)"
      [[ -n "$menu_window" ]] && break
      attempts=$((attempts - 1))
      sleep 0.25
    done
    if [[ -z "$menu_window" ]]; then
      echo "No new EaselWall-owned menu window appeared; refusing a full-display fallback." >&2
      exit 1
    fi
  fi

  screencapture -x -o -t png -l "$menu_window" "$menu_capture"
  if [[ ! -s "$menu_capture" ]]; then
    echo "Menu capture is empty; grant Screen Recording permission to the terminal." >&2
    exit 1
  fi
  python3 scripts/easelwall_capture_provenance.py \
    --ready "$ready_file" \
    --capture "$menu_capture" \
    --kind menu \
    --pid "$CAPTURE_PID" \
    --window-id "$menu_window"
  copy_fresh_wallpapers "$menu_wallpaper_marker" "menu-wallpaper"
  local pending_image="$SOURCE_DIR/.menu.png.capture.$$"
  local pending_ready="$SOURCE_DIR/.menu.ready.json.capture.$$"
  cp "$menu_capture" "$pending_image"
  cp "$ready_file" "$pending_ready"
  mv "$pending_image" "$SOURCE_DIR/menu.png"
  mv "$pending_ready" "$SOURCE_DIR/menu.ready.json"
  stop_capture_app
}

if [[ "$RUN_MODE" == "compose" ]]; then
  python3 scripts/compose_app_store_screenshots.py \
    --source-dir "$SOURCE_DIR" \
    --output-dir "$OUTPUT_DIR"
  exit 0
fi

stage_menu_fallback
MENU_RECOVERY_NEEDED=1
archive_existing_sources
./scripts/prepare_appstore_project.py "$SCREENSHOT_PROJECT_SPEC"
xcodegen generate \
  --spec "$SCREENSHOT_PROJECT_SPEC" \
  --project . \
  --project-root .
xcodebuild -project EaselWall.xcodeproj \
  -scheme EaselWall \
  -configuration Screenshot \
  -destination 'platform=macOS' \
  -derivedDataPath "$DERIVED_DATA" \
  build
xcrun swiftc \
  -module-cache-path "$RUN_DIR/swift-module-cache" \
  scripts/easelwall_capture_support.swift \
  -o "$SUPPORT_TOOL"
"$SUPPORT_TOOL" save-wallpapers "$WALLPAPER_STATE"
WALLPAPERS_SAVED=1

# Keep screenshot-mode selection offline by marking every remote-only painting
# as already seen. The remaining candidates are the 30 bundled paintings.
remote_ids=()
while IFS= read -r painting_id; do
  [[ -n "$painting_id" ]] && remote_ids+=("$painting_id")
done < <(jq -r '.paintings[] | select(.localFilename == null) | .id' Resources/Paintings/catalog.json)
CFFIXED_USER_HOME="$RUN_HOME" defaults write "$CAPTURE_BUNDLE_ID" matEnabled -bool true
CFFIXED_USER_HOME="$RUN_HOME" defaults write "$CAPTURE_BUNDLE_ID" matColorHex -string F5F0EB
CFFIXED_USER_HOME="$RUN_HOME" defaults write "$CAPTURE_BUNDLE_ID" matSpacing -string gallery
CFFIXED_USER_HOME="$RUN_HOME" defaults write "$CAPTURE_BUNDLE_ID" uniquePerDisplay -bool true
CFFIXED_USER_HOME="$RUN_HOME" defaults write "$CAPTURE_BUNDLE_ID" launchAtLogin -bool false
CFFIXED_USER_HOME="$RUN_HOME" defaults write "$CAPTURE_BUNDLE_ID" paintingHistory -array "${remote_ids[@]}"

WALLPAPER_MARKER="$RUN_DIR/wallpaper-start"
touch "$WALLPAPER_MARKER"
capture_settings_tab appearance render
for tab in "${CAPTURE_TABS[@]:1}"; do
  capture_settings_tab "$tab"
done
capture_menu

if ! cleanup_resources; then
  trap - EXIT
  report_recovery_state
  exit 1
fi
arm_signal_traps

python3 scripts/compose_app_store_screenshots.py \
  --source-dir "$SOURCE_DIR" \
  --output-dir "$OUTPUT_DIR"

ignore_cleanup_signals
if ! finalize_safe_cleanup; then
  trap - EXIT
  echo "Screenshots were composed, but private-run cleanup or lock release failed." >&2
  echo "Inspect $RUN_DIR and $LOCK_DIR before retrying." >&2
  exit 1
fi
trap - EXIT

echo "Real App Store screenshots are ready in $OUTPUT_DIR"
echo "Review every PNG visually before any upload."
