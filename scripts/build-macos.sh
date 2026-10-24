#!/usr/bin/env bash
# Build dist/MOG-Client-macos-<arch>.dmg (an ad-hoc signed "MOG Client.app" inside).
#
#   scripts/build-macos.sh [version]      (default: version from pyproject.toml)
#
# Run it on a Mac: PyInstaller does not cross-compile, and sips, iconutil, codesign and hdiutil are macOS tools.
# Needs python3 (>= 3.10) with venv; PyInstaller and PySide6 are fetched into ./build. The build is for the
# architecture it runs on (arm64 on Apple silicon). It is not notarized, so Gatekeeper asks once: right-click > Open,
# or `xattr -dr com.apple.quarantine "/Applications/MOG Client.app"`.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD
VERSION=${1:-$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")}
ARCH=$(uname -m)
APP_NAME="MOG Client"
BUNDLE_ID=${MOG_BUNDLE_ID:-io.github.MOG-My-Own-Games.MOG-Client}
BASE_VERSION=${VERSION%%-*} # CFBundle versions are numbers: a branch build (0.43.1-feat-x) keeps the number

rm -rf build/macos "dist/MOG-Client-macos-$ARCH.dmg"
mkdir -p build/macos dist
# MOG_UPDATE_METHOD=none is the default: the updater only knows the AppImage and the exe.
printf '__version__ = "%s"\nUPDATE_METHOD = "%s"\n' "$VERSION" "${MOG_UPDATE_METHOD:-none}" >mog_client/_version.py

python3 -m venv build/venv
build/venv/bin/pip install --quiet --upgrade pip
build/venv/bin/pip install --quiet ".[gui,build]"

# The icon: the 256 px PNG at the sizes an .icns holds up to there.
ICONSET=build/macos/mog-client.iconset
mkdir -p "$ICONSET"
for size in 16 32 128 256; do
  sips -z $size $size packaging/mog-client.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) packaging/mog-client.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o build/macos/mog-client.icns

build/venv/bin/pyinstaller --noconfirm --clean --onedir --windowed --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" --icon "$ROOT/build/macos/mog-client.icns" \
  --distpath build/macos/pyi --workpath build/macos/work --specpath build/macos \
  --add-data "$ROOT/mog_client/gui/assets:mog_client/gui/assets" \
  --paths "$ROOT" packaging/entry.py

APP="build/macos/pyi/$APP_NAME.app"
PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$BASE_VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$BASE_VERSION" "$PLIST"
plutil -replace NSHighResolutionCapable -bool true "$PLIST"
plutil -replace LSApplicationCategoryType -string "public.app-category.games" "$PLIST"
# Changing the plist broke the signature PyInstaller put on; sign again (ad hoc, no developer account needed).
codesign --force --deep --sign - "$APP"

STAGE=build/macos/dmg
mkdir -p "$STAGE"
cp -a "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
OUT="dist/MOG-Client-macos-$ARCH.dmg"
hdiutil create -quiet -volname "$APP_NAME" -srcfolder "$STAGE" -format UDZO -ov "$OUT"
echo "Built $OUT"
