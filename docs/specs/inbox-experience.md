# Inbox, focus card and board

**Status** — Draft, not implemented. Covers [US-20, US-21, US-22 and US-28](../user-stories.md). When implemented, it replaces the Home and Tasks layout in the [desktop workspace](dashboard.md). Connection, project, rule, settings and activity-log behavior in that spec stays in force.

## Intent

The app should feel like an email client for agents. Each task is one row with one agent behind it. A row reaches the inbox only when the agent needs the user. The user can answer it in a few seconds from the keyboard. Agents that are working, waiting on someone else, or snoozed stay out of the inbox. The board shows them.

This model is taken from [Agentbox](https://github.com/savannahfeder/agentbox), an inbox for coding agents. MeMyselfAI differs in two ways. Most rows start from discovery in connected apps instead of being typed by the user. Every external write still follows the [dry-mode contract](task-workflow.md). Agentbox code is GPL-3.0. Copy no code or assets from it. Reuse only interaction patterns.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Row | One task as shown in the inbox or on the board. Existing task records become rows without migration. |
| Thread | The conversation between the user and the task's agents: summaries, questions, replies and proposals in order. Each agent run is still a fresh process. A reply starts a new run with the thread as context. |
| Card | A row opened in focus mode. |
| Option | A numbered next step an agent offers at the end of its message. |
| Instance | One running copy of the app, on the laptop or a VPS. See [instances and sync](instances-and-sync.md). |

## Inbox

The inbox lists rows that need the user, newest-needed first. The [orchestrator](hermes-orchestrator.md) thread is pinned above the list. It holds the user's conversation with the agent that discovers and plans work. Below it the order is:

1. **Urgent.** A row is urgent when a project rule marks its decision kind urgent, a stated deadline is within 24 hours, or the delivery outcome is uncertain. Urgent rows sit under their own heading.
2. **Needs you.** Proposals waiting for approval, questions, failures that need a decision, and finished results the user has not read.
3. **Found for you.** Discovered work and proposed projects that wait for triage. See [proactive discovery](proactive-discovery.md). This section is capped at the daily discovery budget so discovery cannot flood the inbox.

Each row shows two lines. Line one is what the agent needs from the user, in bold, in under 110 characters. Line two is the project, the source with its account (for example "Gmail · work@"), and the instance where the agent ran. Rows with the same title stay distinguishable by these labels, as required today.

Rows leave the inbox when the user answers, approves, dismisses, snoozes or closes them. A row comes back when its agent needs the user again.

## Focus card

Opening a row shows one card:

- **Line one** — the agent's question or the decision it needs, in bold.
- **Summary** — what the agent did and found, in plain sentences, at most about 200 words. The **S** key hides or shows it.
- **Evidence** — the triggering activity with source, account and time, and links to related activity. Connected text renders as text.
- **Proposal** — for an external write: the operation, sending account, destination, full content or field changes, and the action version. This block is never collapsed. This is the existing [review contract](dashboard.md).
- **Changes** — for a coding task, the worktree diff opens beside the card.
- **Options** — one to nine numbered next steps. Exactly one is marked recommended. The agent writes them. The app adds **Dismiss** and **Later** to every card.

An option can do one of three things:

| Option kind | Effect |
| --- | --- |
| Local | Resumes the task with the chosen instruction. Nothing leaves the instance. Example: "Draft a shorter reply". |
| Approve | Confirms one displayed action version for delivery. Only allowed when the card shows that version in full. |
| Close | Marks the task done or dismissed and records the reason. |

Agents produce options as data, not as prose the app parses. The [local agent contract](local-agent.md) gains an `options` field on task results: `id`, `label`, `kind`, `recommended`, and for approvals the `action_version` it confirms. The app rejects a result whose approve option names a version other than the one it is displaying.

## Sending

Approving a write dispatches it immediately, with no delay, through the [connector contract](connectors.md). The card moves to Working, then shows the receipt or the failure. A send cannot be undone, so **E** and the option number act on an approval only when its full proposal is on screen. With several instances, the instance holding the task's lock sends, as described in [instances and sync](instances-and-sync.md#locks).

## Board

**B** switches between the inbox and the board. The board has one column per stage. Task states map to columns:

| Column | Task states |
| --- | --- |
| Found | Proposed (discovered, not triaged) |
| Queued | Proposed and accepted, waiting for agent capacity |
| Working | Preparing, Checking, Sending |
| Needs you | Review, NeedsInput, Uncertain, failed with a retry decision |
| Waiting | Snoozed, waiting on someone else, follow-ups scheduled for later |
| Done | Complete, Skipped, Dismissed (last 7 days by default) |

The user cannot drag a row into a column that implies an external write. Dragging to Done closes the task. Dragging to Queued accepts a found task. Each Working card shows its instance and elapsed time.

## Starting work

**N** opens a compose box. The user types one task in plain language, or pastes a list. Each list item becomes its own row. The app assigns a project by the existing project assignment rules. The user can change it before sending. New rows start Queued and go to the next instance with capacity.

## Later

**L** snoozes a row until a chosen time. Presets: in 3 hours, this evening (18:00), tomorrow morning (09:00), next Monday (09:00), or a custom time in the user's local timezone. A snoozed row sits in Waiting and returns to Needs you at that time on whichever instance is online. Snoozing never pauses an external deadline the agent recorded. A deadline within 24 hours makes the row urgent again.

## Keyboard

| Key | Where | Action |
| --- | --- | --- |
| J / ↓, K / ↑ | Inbox | Move between rows |
| ← → | Board | Move between columns |
| ↵ | Inbox, board | Open the selected row |
| esc | Anywhere | Go back one level: diff, card, list |
| 1–9 | Card | Pick that option |
| E | Card | Pick the recommended option. When it is an approval, the full proposal must be on screen. |
| R | Card | Reply in your own words. The agent resumes with the reply. |
| ⌘↵ | Compose, reply | Send |
| N | Anywhere | New task |
| L | Card, inbox | Later |
| Z | Anywhere | Undo the last dismiss, snooze or close. Sends cannot be undone. |
| S | Card | Show or hide the summary |
| B | Inbox, board | Switch list and board |
| ⌘K | Anywhere | Command palette: every action by name, including connections, projects, instances and settings |

Single-letter keys do nothing while a text field has focus. All actions are also reachable by pointer, and focus stays visible.

## Navigation

A narrow left rail holds Inbox, Board, Activity, Connections, Projects, Instances and Settings. Activity, Connections, Projects and Settings keep their current behavior. Instances is new, see [instances and sync](instances-and-sync.md). The command palette reaches all of them.

## Notifications

When a row enters Urgent or Needs you and the dashboard window is not focused, the desktop app posts one system notification. It names the row's line one and nothing from its content. Notifications are batched to at most one per five minutes per row. The user can turn them off in Settings. A headless VPS instance does not notify. The laptop notifies for rows that reach Needs you on any paired instance.

## First run

1. The app checks for Claude Code and Codex CLI, as today.
2. It installs the [orchestrator](hermes-orchestrator.md) and asks for its model provider.
3. It offers to connect a first source, starting with mail. Skipping is allowed.
4. It asks where agents should run: **This computer**, **This computer and a VPS**, or **A VPS only**. See [instances and sync](instances-and-sync.md).
5. After the first connection, the orchestrator runs a first sweep. It posts the outcome in its thread: what it read, what it found, and options to accept the found tasks.

An explicit sample workspace still demonstrates the whole flow without accounts.

## Acceptance criteria

- With fixture tasks in every state, the inbox lists exactly the Review, NeedsInput, Uncertain, decision-needing failure, unread result and untriaged found rows. Urgent rows come first under their heading. Working and snoozed rows appear only on the board.
- Each row shows line one, project, source with account, and instance. Two rows with the same title are distinguishable.
- Opening a review card shows the account, destination, full content and version. Pressing **E** on it confirms exactly that version, and the fixture connector records one write at once. **Z** afterwards does not affect the send.
- A task result whose approve option names a stale version is rejected and shown as a failure. A revised proposal needs a fresh approval.
- **1–9**, **R**, **L**, **N**, **S**, **B** and **⌘K** perform their actions. Letter keys are inert while typing in a field. Every action is also reachable by pointer.
- Pasting a three-line list into compose creates three rows with independent agents.
- A row snoozed until a time reappears in Needs you at that time after an app restart. A snoozed row whose deadline falls within 24 hours becomes urgent.
- The board places each fixture state in the mapped column. Dragging a row to Done closes it. Dragging a found row to Queued starts it. No drag dispatches a write.
- The found section never shows more rows than the daily discovery budget.
- A notification contains only line one and is not repeated for the same row within five minutes.
- A narrow window keeps the inbox, card, board and rail usable.
