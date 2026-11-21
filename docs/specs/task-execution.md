# Task agents and completion

**Status** — Draft. Covers [US-07, US-08, and US-09](../user-stories.md).

## One agent per task

When a task becomes actionable, the app spawns a fresh agent through the local AI CLI. The agent receives the task's project, source references, instructions, applicable decision rules, and completion criteria. Its progress belongs to the task and survives a CLI exit. On retry, the app restores that progress and checks existing receipts before resuming. Two workers must never dispatch the same task action concurrently.

For a coding task in a Git repository, prefer a dedicated worktree and branch for that task. Two bug fixes in the same repository can then proceed independently, including when the user's own checkout contains uncommitted changes. Reuse the task's worktree when resuming. If the agent cannot obtain an isolated checkout, it can still investigate read-only and ask how to proceed before editing a shared working copy.

## Global work and project repositories

Every workspace has a built-in Global scope for general computer tasks. Creating a task requires only a title. Named projects group related work and can contain child projects and zero or more Git checkouts. Global is the root. A project can be moved under another project. Reject moves that would create a cycle. A group can draw on its descendants’ checkouts; when a GitHub source matches a parent and its child, prefer the more specific child. Connections remain workspace-wide. Existing projects, including Personal, retain their tasks and rules when upgraded. A legacy single checkout becomes a one-entry list. If a named Global already exists, the built-in scope gets a distinct name. Rules continue to apply to their own scope.

Discovery prefers a unique named project containing the source GitHub repository. Unmatched work defaults to Global. Coding tasks in Global may use a registered checkout from any project. A task works in one isolated checkout. Prefer the source repository, explicit repository evidence, or the sole checkout of a named project. When the target is ambiguous, ask through a decision agent before preparing edits. Continue independent work. After selection, retries and publication use the same checkout.

Verify a fresh workspace and an upgraded workspace, a non-coding task without a repository, selection of the second repository in a group, and clarification when several repositories fit. The project editor uses the native folder picker to add or replace each repository and supports removing a link.

## Evidence of completion

| Task | Evidence required to mark it complete |
| --- | --- |
| Bug report | An open issue records the bug. A PR containing the fix links to the issue and the hosting provider reports the PR is mergeable. A delivered message tells the original reporter where to find the fix. The task can finish before the PR is merged. |
| Email requesting information | The agent identifies each requested element, checks that the outgoing answer covers each one, and records successful delivery of that answer. Missing information leaves the task open for clarification. |
| Commitment | The promised outcome has been delivered according to the follow-up task's own completion criteria. |

Completing one external action advances the task. The agent checks the remaining criteria before marking the whole task complete. In dry mode, a prepared issue, PR, notification, or email can await confirmation while the dashboard shows which criteria are still outstanding.

The desktop bug workflow verifies the worktree's origin and HEAD against the configured GitHub repository and its default branch before publishing a fix. The branch preview names the base revision, commit message, paths, file modes and full contents. A moved base or a newly assigned issue pauses delivery. The PR must target the recorded base from the published fix branch and revision and link the original open issue. The reporter receives the PR URL by email, in the original Slack thread, or in the GitHub conversation with the reporter mentioned. An uncertain notification remains open until reconciled.

## Promises become work

When drafting a commitment such as “I'll send the figures on Friday,” show the promised work and timing alongside the proposed message. Once the message is delivered, create a linked follow-up task for each distinct promise, with the project, beneficiary, source message, and any stated deadline or trigger. The scheduler makes the follow-up actionable in time to address it. Where timing or scope is unclear, ask for clarification. Reconcile uncertain delivery before activating the follow-up. Discarding a draft discards its proposed commitments. A commitment already made by the user in connected content also creates a follow-up when discovered.

## Acceptance criteria

- Two distinct tasks start distinct CLI agents. Two coding agents fixing bugs in the same repository use separate worktrees when available and preserve the user's checkout.
- Given an existing issue and linked PR after a restart, the agent resumes from the missing evidence. A conflicting PR or an undelivered reporter notification keeps the bug task open.
- Given an email requesting a price and delivery date, sending only the price leaves the task incomplete. A delivered answer covering both satisfies the task.
- A sent email promising a Friday update can complete the email task and leave a separate Friday follow-up open. Replaying the delivery event or rediscovering the same sent message produces one follow-up for that promise.
- A mismatched repository, branch revision, file type, PR head, or issue link prevents completion. An executable script keeps its tracked mode in the published branch. If the final reporter message fails, receipt of a branch or PR alone cannot close the task.
