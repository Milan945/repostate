---
name: repostate-review
description: Review code changes and save the review as structured findings in .repostate/, scoped to a branch, a worktree, the current session's changes, a commit range or uncommitted work, so it shows in the repostate dashboard. Use when the user asks for a code review, a review of this branch, PR or session, a re-review after fixes, or to save or share review findings.
---

# repostate-review

A review is one record with a **scope** (what was reviewed) and a list of **findings**. Reviews are personal by default: they belong to one branch or session and are not committed unless the user shares them.

The CLI is `python3 .repostate/repostate.py`. If `.repostate/` does not exist yet, set it up first with the **repostate** skill (`python3 <repostate skill folder>/scripts/repostate.py init`). Never write review files by hand.

## 1. Decide the scope

Pick the narrowest scope that matches the request, and say which one you picked.

| User says | `--scope` | What to diff |
|---|---|---|
| "review this branch / PR" (default) | `branch` | `git diff $(git merge-base <base> HEAD)..HEAD`; base is the repo's default branch unless they name one (`--base develop`) |
| "review what you just did / this session" | `session` | the files you changed in this session; pass `--session <id>` if you know one |
| "review this worktree" | `worktree` | same as branch, from inside that worktree |
| "review commits X..Y" | `commits` | `git diff X..Y`, pass `--range X..Y` |
| "review my uncommitted changes" | `working` | `git diff HEAD` plus untracked files |

The CLI fills in branch, base, commit hashes, worktree and file count from git.

## 2. Review

Read the diff and enough surrounding code to judge it. Look for, in this order: correctness and data loss, security, concurrency and failure handling, API or schema breaks, missing tests for risky paths, then maintainability. Only report what you can point to in the code. Each finding needs a file and, where possible, a line.

Severity:

- **blocking**: wrong behaviour, data loss, security hole, or a break that must be fixed before merge.
- **major**: likely bug, risky pattern or missing test for risky code; should be fixed in this branch.
- **minor**: clarity, naming, small duplication; fine to fix later.
- **note**: observation or question, no change required.

## 3. Save it in one call

Write the whole review as JSON on stdin:

```bash
python3 .repostate/repostate.py review new --scope branch --base main --data - <<'EOF'
{
  "title": "Review of feat/idempotent-payments",
  "summary": "Retry safety is close; one crash window remains between the charge and the key write.",
  "findings": [
    {"severity": "blocking", "file": "payments/charge.ts", "line": 88,
     "title": "Key written after the Stripe call",
     "body": "A crash between the two calls allows a double charge.",
     "suggestion": "Insert the key in the same transaction before calling the provider."},
    {"severity": "minor", "file": "payments/keys.ts", "line": 31, "title": "TTL constant duplicated"}
  ]
}
EOF
```

It prints the review id (`R-…`). Findings get ids `F1`, `F2`, … in order. To add one later: `review finding R-bn57 --severity major --file x.ts --line 12 --title "…" --body "…"`.

Then give the user a short summary in chat: counts by severity and the blocking items. The dashboard's **Code reviews** page has the full list.

## 4. Follow-ups

- **Turn findings into work** when the user wants them fixed: `add tasks --title "<finding title>" --priority high --tags review`, then link it: `review link R-bn57 F1 T-3ghh`. The dashboard shows the task on the finding. Mention the task ids in your summary.
- **After fixes**, re-check each open finding and mark it: `review resolve R-bn57 F1 --status fixed --note "key now written first"` (or `wontfix` with a reason).
- **Outdated reviews**: the dashboard marks a review outdated when its branch has new commits. For a re-review, create a new review rather than editing the old one, and resolve the old one's findings that are fixed.
- **Close** a finished review: `update R-bn57 --status closed`.
- **Share with the team** only when asked (for example to attach to a PR): `promote R-bn57`. It is then committed with `.repostate/team/`.
- **Clean up** after merges: `archive` moves reviews of merged or deleted branches into the archive (shown with "Show archived" in the dashboard). Use `--dry-run` first.
