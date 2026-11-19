#!/usr/bin/env sh
# Run from a source checkout on Linux (development). Packaged installs use: systemctl --user start live-clipper
cd "$(dirname "$0")" && unset ANTHROPIC_API_KEY && exec .venv/bin/python -m web --supervise "$@"
