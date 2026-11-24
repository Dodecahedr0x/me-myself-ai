# Running MeMyselfAI on a VPS

A VPS instance runs the same service as the Mac app, without a window. It keeps polling your accounts, running the orchestrator and working on tasks while your laptop is closed. You reach its dashboard through an SSH tunnel. The service never opens a public port. This is phase 1 of [instances, sync and sharing work](specs/instances-and-sync.md). A VPS and a laptop do not sync with each other yet.

Requirements: a Linux server you control with at least 2 GB of memory, SSH access, and either Docker or a Debian or Ubuntu system with systemd. Put the server's disk on encrypted storage if your provider offers it. Activity and drafts are stored on it in plain SQLite.

## Option A: systemd

On the server, from a checkout of this repository:

```sh
sudo ./deploy/install-vps.sh
```

The script installs Python 3, the `cryptography` package, Git and Node.js. It creates a `memyselfai` system user, copies the app to `/opt/memyselfai` and keeps the workspace in `/var/lib/memyselfai/workspace`. It then starts `memyselfai.service` on loopback port 8801. Running it again updates the code and keeps your data.

Connector credentials are stored in `vault.json` in the workspace, encrypted with AES-256-GCM. The key is a systemd credential, `memyselfai-vault-key`. When `systemd-creds` is available, the key is stored encrypted in `/etc/credstore.encrypted` and bound to this host's TPM or credential secret. Otherwise it is a root-only file in `/etc/credstore`. Back up the key with the workspace. Without it, the stored credentials cannot be read, and you would need to reconnect the accounts.

## Option B: Docker

```sh
git clone <this repository> memyselfai && cd memyselfai
(umask 077 && openssl rand -hex 32 > vault.key) && sudo chown 10001 vault.key
docker compose up -d --build
```

The container runs as user 10001. The workspace, the agent CLIs you install and their sign-ins live in the `memyselfai-data` volume. The port is published on the server's loopback only. `vault.key` is the credential key: keep it out of the repository (it is ignored) and back it up.

## Opening the dashboard

From your computer:

```sh
./deploy/vps-open.sh you@your-server            # systemd
./deploy/vps-open.sh you@your-server --docker   # Docker; set MEMYSELFAI_COMPOSE_DIR if not ./memyselfai
```

The script opens an SSH tunnel from local port 8801 to the server's loopback port 8801. It then asks the service for a fresh one-time link and opens it in your browser. Use the same port on both ends, because the dashboard rejects requests addressed to another host or port. Each link works once. Run the script again for another browser.

Doing it by hand: `ssh -L 8801:127.0.0.1:8801 you@your-server`. Then, on the server, run `sudo systemctl kill -s USR1 memyselfai` and open the link in `/var/lib/memyselfai/workspace/launch-url`.

## Signing in the agents

Agent CLIs run as the service user, so sign them in as that user. Settings → Agents shows which ones are ready.

```sh
# systemd
sudo -u memyselfai -i npm install -g @anthropic-ai/claude-code @openai/codex
sudo -u memyselfai -i claude auth login
sudo -u memyselfai -i codex login --device-auth
# Docker
docker compose exec memyselfai npm install -g @anthropic-ai/claude-code @openai/codex
docker compose exec -it memyselfai claude auth login
```

Install Hermes with its own installer as the same user, then use the **Sign in** command shown in Settings → Agents. On Linux, Claude Code stores its login in `~/.claude/.credentials.json`, which Hermes also reads. Logins on the VPS are separate from your Mac's.

## What works on a VPS today

| Feature | VPS |
| --- | --- |
| Dashboard, tasks, projects, rules, orchestrator, agents | Yes |
| GitHub with a token, Slack with a token, IMAP mail with an app password | Yes |
| Encrypted credential store | Yes |
| Google browser sign-in | Not yet verified. The consent page opens a browser on the server. Use IMAP mail meanwhile. |
| Local folders, frontmost app, Claude and ChatGPT desktop chats | No. These are Mac-only sources. |
| Native folder picker | No. Type repository paths on the server instead. |
| Sync with your Mac | Not yet (phase 2) |
