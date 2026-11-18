#!/usr/bin/env bash
# Adds new .deb/.rpm files to the apt + dnf repositories kept on the gh-pages branch and re-signs them.
#   scripts/publish-repos.sh <dist-dir> <site-dir>
# Needs: apt-ftparchive (apt-utils), createrepo_c, gpg with the signing key imported.
# Env:   GPG_KEY_ID, GPG_PASSPHRASE, PAGES_URL (e.g. https://you.github.io/live-clipper)
set -euo pipefail
DIST="$(cd "$1" && pwd)"; SITE="$(cd "$2" && pwd)"
: "${GPG_KEY_ID:?}" "${PAGES_URL:?}"
GPG=(gpg --batch --yes --pinentry-mode loopback --passphrase "${GPG_PASSPHRASE:-}" -u "$GPG_KEY_ID")
KEEP=5

mkdir -p "$SITE/deb/pool/main" "$SITE/rpm"
cp "$DIST"/*.deb "$SITE/deb/pool/main/"
cp "$DIST"/*.rpm "$SITE/rpm/"
ls -t "$SITE"/deb/pool/main/*.deb | tail -n +$((KEEP + 1)) | xargs -r rm -f
ls -t "$SITE"/rpm/*.rpm | tail -n +$((KEEP + 1)) | xargs -r rm -f

# ---- apt (Debian/Ubuntu): dists/stable/main/binary-amd64/Packages + signed Release/InRelease
cd "$SITE/deb"
mkdir -p dists/stable/main/binary-amd64
apt-ftparchive packages pool > dists/stable/main/binary-amd64/Packages
gzip -kf dists/stable/main/binary-amd64/Packages
apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=live-clipper -o APT::FTPArchive::Release::Label=live-clipper \
  -o APT::FTPArchive::Release::Suite=stable -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Architectures=amd64 -o APT::FTPArchive::Release::Components=main \
  release dists/stable > dists/stable/Release
"${GPG[@]}" --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
"${GPG[@]}" --clearsign -o dists/stable/InRelease dists/stable/Release

# ---- dnf (Fedora/RHEL): repodata + signed repomd.xml (the .rpm files are signed by nfpm)
cd "$SITE/rpm"
createrepo_c --update .
"${GPG[@]}" --armor --detach-sign -o repodata/repomd.xml.asc repodata/repomd.xml

# ---- public key, repo file, landing page
gpg --armor --export "$GPG_KEY_ID" > "$SITE/gpg.key"
cat > "$SITE/rpm/live-clipper.repo" <<REPO
[live-clipper]
name=Live Clipper
baseurl=${PAGES_URL}/rpm
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=${PAGES_URL}/gpg.key
REPO
cat > "$SITE/index.html" <<HTML
<!doctype html><meta charset="utf-8"><title>Live Clipper packages</title>
<body style="font-family:system-ui;max-width:760px;margin:40px auto;line-height:1.5">
<h1>Live Clipper packages</h1>
<h2>Fedora (dnf)</h2><pre>sudo curl -fsSL -o /etc/yum.repos.d/live-clipper.repo ${PAGES_URL}/rpm/live-clipper.repo
sudo dnf install live-clipper
systemctl --user enable --now live-clipper</pre>
<h2>Ubuntu / Debian (apt)</h2><pre>sudo install -d /etc/apt/keyrings
curl -fsSL ${PAGES_URL}/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/live-clipper.gpg
echo "deb [signed-by=/etc/apt/keyrings/live-clipper.gpg] ${PAGES_URL}/deb stable main" | sudo tee /etc/apt/sources.list.d/live-clipper.list
sudo apt update && sudo apt install live-clipper
systemctl --user enable --now live-clipper</pre>
<h2>Windows</h2><p>Download the latest <b>setup.exe</b> from the GitHub Releases page.</p></body>
HTML
touch "$SITE/.nojekyll"
echo "repositories updated in $SITE"
