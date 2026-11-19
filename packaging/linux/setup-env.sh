#!/bin/sh
# Creates /opt/live-clipper/.venv with a uv-managed Python 3.12 and the app's dependencies.
# Run automatically after install/upgrade; safe to re-run by hand.
set -e
APP=/opt/live-clipper
export UV_PYTHON_INSTALL_DIR="$APP/python" UV_CACHE_DIR=/var/cache/live-clipper/uv UV_NO_PROGRESS=1
# Ignore system/user uv settings: Fedora's uv config sets python-downloads = "manual", which would stop
# uv from fetching the private Python 3.12 this app uses.
export UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=automatic
"$APP/bin/uv" venv --quiet --allow-existing -p 3.12 "$APP/.venv"
"$APP/bin/uv" pip install --quiet --python "$APP/.venv/bin/python" -r "$APP/requirements.txt"
