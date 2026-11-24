# Guided setup and VPS setup over SSH

**Status** — Implemented in the desktop app, except where marked *planned*. Covers [US-32 and US-33](../user-stories.md). Builds on the [local agent](local-agent.md), [agents and the app MCP server](agents-and-app-mcp.md) and [instances and sync](instances-and-sync.md) specs, and on the [VPS guide](../vps.md).

## Intent

A new user should reach a working agent without reading the README. The app works out what is already done, does what it can on its own, and asks only for what needs the user: a sign-in in a browser or terminal, a choice of account, or a password it is not allowed to keep.

## Setup guide

**Setup** is a page in the sidebar. While setup is incomplete, Home opens with a short card that names the next step and links to the page. A workspace with no connections, projects or tasks opens on Setup the first time. Dismissing the card hides it until a required step stops being ready again.

The guide shows one row per step. Each row has a state the app works out itself on every refresh, a one-line explanation, and at most two actions.

| Step | Ready when | What the app does on its own | What it asks the user |
| --- | --- | --- | --- |
| Task agent | The selected CLI is installed and signed in | Picks the installed CLI (Claude Code first, then Codex), as before | Install the CLI, or sign in: **Sign in** opens Terminal with the exact command |
| Accounts | At least one connection exists | If the GitHub CLI is signed in, offers **Connect GitHub as @login** in one click, reusing that login | Pick Google, Slack or other sources |
| Projects | At least one project has a repository, or the user marks it skipped | — | Add a project, or skip |
| Orchestrator | Hermes is ready | Writes Hermes' configuration (existing behaviour) | Install Hermes or sign in |
| Always-on server *(optional)* | A paired instance exists | Installs, starts and pairs a VPS (below) | Choose an SSH host, and a sudo password if the server needs one |
| Delivery | Always ready | Starts in dry mode | Nothing. The row says every send waits for approval |

Required steps: task agent and accounts. The card counts the required steps that are not ready. Optional steps never keep the card open.

Reading the step states never starts inference and never writes to a connected account. The GitHub one-click connection only reads, like any other connection.

## VPS setup over SSH

The Always-on server row lists the hosts in the user's SSH configuration (`~/.ssh/config` and the files it includes). Wildcard patterns are not hosts and are left out. The user can also type a `user@host` destination. The app resolves each host with `ssh -G`, so the shown address, user and port are what OpenSSH would use.

Choosing **Set up** on a host runs these steps in the background. The guide shows each step's progress and result. The app runs one setup at a time.

1. **Connect.** Open one SSH connection in batch mode, reused by every later step. Check that the server runs Linux with systemd and whether the login is root, has password-free sudo, or needs a sudo password. Detect an existing install.
2. **Ask for a sudo password, only if needed.** The guide shows a password field for this host. The password stays in memory for this run, goes to `sudo` on standard input, and is never written to disk, logged or synced. Cancelling ends the run.
3. **Copy the app.** Stream the service files (`*.py`, `ui`, `vendor`, `deploy`) as a tar archive to a temporary folder in the SSH user's home.
4. **Install.** Run `deploy/install-vps.sh` as root. On a server where it is already installed, this updates the code and keeps the workspace, key and logins. If `ufw` is active, allow the sync port 8802. The app cannot change a provider's cloud firewall.
5. **Install the task agent CLI.** Install the CLI selected on the Mac (`@anthropic-ai/claude-code` or `@openai/codex`) for the service user with npm, unless it is already installed.
6. **Wait for the service** to report active, for up to 60 seconds.
7. **Pair.** Through the SSH connection (`ssh -W` to the server's loopback dashboard port), request a fresh one-time dashboard link from the service, open a session with it and request a pairing code. Then join from the Mac at `<resolved host>:8802`. If the server is already a paired instance at that address, skip this step.
8. **Sign in the agent on the server.** This needs the user, because the subscription login is interactive. **Sign in on server** opens Terminal with `ssh -t <host> sudo -u memyselfai -i claude auth login` (or the Codex equivalent). The row turns ready once the paired server reports the agent as ready.

After pairing, connections and the orchestrator move to the server by themselves, as the instances spec describes.

### Failures

Each failure stops the run, keeps the completed steps' results, and shows what went wrong and the one thing to do about it. **Retry** starts the run again from the first step. Re-running is safe, because the installer is idempotent and pairing is skipped once done.

| Failure | Message |
| --- | --- |
| SSH needs a password, key passphrase or a new host-key confirmation | Connect once from Terminal (`ssh <host>`), add the key to the SSH agent, then retry |
| Not Linux, or no systemd | This server can't run the automatic setup. Use the Docker steps in the VPS guide |
| Wrong sudo password | Ask again for the password |
| Installer or npm fails | The last lines of its output |
| Service not active after 60 seconds | Check `journalctl -u memyselfai` on the server |
| Port 8802 unreachable from the Mac | Allow TCP port 8802 in the provider's firewall, then retry |

### Permission boundaries

- The setup writes only to the chosen server, which the user picked by name. It never sends anything to a connected account.
- It uses the user's own SSH configuration and agent. It stores no SSH keys or passwords. `BatchMode=yes` prevents any hidden prompt.
- The SSH control socket lives in a private temporary folder and is closed when the run ends.
- Pairing uses the existing one-time code and pinned certificates. The code travels only inside the SSH connection.

## Acceptance criteria

- On a fresh workspace, the app opens on Setup. With Claude Code installed and signed in, the task agent row is ready without any click.
- With the GitHub CLI signed in and no connection, one click on **Connect GitHub as @login** adds the account. The accounts row turns ready.
- Home shows the setup card while a required step is not ready, and hides it once dismissed or complete.
- The VPS host list includes hosts from an included SSH config file and leaves out `Host *` patterns.
- A setup run against a fake `ssh` executable goes through connect, copy, install, agent install, wait and pair in order, and records each step's result.
- A server that needs a sudo password pauses the run and resumes with the password on standard input. The password does not appear in the stored run state, activity log or command arguments.
- A non-Linux server, a failed SSH connection and an unreachable sync port each stop the run with the message above. Retry starts again.
- An already-paired server at the same address skips pairing.

Verified with fixture tests and a fake `ssh` executable. A run against a real VPS is *not verified yet*.
