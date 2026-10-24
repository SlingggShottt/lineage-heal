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

## What this changes in practice

- A direct push to `main` is refused for everyone but an administrator bypassing the rule.
- A pull request cannot be merged until CI is green; the merge button stays disabled.
- Dependabot pull requests arrive on Monday mornings, go through the same CI, and are merged by a
  person. Its titles begin `build(deps):` or `ci(deps):`.
