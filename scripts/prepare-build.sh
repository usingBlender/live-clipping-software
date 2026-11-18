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
  # Bundle a ready-made Python 3.12 (python-build-standalone) so the installer never has uv download one.
  # uv-managed downloads create a folder junction that some Windows security policies refuse (os error 448).
  PBS_URL=$(python - <<'PY'
import json, os, re, urllib.request
api = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
hdr = {"Accept": "application/vnd.github+json", "User-Agent": "live-clipper-ci"}
if os.environ.get("GITHUB_TOKEN"):
    hdr["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
get = lambda u: json.load(urllib.request.urlopen(urllib.request.Request(u, headers=hdr)))
rel = get(api)
pat = re.compile(r"^cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$")
page = 1
while True:
    assets = get(f"https://api.github.com/repos/astral-sh/python-build-standalone/releases/{rel['id']}/assets?per_page=100&page={page}")
    hit = next((a["browser_download_url"] for a in assets if pat.match(a["name"])), None)
    if hit or not assets:
        print(hit or "")
        break
    page += 1
PY
)
  test -n "$PBS_URL" || { echo "python-build-standalone asset not found"; exit 1; }
  echo "bundling $PBS_URL"
  curl -fsSL "$PBS_URL" -o build/python.tar.gz
  tar -xzf build/python.tar.gz -C build && rm build/python.tar.gz   # -> build/python/python.exe
  test -f build/python/python.exe
fi
echo "build ready: version $VERSION ($TARGET)"
