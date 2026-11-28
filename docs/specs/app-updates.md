# Updating the app from GitHub releases

**Status** — Implemented in the macOS app and the release workflow. Covers [US-38](../user-stories.md). Builds on the [desktop runtime](desktop-runtime.md).

## Intent

A user installs MeMyselfAI once, into Applications, and it stays current without reinstalling it or opening anything by hand. New versions are published as GitHub releases of this repository. The installed app finds them, installs them and restarts itself. Agent work interrupted by the restart is queued again, as on any quit.

## Publishing a release

Pushing a tag named `v<major>.<minor>[.<patch>]` runs `.github/workflows/release.yml`. It builds the app on an Apple silicon and an Intel macOS runner with `MEMYSELFAI_VERSION` set to the tag without its `v`. The build script stamps that version into `CFBundleShortVersionString` and `CFBundleVersion`. Each build is zipped with `ditto` as `MeMyselfAI-macos-arm64.zip` or `MeMyselfAI-macos-x86_64.zip`, next to a `.sha256` file. One job then creates the GitHub release for the tag with all four files. When the repository secret `OAUTH_CLIENTS_JSON` is set, the build writes it to `oauth-clients.json` first, so the release is a preconfigured build. Without the secret the release has no bundled sign-in clients.

A build without `MEMYSELFAI_VERSION` keeps the version in `desktop/Info.plist`.

## Which copies update

Only a copy installed in `/Applications` or `~/Applications` updates itself. A build left in `build/`, a task worktree or a translocated download never updates. A developer's or agent's build therefore always runs the code it was built from. A copy whose folder the user cannot write to does not update either. It shows the new version in its menu instead.

## Checking

An installed copy checks 30 seconds after launch, then every six hours, and when the user chooses **Check for Updates…** in the app menu or the menu bar menu. It reads `https://api.github.com/repos/Dodecahedr0x/me-myself-ai/releases/latest` over HTTPS without credentials. That endpoint skips drafts and prereleases. The tag's version is compared with the running version number by number, so `0.10` is newer than `0.9`. An update needs a newer version and an asset named for this Mac's architecture.

The menu bar menu shows a line under the service state: the running version and *up to date*, *checking*, *downloading*, *installing* or the reason the last attempt failed. A failed automatic check is retried at the next interval. A failure never stops the services.

## From Settings

**Settings → Workspace → App updates** appears only inside the Mac app, because the updater lives in its shell; a browser opened on the service or a VPS shows no row. The row shows the same line as the menu (running version, *up to date*, *version X available*, *checking*, *downloading*, *installing*, *restarting* or the failure) and when the last check ran. It changes as the updater moves, without a reload.

- **Check for updates** reads the feed but does not install. When a newer release exists for this Mac, the row says so, warns that installing restarts the app and stops running agents (their tasks resume after it reopens), and offers **Install version X and restart**.
- **Install version X and restart** downloads, verifies and installs the release that check found, exactly as below, then restarts. If anything fails the row shows why and the old app keeps running.
- While a check or install runs, the buttons are hidden. A copy outside Applications shows *updates only from Applications* and no buttons; one in a folder it can't write shows the available version and no install button.

Automatic checks and **Check for Updates…** in the menus still install a newer release without asking. The page reaches the shell through the `appUpdate` script message handler, which answers only the app's own page in its main frame. It accepts `status`, `check` and `install`, and replies with `{version, phase, line, installed, latest?, checked_at?}`; every change is pushed back to the page through `window.appUpdateChanged`.

## Verifying

The app downloads the zip into a temporary folder on the same volume as the installed app. It needs the asset's SHA-256 from the release API's `digest` field, or else the release's `.sha256` file for that asset. It refuses the download when neither is present or the hash differs. It expands the zip with `ditto` and accepts it only when it holds exactly one `MeMyselfAI.app`, `codesign --verify --deep --strict` passes, the bundle identifier is `local.memyselfai.desktop` and its version equals the release's.

The builds are ad-hoc signed. Their signature proves the bundle is intact, not who built it. Trust rests on HTTPS to GitHub and the hash GitHub reports for the asset. Anyone who can publish releases on the repository can ship code to installed copies.

## Installing and restarting

With a verified bundle, the app replaces itself in place with `FileManager.replaceItemAt`, which keeps the old copy until the new one is in place. It then quits through the normal quit path. Services stop their agents, and those tasks are queued again on the next start, as the [desktop runtime](desktop-runtime.md) describes. A small helper waits for the old process to exit and opens the new app with the same arguments, so a `--data` workspace is kept. At launch the service copies the new build into `app-runtime/` because its signature seal changed.

If replacing fails, the old app keeps running and the menu shows why. Nothing in the workspace data changes during an update.

## Acceptance criteria

1. A `v0.2.0` tag produces a release with arm64 and x86_64 zips and their `.sha256` files, and the bundles inside report version `0.2.0`.
2. A copy of version `0.1.0` in `~/Applications`, pointed at a feed whose latest release is `0.2.0`, downloads it, replaces itself and is running `0.2.0` from the same path, with the same `--data` folder, without user action.
3. A download whose hash differs from the published digest is refused. The installed copy is unchanged and the menu shows the failure.
4. A build run from `build/` never checks or installs.
5. A latest release that is equal or older, or that has no asset for this architecture, leaves the app as it is and the menu shows *up to date*.
6. In an installed `0.1.0` pointed at a `0.2.0` feed, **Settings → Workspace → Check for updates** shows *version 0.2.0 available* and an install button, and nothing is downloaded. **Install version 0.2.0 and restart** replaces the app and it reopens as `0.2.0`. A `0.1.0` feed shows *up to date* and no install button.
7. The App updates row is absent when the dashboard is opened in a browser instead of the app.

For testing, the environment variable `MEMYSELFAI_UPDATE_FEED` replaces the releases URL. The app accepts only `https` feeds, or `http` on `127.0.0.1`.

## Open questions

- The restart interrupts running agents. A later version could wait until no agent is running, or until the Mac is idle.
- There is no switch to turn automatic installs off. Settings can install on demand, but automatic checks still install by themselves.
