.PHONY: build release dmg sign notarize sha256 clean generate-project archive export-appstore upload-appstore screenshots screenshots-clean

APP_NAME = EaselWall
BUNDLE_ID = com.ntindle.EaselWall
SCHEME = EaselWall
BUILD_DIR = build
RELEASE_DIR = $(BUILD_DIR)/release
VERSION ?= $(shell git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo "0.0.0-dev")
DMG_NAME = $(APP_NAME)-$(VERSION).dmg
IDENTITY ?= Developer ID Application

generate-project:
	xcodegen generate

build: generate-project
	xcodebuild -project EaselWall.xcodeproj \
		-scheme $(SCHEME) \
		-configuration Debug \
		-destination 'platform=macOS' \
		build

release: generate-project
	xcodebuild -project EaselWall.xcodeproj \
		-scheme $(SCHEME) \
		-configuration Release \
		-destination 'platform=macOS' \
		-derivedDataPath $(BUILD_DIR)/DerivedData \
		MARKETING_VERSION=$(VERSION) \
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

archive: generate-project
	xcodebuild -project EaselWall.xcodeproj \
		-scheme $(SCHEME) \
		-configuration AppStore \
		-destination 'generic/platform=macOS' \
		-archivePath "$(ARCHIVE_PATH)" \
		MARKETING_VERSION=$(VERSION) \
		CURRENT_PROJECT_VERSION=$(shell echo $(VERSION) | tr -dc '0-9' | head -c 4) \
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

# Direct CLI upload using App Store Connect API key.
# Requires env: APP_STORE_CONNECT_API_KEY_ID, APP_STORE_CONNECT_API_ISSUER_ID,
# APP_STORE_CONNECT_API_KEY_PATH (path to AuthKey_XXXX.p8).
upload-appstore: export-appstore
	xcrun altool --upload-app \
		-f "$(EXPORT_PATH)/$(APP_NAME).pkg" \
		--type macos \
		--apiKey "$(APP_STORE_CONNECT_API_KEY_ID)" \
		--apiIssuer "$(APP_STORE_CONNECT_API_ISSUER_ID)"
