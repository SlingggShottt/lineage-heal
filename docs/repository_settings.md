# Repository settings

> The GitHub settings that cannot live in the repository, and how to check them. The files that
> can (pull request and issue templates, Dependabot, the CI workflow) are in `.github/`.
> Satisfies NFR-SEC-05 (`main` is protected: pull requests and passing CI required).

Run these once, as a repository admin, from a checkout, with the GitHub CLI signed in
(`gh auth status`). They look the repository up, so they keep working if it is renamed or moved.

```bash
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
```

## 1. Merge rules

Squash merging only, so each pull request becomes one commit on `main` whose message is the pull
request title (a Conventional Commit), and branches are deleted once merged.

```bash
gh repo edit "$REPO" \
  --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false \
  --delete-branch-on-merge
```

## 2. Protect `main`

Pull requests are required, and the CI job must pass before one can be merged. No approving
review is required, because the team is two people who merge their own pull requests after CI;
force pushes and deleting `main` are blocked. The administrator can still override in an
emergency (`enforce_admins` is off); turn it on if that is not wanted.

The required check is the **name of the CI job**, `Python (format, lint, types, tests)`, from
`.github/workflows/ci.yml`. If that job is ever renamed, update the rule too (a test fails if
this page and the workflow disagree).

```bash
gh api -X PUT "repos/$REPO/branches/main/protection" --input - <<'EOF'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["Python (format, lint, types, tests)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

## 3. Dependabot security updates

Version updates come from `.github/dependabot.yml`. Security updates (a pull request when a
dependency has a published vulnerability) are a separate switch:

```bash
gh api -X PUT "repos/$REPO/automated-security-fixes"
```

Secret scanning and push protection are already on; they cover the secret-scanning part of
LH-081 for pushes, though not a CI job over the diff.

## 4. Check the result

These only read.

```bash
gh repo view "$REPO" \
  --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,deleteBranchOnMerge
gh api "repos/$REPO/branches/main/protection" \
  --jq '{checks: .required_status_checks.contexts, pull_request_required: (.required_pull_request_reviews != null), force_push: .allow_force_pushes.enabled, deletions: .allow_deletions.enabled}'
```

Expected: only squash merging allowed, branches deleted on merge, the check listed above, a pull
request required, and force pushes and deletions both `false`.

## 5. Create the LineageHeal GitHub App

The healing workflow acts on GitHub as a bot, not as a person (IF-SW-06). One App serves as the
bot identity now and as the dashboard sign-in later (LH-060). You do this once, in a browser.

1. GitHub, **Settings → Developer settings → GitHub Apps → New GitHub App**. Use the account or
   organisation that owns the repository.
2. Name it `LineageHeal` (or similar). Homepage URL: the repository's URL.
3. Untick **Webhook → Active**; nothing receives webhooks.
4. **Repository permissions**, and nothing else (NFR-SEC-02):
   Contents: read and write. Pull requests: read and write. Issues: read and write.
   Actions: read and write (to start the workflow). Metadata: read.
5. **Where can this App be installed?** Only on this account.
6. Create it, then note the **App ID** shown on its page.
7. **Generate a private key.** A `.pem` file downloads. Keep it out of the repository.
8. **Install App** on the repository only (not "all repositories"). The installation's ID is the
   number at the end of the installation's settings URL (`.../installations/<id>`).
9. Put the values in `.env`, which is git-ignored. Never paste them in chat or commit them:

   | Variable | Value |
   |---|---|
   | `GITHUB_APP_ID` | the App ID |
   | `GITHUB_APP_INSTALLATION_ID` | the installation ID |
   | `GITHUB_APP_PRIVATE_KEY` | the `.pem` on one line, each newline written as `\n` |
   | `GITHUB_REPOSITORY` | `owner/name` |

   One way to write the key on one line: `awk 'NF {printf "%s\\n", $0}' key.pem`.
10. Check it, reading only: `uv run lineageheal dev github-smoke`. It should print the commit
    `main` is at. An error names the setting that is wrong.

### Dashboard sign-in (LH-059)

The same App signs people in to the dashboard. In the App's settings:

1. **Callback URL:** `<API_PUBLIC_URL>/auth/callback`, for example
   `http://localhost:8000/auth/callback` while developing. It must equal what `API_PUBLIC_URL`
   says, character for character.
2. **Expire user authorization tokens:** leave it ticked. A session then ends when its GitHub
   token does (8 hours), and a leaked token does not live for ever.
3. **Request user authorization (OAuth) during installation:** leave it unticked, and leave
   **Webhook** off.
4. **Generate a new client secret** and note the **Client ID**.
5. Set these in `.env`, never in chat or in the repository:

   | Variable | Value |
   |---|---|
   | `GITHUB_OAUTH_CLIENT_ID` | the App's client ID (`Iv1....`) |
   | `GITHUB_OAUTH_CLIENT_SECRET` | the client secret you generated |
   | `SESSION_SECRET` | at least 32 random characters: `openssl rand -hex 32`. It encrypts the GitHub tokens kept for approvers, so changing it signs everyone out |
   | `APPROVER_GITHUB_LOGINS` | comma-separated GitHub logins that may approve and reject, for example `alice,bob`; everyone else is read-only |
   | `API_PUBLIC_URL`, `DASHBOARD_URL` | where a browser reaches the API and the dashboard |

6. Check it: `make api`, then open `http://localhost:8000/auth/login` in a browser. After you
   approve on GitHub you land on `DASHBOARD_URL`, and `http://localhost:8000/me` shows your
   login with `is_approver` true if you are on the list.

The dashboard and API are meant to share one origin (a dev proxy in Vite, a proxy on the
dashboard host in production), so the session cookie stays `SameSite=Lax`; see D-60.

Later steps (CI secrets for the workflow) are added by the tasks that need them.

## What this changes in practice

- A direct push to `main` is refused for everyone but an administrator bypassing the rule.
- A pull request cannot be merged until CI is green; the merge button stays disabled.
- Dependabot pull requests arrive on Monday mornings, go through the same CI, and are merged by a
  person. Its titles begin `build(deps):` or `ci(deps):`.
