# Connected accounts and replies

**Status** — Implementation contract for the desktop app. Covers [US-01, US-02, US-03 and US-05](../user-stories.md) and the [dry-mode workflow](task-workflow.md).

Connections belong to the workspace. GitHub accounts, Slack channels and mailboxes define what each connection monitors. Every project can use these sources. Direct credentials and Google connector profile keys live in macOS Keychain. The local configuration contains provider settings. A scan reads available records in an inclusive UTC time window. Each record carries a stable provider key, time, original text, sending identity, exact reply destination, and source reference. A failure on any page fails the scan so the caller can retain its checkpoint. Provider retention and the connected account's permissions determine what history is available.

- **Email** reads the selected IMAP mailbox and optional sent mailbox using verified TLS, read-only selection and body peeks. It filters on server arrival time. Replies use the original Reply-To or From address. For a sent message, follow-ups use the original To address. A reply goes to a single displayed address with its subject and threading headers. Configure an account that supports password or app-password authentication and SMTP submission on port 465 (TLS) or 587 (STARTTLS).
- **Slack** reads one configured conversation and refreshes threads identified by local evidence, using the requested time bounds for both history and replies. Slack cannot list replies to an unknown parent older than the window through conversation history. Recovery reports show that coverage gap. Connect a user token with the appropriate conversation history scope and `chat:write`. The reply target includes the conversation ID and parent timestamp. Account identity includes the workspace and user IDs.
- **GitHub.com** reads issues, pull requests and their conversation comments across the CLI account’s repositories, or in the selected repository for a manual token. Updated issue/PR snapshots identify assignees. Pull requests include directly requested reviewers. Comment replies go to the issue or PR conversation. A fine-grained user token needs read access to Issues and Pull requests, plus write access for the kind of conversation where the user wants to comment. Publishing a bug fix also requires Contents write and Pull requests write. Inline code-review discussions and team review requests are outside this adapter's scope.

Only the app's action executor invokes delivery after its [authorization checks](delegation-and-style.md). The connector verifies the current account and source target before sending. It returns a provider reference or SMTP-accepted Message-ID. Transport failures after dispatch leave delivery uncertain. The connector never retries a write.

For the coding workflow, the executor may also use a repository-scoped GitHub transport for approved issue, branch, commit and pull-request operations. Its path always starts inside the configured repository. The executor checks the authenticated identity against the approved account before dispatch.

## Acceptance criteria

- A paginated fixture returns every accessible in-window item once, including Slack replies to a known parent that predates the scan. The scan reports the limitation on unknown old threads. A failed page raises an error.
- The IMAP fixture uses read-only access and preserves unseen flags. A boundary case near midnight uses the server's INTERNALDATE and UTC offset. The SMTP fixture verifies TLS before authentication, the envelope recipient and the thread reference.
- GitHub issue and PR comments use their repository and number. A Slack reply uses its original parent thread. A changed identity or a mismatched target stops delivery.
- Polling produces zero provider writes. Confirmed sends return a receipt on acceptance and distinguish known rejection from an unknown outcome. Credential values stay out of arguments, logs, config and exception messages.

## Connecting without entering a token

Connections offers the current GitHub CLI sign-in, Slack user tokens, browser authorization for Slack and Google, and manual setup. Slack discovers accessible channels automatically. Google identifies the account and granted services. See [workspace accounts](account-wide-connections.md). Display names and mail server settings are derived from that choice. Reconnecting preserves the connection ID, reviewed target and previously verified account. Cancelling, declining consent, closing a flow or letting it expire discards its temporary credentials. Opening the Connections view performs no provider login.

Browser authorization uses an app registration. A build may supply the public desktop client configuration for all its users. Slack registration belongs to the publisher. Google uses the bundled Workspace extension for consent when a direct desktop registration is unavailable. Secrets and rotating tokens stay in Keychain. Gmail IMAP and SMTP use XOAUTH2 over TLS with Google's endpoints. Concurrent readers share the refreshed credential, and disconnect removes it. Existing manual connections continue to work. See [browser sign-in](browser-signin.md) and [GitHub sign-in](github-signin.md).

Verify the complete flow using synthetic accounts, including cancellation, expired grants, setup once across multiple connections, target selection, duplicate submission, and account or repository changes during reconnection.

Connecting the same verified account and source again renews its existing connection and preserves its label and history.

## Project assignment

Discovery assigns a project to each new task. For GitHub events, a unique match between the source repository and one of a named project's local GitHub origins takes precedence. Otherwise discovery can select an existing project using the event and thread. Unmatched work goes to the built-in Global scope. The selected project governs its worktree, rules and follow-ups. Local bug tasks can use a workspace GitHub connection matching their checkout's origin. An ambiguous account or missing repository requires clarification.

Verify that two requests from one channel can create tasks in different projects, that manual and browser setup need no project, and that upgrading preserves existing task projects, connection IDs, credentials, cursors and pending approvals.
