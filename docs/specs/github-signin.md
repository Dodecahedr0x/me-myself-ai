# GitHub account connection

**Status** — Desktop implementation contract for [US-01](../user-stories.md) and [source discovery](connectors.md).

Connect GitHub checks the GitHub.com account configured in the local GitHub CLI. If needed, the user can authorize the CLI in the system browser. The dashboard shows the verified login and connects it without a repository picker. The CLI manages its credentials. Subsequent operations request that specific login's credential. Disconnecting the app leaves the CLI signed in.

The service pages through repositories visible to the account as owner, collaborator, or organization member. It refreshes the list automatically and on Sync now. Each repository has its own discovery cursor and can produce issue, PR, review request and conversation tasks. Initial discovery and missed-work recovery observe the existing three-hour window. An empty account can connect. A partial repository listing retains the previous inventory. A failed repository scan can retry independently.

The dashboard shows one account and its repository coverage. Browse open work lists the account’s assigned issues, authored issues, authored PRs and individual review requests with pagination. A user can submit an open item in a monitored repository for discovery, including an older item. This explicit read uses the original provider key and preserves ownership checks and existing tasks. Each source event, task, reply and bug workflow retains its exact repository and account. Existing repository-specific credentials continue working. Connecting the same CLI login can adopt its older repository-specific CLI connections while preserving event IDs, checkpoints and reviews.

Verify account setup without listing or selecting repositories, paginated enumeration, changing membership, failure and retry, account-specific token lookup, and identical issue numbers across repositories using fixtures. [GitHub CLI authentication](https://cli.github.com/manual/gh_auth_login) · [Repository listing](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user)
