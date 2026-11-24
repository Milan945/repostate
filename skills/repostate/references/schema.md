# Record shapes

Every record is a JSON object in `.repostate/<scope>/<type>/<id>.json`. The CLI sets `id`, `author`, `created_at` and `updated_at`; everything else comes from flags, `--set key=value` or `--data '<json>'`. Unknown fields are kept and shown in the dashboard, so teams can add their own.

Common fields on any type:

| Field | Type | Notes |
|---|---|---|
| `id` | string | `<prefix>-<random>`, e.g. `T-3ghh`. Never change it. |
| `title` | string | Required on every type except progress. |
| `status` | string | Must be one of the type's statuses (`repostate.py types`). |
| `body` | markdown | Longer description. |
| `tags` | list of strings | |
| `author` | string | git user.email of whoever created it. |
| `created_at`, `updated_at`, `done_at` | ISO 8601 UTC | `done_at` is set when the status becomes a done status. |
| `source` | object | `{file, line}` when imported from markdown. |

## tasks (`T-`)

```json
{
  "id": "T-3ghh", "title": "Return stored response for repeated keys",
  "status": "doing", "priority": "high",
  "goal": "G-89hu", "parent": "T-0abc", "depends_on": ["T-78nr"],
  "assignee": "priya", "tags": ["payments"], "due": "2026-12-05",
  "body": "…", "author": "milan@example.com",
  "created_at": "2026-11-24T05:10:00Z", "updated_at": "2026-11-24T06:00:00Z"
}
```

`priority` is one of `config.priorities` (default `low, medium, high, urgent`). `goal`, `parent` and `depends_on` must point at existing ids.

More task fields, all optional:

| Field | Type | Shown as |
|---|---|---|
| `slice` | string | Slices view, slice progress and hours |
| `estimate_hours`, `actual_hours` | number | Hours view; `hours <id> <n>` adds to actual |
| `requirements` | list of ids | chips on the task, Traceability page |
| `repos` | list (`owner/repo` or URL) | links |
| `prs` | list (`#12`, `owner/repo#12` or URL) | links |
| `figma` | list of URLs | links |
| `evidence` | list of URLs or repo paths | links, or thumbnails for images |
| `waiting_on` | string | "Waiting on others" list |
| `history` | list | written by the CLI on every status change and `hours`; drives the task timeline |

Put the task's requirements text and acceptance notes in `body`.

## goals (`G-`)

```json
{ "id": "G-89hu", "title": "Payments are safe to retry", "status": "active",
  "target_date": "2026-12-15", "body": "No double charges after a crash." }
```

Progress on a goal is computed from the tasks whose `goal` points at it.

## progress (`P-`)

```json
{ "id": "P-hkst", "summary": "Added unique index for idempotency keys",
  "details": "3 files, tests pass", "tasks": ["T-hjza"], "files": ["db/migrations/0042.sql"],
  "branch": "feat/idempotent-payments", "commit": "ce64f4e", "session": "…" }
```

`summary` replaces `title`. `log` fills `branch` and `commit` from git.

## decisions (`D-`): architecture changes

```json
{ "id": "D-sqc3", "title": "Idempotency keys stored in Postgres, not Redis",
  "status": "accepted",
  "context": "Why this came up.", "decision": "What we chose.",
  "consequences": "What changes because of it.",
  "alternatives": ["Keep Redis with persistence", "Store in Stripe metadata"],
  "before": "API → Redis → Stripe", "after": "API → Postgres → Stripe",
  "components": ["payments/", "db/migrations/"], "tasks": ["T-hjza"],
  "supersedes": "D-0old", "superseded_by": null }
```

Statuses: `proposed → accepted → superseded → rejected`. Optional `owner` and `waiting_on`.

## testcases (`TC-`)

```json
{ "id": "TC-014", "title": "Badge caps at 99+", "status": "fail", "kind": "ui",
  "task": "T003", "bug": "BUG-007", "requirements": ["MSG-03"],
  "steps": "…", "expected": "Shows 99+", "status_note": "…", "evidence": [] }
```

`status` is the result: `not_run`, `pass`, `pass_with_question`, `fail`, `blocked`. `pass` and `pass_with_question` count as passing. A test with no `requirements` inherits its task's for traceability.

## bugs (`B-`)

```json
{ "id": "BUG-007", "title": "Badge shows 100 instead of 99+", "status": "open",
  "severity": "medium", "task": "T003", "testcase": "TC-014", "fix_commit": "a1b2c3d" }
```

Statuses `open → fixing → fixed`, or `wontfix`. `severity`: `low`, `medium`, `high`, `critical`.

## requirements (`REQ-`, usually your own ids)

```json
{ "id": "MSG-03", "title": "Unread count is visible", "body": "…" }
```

No status: the Traceability page works it out from the tasks and tests that name the id.

## questions (`Q-`)

```json
{ "id": "Q-4k2p", "title": "Do we need read receipts in v1?", "status": "open",
  "owner": "milan", "waiting_on": "Client", "task": "T006", "answer": null }
```

Statuses `open → answered`. Shown on the Decisions page.

## reviews (`R-`): code reviews

```json
{ "id": "R-bn57", "title": "Payments branch review", "status": "open",
  "summary": "Retry safety is close; one crash window remains.",
  "scope": { "type": "branch", "branch": "feat/idempotent-payments", "base": "main",
             "base_sha": "54117e7", "head_sha": "ce64f4e", "worktree": "checkout-api",
             "files_changed": 14, "session": null, "range": null },
  "findings": [
    { "id": "F1", "severity": "blocking", "file": "payments/charge.ts", "line": 88,
      "title": "Key written after the Stripe call", "body": "…", "suggestion": "…",
      "status": "open", "task": "T-3ghh", "resolution": null } ] }
```

- `scope.type`: `branch`, `session`, `worktree`, `commits` (with `range`) or `working` (uncommitted changes).
- `severity`: `blocking`, `major`, `minor`, `note`.
- finding `status`: `open`, `fixed`, `wontfix`.
- The dashboard marks a review **outdated** when its branch has moved past `head_sha`, and **branch deleted** when the branch no longer exists.

## notes (`N-`)

`title` and `body`. Personal by default.

## Custom types

Any type added under `types` in `config.json` (see customize.md). Records use the common fields plus whatever `fields` the type declares.
