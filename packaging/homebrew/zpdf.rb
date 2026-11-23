# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
# Homebrew Cask template for ZPDF (macOS unsigned DMG).
# To install from a local tap or file:
#   brew install --cask ./packaging/homebrew/zpdf.rb

cask "zpdf" do
  version "0.61.8"
  sha256 :no_check

  url "https://github.com/Tempura-446/ZPDF-releases/releases/download/v#{version}/ZPDF-#{version}-macos.dmg"
  name "ZPDF"
  desc "Offline PDF reader, editor, redactor, OCR, and document manager"
  homepage "https://github.com/Tempura-446/ZPDF-releases"

  app "ZPDF.app"

  caveats <<~EOS
    ZPDF is distributed as an unsigned open-distribution app (no Apple Developer fee).
    If macOS Gatekeeper warns on first launch, run:
      xattr -cr /Applications/ZPDF.app
  EOS

  zap trash: [
    "~/Library/Application Support/ZPDF",
    "~/Library/Preferences/io.github.pdfmanager.plist",
  ]
end
