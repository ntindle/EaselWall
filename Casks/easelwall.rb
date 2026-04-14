# Install: brew install ntindle/easelwall/easelwall

cask "easelwall" do
  version "1.0.0"
  sha256 :no_check

  url "https://github.com/ntindle/EaselWall/releases/download/v#{version}/EaselWall-#{version}.dmg"
  name "EaselWall"
  desc "Daily impressionist painting wallpapers with museum-style mats"
  homepage "https://easelwall.com"

  depends_on macos: ">= :sonoma"

  livecheck do
    url :url
    strategy :github_latest
  end

  app "EaselWall.app"

  zap trash: [
    "~/Library/Application Support/EaselWall",
    "~/Library/Preferences/com.ntindle.EaselWall.plist",
    "~/Library/LaunchAgents/com.ntindle.EaselWall.rotation.plist",
  ]
end
