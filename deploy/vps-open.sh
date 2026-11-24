#!/bin/sh
# Open the dashboard of a MeMyselfAI VPS through an SSH tunnel. Usage:
#   ./deploy/vps-open.sh user@server            (systemd install)
#   ./deploy/vps-open.sh user@server --docker   (docker compose install, run from the compose folder)
# The tunnel uses the same local port as the server, because the dashboard checks the Host header.
set -eu
HOST="${1:?Usage: vps-open.sh user@server [--docker]}"
PORT="${MEMYSELFAI_PORT:-8801}"
COMPOSE_DIR="${MEMYSELFAI_COMPOSE_DIR:-memyselfai}"

if ! nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
    ssh -f -N -o ExitOnForwardFailure=yes -L "$PORT:127.0.0.1:$PORT" "$HOST"
fi

if [ "${2:-}" = "--docker" ]; then
    URL=$(ssh "$HOST" "cd '$COMPOSE_DIR' && docker compose kill -s USR1 memyselfai >/dev/null && sleep 1 && docker compose exec -T memyselfai cat /data/workspace/launch-url")
else
    URL=$(ssh -t "$HOST" "sudo systemctl kill -s USR1 memyselfai && sleep 1 && sudo cat /var/lib/memyselfai/workspace/launch-url" | tr -d '\r')
fi

echo "$URL"
case "$(uname)" in
    Darwin) open "$URL" ;;
    Linux) command -v xdg-open >/dev/null && xdg-open "$URL" ;;
esac
