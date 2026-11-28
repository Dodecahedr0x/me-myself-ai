# Task agents and completion

**Status** — Draft. Covers [US-07, US-08, and US-09](../user-stories.md).

## One agent per task

When a task becomes actionable, the app spawns a fresh agent through the local AI CLI. The agent receives the task's project, source references, instructions, applicable decision rules, and completion criteria. Its progress belongs to the task and survives a CLI exit. On retry, the app restores that progress and checks existing receipts before resuming. Two workers must never dispatch the same task action concurrently.

Each coding task gets a private clone of the selected checkout, starting where the [task start point](#task-start-point) says. Its branch, Git objects and working files live together under app storage. Retries reopen that clone even if the source folder has moved. Older linked worktrees migrate on use, preserving their revision and local edits. A task can run local commands in its private workspace and use their exit status and output as completion evidence. Existing tasks waiting for the user to run commands or blocked by a Git preparation permission error receive one new preparation attempt.

### Task start point

A task's private clone starts from the code the task is about, not from whatever branch the user's folder happens to be on.

- **Pull request work** starts at the pull request's head, `refs/pull/<n>/head`, fetched from the folder's origin. The number comes from the task's GitHub event or a step ancestor's event. Without one, it comes from a single pull request named in the title, such as `PR #316` or `PR repo#316`, or failing that in the body. Two different numbers in the same text count as none.
- **Other work**, including GitHub issues and bug fixes, starts at the origin's current default branch. That is the revision the bug workflow checks before publishing a fix.
- **Offline or refused fetches** fall back to the folder's own `origin/HEAD` (or `origin/main`, then `origin/master`), and finally to the folder's HEAD.

The fetch uses the user's own Git setup and credentials, reads only, and writes into the private clone. The user's folder keeps its branches, refs and files. The clone records where it started in `memyselfai.start` and any pull request number in `memyselfai.pull`. An existing clone keeps its branch, so retries and resumed tasks are unchanged. A clone made to receive a handed-over task keeps the handed-over commit. When its base commit is missing and the task works on a pull request, the receiver fetches that pull request's head as well.

Acceptance, verified in `tests/test_worker.py` and `tests/test_sync.py` with a bare origin and a folder on a feature branch whose `origin/main` is behind:

- A prepared task starts at the origin's current default branch, not the folder's feature branch.
- A pull request task starts at the pull request's head.
- With the origin unreachable, the task starts at the folder's `origin/main`.
- Without the start point option, as in handoff imports, the clone starts at the folder's HEAD.
- The folder's refs and current branch are unchanged afterwards.
- The pull request number is read from a title, from an ancestor's GitHub event, and is absent for issue work or for two different numbers.

## Global work and project repositories

Every workspace has a built-in Global scope for general computer tasks. Creating a task requires only a title. Named projects group related work and can contain child projects and zero or more Git checkouts. Global is the root. A project can be moved under another project. Reject moves that would create a cycle. A group can draw on its descendants’ checkouts; when a GitHub source matches a parent and its child, prefer the more specific child. Connections remain workspace-wide. Existing projects, including Personal, retain their tasks and rules when upgraded. A legacy single checkout becomes a one-entry list. If a named Global already exists, the built-in scope gets a distinct name. Rules continue to apply to their own scope.

Discovery prefers a unique named project containing the source GitHub repository. Unmatched work defaults to Global. Coding tasks in Global may use a registered checkout from any project. A task works in one isolated checkout. Prefer the source repository, explicit repository evidence, or the sole checkout of a named project. When the target is ambiguous, ask through a decision agent before preparing edits. Continue independent work. After selection, retries and publication use the same checkout.

For a connected GitHub task without a matching registration, look for the repository folder under selected activity folders, beside registered checkouts, and in common local development folders including Documents. Search a bounded number of directory levels, skip symbolic links and app storage, and verify the full owner/repository against Git origin. Use a unique verified checkout automatically. For newly discovered work in Global, group it under an existing matching project or create a project named for the verified owner/repository. Existing Global repository questions can join that project when they have no linked task family or project decision rules to preserve. An existing explicit project keeps its scope.

Verify that an already waiting task and a newly discovered issue use the matching private clone without a settings visit. A namesake from another owner, an ignored folder and a disconnected source cannot supply a checkout. Two matching copies offer distinct path choices in the queue and show their full paths in task details. When no eligible local checkout exists, the user can choose a folder directly from the task.

Verify a fresh workspace and an upgraded workspace, a non-coding task without a repository, selection of the second repository in a group, and clarification when several repositories fit. The project editor uses the native folder picker to add or replace each repository and supports removing a link.

When a GitHub issue has a uniquely matching registered checkout, its first task agent receives the selected repository path, the verified task worktree and bounded file context even if discovery called it a generic task. A decision about that work receives the same checkout identity. When repository selection is the only pending question, saving project settings or reopening the app rechecks the current registrations and resumes preparation on a unique match. Ambiguous, disconnected and ignored repositories remain ineligible for automatic resumption. Users can retry a preparation question after supplying missing context through project settings.

A local task in a named project can see its matching checkout during classification. If the task needs code, the app prepares an isolated worktree on the next pass.

If Git cannot inspect a registered folder because access is denied or the read times out, the task identifies that folder and offers a retry. The dashboard remains usable while macOS asks for folder access.

## Resuming preparation when local evidence is available

A delegated step receives its parent's request, recent answers and available results from related steps in the same project. When the parent has a uniquely identified registered checkout, the step prepares its own private clone from it. Each agent uses the files named in its instructions first. A request for additional files can trigger one more bounded read from that worktree. The worker reports unavailable files and the applicable limits.

Verify that a named file after 24 other files reaches the worker. A waiting parent or delegated step gets another preparation pass when a related step finishes or a parent answer arrives, including across restart. An unchanged result cannot cause a retry loop. Upgrade recovery retries existing checkout and parent-context questions when a matching registration is available. Genuine user decisions, ignored work, disconnected sources and pending deliveries retain their normal controls.

Git timeouts during local preparation receive two delayed retries. A persistent timeout leaves the task available for manual retry with the folder and error recorded.

If macOS blocks reading the registered repository's identity, the scheduler checks it in the background once per recovery pass and shares the result across waiting tasks. Granting folder access resumes eligible tasks without a settings change or restart.

## Evidence of completion

| Task | Evidence required to mark it complete |
| --- | --- |
| Bug report | An open issue records the bug. A PR containing the fix links to the issue and the hosting provider reports the PR is mergeable. A delivered message tells the original reporter where to find the fix. The task can finish before the PR is merged. |
| Email requesting information | The agent identifies each requested element, checks that the outgoing answer covers each one, and records successful delivery of that answer. Missing information leaves the task open for clarification. |
| Commitment | The promised outcome has been delivered according to the follow-up task's own completion criteria. |

Completing one external action advances the task. The agent checks the remaining criteria before marking the whole task complete. In dry mode, a prepared issue, PR, notification, or email can await confirmation while the dashboard shows which criteria are still outstanding.

The desktop bug workflow verifies the worktree's origin and HEAD against the configured GitHub repository and its default branch before publishing a fix. When the default branch has moved on since the task started, GitHub must report it strictly ahead of the worktree's HEAD and none of its newer commits may change, rename or replace a path the fix edits; the fix is then published on the current default branch revision. Newer commits touching an edited path, a diverged or unpublished HEAD, or a comparison of 300 or more files stop preparation with a message naming the reason, and the conflicting paths when there are any. The branch preview names the base revision, commit message, paths, file modes and full contents. A moved base or a newly assigned issue pauses delivery. The PR must target the recorded base from the published fix branch and revision and link the original open issue. The reporter receives the PR URL by email, in the original Slack thread, or in the GitHub conversation with the reporter mentioned. An uncertain notification remains open until reconciled.

## Promises become work

When drafting a commitment such as “I'll send the figures on Friday,” show the promised work and timing alongside the proposed message. Once the message is delivered, create a linked follow-up task for each distinct promise, with the project, beneficiary, source message, and any stated deadline or trigger. The scheduler makes the follow-up actionable in time to address it. Where timing or scope is unclear, ask for clarification. Reconcile uncertain delivery before activating the follow-up. Discarding a draft discards its proposed commitments. A commitment already made by the user in connected content also creates a follow-up when discovered.

## Acceptance criteria

- Two distinct tasks start distinct CLI agents. Verify that their branches and Git metadata live under app storage, that the source retains its original refs and uncommitted changes, and that a retry succeeds after moving the source. Migrate an earlier linked worktree with staged, unstaged and deleted files. A task waiting on command access retries once and receives actual command output.
- Given an existing issue and linked PR after a restart, the agent resumes from the missing evidence. A conflicting PR or an undelivered reporter notification keeps the bug task open.
- Given an email requesting a price and delivery date, sending only the price leaves the task incomplete. A delivered answer covering both satisfies the task.
- A sent email promising a Friday update can complete the email task and leave a separate Friday follow-up open. Replaying the delivery event or rediscovering the same sent message produces one follow-up for that promise.
- A mismatched repository, branch revision, file type, PR head, or issue link prevents completion. A default branch that moved on without touching the edited files yields a preview based on its current revision, verified in `tests/test_bugflow.py`; one that changed an edited or renamed file, or diverged, stops before any write. An executable script keeps its tracked mode in the published branch. If the final reporter message fails, receipt of a branch or PR alone cannot close the task.
