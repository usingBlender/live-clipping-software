#!/bin/sh
echo "Live Clipper: preparing Python and dependencies (first install downloads ~1-2 GB)..."
if /opt/live-clipper/setup-env.sh; then
  echo "Live Clipper is installed."
  echo "  Start it now and at every login:  systemctl --user enable --now live-clipper"
  echo "  Then open http://127.0.0.1:8787"
  echo "  A running copy restarts itself into the new version within a minute."
else
  echo "WARNING: dependency download failed. Re-run:  sudo /opt/live-clipper/setup-env.sh" >&2
fi
exit 0
