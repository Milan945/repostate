# What the markdown importer understands

`repostate.py import <files…> [--type tasks|goals|progress|decisions] [--dry-run] [--personal]`

The type is guessed from the file name (`tasks`, `todo`, `backlog`, `plan` → tasks; `progress`, `log`, `journal`, `worklog` → progress; `goals`, `roadmap`, `milestones` → goals; `adr`, `decision`, `0007-…` → decisions). Pass `--type` when the name doesn't say. Original files are never changed. Each imported record keeps `source: {file, line}`, and importing the same file again skips items already imported.

## Tasks and goals

```markdown
## In progress                       ← heading = group; status words set the status
- [ ] Return stored response @priya #payments P1
  - [ ] Write the lookup             ← nested = subtask (parent link)
  - [x] Add the migration
- [~] Batch inventory checks !medium ← [~] or [/] = in progress

## Todo
- [ ] Move webhook retries to a worker due: 2026-12-05
- Alert on duplicate charges         ← plain bullets count too

## Blocked                           ← tagged "blocked" unless config has a blocked status
- [ ] Split webhook handling

## Done
- [x] Unique index for idempotency keys
- ~~Profile checkout hot path~~      ← strikethrough = done
```

| Markdown | Becomes |
|---|---|
| `- [ ]` / `- [x]` / `- [~]` | status: first / done / in-progress status of the type |
| Heading containing done, complete, shipped | items under it are done |
| Heading containing in progress, doing, current, wip | unchecked items are in progress |
| Heading containing todo, backlog, next, planned | todo |
| Heading containing blocked, waiting, on hold | `blocked` status if configured, else todo plus a `blocked` tag |
| `#word` | tag |
| `@name` | assignee |
| `P0` `P1` `P2` `P3`, `!urgent` `!high` `!medium` `!low`, `(high)` | priority urgent / high / medium / low |
| `due: 2026-12-05` or `by 2026-12-05` | `due` |
| Indented item | subtask with `parent` (tasks) or a line in the goal's body (goals) |
| Indented non-list text | appended to the item's `body` |

## Progress

```markdown
## 2026-11-23                        ← a date in a heading dates every entry under it
- Added unique index for idempotency keys
  - 3 files changed, tests pass      ← nested bullets become details
- 2026-11-20: Migrated key lookups   ← a leading date overrides the heading
```

## Decisions (ADR files)

One file per decision: `# Title` (a leading `7.` or `ADR-7:` is dropped), a `Status: Accepted` line or `## Status` section, and `## Context`, `## Decision`, `## Consequences` sections. Other sections go into `body`.

## When the importer isn't enough

Free-form notes, tables, or files that mix many kinds of content: read the file and create records yourself with `repostate.py add <type> --data '<json>'`, keeping the user's wording. Add `"source": {"file": "<path>"}` so the origin is visible.
