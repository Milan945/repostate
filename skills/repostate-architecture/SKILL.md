---
name: repostate-architecture
description: Record architecture decisions and structural changes (new services or modules, data store or schema changes, changed boundaries, dependency swaps, new integrations) as decision records in .repostate/, with context, before and after, affected components and linked tasks, so they show in the repostate dashboard. Use when the user proposes, makes or asks to document an architectural change, asks what architecture changed in a branch, or wants an ADR.
---

# repostate-architecture

A decision record (`D-…`) captures one architectural choice: why it came up, what was chosen, what it changes, and what it replaced. Decisions are **team** records by default, because architecture concerns everyone with the repo.

The CLI is `python3 .repostate/repostate.py`. If `.repostate/` does not exist yet, set it up with the **repostate** skill first. Never write decision files by hand.

## When to record one

Record a decision when a change is hard to reverse or changes how parts of the system fit together:

- adding, removing, splitting or merging a service, module, package or worker
- changing a data store, schema shape, or where a piece of data lives
- changing a public API, event format or integration contract
- adopting or dropping a framework, library or infrastructure component
- a cross-cutting rule (auth model, error handling, caching strategy)

Do not record routine refactors, bug fixes or style changes.

## Statuses

`proposed` (being discussed, not built) → `accepted` (agreed or built) → later `superseded` (replaced by a newer decision) or `rejected` (decided against). If the user is only exploring an idea, save it as `proposed`; switch to `accepted` when they confirm or the change lands.

## Saving a decision

```bash
python3 .repostate/repostate.py add decisions --status proposed --data - <<'EOF'
{
  "title": "Split webhook handling into its own worker",
  "context": "Provider retries currently run on request threads, so a burst of webhooks slows checkout.",
  "decision": "Webhooks are written to a queue by the API and processed by a separate worker.",
  "consequences": "One more deployable and a queue to operate. Checkout latency no longer depends on webhook volume.",
  "alternatives": ["Rate-limit webhooks in the API", "Process webhooks inline but async"],
  "before": "API handles webhooks inline",
  "after": "API → queue → webhook worker",
  "components": ["webhooks/", "infra/queue.tf"],
  "tasks": ["T-78nr"]
}
EOF
```

Write `before` and `after` as one short line each, using arrows for flows. `components` are paths or module names. `tasks` links the work that implements it; for new work, create the tasks first with `add tasks … --tags architecture`.

Add `"owner": "<name>"` when someone owns the decision, and `"waiting_on": "<name>"` while it waits on another person; the dashboard's Decisions page shows owner and status in a table and lists what is waiting on others. A question that is not yet a decision goes in as an open question: `add questions --title "…" --owner … --waiting-on …` (see the repostate skill).

Confirm in chat with the id, the status, and one sentence on the change.

## Replacing an earlier decision

```bash
$R add decisions --status accepted --data '{"title": "…", "supersedes": "D-0old", …}'
$R update D-0old --status superseded --set superseded_by=D-new
```

## "What architecture changed in this branch?"

1. Look at `git diff --stat $(git merge-base main HEAD)..HEAD` and the changed files for structural signals: new top-level folders or packages, new services or Dockerfiles, migrations, changed dependency manifests, new queues or external clients, renamed public interfaces.
2. List existing decisions: `$R list decisions --json`, and match changes to them.
3. Report to the user: changes already covered by a decision, and changes that look architectural but have none.
4. Offer to record the uncovered ones as `proposed`, and create them only when the user agrees.

## Before and after diagrams

`before` and `after` are short text so they show on the dashboard cards. For a fuller picture, put a Mermaid diagram in `body` inside a fenced block; the dashboard shows it as plain text in the decision's detail view.
