# MeMyselfAI

A macOS dashboard for an agent that picks up work from email, Slack, and GitHub. Each task runs through your selected AI CLI, Claude Code or Codex. The app starts in dry mode and queues external actions for your approval.

## Open the app

Build with the macOS Command Line Tools, then open the app bundle. The desktop host requires Python 3.9 or later and macOS 12 or later. The first build downloads and verifies the Node.js runtime used by the Google connector.

```sh
./scripts/build-app.sh
open build/MeMyselfAI.app
```

To install it, download the latest `MeMyselfAI-macos-arm64.zip` (or `x86_64` for Intel) from [Releases](https://github.com/Dodecahedr0x/me-myself-ai/releases), or move your own build into `/Applications` or `~/Applications`. An installed copy checks GitHub every six hours, installs newer releases and reopens itself. Builds run from `build/` never update. Maintainers publish a release by pushing a tag such as `v0.2.0`. [App updates spec](docs/specs/app-updates.md)

For the browser interface, run `./scripts/run-local.sh` and open the one-time local URL it prints. Closing the desktop window leaves the agent running. Quit MeMyselfAI to stop it.

To run it on a Linux server instead, with Docker or systemd, see [Running MeMyselfAI on a VPS](docs/vps.md).

## Try the workflow

Open a task to read its Markdown context. **Close task** removes it from the open queue and keeps its history under **Tasks → Closed**. In **Settings → Edit ignore list**, select projects or enter GitHub repositories and local checkout paths. Project exclusions include subprojects and their repositories. Removing an entry resumes eligible tasks with fresh approvals.

To use the app on itself, open **Tasks → Improve MeMyselfAI**. **Run checks** exercises the installed dashboard request handlers and GitHub bug workflow against fixtures. **Report a finding** captures a problem you observed. Both put the result in the **MeMyselfAI** project. A failed check links directly to its finding.

Choose **Connect app repository** to use your signed-in GitHub CLI for this repository. An existing connection also works. For fixes, link a local checkout at the published default branch under **Projects → MeMyselfAI**. The app prepares an issue first, then a fix branch, a linked PR and a comment review of that PR's changes. Open the finding in Tasks to approve each write in dry mode and follow its GitHub links. [Self-improvement spec](docs/specs/self-improvement.md)

1. Open **Settings** and load the sample workspace. Select the sample release reply in **Tasks**, inspect its account, destination, and content, then approve. Sample deliveries stay on your machine.
2. Install and sign in to Claude Code or Codex CLI on your Mac. Choose it under **Settings → Agent provider**. The CLI runs locally and sends the supplied task context to its AI provider for inference. You can switch CLIs between runs.
3. General tasks start in **Global**. Under **Projects**, group related work into projects and subprojects, and link their local Git repositories. Each coding task gets a private clone under app storage. It can run tests there while you keep working in your own checkout.
4. Open **Connections**, choose a provider and sign in once for the workspace. GitHub connects your CLI account, discovers its repositories automatically, and tracks issue and pull request activity across them. Use **Browse work** to see open assignments, issues, PRs and review requests. Slack monitors the workspace’s accessible channels. Google lists the services authorized for each account. Other email hosts and manual credentials are available under manual setup.
5. Leave dry mode on while assessing coverage, results and writing style. Set authority by project and decision type under **Decision rules**. When you report a miss, the recovery agent scans all connected sources for the three hours ending at the report time.

**Tasks → Suggested tasks** combines recent activity across connected sources. Under **Connections → Local activity → Choose activity**, select work folders and optionally sample the frontmost app name. The agent considers filenames, modification times, your Git commit subjects and connected messages from the last three hours. The selected CLI can send that evidence to its AI provider. Inspect supporting activity, then start or dismiss a suggestion.

In **Connections → Local activity**, connect **Claude** or **ChatGPT** to find unfinished follow-ups in completed desktop work sessions. The selected AI CLI receives excerpts from the last three hours. Regular chat history can be included by choosing a folder containing the provider’s extracted conversation export. Refresh the export to include newer cloud chats. Results appear in **Tasks → Suggested tasks**.

**Context** shows the schedules and shared context the app's Hermes maintains. MeMyselfAI installs its own pinned [Hermes Agent](https://github.com/NousResearch/hermes-agent) on first start, on your Mac and on a VPS, into the workspace data folder with its own Hermes home. A Hermes you installed yourself, and its `~/.hermes`, are never used or changed. To open the app's copy directly, run `memyselfai-hermes`, for example `memyselfai-hermes dashboard`, or choose **Settings → Agents → Open dashboard**. Hermes can only change local context and schedules, and dispatch tasks. Those tasks still follow dry mode. [Context and schedules spec](docs/specs/context-and-schedules.md), [Hermes install](docs/specs/hermes-orchestrator.md#install)

Tasks, drafts, history, worktrees, and Markdown skills live under `~/Library/Application Support/MeMyselfAI/`. Connector credentials are stored in macOS Keychain. The workflow and style files reflect active revisions. Review style suggestions and revert workflow revisions through **Skills & style**.

**Settings → Agents** shows whether Claude Code, Codex CLI and the Hermes orchestrator are installed and signed in. **Sign in** opens each one's own login in Terminal. Task agents and Hermes act in the app only through its MCP server, scoped to their role and task. [Agents and the app MCP server](docs/specs/agents-and-app-mcp.md)

Claude Code uses its existing CLI sign-in. The isolated Codex worker supports Codex's file-backed sign-in or a provider API key available to the app process. A Codex login stored exclusively in Keychain currently requires another supported authentication method. The adapters have been tested with Claude Code 2.1.328 and Codex CLI 0.166.0.

## Development

### Account sign-in setup

GitHub requires the GitHub CLI (`gh`) on your Mac.

**Connect Slack**

Open **Connections → Connect Slack**, paste a user access token, a refresh token, or both, then choose **Connect workspace**. For a refresh token, use the issuing Slack app’s client ID and, for server-issued tokens, its client secret. MeMyselfAI stores the replacement token pair in Keychain. MeMyselfAI discovers the public and private channels the token can access and refreshes the inventory every 15 minutes.

**Connect Google**

Click **Connections → Connect Google**, choose your account in the browser and approve the Google Workspace extension. The account appears in Connections automatically. Use **Add Google account** for another address. **View log** on each account shows its activity.

The bundled connector reads Gmail, Calendar and Drive, including Docs, Sheets and Slides. In dry mode you can review a reply or deadline event before it is sent to Google. When the publisher provides a direct Google desktop client, the same **Connect Google** button includes read access to Tasks and saved Contacts. Choosing an existing account upgrades its connection in place. [Account connection spec](docs/specs/account-wide-connections.md)

**Publisher setup**

Optional Slack browser sign-in requires a registered MeMyselfAI desktop client. Publishers can generate the desktop app manifest with `python3 -c 'import json, signin; print(json.dumps(signin.slack_manifest(), indent=2))'`. Supply the registered app’s public client ID in the build. Enable PKCE and register `http://localhost:43827/oauth/callback` with the user scopes listed in the [browser sign-in spec](docs/specs/browser-signin.md). To offer sign-in in other workspaces, enable distribution in Slack.

To ship a preconfigured build, put an `oauth-clients.json` file at the repository root before running the build script. Use an `email` object with the Google Desktop app's `client_id` and `client_secret`, and a `slack` object with the Slack app's `client_id`. Either provider can be supplied independently. Use desktop app registration values intended for distribution. The build copies these into the app bundle. User account grants are stored separately in Keychain. See the [browser sign-in spec](docs/specs/browser-signin.md) for provider configuration and permissions.

Run the fixture suite with `python3 -m unittest discover -s tests -v`. The tests use temporary workspaces and simulated provider deliveries. Use `./scripts/run-local.sh --data /path/to/test-workspace` for a separate workspace.

See the [product overview](docs/README.md), [runtime spec](docs/specs/desktop-runtime.md), and [repository rules](AGENTS.md).
