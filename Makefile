.PHONY: build release dmg sign notarize sha256 clean generate-project generate-appstore-project validate-release-version archive export-appstore upload-appstore screenshots screenshots-clean appstore-marketing-screenshots app-store-report-status app-store-report-bootstrap app-store-report-snapshot app-store-report-fetch app-store-report marketing-videos marketing-video

APP_NAME = EaselWall
BUNDLE_ID = com.ntindle.EaselWall
SCHEME = EaselWall
BUILD_DIR = build
RELEASE_DIR = $(BUILD_DIR)/release
VERSION ?= $(shell git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo "0.0.0-dev")
# Direct, App Store, and local builds share RUN.CHANNEL.ATTEMPT formatting.
# RUN is UTC seconds since 2025-01-01; CI fixes channels to 1 (direct) and 2
# (App Store), while local builds default to 0. Override these for a
# reproducible or channel-specific local build.
SHIPPED_BUILD_FLOOR = 837975
BUILD_RUN ?= $(shell current_epoch=$$(date -u +%s); echo $$((current_epoch - 1735689600)))
BUILD_CHANNEL ?= 0
BUILD_ATTEMPT ?= 0
override BUILD_NUM := $(BUILD_RUN).$(BUILD_CHANNEL).$(BUILD_ATTEMPT)
DMG_NAME = $(APP_NAME)-$(VERSION).dmg
IDENTITY ?= Developer ID Application

generate-project:
	xcodegen generate

APPSTORE_PROJECT_SPEC = $(BUILD_DIR)/project-appstore.yml

generate-appstore-project:
	./scripts/prepare_appstore_project.py "$(APPSTORE_PROJECT_SPEC)"
	xcodegen generate --spec "$(APPSTORE_PROJECT_SPEC)" --project . --project-root .

validate-release-version:
	@build_num="$(BUILD_NUM)"; \
	if ! printf '%s\n' "$(VERSION)" | grep -Eq '^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$$'; then \
		echo "VERSION must use canonical MAJOR.MINOR.PATCH components from 0 to 999 (got: $(VERSION))" >&2; \
		exit 1; \
	elif ! printf '%s\n' "$(BUILD_RUN)" | grep -Eq '^(0|[1-9][0-9]*)$$'; then \
		echo "BUILD_RUN must be UTC seconds since 2025-01-01" >&2; \
		exit 1; \
	elif [ "$(BUILD_RUN)" -le "$(SHIPPED_BUILD_FLOOR)" ]; then \
		echo "Build run $(BUILD_RUN) must exceed shipped build $(SHIPPED_BUILD_FLOOR)" >&2; \
		exit 1; \
	elif ! printf '%s\n' "$(BUILD_CHANNEL)" | grep -Eq '^(0|[1-9][0-9]*)$$'; then \
		echo "BUILD_CHANNEL must be canonical numeric" >&2; \
		exit 1; \
	elif ! printf '%s\n' "$(BUILD_ATTEMPT)" | grep -Eq '^(0|[1-9][0-9]*)$$'; then \
		echo "BUILD_ATTEMPT must be numeric" >&2; \
		exit 1; \
	elif ! printf '%s\n' "$(BUILD_NUM)" | grep -Eq '^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*)){0,2}$$'; then \
		echo "BUILD_NUM must contain 1-3 numeric components" >&2; \
		exit 1; \
	elif [ "$${#build_num}" -gt 18 ]; then \
		echo "BUILD_NUM must be at most 18 characters" >&2; \
		exit 1; \
	fi

build: generate-project
	xcodebuild -project EaselWall.xcodeproj \
		-scheme $(SCHEME) \
		-configuration Debug \
		-destination 'platform=macOS' \
		build

release: validate-release-version generate-project
	xcodebuild -project EaselWall.xcodeproj \
		-scheme $(SCHEME) \
		-configuration Release \
		-destination 'platform=macOS' \
		-derivedDataPath $(BUILD_DIR)/DerivedData \
		MARKETING_VERSION="$(VERSION)" \
		CURRENT_PROJECT_VERSION="$(BUILD_NUM)" \
		build
	@mkdir -p $(RELEASE_DIR)
	@cp -R "$(BUILD_DIR)/DerivedData/Build/Products/Release/$(APP_NAME).app" "$(RELEASE_DIR)/"
	@echo "Built: $(RELEASE_DIR)/$(APP_NAME).app"

sign: release
	codesign --force --deep --options runtime \
		--sign "$(IDENTITY)" \
		--entitlements EaselWall.entitlements \
		"$(RELEASE_DIR)/$(APP_NAME).app"
	@echo "Signed: $(RELEASE_DIR)/$(APP_NAME).app"

dmg: release
	@rm -f "$(BUILD_DIR)/$(DMG_NAME)"
	hdiutil create -volname "$(APP_NAME)" \
		-srcfolder "$(RELEASE_DIR)" \
		-ov -format UDZO \
		"$(BUILD_DIR)/$(DMG_NAME)"
	@echo "Created: $(BUILD_DIR)/$(DMG_NAME)"

