#!/bin/sh
# Container entry point. The port is published on the host's loopback only (see docker-compose.yml).
set -eu
mkdir -p "$HOME" /data/workspace
exec python3 -B /app/server.py --data /data/workspace --bind 0.0.0.0 --port 8801 \
    --launch-file /data/workspace/launch-url
