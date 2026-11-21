# Testing and improving MeMyselfAI

**Status** — Implementation contract for [US-19](../user-stories.md).

The maintainer can run the installed app's fixture checks from Tasks or record a finding from hands-on testing. A check run uses temporary data, simulated accounts and the bundled bug-workflow fixtures. It records the outcome in Tasks. Each failed check opens a linked finding with reproduction evidence. Repeating a check reuses an outstanding finding. A missing test harness or interrupted run is retryable.

Findings target the MeMyselfAI GitHub repository. The app prepares an issue with the observed behavior before it needs a fix, a checkout or a model. A registered checkout matching the repository is required to prepare code. The same task tracks the issue, isolated fix branch, linked PR and a fresh agent's review of the PR patch. The maintainer sees a comment review bound to the published revision and can approve its exact text. GitHub receipts provide links to the issue, PR and review.

## Acceptance criteria

From App testing the maintainer can connect just the app repository using an already signed-in GitHub CLI. Verify the login and repository before saving the connection. If the workspace already covers this repository, reuse its connection.

- A passing check records a local result. A failed check creates a finding with its test name and reproduction steps. Running the check again while the finding is open reuses the task.
- A manually reported finding gets an issue preview even when the checkout has no commits. Missing GitHub setup leaves the finding available to retry.
- Issue, branch, PR and review each go through the existing versioned approval flow. Restarting after issue delivery resumes the same task. A stale or incomplete patch prevents review publication. An uncertain delivery waits for reconciliation.
- Review completion requires a delivered comment review on the verified PR revision. The maintainer decides whether to merge.