notarize: dmg sign
	codesign --sign "$(IDENTITY)" "$(BUILD_DIR)/$(DMG_NAME)"
	xcrun notarytool submit "$(BUILD_DIR)/$(DMG_NAME)" \
		--keychain-profile "EaselWall" --wait
	xcrun stapler staple "$(BUILD_DIR)/$(DMG_NAME)"
	@echo "Notarized: $(BUILD_DIR)/$(DMG_NAME)"

sha256:
	@shasum -a 256 "$(BUILD_DIR)/$(DMG_NAME)"

clean:
	rm -rf $(BUILD_DIR)
	xcodebuild -project EaselWall.xcodeproj \
		-scheme $(SCHEME) \
		clean 2>/dev/null || true

# --- Mac App Store submission ---
# Override with: make archive APPSTORE_IDENTITY="Apple Distribution: Your Name (TEAMID)"
APPSTORE_IDENTITY ?= Apple Distribution
ARCHIVE_PATH = $(BUILD_DIR)/$(APP_NAME).xcarchive
EXPORT_PATH = $(BUILD_DIR)/appstore-export
EXPORT_OPTIONS = $(BUILD_DIR)/ExportOptions-AppStore.plist

archive: validate-release-version generate-appstore-project
	xcodebuild -project EaselWall.xcodeproj \
		-scheme $(SCHEME) \
		-configuration AppStore \
		-destination 'generic/platform=macOS' \
		-archivePath "$(ARCHIVE_PATH)" \
		MARKETING_VERSION="$(VERSION)" \
		CURRENT_PROJECT_VERSION="$(BUILD_NUM)" \
		archive
	@echo "Archived: $(ARCHIVE_PATH)"

$(EXPORT_OPTIONS):
	@mkdir -p $(BUILD_DIR)
	@printf '%s\n' \
	  '<?xml version="1.0" encoding="UTF-8"?>' \
	  '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	  '<plist version="1.0">' \
	  '<dict>' \
	  '  <key>method</key><string>app-store-connect</string>' \
	  '  <key>destination</key><string>upload</string>' \
	  '  <key>signingStyle</key><string>automatic</string>' \
	  '</dict>' \
	  '</plist>' > $(EXPORT_OPTIONS)

export-appstore: archive $(EXPORT_OPTIONS)
	xcodebuild -exportArchive \
		-archivePath "$(ARCHIVE_PATH)" \
		-exportPath "$(EXPORT_PATH)" \
		-exportOptionsPlist "$(EXPORT_OPTIONS)" \
		-allowProvisioningUpdates
	@echo "Exported to: $(EXPORT_PATH)"

# --- App Store screenshots ---
# Override count with: make screenshots COUNT=8
COUNT ?= 5

screenshots:
	@./scripts/screenshot.sh auto $(COUNT)
	@echo ""
	@echo "Next steps:"
	@echo "  - For Settings window:  ./scripts/screenshot.sh window  (then click it)"
	@echo "  - For menu dropdown:    ./scripts/screenshot.sh menubar"
	@echo "  - All output:           ls screenshots/"

screenshots-clean:
	@./scripts/screenshot.sh clean

# Render the five deterministic, privacy-safe 1280x800 upload assets.
appstore-marketing-screenshots:
	@python3 scripts/render_app_store_screenshots.py

# --- App Store proceeds reporting ---
# Credentials are inherited from the caller's environment. `report` itself is
# offline and can visualize previously fetched data without credentials.
app-store-report-status:
	@python3 scripts/app_store_reports.py status

app-store-report-bootstrap:
	@python3 scripts/app_store_reports.py bootstrap

app-store-report-snapshot:
	@python3 scripts/app_store_reports.py fetch --access-type ONE_TIME_SNAPSHOT

app-store-report-fetch:
	@python3 scripts/app_store_reports.py fetch --access-type ONGOING

app-store-report:
	@python3 scripts/app_store_reports.py report

# --- Organic marketing assets ---
# Render the complete silent 9:16 master batch. Add licensed platform audio
# and approve each post in TikTok before publishing.
marketing-videos:
	@python3 scripts/render_social_videos.py --all

# Render one concept: make marketing-video VIDEO=museum-morning
marketing-video:
	@python3 scripts/render_social_videos.py --id "$(VIDEO)"

# Direct CLI upload using App Store Connect API key.
# Requires env: APP_STORE_CONNECT_API_KEY_ID, APP_STORE_CONNECT_API_ISSUER_ID,
# APP_STORE_CONNECT_API_KEY_PATH (path to AuthKey_XXXX.p8).
upload-appstore: export-appstore
	xcrun altool --upload-app \
		-f "$(EXPORT_PATH)/$(APP_NAME).pkg" \
		--type macos \
		--apiKey "$(APP_STORE_CONNECT_API_KEY_ID)" \
		--apiIssuer "$(APP_STORE_CONNECT_API_ISSUER_ID)"
