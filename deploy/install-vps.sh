#!/bin/sh
# Install MeMyselfAI as a systemd service on a Debian or Ubuntu server. Run as root from a checkout:
#   sudo ./deploy/install-vps.sh
# Re-running updates the code and keeps the workspace, credentials and key. See docs/vps.md.
set -eu

PORT="${MEMYSELFAI_PORT:-8801}"
# Paired instances connect here with mutual TLS. Set MEMYSELFAI_SYNC_PORT= (empty) to turn it off.
SYNC_PORT="${MEMYSELFAI_SYNC_PORT-8802}"
APP=/opt/memyselfai
HOME_DIR=/var/lib/memyselfai
DATA="$HOME_DIR/workspace"
UNIT=/etc/systemd/system/memyselfai.service
SRC=$(cd "$(dirname "$0")/.." && pwd)

if [ "$(id -u)" != 0 ]; then
    echo "Run this script as root, for example with sudo." >&2
    exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-cryptography git ca-certificates curl nodejs npm openssl
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || {
    echo "Python 3.9 or newer is required." >&2; exit 1; }
python3 -c 'import cryptography' || {
    echo "Install the Python 'cryptography' package, then run this script again." >&2; exit 1; }

id memyselfai >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --create-home \
    --shell /bin/bash memyselfai
install -d -o memyselfai -m 700 "$HOME_DIR" "$DATA"
# Agent CLIs installed by the service user (npm -g, Hermes) go to its own home.
cat > "$HOME_DIR/.profile" <<PROFILE
export NPM_CONFIG_PREFIX="$HOME_DIR/.local"
export PATH="$HOME_DIR/.local/bin:\$PATH"
PROFILE
chown memyselfai "$HOME_DIR/.profile"

# Copy only the files the service needs. The code is owned by root and read-only to the service.
rm -rf "$APP.new"
install -d -m 755 "$APP.new"
cp "$SRC"/*.py "$APP.new/"
cp -R "$SRC/ui" "$SRC/vendor" "$APP.new/"
rm -rf "$APP.old"
[ -d "$APP" ] && mv "$APP" "$APP.old"
mv "$APP.new" "$APP"
chmod -R go-w "$APP"

# The credential key. Prefer systemd's encrypted credentials, which bind the key to this host.
KEY_LINE=""
# "systemd-creds setup" creates the host credential secret if missing; it is safe to repeat.
if command -v systemd-creds >/dev/null 2>&1 && systemd-creds setup >/dev/null 2>&1; then
    install -d -m 700 /etc/credstore.encrypted
    if [ ! -f /etc/credstore.encrypted/memyselfai-vault-key ]; then
        openssl rand -hex 32 | systemd-creds encrypt --name=memyselfai-vault-key - \
            /etc/credstore.encrypted/memyselfai-vault-key
    fi
    KEY_LINE="LoadCredentialEncrypted=memyselfai-vault-key:/etc/credstore.encrypted/memyselfai-vault-key"
else
    install -d -m 700 /etc/credstore
    if [ ! -f /etc/credstore/memyselfai-vault-key ]; then
        (umask 077 && openssl rand -hex 32 > /etc/credstore/memyselfai-vault-key)
    fi
    KEY_LINE="LoadCredential=memyselfai-vault-key:/etc/credstore/memyselfai-vault-key"
fi

cat > "$UNIT" <<EOF
[Unit]
Description=MeMyselfAI personal agent
After=network-online.target
Wants=network-online.target

[Service]
User=memyselfai
WorkingDirectory=$HOME_DIR
Environment=HOME=$HOME_DIR
Environment=NPM_CONFIG_PREFIX=$HOME_DIR/.local
Environment=PATH=$HOME_DIR/.local/bin:/usr/local/bin:/usr/bin:/bin
$KEY_LINE
ExecStart=/usr/bin/env python3 -B $APP/server.py --data $DATA --port $PORT --launch-file $DATA/launch-url${SYNC_PORT:+ --sync-port $SYNC_PORT}
Restart=on-failure
RestartSec=5
# Time to hand work to a paired instance before stopping.
TimeoutStopSec=60
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$HOME_DIR
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
EOF

if [ -n "${MEMYSELFAI_NO_SYSTEMD:-}" ]; then
    echo "Installed without starting systemd (MEMYSELFAI_NO_SYSTEMD is set)."
    exit 0
fi
systemctl daemon-reload
systemctl enable memyselfai >/dev/null
systemctl restart memyselfai
rm -rf "$APP.old"

cat <<EOF

MeMyselfAI is running on this server's loopback port $PORT.${SYNC_PORT:+
Paired instances reach it on port $SYNC_PORT. Allow that port in your firewall to sync with your laptop.}

From your computer:
  ./deploy/vps-open.sh <user>@<this-server>

Sign in to an agent CLI on this server (once per CLI), for example:
  sudo -u memyselfai -i npm install -g @anthropic-ai/claude-code && sudo -u memyselfai -i claude auth login
EOF
