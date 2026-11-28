# Local agent execution

**Status** — Implementation contract for [US-04, US-05, US-07, US-09, US-11, US-14 and US-16](../user-stories.md). Task runs started by the running service also receive the app's own MCP server, scoped to their task, as described in [agents, capabilities and the app MCP server](agents-and-app-mcp.md). Such runs drop Claude Code's safe mode, which would disable that server. Every other restriction below still applies. The [task execution](task-execution.md), [decision](delegation-and-style.md) and [discovery learning](activity-and-improvement.md) specs define the product outcomes.

Each discovery, preparation, decision, style or improvement request starts a fresh process using the selected Claude Code (`claude`) or OpenAI Codex (`codex`) CLI. Settings lists both providers and whether their executables are installed. First-run selection prefers Claude Code when available, then Codex, and suggests Claude Code when neither is installed. The user signs into the chosen CLI outside the app. Task context goes to that provider for inference. Changing the selection affects subsequent requests. A failure never sends the request to the other provider.

Each process receives bounded task context on standard input and uses the provider's existing sign-in. It starts in the verified task worktree when one has been prepared. Otherwise, the app creates a private subdirectory under its workspace's `agent-workspaces` folder. Each task reuses its directory across retries. Background requests without a task have a folder per role. The agent receives its actual working directory and the task activity records it. Repository edits still require a verified worktree. The app excludes connector credentials from the environment, disables personal hooks, plugins and MCP servers, and disables native CLI tools. During task preparation the app can execute a validated local command request in a separate sandbox. The selected CLI returns a structured proposal. The app validates the result before acting on it.

Claude Code uses its existing sign-in in safe mode. Codex uses its default file-backed sign-in or a provider API key. For file-backed sign-in, only the credential file and its refresh lock are linked into the temporary Codex home. Codex manages its own credentials and can refresh them across concurrent sessions. A Keychain-only Codex configuration requires a provider API key or a file-backed Codex login.

| Role | Returned proposal |
| --- | --- |
| Discovery | Relevance, title, reason, decision kind, ownership for this specific task, commitments and optional style suggestion |
| Task | Local result, local command request, outbound draft, question or skip, with text, decision kind, commitments and optional local file replacements or style suggestion. A question can include up to six independent tasks with a title and body. Drafts can identify whether verified delivery satisfies the remaining criteria and which step still needs attention. |
| Decision | A one-line question, context for the task detail and up to three choices with short labels and explanations. A separately proposed autonomous answer is evaluated against project rules. |
| Improvement | Full proposed discovery Markdown and the reason for changing it |
| Style | A proposed instruction grounded in user-authored evidence, or empty text if there is no reusable guidance. A question can clarify the scope of a possible preference. |

The caller supplies the task's source, applicable rules, approved style, progress and receipts. Connected text and repository contents are evidence. An agent may prepare a proposal but cannot authorize or dispatch it. Invalid JSON, malformed records, oversized inputs, a missing or signed-out CLI, timeouts and failed processes produce retryable errors. The app owns scheduling, authorization, delivery, evidence checks and durable progress.

