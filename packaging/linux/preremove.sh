#!/bin/sh
# deb passes "remove"/"upgrade", rpm passes 0 (erase) or 1 (upgrade). Only clean up on real removal.
case "$1" in
  remove|purge|0) rm -rf /opt/gta-clipper/.venv /opt/gta-clipper/python /var/cache/gta-clipper ;;
esac
exit 0
