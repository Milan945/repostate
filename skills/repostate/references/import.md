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

## Tables

A markdown table becomes one record per row. One file can hold several tables of different kinds; each table's type comes from `--type`, else its columns, else the heading above it, else the file name (`tests.md`, `defects.md`, `requirements.md`, `questions.md`, `tasks.md`).

```markdown
## Tasks
| ID   | Task                 | Slice | Status      | Depends on | Est dev h | Actual h | Requirements | PR  |
|------|----------------------|-------|-------------|------------|-----------|----------|--------------|-----|
| T001 | Conversation list API| A     | ✅ Done     | —          | 6         | 7        | MSG-01       | #12 |
| T004 | Message thread API   | B     | In progress | T001       | 8         | 3        | MSG-04       |     |

## Test cases
| TC     | Scenario           | Task | Kind | Result                         | Bug     |
|--------|--------------------|------|------|--------------------------------|---------|
| TC-005 | Long names truncate| T002 | UI   | Fail                           | BUG-001 |
| TC-003 | Empty state        | T002 | UI   | Pass with question: copy final? |         |

## Defects
| ID      | Defect                  | Severity | Status | Task | Test case | Fix commit |
```

| Column heading (any of) | Field |
|---|---|
| ID, #, Key; or the type's own (TC, Test ID, Bug ID, Req) | the record's id, kept as written (a bare number gets the type's prefix) |
| Title, Name, Summary, Description, Scenario; or the type's own (Task, Test case, Defect, Question, Decision, Requirement) | `title` |
| Status, State, Result, Outcome | `status`, mapped to the type's statuses; words and ✅ ❌ 🚧 ⏳ are understood |
| Slice, Phase, Milestone, Epic, Sprint | `slice` |
| Depends on, Dependencies, Deps, Blocked by, After | `depends_on` |
| Estimate, Est, Est h, Dev h, Est dev h | `estimate_hours` |
| Actual, Actual h, Spent | `actual_hours` |
| Requirements, Reqs, Covers | `requirements` |
| Task (in test, bug, question tables) | `task` |
| Test case, TC (in bug tables) | `testcase` |
| Bug, Defect (in test tables) | `bug` |
| Kind, Type, Layer | `kind` |
| Severity, Sev | `severity` (Blocker/Major/Minor and S1–S4 are mapped) |
| Fix commit, Commit, Fixed in | `fix_commit` |
| PR, Pull request · Repo · Figma, Design · Evidence, Screenshots | `prs` · `repos` · `figma` · `evidence` (markdown links keep their URL) |
| Assignee, Owner, Who · Waiting on · Answer · Due · Tags · Notes | `assignee`/`owner` · `waiting_on` · `answer` · `due` · `tags` · `body` |
| anything else | kept under the heading's name in snake_case |

- `-`, `—`, `n/a`, `none` and empty cells are skipped.
- A status the importer cannot map becomes the type's first status, and the original text is kept in `status_note`.
- Rows whose id already exists are skipped. With `--update` they are refreshed instead: every non-empty cell overwrites the record's field. Use it only while the markdown table is still the source of truth, because edits made through the CLI to those fields are replaced.
- Links between rows (dependencies, task, bug) may point at rows imported later; run `validate` afterwards to catch ids that never appeared.

## When the importer isn't enough

Free-form notes, tables, or files that mix many kinds of content: read the file and create records yourself with `repostate.py add <type> --data '<json>'`, keeping the user's wording. Add `"source": {"file": "<path>"}` so the origin is visible.
