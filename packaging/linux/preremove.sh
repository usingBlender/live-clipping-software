#!/bin/sh
# deb passes "remove"/"upgrade", rpm passes 0 (erase) or 1 (upgrade). Only clean up on real removal.
case "$1" in
  remove|purge|0) rm -rf /opt/live-clipper/.venv /opt/live-clipper/python /var/cache/live-clipper ;;
esac
exit 0
