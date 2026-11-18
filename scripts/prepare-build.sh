#!/usr/bin/env bash
# Prepares build/ for packaging.   scripts/prepare-build.sh <version> <linux|windows>
#  - build/app      : the app files (from git, without CI/dev folders), version stamped
#  - build/uv(.exe) : the uv binary that installs Python + dependencies on the user's machine
#  - fonts/         : Montserrat ExtraBold for captions (SIL Open Font License)
set -euo pipefail
VERSION="${1:?version}"; TARGET="${2:?linux|windows}"
rm -rf build && mkdir -p build/app
git archive HEAD | tar -x -C build/app
rm -rf build/app/.github build/app/packaging build/app/scripts build/app/tests build/app/config.yaml
sed -i "s/^__version__ = .*/__version__ = \"${VERSION}\"/" build/app/common/version.py
grep -q "\"${VERSION}\"" build/app/common/version.py

FONT_URL="https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf"
curl -fsSL "$FONT_URL" -o build/app/fonts/Montserrat-ExtraBold.ttf || echo "::warning::caption font download failed"

UV="https://github.com/astral-sh/uv/releases/latest/download"
if [ "$TARGET" = linux ]; then
  curl -fsSL "$UV/uv-x86_64-unknown-linux-gnu.tar.gz" | tar -xz -C build --strip-components=1 uv-x86_64-unknown-linux-gnu/uv
  test -x build/uv
else
  curl -fsSL "$UV/uv-x86_64-pc-windows-msvc.zip" -o build/uv.zip
  unzip -o -q build/uv.zip uv.exe -d build && rm build/uv.zip
  test -f build/uv.exe
fi
echo "build ready: version $VERSION ($TARGET)"
