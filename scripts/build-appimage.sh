#!/usr/bin/env bash
# Build MOG-Client-x86_64.AppImage into ./dist (version is embedded, not in the filename).
#
#   scripts/build-appimage.sh [version]      (default: version from pyproject.toml)
#
# Needs: python3 (>= 3.10) with venv, curl. Everything else (PyInstaller,
# PySide6, appimagetool) is fetched into ./build. Build on the oldest distro
# you want to support: the AppImage bundles Python and Qt but uses the host's glibc.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD
VERSION=${1:-$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")}
ARCH=${ARCH:-x86_64}
APPIMAGETOOL_URL=${APPIMAGETOOL_URL:-https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage}

rm -rf build/appimage dist/MOG-Client-*.AppImage
mkdir -p build dist
# MOG_UPDATE_METHOD=none builds without self-update (e.g. for a Flatpak).
printf '__version__ = "%s"\nUPDATE_METHOD = "%s"\n' "$VERSION" "${MOG_UPDATE_METHOD:-appimage}" >mog_client/_version.py

python3 -m venv build/venv
build/venv/bin/pip install --quiet --upgrade pip
build/venv/bin/pip install --quiet ".[gui,build]" patchelf

build/venv/bin/pyinstaller --noconfirm --clean --onedir --name mog-client \
  --distpath build/appimage/pyi --workpath build/appimage/work --specpath build/appimage \
  --paths "$ROOT" packaging/entry.py

APPDIR=build/appimage/MOG-Client.AppDir
mkdir -p "$APPDIR/usr/bin"
cp -a build/appimage/pyi/mog-client/. "$APPDIR/usr/bin/"

# Some Python builds (e.g. the ones GitHub's setup-python ships) mark libpython
# as needing an executable stack, which current glibc refuses to load
# ("cannot enable executable stack as shared object requires"). Nothing in it
# actually needs one, so clear the flag on every bundled library.
find "$APPDIR/usr/bin" -type f -name '*.so*' ! -type l -print0 |
  while IFS= read -r -d '' lib; do
    build/venv/bin/patchelf --clear-execstack "$lib" 2>/dev/null || true
  done
cp packaging/mog-client.desktop "$APPDIR/mog-client.desktop"
cp packaging/mog-client.png "$APPDIR/mog-client.png"
ln -sf mog-client.png "$APPDIR/.DirIcon"
cat >"$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/bin/mog-client" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

TOOL=${APPIMAGETOOL:-build/appimagetool.AppImage}
if [ ! -x "$TOOL" ]; then
  curl -fsSL "$APPIMAGETOOL_URL" -o "$TOOL"
  chmod +x "$TOOL"
fi

OUT="dist/MOG-Client-$ARCH.AppImage"
# --appimage-extract-and-run: works where FUSE is unavailable (CI, containers).
ARCH=$ARCH "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"
echo "Built $OUT"
