#!/bin/sh
# Browser fallback. The service prints a one-time local dashboard URL.
set -eu
cd "$(dirname "$0")/.."
for python in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)" /usr/bin/python3; do
    if [ -x "$python" ] && "$python" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
        exec "$python" -B server.py "$@"
    fi
done
echo "Install Python 3.9 or newer and run this script again." >&2
exit 1
