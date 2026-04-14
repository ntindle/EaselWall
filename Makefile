.PHONY: build release dmg sign notarize sha256 clean generate-project

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
