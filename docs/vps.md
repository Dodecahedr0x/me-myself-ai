# Running MeMyselfAI on a VPS

A VPS instance runs the same service as the Mac app, without a window. It keeps polling your accounts, running the orchestrator and working on tasks while your laptop is closed. You reach its dashboard through an SSH tunnel. The service never opens a public port. It can also pair with the Mac app so both share one workspace. See [pairing with your laptop](#pairing-with-your-laptop) and [instances, sync and sharing work](specs/instances-and-sync.md).

Requirements: a Linux server you control with at least 2 GB of memory, SSH access, and either Docker or a Debian or Ubuntu system with systemd. Put the server's disk on encrypted storage if your provider offers it. Activity and drafts are stored on it in plain SQLite.

## Easiest: from the Mac app

Open **Setup** in the Mac app, pick one of the hosts from your SSH config under **Always-on server** and choose **Set up server**. The app copies itself to the server, runs the systemd installer below, opens port 8802 if `ufw` is on, installs your task agent and pairs the server with your Mac. It asks for a sudo password only if the server needs one, and never saves it. Then choose **Sign in on server** to sign in the task agent there. See [guided setup](specs/guided-setup.md#vps-setup-over-ssh). The manual options below do the same by hand.

## Option A: systemd

On the server, from a checkout of this repository:

```sh
sudo ./deploy/install-vps.sh
```

The script installs Python 3, the `cryptography` package, Git and Node.js. It creates a `memyselfai` system user, copies the app to `/opt/memyselfai` and keeps the workspace in `/var/lib/memyselfai/workspace`. It then starts `memyselfai.service` on loopback port 8801, with the sync listener on port 8802. Set `MEMYSELFAI_SYNC_PORT=` (empty) to leave sync off. Running it again updates the code and keeps your data.

Connector credentials are stored in `vault.json` in the workspace, encrypted with AES-256-GCM. The key is a systemd credential, `memyselfai-vault-key`. When `systemd-creds` is available, the key is stored encrypted in `/etc/credstore.encrypted` and bound to this host's TPM or credential secret. Otherwise it is a root-only file in `/etc/credstore`. Back up the key with the workspace. Without it, the stored credentials cannot be read, and you would need to reconnect the accounts.

## Option B: Docker

```sh
git clone <this repository> memyselfai && cd memyselfai
(umask 077 && openssl rand -hex 32 > vault.key) && sudo chown 10001 vault.key
docker compose up -d --build
```

The container runs as user 10001. The workspace, the agent CLIs you install and their sign-ins live in the `memyselfai-data` volume. The dashboard port is published on the server's loopback only. The sync port 8802 is public. `vault.key` is the credential key: keep it out of the repository (it is ignored) and back it up.

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

## Pairing with your laptop

The VPS accepts paired instances on port 8802. Allow that port in the server's firewall. Only instances you pair get past the pairing step, and all sync traffic uses mutual TLS with pinned certificates. Your laptop dials out, so it needs no open port.

1. In the VPS dashboard, open Settings → Instances and choose **Add instance**. A code appears. It works once, for 10 minutes.
2. In the Mac app, open Settings → Instances, choose **Join** and enter `your-server:8802` and the code.
3. That's all. The code proves both ends, so neither side asks you to confirm. Both show each other's fingerprint in Instances if you want to check it.

Tasks, projects, rules, activity and logins from the app's credential store now sync both ways within seconds. Each task, connection and the orchestrator has one owner instance, shown on its row. Only the owner works on it, polls it or sends for it. Connections and the orchestrator move to the VPS by themselves, because an always-on instance is preferred. The orchestrator moves only once Hermes is ready there. Approving a draft on either instance sends it from the task's owner.

**Splitting work.** Each instance runs at most its number of agent slots at once. The default is 2 on a Mac and the CPU count minus one on a server. When the instance that owns a queued task has no free slot, it hands the task to a paired instance that has one, and an idle instance asks for waiting tasks. In Agent settings, each instance can be set to **Run here first**, **Overflow only** or **Never run agents**. A coding task moves with its worktree, including uncommitted edits, to another instance. If that instance has no checkout of the repository, it clones one with its own Git credentials. **Add checkout** under Settings → Instances lets it reuse a clone you already have. Tasks from local folders or desktop chats, and Google browser-profile sources, stay where they started. **Run on…** in a task's detail moves one task by hand.

**If an instance is lost.** When an instance has been silent for 15 minutes, or right after you remove it, the best remaining instance takes over its work automatically. You are not asked. Work tied to its local folders stays with it, and a coding task whose worktree was only on that instance restarts from a fresh worktree. Anything that was being sent is marked for you to check. A draft it had prepared waits for your approval, even where rules would send it automatically. If the instance comes back, it stops working on what was taken over.

**Sleep and shutdown.** Before the Mac sleeps or the app quits, it hands its tasks, connections and the orchestrator to the VPS, holding sleep for up to 25 seconds. A task moves only if the VPS has a signed-in task CLI, and the orchestrator only if the VPS has Hermes ready. Stopping the VPS hands its items to the laptop in the same way, if the laptop is connected.

Logins held by other tools do not sync: `gh` sign-ins, Google sign-ins through a browser profile, and AI subscriptions. Sign those in on each instance. **Remove** stops syncing with an instance and rotates the workspace key. The removed instance keeps the logins it had, so reconnect each account afterwards to revoke them.

## What works on a VPS today

| Feature | VPS |
| --- | --- |
| Dashboard, tasks, projects, rules, orchestrator, agents | Yes |
| GitHub with a token, Slack with a token, IMAP mail with an app password | Yes |
| Encrypted credential store | Yes |
| Google browser sign-in | Not yet verified. The consent page opens a browser on the server. Use IMAP mail meanwhile. |
| Local folders, frontmost app, Claude and ChatGPT desktop chats | No. These are Mac-only sources. |
| Native folder picker | No. Type repository paths on the server instead. |
| Sync with your Mac | Yes: pairing, shared tasks and logins, splitting queued work by free agent slots, and handing work over before the laptop sleeps or quits |
