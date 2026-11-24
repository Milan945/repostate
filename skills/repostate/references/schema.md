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

Statuses: `proposed → accepted → superseded → rejected`.

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