A coding task gets a separate named Git branch and worktree under app-managed storage outside the source repository. Retries verify and reuse that checkout. File context comes from a bounded selection of small UTF-8 files. Credentials, Git metadata, hidden paths, symlinks and special files are excluded. A fixed list of project configuration dotfiles is the exception: CI directories such as `.github`, `.gitlab` and `.circleci`, and files such as `.gitignore`, `.gitattributes` and `.editorconfig` can be read and edited, so a task can add a CI workflow. Other hidden names, including `.git`, `.env`, `.npmrc` and `.ssh`, stay excluded. The worker accepts bounded whole-file replacements at relative paths under the task worktree. Replacing an existing file requires its complete original text in the supplied context and matching content at application time. For an existing text file too large to supply in full, up to 1 MiB, the agent can instead return exact-text replacements: each names the file, a non-empty `old` passage and its `new` text. The passage must occur exactly once in the file as it stands, so a stale or ambiguous edit is refused instead of applied. Several replacements of one file apply in order and cannot be mixed with a whole-file replacement of it. The agent inspects such a file through a local command. The app stores the resulting full text for publication. Saved changes have their own bounds of 1 MiB per file and 4 MiB per task, so a task that edits a large file or keeps editing over several attempts is not rejected by the per-response limits. Each rejected edit names its file and the check it failed. A failed write leaves earlier completed replacements available for inspection and retry. For a local command request the app launches a separate [command sandbox](#command-sandbox) in the task directory. The command can read and write working files and use installed toolchains. The app owns the clone’s Git metadata. Each inference sequence can request up to four commands. Each command has a three-minute deadline and returns the last 12 KiB of combined output. It receives a disposable home and temporary directory. The sandbox denies access to other workspaces, Keychain services and outbound network connections. The worker returns bounded command output and exit status to the selected provider. A timeout or launch failure is recorded as evidence.

## Acceptance criteria

The app supplies its bundled task-interaction skill to both providers for task preparation and decision requests. Verify that a fresh agent receives the skill with CLI tools disabled. Formatting an existing question preserves its original context and decision linkage, and produces no answer or external action.

During task preparation, a CLI timeout schedules up to two retries with the selected provider, after 30 and 120 seconds. Each retry has a longer inference deadline. The retry count survives a restart. Exhausting the attempts shows the error and a manual retry starts a fresh series.

A task proposal may include finished local edits alongside a question. The app verifies and saves those edits in the task worktree before requesting further context or routing the question. Subsequent attempts receive the saved paths and retain earlier changes when preparing a publication preview. A bug with partial edits must finish preparation before publication. Changes that depend on the answer stay in the question text.

Verify that Python source containing a detector pattern or a quoted header-only test fixture can be read. Private-key material in a source file, credential paths, binary data and files above the byte limit remain ineligible. The worker reports which check excluded a requested file.

The task worker receives a bounded list of available paths. It prioritizes named files within the 24-file cap. Ordinary selection allows 24 KiB per file and 72 KiB total. Explicitly named files can use up to 104 KiB each and 136 KiB total. When an agent asks for another file, the app can make one further inference with the requested originals or the reason they were omitted. The same path and content checks apply to both selections.

- Verify the actual working directory of both CLI processes. A coding task starts in its worktree, a task with a matching registration starts classification in a private app folder, and two tasks without a match get distinct app folders. A retry reuses the same folder. Credentials, schemas and response files remain isolated from each working directory.
- Settings detects executables from the shell path and normal desktop install locations. Selecting a missing CLI leaves the task available to retry. A failed or malformed response cannot become a draft, including when the CLI exits successfully with an error envelope.
- Both providers receive the same role and task facts. Verify that a task command writes a file in its workspace, runs a fixture and returns its exit status. Attempts to access a sibling workspace or contact a connected account fail.
- Two tasks for one repository get distinct worktrees. Resuming the same task retains its edits. The user's branch and uncommitted changes remain intact.
- An invalid edit batch is rejected during validation. A 169 KiB source file can be changed by an exact-text replacement and the change is saved for publication. A passage that is absent or occurs twice is refused with the file named and leaves the file unchanged. A whole-file replacement above the per-response limit is refused with its path and the limit. An attempted absolute path, traversal, symlink, Git metadata or credential-file write cannot modify the user's checkout or another local file.
- A task can create or replace `.github/workflows/<name>.yml` and `.gitignore` in its worktree, and both appear in later file context. `.git/config`, `.env` and `.npmrc` remain unreadable and uneditable.
- Drafting and applying local changes can be verified with a fixture CLI and temporary Git repositories without provider accounts.

A task waiting on a question may identify independent local steps. Each gets a child agent and its own task record. A shared decision can block several steps in the same project. Answering that decision resumes each dependent task. The parent receives child outcomes as context. A project rule set to “Leave to me” pauses that decision for the user.

## Command sandbox

Task commands run in a kernel-enforced sandbox on every instance, with the same boundary on each platform:

- **Writable:** the task folder and a disposable home and temporary folder. The clone's `.git` metadata stays read-only, and the task folder itself cannot be removed.
- **Readable:** system toolchains. On macOS, the system, Xcode command-line tools and Homebrew. On Linux, `/usr`, the loader links, CA certificates and `/opt`.
- **Denied:** outbound network, other workspaces, the app's data folder and credentials, Keychain services on macOS, and the provider's environment.

**macOS** uses `sandbox-exec` with the app's profile.

**Linux** uses bubblewrap (`bwrap`). Each command gets new user, network, PID, IPC and UTS namespaces, a private `/proc`, `/dev` and `/tmp`, and bind mounts of only the paths above. The VPS installer and the Docker image install `bubblewrap`. The systemd unit keeps its hardening, except `ProtectKernelTunables`: that setting stops bubblewrap mounting a private `/proc`, and the unprivileged service user cannot change kernel settings anyway. Ubuntu's AppArmor rule for unprivileged user namespaces allows `/usr/bin/bwrap`.

A missing bubblewrap, or bubblewrap refused by the host (for example a container's seccomp policy), fails the command with a message that names the cause. It does not report it as the command's own exit status.

Acceptance, verified by `tests/test_worker.py` on macOS, and on Ubuntu 26.04 as the service user under the unit's hardening:

- A command writes a file in the task folder, exits with its own status and returns its output.
- It cannot read a file outside the task folder through a symlink, write `.git/config`, open a network connection, read `/etc/shadow`, or see a provider token from the service environment.
- On Linux, `/proc` is available inside the sandbox.
- Without bubblewrap, the command fails with an install instruction.


## Toolchains and dependencies

**Status** — Implemented on macOS and Linux. Covers the request that agents install what a task needs, such as Go or Rust, on every instance without asking the user.

A task agent can ask for a setup step with outcome `setup` and one of these texts:

| Step | What the app does |
|---|---|
| `install go` | Reads `https://go.dev/dl/?mode=json`, takes the newest stable release for this OS and CPU, downloads it from `go.dev`, checks its published SHA-256 and unpacks it into `toolchains/go-<version>`. `toolchains/go` then points at that release. |
| `install rust` | Downloads `rustup-init` for this platform and its `.sha256` from `static.rust-lang.org`, checks it, and installs the minimal stable toolchain with `RUSTUP_HOME` and `CARGO_HOME` under `toolchains/rust`. |
| `install c` | Reads `https://ziglang.org/download/index.json`, downloads the newest Zig release for this platform, checks its SHA-256 and unpacks it into `toolchains/zig-<version>`. It adds `cc`, `gcc`, `clang`, `c++`, `g++`, `clang++`, `ar` and `ranlib` wrappers that run Zig's bundled Clang. These come after the system's own tools on `PATH`, so an installed compiler, such as Xcode's on macOS, keeps priority. Rust uses it to link when the system has no `cc`. |
| `fetch go <folder>` | Runs `go mod download` in that folder of the task worktree. |
| `fetch rust <folder>` | Runs `cargo fetch` there. |
| `fetch npm <folder>` | Runs `npm ci --ignore-scripts` there, or `npm install --ignore-scripts` without a lockfile, against `https://registry.npmjs.org/`. |

The folder is a relative path inside the task worktree, `.` for its root, and must contain the manifest. Each inference sequence can take up to four setup steps, besides its four commands. Each result returns to the agent with the command results, including the step, its exit status and the last 12 KiB of output.

**Authority.** Installing reads only from the listed official hosts and writes only under the instance's `toolchains/` folder in its data directory. Nothing is installed system-wide, no package manager runs as root, and the user is not asked: this is local preparation, not an external write. A toolchain stays installed for later tasks on that instance. Toolchains are per instance and do not sync. Each instance installs what its own tasks ask for.

**Fetching** runs one of the fixed commands above in the [command sandbox](#command-sandbox) with outbound network allowed. Package install scripts are disabled, and Go and Cargo fetches compile nothing, so no repository code runs while the network is open. Downloads go to a dependency cache for that task, under `toolcache/task-<id>` in the data directory, and to `node_modules` in the named folder. Known risk: a fetch can reach the package registries and any module source a manifest names. A manifest edited in the task could encode data in the names it requests. The manifest is part of the task's proposed edits, which the user reviews before anything is published.

**Nothing in setup.** No language toolchain or compiler is part of instance setup. The VPS installer installs only what the app itself needs, plus `bubblewrap`, the command sandbox. Everything a task needs is added when an agent asks for it.

**Commands** see installed toolchains read-only and first on `PATH`, and the task's dependency cache read-write. They still have no network. Go runs with `GOFLAGS=-modcacherw` and the task's `GOPATH`, `GOMODCACHE` and `GOCACHE`. Cargo runs with the task's `CARGO_HOME` and the shared `RUSTUP_HOME`. npm runs with the task's cache.

**Open:** a task's dependency cache is kept until the task folder is removed. There is no size cap yet.

Acceptance, verified by `tests/test_toolchains.py` with stubbed downloads, and on the VPS and this Mac with real ones:

- A Go install accepts only an archive whose SHA-256 matches the published one, rejects archive paths outside its folder, and leaves the previous toolchain in place when it fails.
- A Rust install refuses a `rustup-init` whose checksum does not match.
- A C install refuses a Zig archive whose checksum does not match, and puts its wrappers after the system's tools on `PATH`.
- On a VPS without a C compiler, `cargo test` fails with linker `cc` not found. After `install c` it passes, and `cc` builds and runs a C program.
- A fetch step accepts only the listed kinds and a folder inside the worktree that contains the manifest. It runs the fixed command with network, and an ordinary command run afterwards still has no network.
- On a VPS without Go, a task asks for `install go` and `fetch go`, then runs `go test` in its sandbox.
