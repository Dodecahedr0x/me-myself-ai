# Fixture checks on pull requests

**Status** — Implemented in `.github/workflows/tests.yml`. Extends [US-19](../user-stories.md) and the [self-improvement workflow](self-improvement.md). The [release workflow](app-updates.md#publishing-a-release) is separate and unchanged.

## Intent

Every pull request, including one the app prepares for its own repository, shows a test result next to its code review. The maintainer sees from the PR alone whether the fixture suites pass on the head revision, without checking it out.

## What runs, and when

The workflow `Tests` runs on every `pull_request` event (opened, reopened, head updated) and on every push to `main`. A newer head on the same PR cancels the older run, so each head commit ends with its own result.

| Check | Runner | Commands |
| --- | --- | --- |
| Fixture suites (macOS) | `macos-15`, Python 3.13, Node.js 22 | `python3 -m unittest discover -s tests -v`, then `node --test tests/test_google_identity.cjs` |
| Python fixtures (Linux service) | `ubuntu-24.04`, Ubuntu's `python3` with `python3-cryptography` and `bubblewrap` | `python3 -m unittest discover -s tests -v` |

The macOS check is the one the issue asked for. It is the app's platform, and it runs the tests that need it: the macOS command sandbox and the bundled Google Workspace connector runtime. The Linux check runs the suite as the headless VPS service sees it, so the vault tests (which need `cryptography`) and the bubblewrap sandbox test run there instead of being skipped. A test that cannot run on a runner is skipped with a reason by its `skipUnless`/`skipIf`, never deleted.

Ubuntu 24.04 blocks unprivileged user namespaces through AppArmor, which bubblewrap needs. The Linux check lifts that restriction for its runner (`kernel.apparmor_restrict_unprivileged_userns=0`), matching Debian, which the VPS image uses.

Either command exiting non-zero fails its check. A failing check blocks nothing automatically: the repository has no branch protection rule, and the maintainer decides whether to merge, as with reviews. The check does not build the app. `./scripts/build-app.sh` stays a local step for each change ([AGENTS.md](../../AGENTS.md)) and runs in CI only for release tags.

## Bugs the first runs found

- An instance identity made by an `openssl` that is OpenSSL 1.1 (as on the macOS runner's `PATH`) carried `basicConstraints` twice, because OpenSSL 1.1's default config adds it too. OpenSSL 3 then refuses the certificate as a trust anchor and paired instances report each other as unreachable. `sync.make_identity` now passes its own minimal config, so OpenSSL 1.1, OpenSSL 3 and LibreSSL all produce the same extensions.
- The setup guide could miss the signed-in GitHub CLI login for its first two minutes after the machine boots, because its cache started at monotonic time 0. It now starts empty.

## Permission boundary

The workflow references no secrets. Its token has `contents: read` only and checkout keeps no credentials (`persist-credentials: false`). Nothing signs in to a connector, Keychain or an AI CLI. The tests use temporary workspaces, fixture accounts such as `work@example.test` and simulated deliveries. The connector runtime test substitutes the OS browser launcher and cancels consent before any Google request. Pull requests from forks get the same read-only token.

## Local equivalent

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_google_identity.cjs
```

On a Mac without `cryptography` the four vault tests skip, and the bubblewrap test always skips. Both run in the Linux check.

## Acceptance criteria

1. Opening or updating a PR runs both checks and reports each on the PR head commit.
2. A push to `main` runs both checks.
3. A deliberately failing fixture fails its check. A later passing revision gets its own passing result on the same PR.
4. The run log shows no secrets and no connector or AI sign-in.

## Open questions

- Whether to require the checks through branch protection. Today they are informational.
