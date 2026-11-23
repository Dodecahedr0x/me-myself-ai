# Autonomous personal agent

The goal is an agent that can take care of all the user's tasks from discovery through completion. Email, Slack, and GitHub are its first sources of work. It picks up incoming requests and spots follow-ups in older activity. The dashboard lets the user see what the agent has done, correct its judgment, and help when it gets stuck. As the agent handles familiar work reliably, routine tasks should need no supervision.

For example, a teammate asks for a status update in Slack. The agent finds the linked GitHub issue, reads its latest update, and drafts a reply before the user opens the dashboard. In the initial dry-mode workflow, it waits for confirmation before posting. The longer-term goal is for the user to grant authority for routine replies so the agent can post, verify delivery, and record the outcome on its own.

## Proposed architecture

```mermaid
flowchart LR
    Slack["Slack access token, refresh token or OAuth"] -->|All accessible channels| Connectors[Connectors]
    Mail["Google Workspace or other IMAP mailboxes"] -->|Account activity| Connectors
    GitHub["GitHub CLI"] -->|Repository activity| Connectors
    Tasks -->|Queue with project, repository, source and counts| Home[Home overview]
    Suggestions --> Home
    Activity -->|Recent activity| Home
    Home -->|Manage connections| Accounts[Connections]
    Connectors -->|Per-account history| Activity[Activity log]
    Accounts -->|Choose account and source| SignIn[Workspace account sign-in]
    SignIn -->|Connect Google| Workspace[Bundled Google Workspace extension]
    Workspace -->|Browser consent| Google[Google account chooser]
    Workspace -->|Verified account| Connectors
    SignIn -->|Publisher-configured build| Direct[Google desktop OAuth]
    Direct -->|One consent for Gmail, Calendar, Drive, Tasks and Contacts| Google
    Direct -->|Granted services| Connectors
    SignIn -->|Store credential or profile key| Keychain[macOS Keychain]
    Keychain -->|Account credential| Connectors
    Connectors --> Discovery[Reactive and proactive discovery]
    Chats["Claude and ChatGPT desktop conversations"] --> Suggestions
    Local["Selected folders and active app"] --> Suggestions[Activity suggestions]
    Connectors --> Suggestions
    Suggestions -->|Start suggested work| Tasks
    Discovery -->|Assign named project or Global| Tasks[Local task queue]
    Tasks -->|Spawn for each task| CLI[Claude Code or Codex CLI]
    Projects["Global scope and named projects"] -->|Registered repositories| Worktree
    CLI -->|Coding tasks| Worktree[Task worktree]
    Style[Approved style file] --> CLI
    CLI -->|Results and drafts| Dashboard[Desktop dashboard]
    Dashboard -.->|Optional correction| CLI
    Dashboard -->|User rules and confirmations| Executor[Action executor]
    CLI -->|Proposed action| Executor
    Executor -->|Authorized write| Sources["Connected Slack, mail or GitHub account"]
    Executor -->|Delivered commitment creates follow-up| Tasks
    Checks[MeMyselfAI fixture tests or maintainer report] --> Findings[Self-improvement findings]
    Findings --> Tasks
    Tasks -->|Issue, fix branch, linked PR and code review| Executor
```

The executor applies dry mode and the user's rules for each project and decision type. A promise in a delivered message creates a task of its own. Each task agent checks its completion criteria and records evidence in the dashboard.

## Planned direction: an inbox for a proactive agent you host

These are drafts. Nothing in this section is implemented yet.

The dashboard becomes an inbox. Each task is one row with one agent. A row appears only when its agent needs the user, and the user answers with a numbered option from the keyboard. A board shows every task by stage. Unlike inboxes for coding agents, most rows come from discovery across mail, chat, social, calendar and code accounts, including proposed projects. A managed Hermes agent runs that discovery as a standing orchestrator. The user can prompt it and get updates from it. Hermes reads connected accounts, and for each item that needs action it dispatches a fresh Claude Code or Codex task agent that writes and sends the reply. Every kind of task follows that same flow. All agents share one setup: the user signs into a Claude or ChatGPT subscription once, and connects each account once. Agents act only through the app's own MCP server, which gives each one the capabilities of its role and task. Dry mode is unchanged. Every external write still needs approval of its exact version, and an approved write is sent immediately.

The app can run on the laptop, on a VPS the user controls, or on both as one workspace. Paired instances sync directly with each other, including logins, with no middleman. Every task, connection and the orchestrator has exactly one owner instance, and only the owner acts on it. Ownership moves only by a signed handoff from the current owner, so each instance keeps working on what it owns without the others. They split work by capacity through handoffs.

```mermaid
flowchart LR
    Sources["Mail, chat, social, calendar, code"] -->|Polled by the connection's owner| Activity[(Synced activity)]
    Activity -->|App MCP: read| Hermes[Hermes orchestrator]
    User[User prompts] --> Hermes
    Hermes -->|Updates| User
    Hermes -->|App MCP: dispatch_task| Tasks[Tasks, one owner each]
    Tasks -->|Handoff by capacity| Laptop[Laptop task agents]
    Tasks -->|Handoff by capacity| VPS[VPS task agents]
    Laptop -->|App MCP: action tools| Executor[Owner's executor]
    VPS -->|App MCP: action tools| Executor
    Executor -->|Dry mode| Inbox[Inbox: needs you]
    Inbox -->|Approved version| Executor
    Executor -->|Send intent, single dispatch| Sources
```

- [Inbox, focus card and board](specs/inbox-experience.md)
- [Agents, capabilities and the app MCP server](specs/agents-and-app-mcp.md)
- [Hermes orchestrator](specs/hermes-orchestrator.md)
- [Proactive discovery and the connector catalog](specs/proactive-discovery.md)
- [Instances, sync and sharing work](specs/instances-and-sync.md)

## Documentation

The first project for this workflow is MeMyselfAI itself. Tasks can test the app, track findings and prepare fixes and reviews in its own repository. [Self-improvement workflow](specs/self-improvement.md)

- [User stories](user-stories.md) describe what the user can delegate and when they might intervene.
- [Task discovery, preparation, and approval](specs/task-workflow.md) is the draft behavioral spec for the first workflow.
- [Task agents and completion](specs/task-execution.md) defines isolation, follow-ups, and the evidence needed to close bugs and emails.
- [Decision rules, writing style, and trust](specs/delegation-and-style.md) defines when agents may act, how they learn the user's voice, and how the user decides to leave dry mode.
- [Reviewing activity and learning from missed work](specs/activity-and-improvement.md) shows how an in-app report triggers recovery and improves future discovery using Markdown skills.
- [Repository rules](../AGENTS.md) describe how to take a change from spec to verification.

## Running the desktop implementation

The macOS app includes the task queue, source polling, Claude Code and Codex CLI workers, isolated Git worktrees, review and delivery, project rules, and Markdown revision history. Setup and build commands are in the [README](../README.md). The [desktop runtime](specs/desktop-runtime.md), [dashboard](specs/dashboard.md), [local agent](specs/local-agent.md), and [connector contracts](specs/connectors.md) describe this implementation.

Coverage depends on the connected accounts and their permissions. Slack covers accessible workspace channels. Google covers granted Workspace services and supports reviewed deadline events. GitHub CLI covers the account’s repositories. Catch-up reports show source gaps. Normal discovery resumes from the last successful sync after sleep or downtime.
