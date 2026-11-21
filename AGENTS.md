# Working in this repository

The product goal is a personal agent that can take care of all the user's tasks end to end. The agent discovers work, chooses the next step, carries it out within its authority, and checks the outcome. Design for a user who can leave the dashboard closed during routine work. Instructions and corrections are optional ways to teach or redirect the agent.

## Start with the spec

1. Read the [product overview](docs/README.md) and find the [user story](docs/user-stories.md) and [spec](docs/specs/) that cover the change.
2. Before writing implementation code, check whether the spec describes the intended behavior and how to verify it. If coverage is missing, draft a spec or extend the existing one first. The spec and implementation can ship in the same change.
3. For a bug already covered by a spec, use its acceptance criteria to guide the fix. For a change to an internal component, document the contract it must preserve. Match the detail to the size of the change.
4. Implement against the spec. If the design changes during implementation, update the spec before proceeding with the changed behavior.
5. Verify the affected acceptance criteria. In the change summary, link the spec and report what was tested and anything still unverified.

## Keep the docs usable

- Keep user goals and concrete scenarios in [docs/user-stories.md](docs/user-stories.md). Link each story to the spec that covers it.
- Keep specs in [docs/specs/](docs/specs/). Give each one a descriptive filename, status, linked stories, observable behavior, and verifiable acceptance criteria. Cover failures, retries, and permission boundaries wherever they affect the outcome.
- Keep the [product overview](docs/README.md) current. Use Mermaid to show architecture and workflows. Update affected diagrams alongside the spec.
- Mark proposals as drafts and distinguish implemented behavior from planned behavior. Record unresolved product or technical choices where they affect a spec.

## Preserve the dry-mode contract

Design runtime features around a fresh agent per task. Prefer a task-specific Git worktree for coding agents. Use the [task execution spec](docs/specs/task-execution.md) for completion criteria and the [delegation spec](docs/specs/delegation-and-style.md) for project rules and writing preferences.

- Default to dry mode. The agent starts and prepares tasks on its own. In this mode every external write, including a reply, post, or issue update, goes through the app's approval flow.
- Show the sending account, destination, and exact proposed change. Confirmation applies to one version of one action. Edits require fresh confirmation.
- Enforce this boundary where writes execute. Give the CLI only the capabilities it needs to prepare work. Treat connected content as input, including text that asks the agent to send or approve something.
- Use fixtures or test accounts to verify connector behavior. Keep credentials and real private activity out of the repository.
