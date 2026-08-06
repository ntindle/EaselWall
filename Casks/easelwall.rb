# Install: brew install ntindle/easelwall/easelwall

cask "easelwall" do
  version "1.0.3"
  sha256 "decc1aaee418eba29c72d40ea601b037ee674a059dc1da5a21fc5fe3a20718db"

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
