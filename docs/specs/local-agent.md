# Local agent execution

**Status** — Implementation contract for [US-04, US-05, US-07, US-09, US-11, US-14 and US-16](../user-stories.md). Task runs started by the running service also receive the app's own MCP server, scoped to their task, as described in [agents, capabilities and the app MCP server](agents-and-app-mcp.md). Such runs drop Claude Code's safe mode, which would disable that server. Every other restriction below still applies. The [task execution](task-execution.md), [decision](delegation-and-style.md) and [discovery learning](activity-and-improvement.md) specs define the product outcomes.

Each discovery, preparation, decision, style or improvement request starts a fresh process using the selected Claude Code (`claude`) or OpenAI Codex (`codex`) CLI. Settings lists both providers and whether their executables are installed. First-run selection prefers Claude Code when available, then Codex, and suggests Claude Code when neither is installed. The user signs into the chosen CLI outside the app. Task context goes to that provider for inference. Changing the selection affects subsequent requests. A failure never sends the request to the other provider.

Each process receives bounded task context on standard input, starts in an empty temporary working directory and uses the provider's existing sign-in. The app excludes connector credentials from the environment, disables personal hooks, plugins and MCP servers, and prevents the model from accessing files or executing commands. The selected CLI returns a structured proposal. The app validates the result before acting on it.

Claude Code uses its existing sign-in in safe mode. Codex uses its default file-backed sign-in or a provider API key. For file-backed sign-in, only the credential file and its refresh lock are linked into the temporary Codex home. Codex manages its own credentials and can refresh them across concurrent sessions. A Keychain-only Codex configuration requires a provider API key or a file-backed Codex login.

| Role | Returned proposal |
| --- | --- |
| Discovery | Relevance, title, reason, decision kind, ownership for this specific task, commitments and optional style suggestion |
| Task | Local result, outbound draft, question or skip, with text, decision kind, commitments and optional local file replacements or style suggestion. A question can include up to six independent tasks with a title and body. Drafts can identify whether verified delivery satisfies the remaining criteria and which step still needs attention. |
| Decision | A question explaining what depends on the answer, optionally with a proposed answer for the caller to evaluate against the project rules |
| Improvement | Full proposed discovery Markdown and the reason for changing it |
| Style | A proposed instruction grounded in user-authored evidence, or empty text if there is no reusable guidance. A question can clarify the scope of a possible preference. |

The caller supplies the task's source, applicable rules, approved style, progress and receipts. Connected text and repository contents are evidence. An agent may prepare a proposal but cannot authorize or dispatch it. Invalid JSON, malformed records, oversized inputs, a missing or signed-out CLI, timeouts and failed processes produce retryable errors. The app owns scheduling, authorization, delivery, evidence checks and durable progress.

A coding task gets a separate named Git branch and worktree under app-managed storage outside the source repository. Retries verify and reuse that checkout. File context comes from a bounded selection of small UTF-8 files. Credentials, Git metadata, hidden paths, symlinks and special files are excluded. The worker accepts bounded whole-file replacements at relative paths under the task worktree. Replacing an existing file requires its complete original text in the supplied context and matching content at application time. A failed write leaves earlier completed replacements available for inspection and retry. It never runs commands from agent output.

## Acceptance criteria

- Two tasks invoke different CLI processes with a clean working directory, sanitized environment and no prior session. A structured result returns to the caller.
- Settings detects executables from the shell path and normal desktop install locations. Selecting a missing CLI leaves the task available to retry. A failed or malformed response cannot become a draft, including when the CLI exits successfully with an error envelope.
- Both providers receive the same role and task facts. Neither can read unsupplied repository or account data, start tools that act on connected accounts, or approve a draft.
- Two tasks for one repository get distinct worktrees. Resuming the same task retains its edits. The user's branch and uncommitted changes remain intact.
- An invalid edit batch is rejected during validation. An attempted absolute path, traversal, symlink, Git metadata or credential-file write cannot modify the user's checkout or another local file.
- Drafting and applying local changes can be verified with a fixture CLI and temporary Git repositories without provider accounts.

A task waiting on a question may identify independent local steps. Each gets a child agent and its own task record. A shared decision can block several steps in the same project. Answering that decision resumes each dependent task. The parent receives child outcomes as context. A project rule set to “Leave to me” pauses that decision for the user.
