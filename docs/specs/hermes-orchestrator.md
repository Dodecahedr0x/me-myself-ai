# Hermes orchestrator

**Status** — Partly implemented. See [implementation status](#implementation-status). Covers [US-23, US-24 and US-30](../user-stories.md). Runs the passes in [proactive discovery](proactive-discovery.md). Shares setup, capabilities and the app MCP server with every other agent, as described in [agents, capabilities and the app MCP server](agents-and-app-mcp.md).

## Intent

One long-lived agent watches the whole workspace. It reads connected accounts, decides what needs action, and dispatches each item to a freshly spawned task agent. It also proposes projects, keeps track of running work, and answers the user. The user can prompt it in plain language and get updates from it. That agent is [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research. Hermes is MIT licensed and has persistent memory, self-improving skills, subagents, MCP tools and an Agent Client Protocol (ACP) server.

Hermes is part of the product. The user never installs, configures or updates it by hand. Hermes decides what should happen. Task agents in Claude Code or Codex do the work, one fresh agent per task. The app enforces authority and performs every send.

```mermaid
flowchart LR
    User[User prompt or option] --> Thread[Orchestrator thread]
    Thread --> Hermes[Hermes via ACP]
    Hermes -->|App MCP: read| Store[(Activity, tasks, projects, rules)]
    Hermes -->|App MCP: dispatch_task| App[App]
    App -->|Spawn with scoped session| Workers[Claude Code or Codex task agents]
    Workers -->|App MCP: action tools| Executor[Action executor]
    Executor -->|Dry mode| Inbox[Review in inbox]
    Executor -->|Rule allows, or approved| Sources[Connected accounts]
    Workers -->|Outcomes| Hermes
    Hermes -->|Updates| Thread
```

## What the app manages

| Concern | Behavior |
| --- | --- |
| Install | On first run and on each new instance, the app installs a Hermes version it pins, into its own data directory. It verifies the download's checksum and uses a private home (`HERMES_HOME`), so it never touches a `~/.hermes` the user already has. Progress and failures appear in Settings → Agents, with a retry. See [Install](#install). |
| Configuration | The app writes Hermes' `config.yaml` from workspace settings. It overwrites any drift on start. |
| Model | Hermes uses the user's Claude or ChatGPT subscription, the same one the task agents use. With the Claude subscription, Hermes (provider `anthropic`) reuses Claude Code's own login. On macOS it reads the Keychain item `Claude Code-credentials`, which can show a one-time macOS access prompt. It writes refreshed tokens back to Claude Code. With the ChatGPT subscription, Hermes needs its own one-time device sign-in into the app's private home (`hermes auth add openai-codex`). It does not reuse `~/.codex/auth.json` automatically. The app offers both from Settings → Agents. An API key is an alternative. Sign-ins belong to each instance and do not sync. See [shared agent setup](agents-and-app-mcp.md#shared-agent-setup). |
| Process | The app runs Hermes one prompt at a time: `hermes -z <prompt> -t memory,todo,session_search,mcp-memyselfai`, resuming the orchestrator's session. Hermes 0.13 ACP mode always enabled its coding toolset (terminal, file writing, browser, code execution), and configuration could not remove it, so ACP is not used. After three failed runs in 10 minutes, the app shows the error on the orchestrator row and retries with backoff: 1, 5, 30 seconds, then every 5 minutes. |
| Health | On start and hourly, the app checks `hermes --version` against the minimum supported version (0.23.3) and runs `hermes mcp test memyselfai` to confirm that Hermes reaches the app server. Failures appear in Settings → Agents and the Instances view. An expired subscription sign-in shows **Sign in again**. |
| Updates | The app updates Hermes only to a version a MemyselfAI release has tested. It never runs `hermes update`. A failed update rolls back to the previous version. |
| Memory and skills | Hermes' `MEMORY.md`, `USER.md`, `SOUL.md` and skills live in the workspace Markdown store. They have revision history and sync between instances. Skill changes Hermes makes follow the existing [improvement review](activity-and-improvement.md). |
| One active copy | Every instance has Hermes installed. Only the instance that owns the orchestrator runs it, under the [ownership protocol](instances-and-sync.md#ownership-protocol). |

## Install

Implemented in `hermes_runtime.py` and tested in `tests/test_hermes_runtime.py`.

- **Where.** Everything is under `<data>/hermes-runtime`: a pinned uv, a uv-managed Python 3.14, the Hermes source and its environment. Hermes' home is `<data>/hermes`. The app looks for Hermes only there, never on `PATH`, so a Hermes the user installed and its `~/.hermes` are neither used nor changed.
- **Pinned and verified.** Hermes 0.23.3 comes from the GitHub release archive of tag `v0.23.3`, and uv 0.13.9 from its release for the platform. Both are checked against SHA-256 values in the code before anything is unpacked. Dependencies come from Hermes' own `uv.lock` (`uv sync --frozen`) with the `anthropic`, `mcp` and `web` extras. A source archive has no git history, so the app writes Hermes' `install-stamp.json` with the pinned version and commit and `updateMechanism: external`, which makes `hermes update` leave the copy to the app. uv ignores the user's uv configuration and keeps its cache and Python under the runtime folder. It deletes the cache after a successful install. Archives with paths or links outside their folder are refused.
- **When.** On every start the app checks the install in the background and installs when the pinned version or its extras are missing. That covers the first launch and app updates. The VPS installer runs the same step as the service user before starting the service. In Docker it happens on first start into the `/data` volume.
- **Updates and rollback.** A new version is installed next to the old one, and `current` switches only after the new `hermes --version` starts with `Hermes Agent v<pinned version>`. A failed install or update keeps the previous version running and shows the error in Settings → Agents with **Retry install**.
- **Platforms.** macOS on Apple silicon and Intel, and Linux on x86_64 and arm64.

### Direct access

The app writes a `memyselfai-hermes` command that runs its Hermes with its private home, for example `memyselfai-hermes dashboard` or `memyselfai-hermes sessions list`. On a Mac it is linked from `~/.local/bin` unless another file has that name. On a VPS the installer adds `/usr/local/bin/memyselfai-hermes`, which runs as the service user. **Settings → Agents → Open dashboard** starts the dashboard in Terminal on a Mac, or shows the command elsewhere. Hermes builds the dashboard on first use with its own Node.js and pnpm, which it downloads into its private home. No system Node.js is needed, but the first build takes about 35 seconds and adds about 1.1 GB to the home. The dashboard binds to loopback only. Using Hermes directly is the user acting; the app's dry mode does not apply there. The app rewrites `config.yaml` on start, so settings changed through Hermes directly can be replaced.

### Kept apart from other Hermes copies

- Hermes keeps per-user gateway locks under `$XDG_STATE_HOME/hermes`, by default `~/.local/state/hermes`, shared by every Hermes of that OS user. The app, the `memyselfai-hermes` command and the sign-in command set `XDG_STATE_HOME` to `<data>/hermes/xdg-state`. As a result, the app's Hermes never shares a lock with the user's own Hermes.
- From 0.23, every `hermes -z` prompt runs through a gateway that Hermes starts itself and that outlives the run. That gateway keeps the MCP connection it opened at start and runs Hermes' own cron scheduler. The orchestrator stops the gateway of its own home before and after each run. It signals only the process that `<data>/hermes/gateway.pid` names, and only when that process runs from `<data>/hermes-runtime`. A gateway of the user's own Hermes is never touched.

Verified on 2026-11-24 with 0.13.0, the first pin:
- A real install on macOS (Apple silicon) took 11 seconds. It reported `Hermes Agent v0.13.0` on Python 3.11.18 and used 339 MB before the cache cleanup. Starting the app on an empty workspace installed it automatically, and Settings → Agents reported it as installed.
- `memyselfai-hermes dashboard` served its page.
- In a clean Debian 12 arm64 container, `deploy/install-vps.sh` installed it as the service user (302 MB), and `memyselfai-hermes --version` worked for root.
- In every case `~/.hermes` was untouched.

- On the user's Ubuntu 26.04 x86_64 VPS, `deploy/install-vps.sh` installed it as the service user (315 MB) next to the user's own Hermes, which kept running untouched. The service restarted on the new code. A run driven by the app reached the app MCP server and registered its tools.

Fixed while verifying: the app wrote Hermes' config with a provider but no model, so every call was rejected with an empty model name. The config now names one: `claude-opus-5-5` for the Claude subscription and `gpt-5.5` for ChatGPT, or the `orchestrator_model` setting. Hermes 0.13 one-shot mode hides errors and exits 0 with no output. The orchestrator therefore reads the last error Hermes logged during the run and shows that.

Verified on 2026-11-24 with 0.23.3, the current pin:
- **Why the pin moved.** With 0.13.0 on the VPS, every Claude subscription call was refused with "Third-party apps now draw from your extra usage, not your plan limits." The user's own Hermes on the same server worked on the plan. Hermes 0.23.3 sends Claude Code's identity headers and `mcp__` tool names on subscription requests, and the same account's calls then count against the plan.
- **macOS (Apple silicon), in a temporary data folder.** The install worked and `memyselfai-hermes --version` reported `Hermes Agent v0.23.3`. `memyselfai-hermes dashboard` built and served its page in 35 seconds, using Node.js 26 and pnpm 12 that it downloaded into its private home.
- **The user's Ubuntu VPS.** The installer upgraded the app's Hermes in place. App-driven runs on the Claude subscription then succeeded: a morning digest, then a sweep that dispatched two tasks and saved five context entries. No gateway was left running afterwards.
- **Fixed while verifying.** The Anthropic SDK moved to an extra. The config needs `_config_version: 54`, without which Hermes refuses to migrate it. A gateway left from one run reused that run's revoked token, or connected between runs, was refused and parked the app's MCP server. The fixes are a fixed token file that the bridge reads for every call, and stopping the gateway around runs. Hermes also stops calling a server after three error results in a row. The app MCP server therefore answers the orchestrator's refused calls as plain results with a `refused` field.

Not verified: the Docker image start path, the macOS app with Claude Code's Keychain login on 0.23.3, and the app bundle build.

## What Hermes can and cannot do

Hermes reaches the workspace only through the [app MCP server](agents-and-app-mcp.md#the-app-mcp-server), with the orchestrator's capabilities. The app writes Hermes' private `config.yaml` with that server as its only MCP server and `approvals.mode: manual`. Each run names its toolsets explicitly.

- **Can** read every connection through the app, dispatch tasks of any declared type, post updates and schedule its own checks. It works autonomously and has no tool that asks the user whether to act.
- **Cannot** call any action tool, so it cannot send, post, comment or change anything outside the app. It cannot approve, change rules, connect accounts or see credentials.
- Its own terminal, file, browser, web, code-execution, messaging, cron and delegation toolsets are not enabled. The app never enables a messaging platform or the HTTP API server. It stops the gateway that one-shot mode starts once each run ends, so Hermes' own cron scheduler and kanban dispatcher never run between app-driven runs. Its approval setting is `manual`, with unattended and cron modes set to `deny`.
- Connected content is data. Text in an email asking Hermes to send, approve or connect something has no authority.

## Dispatching work

When Hermes decides that an item needs action, it calls `dispatch_task` with a task type, project, evidence and the account and destination the work is bound to. The app then:

1. creates the task and assigns an owner instance by capacity, as in [dividing work](instances-and-sync.md#dividing-work).
2. spawns a fresh task agent on the owner, with a session token scoped to that task's evidence and write capability.
3. reports the outcome back to Hermes when the task finishes, waits for the user, or fails.

This is the same for every task type: email replies, chat replies, calendar responses, bug fixes and follow-ups. Each type's details live in its [declaration](agents-and-app-mcp.md#task-types).

## Talking to the orchestrator

- The orchestrator has one permanent thread, pinned at the top of the inbox list as **Orchestrator**. It counts as Needs you only when Hermes raises something.
- **⌘K → Ask the orchestrator**, or **R** on its row, sends a prompt. Examples: "What's on my plate this week?", "Pull everything about the Lisbon trip into a project", "Why is the invoice task stuck?", "Stop suggesting newsletter follow-ups."
- Hermes replies in the thread. When the user asks for work, Hermes dispatches it instead of offering choices.
- **Updates.** Each morning at a time the user sets (default 08:30), Hermes posts a digest: what it found and dispatched, what finished, what is stuck, and what needs the user. It also posts when a task it dispatched finishes or fails. The user can mute digests or change their time.
- Instructions the user gives become entries in Hermes' memory or skills. Both are shown with their revision history and can be reverted.

### Message format and actions

Digests, reports and replies are short, scannable and end in things the user can do in the app. Implemented in `orchestrator.py` (`FORMAT`, `task_refs`) and `ui/app.js` (`chatThread`, `chatActions`).

- **Shape.** Hermes is told to write Markdown for a narrow chat panel. It opens with one sentence saying the most important thing. Bold labels on their own line follow: **Needs you**, **Stuck**, **Done** and **In progress**, in that order. Empty sections are left out. There are no `#` headings, tables or sign-off, and the message stays under about 200 words. A plain question gets a plain answer in the same style.
- **Task links.** Every task Hermes mentions is a link written `[title](task:<id>)`, with the id taken from `list_tasks` or `read_task`. Under **Needs you**, each item starts with the verb the user acts with in the app (Approve, Answer, Retry, Record the outcome, Allow folder access) and says in a few words why.
- **Validation.** When a message is stored, the app keeps the ids of links that name an existing task, in order, at most 12. A link to an unknown id renders as plain text, so Hermes cannot invent a target. The digest also lists every task that needs the user when it is written (approval, question, failure or blocked delivery), even ones Hermes left out, so the digest never misses a decision.
- **In the chat.** A `task:` link renders as an inline button that opens the task: in the Inbox when it needs attention, otherwise in its full details. Under each Hermes message, an action card lists the linked tasks with their current status. Each card offers the task's primary action, the same one the Inbox shows (**Review & approve**, **Answer**, **Allow folder access**, **Retry**, **Record outcome**), plus **Open**. A finished or closed task shows only **Open**. Actions use the existing task controls, so approving still opens the exact version for confirmation and a stale version is refused.
- Only Hermes messages get `task:` links and action cards. Task bodies, questions and activity text keep the stricter renderer that allows web links only.

## Discovery passes

The sweep and the daily project pass in [proactive discovery](proactive-discovery.md) run as prompts to Hermes. The app sends them on its own schedule. The safety limit, deduplication and dismissal learning are enforced by the app's tools, not left to Hermes. When Hermes is unavailable, the existing fresh-CLI discovery continues, and the orchestrator section says why Hermes is waiting.

## Acceptance criteria

- On a clean macOS and a clean Linux instance, first run installs the pinned Hermes into the app's data directory. After the user signs into Claude Code or Codex, Hermes passes its health checks with that subscription, with no API key. An existing `~/.hermes` is unchanged.
- A failed download, bad checksum, failed health check or expired sign-in shows in Settings → Agents with the fixing action. Discovery continues through the fallback.
- Killing the Hermes process restarts it with backoff. Three crashes in 10 minutes show an error on the orchestrator row.
- Every Hermes run names only the memory, todo, session_search and app MCP toolsets. Its config lists the app server as its only MCP server. `hermes mcp test memyselfai` lists only orchestrator tools. A Hermes call to `send_email` is refused.
- With fixture mail, Hermes dispatches an `email_reply` task. A new task agent drafts and sends it through the app, ending in a review card in dry mode. With a rule allowing it, the fixture records one write.
- The safety limit and deduplication hold even when a fixture Hermes dispatches 30 items for the same evidence.
- A fixture email telling the orchestrator to send a reply produces at most a dispatched task whose send goes through review. The connector records zero writes before approval.
- Asking "What's on my plate this week?" returns an answer grounded in fixture tasks, with references. Asking for work dispatches it.
- The morning digest posts once a day at the set time on whichever instance owns the orchestrator, including after ownership moves.
- A digest whose text links one task with `task:<id>` and a made-up id stores only the real id, plus every task awaiting approval, an answer, a retry or a delivery outcome. In the chat the made-up link is plain text, the real one is a button that opens the task, and the action card under the message offers **Review & approve** for a proposal. Clicking it opens the exact version for confirmation.
- Digests and replies follow the shape above: a lead sentence, bold section labels, one bullet per task, no headings or tables.
- Editing Hermes' memory on one instance appears on the other. Reverting a revision restores the earlier text.
- Updating to a broken fixture Hermes version rolls back automatically.

## Open questions

- **Answered for Claude (2026-11-24).** Hermes before 0.23 was classed by Anthropic as a third-party app. Its subscription calls drew from extra usage and were refused without it. From 0.23.3, Hermes identifies its subscription requests the way Claude Code does, and they count against the plan. A future Anthropic change could reclassify it. The orchestrator would then show Anthropic's message, and the fixes would be extra usage, an API key or the ChatGPT subscription. OpenAI's terms for Codex sign-in inside Hermes are still unchecked.
- Hermes' macOS desktop app supports Apple Silicon only. This spec uses only the command-line install. Intel Mac support needs testing.
- Should the orchestrator also be reachable from the user's own Telegram or Signal through Hermes' gateway? That would be a message sent by a third-party service, so it is off in this draft.

## Implementation status

Implemented in `orchestrator.py` and tested in `tests/test_orchestrator.py`. The tests use a stand-in `hermes` executable that connects to the real app MCP server through the bridge.

- **Schedule.** The scheduler checks every second and runs at most one Hermes run at a time:
  - A user prompt runs first.
  - The digest runs once a day after 08:30 local time.
  - A sweep runs when activity or tasks have changed since the last sweep. That happens 60 seconds after the newest activity settles, at most every 2 minutes, and otherwise at least every 15 minutes.
  - Nothing runs when nothing has changed.
- **Each run** gets a fresh orchestrator session token, written to `<data>/hermes/mcp-token` (mode 0600). The token is revoked and the file deleted afterwards. The MCP bridge reads the file for every call, so a bridge that outlives a run holds no authority. The app stops Hermes' gateway before and after the run. The app rewrites Hermes' config and runs `hermes -z` with the `memory`, `todo`, `session_search` and `memyselfai` toolsets. In one-shot mode, an MCP server's toolset is named after the server itself. Runs time out after 10 minutes. Failures retry after 1, 5, 30 and then 60 minutes.
- **Autonomy.** The orchestrator never asks the user to decide whether to act. It has no tool to propose or ask. Its prompt tells it to dispatch anything that plausibly needs action and to let the task agent skip what does not.
- **Safety limit.** `dispatch_task` stops at a daily limit of new tasks (default 50, adjustable up to 500). It counts tasks the orchestrator created since local midnight. Returning an existing task costs nothing.
- **Context.** Sweeps also curate the [shared context and schedules](context-and-schedules.md). A cleanup run follows once a day after 07:00 when an entry is stale.
- **Conversation.** The [Overview dashboard](dashboard.md#overview-dashboard) has a chat with Hermes: status, the latest messages, a message box, quick prompts, a report request, **Sweep now** and **Pause**. New tasks today against the limit is one of its stats. Sweep summaries go to the activity log. Digests and replies go to the thread.
- When Hermes is not ready, runs wait and a prompt gets an answer explaining what to fix.

Not implemented yet:

- **Reactive pass.** The per-event pass still uses the existing fresh-CLI discovery. Both paths create tasks keyed by the activity item, so they never duplicate each other.
- The project pass, a configurable digest time, resuming one Hermes session across runs, and a notification when a dispatched task finishes.
- Running on whichever instance owns the orchestrator. Today it runs on the single local instance.

Live runs with the Claude subscription were verified on the VPS (see [Install](#install)). On a Mac, the first run reads Claude Code's Keychain login, which can show a macOS prompt; that path has not been verified.
