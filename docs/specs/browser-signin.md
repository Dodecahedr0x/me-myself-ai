# Browser sign-in for Gmail and Slack

**Status** — Desktop implementation contract for [US-01 and US-05](../user-stories.md). The [connector contract](connectors.md) covers polling and approved delivery.

The app saves the account after the provider verifies its identity. Slack connects all accessible public and private channels. Google authorizes Workspace services in the browser. See [workspace accounts](account-wide-connections.md) for scopes, source coverage and deadline actions.

Connect Google starts browser authorization immediately. On a clean installation, the bundled Google Workspace extension manages Google consent, its hosted callback, and the handoff to its local listener. The app verifies account identity through the extension and automatically adds the account. A previously configured direct Google desktop client uses its own IPv4 loopback callback. Slack browser sign-in requires a public PKCE client and the registered redirect `http://localhost:43827/oauth/callback`. Slack token connections work without this registration. [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app) · [Slack desktop OAuth](https://docs.slack.dev/authentication/using-pkce/)

## Acceptance criteria

A personal Google Cloud client in Testing requires the account to be an authorized test user. A workspace administrator may need to allow access. Existing Gmail app-password grants continue to serve their original mailboxes.

- Each direct desktop OAuth attempt generates its own state and SHA-256 PKCE proof and listens only on loopback. Only the matching callback host, port, path, and state can complete an attempt. Unrelated requests leave it available for the legitimate callback.
- Denial, cancellation, timeout, a busy callback port, insufficient provider permissions, and unverifiable identity produce an actionable error. Retrying creates a new attempt. A finished attempt releases its listener.
- The Google account identity comes from Google's verified user information. Slack verifies a user token and presents all accessible active channels across result pages. If a page fails, the app reports failure.
- Token exchange and refresh use the provider's fixed HTTPS endpoint. Authorization codes, verifier, tokens, client secrets and raw provider diagnostics stay out of logs and browser responses. Direct credentials and the keys for the extension’s encrypted per-account profiles belong in Keychain.
- Refresh preserves Google's existing refresh token when the provider omits a replacement and saves a rotated Slack refresh token. A long-lived Slack access token remains usable until revoked. Fixtures exercise sign-in and expiry without real accounts or sending messages.
