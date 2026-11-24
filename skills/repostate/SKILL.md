---
name: repostate
description: Keep a repo's tasks, goals, progress log, open questions and notes as JSON in .repostate/ with a static HTML dashboard, instead of tasks.md or progress.md files. Use when the user asks to plan or track work, add or update tasks or goals, set slices, dependencies, estimates or actual hours, see what is ready to start or what is left, log what a session did, record an open question or something waiting on another person, set up project tracking in a repo, import existing tasks.md, progress.md, goals.md, ADR files or markdown tables, or open the project dashboard. Also use at the start and end of a work session in any repo that already has a .repostate/ folder.
---

# repostate

Project state lives in the repo as one JSON file per record:

```
.repostate/
  team/<type>/<id>.json        committed: everyone with the repo sees it
  personal/<type>/<id>.json    gitignored: only this machine
  config.json                  team settings (statuses, custom fields, custom types)
  config.local.json            personal overrides, gitignored
  index.html                   the dashboard (static, no server)
  data.js                      generated for the dashboard, gitignored
  repostate.py                 the CLI, vendored so teammates can use it without this skill
```

## The one rule

**Never write or edit files in `.repostate/` by hand.** Always go through the CLI. It creates unique ids, validates fields, writes atomically, and rebuilds the dashboard data after every change. If a file was edited by hand or merged by git, run `validate`.

The CLI is `python3 .repostate/repostate.py` (use `python` on Windows if `python3` is missing). Every command accepts `--json` for machine-readable output; use it whenever you need to read results.

## Setting up a repo

If `.repostate/` does not exist, run the copy that ships with this skill, from the repo root:

```bash
python3 <this skill's folder>/scripts/repostate.py init
```

`init` creates the folder, copies the CLI and dashboard in, and lists any markdown files it can import. Add `--hooks` if the user wants the dashboard to rebuild after every pull, and `--actions` if they want a GitHub Action that posts a summary on each pull request. Running `init` again on an existing repo upgrades the CLI and dashboard and leaves the data alone.

## Importing existing markdown

When the repo has `tasks.md`, `progress.md`, `goals.md`, `TODO.md`, ADR files or similar:

1. Preview first: `python3 .repostate/repostate.py import tasks.md progress.md --dry-run`
2. Show the user the preview (counts and a few items) and ask before importing.
3. Import: the same command without `--dry-run`. Re-running is safe: items already imported are skipped.
4. Never delete or rewrite the original markdown files. Tell the user they can remove them once happy.

The importer understands checkbox lists, headings used as status sections ("Done", "In progress", "Blocked"), nesting as subtasks, `#tags`, `@assignee`, `P0`–`P3` or `!high` priorities, `due: YYYY-MM-DD`, dated progress headings, and ADR sections. It also reads **markdown tables** of tasks, test cases, defects, decisions, open questions and requirements, keeps the ids in them (`T001`, `TC-014`), and one file can hold several tables. `--update` refreshes records that already exist from changed rows; the markdown row then wins, so only use it while the markdown file is still the source of truth. See `references/import.md`. If a file is free-form prose the importer can't structure, read it yourself and create records with `add … --data '<json>'`, one per item, keeping the user's wording.

## Everyday commands

```bash
R="python3 .repostate/repostate.py"

$R list tasks --open                 # what is left (add --mine, --status doing, -q text)
$R add tasks --title "Return stored response for repeated keys" --priority high --goal G-x7k2 --tags payments
$R start T-3ghh                      # todo -> in progress
$R done T-3ghh                       # mark finished
$R update T-3ghh --assignee priya --add-depends T-78nr --set estimate_hours=3
$R add goals --title "Payments are safe to retry" --set target_date=2026-12-15
$R log "Added unique index for idempotency keys" --details "3 files, tests pass" --done T-hjza
$R add notes --title "Ask Priya about retry limits" --body "…"      # notes default to personal
$R show T-3ghh
$R open                              # build and open the dashboard
```

Ids look like `T-3ghh` (tasks), `G-` goals, `P-` progress, `D-` decisions, `Q-` questions, `R-` reviews, `N-` notes. They are random so two people on different branches never create the same id. If the project has its own numbering, pass it: `add tasks --id T037 --title …`. A project that wants `T001, T002, …` generated can set `"ids": "sequential"` on the type in `config.json` (see `references/customize.md`; two branches can then collide, so use it for solo work).

## Task details: slices, dependencies, hours, links

```bash
$R add tasks --id T037 --title "Mute a conversation" --slice D3 --estimate 6 \
   --depends T005,T006 --add requirements=MSG-09 --body "What done looks like…"
$R update T037 --add prs=#41 --add repos=acme/app --add figma=https://figma.com/file/…
$R update T037 --add evidence=https://…/recording.mp4
$R hours T037 2.5 --note "API wiring"          # adds to actual hours
$R log "Mute toggle works" --tasks T037 --hours 3   # progress entry; books 3 h on T037
$R list tasks --open --json                    # includes depends_on, slice, estimate_hours, actual_hours
```

- `--slice` groups tasks on the dashboard's Slices view, which also lists what is **ready to start**: not started, with every dependency done.
- `--depends` / `--add-depends` draw the dependency graph and its critical path. Before starting a task, check its dependencies are done.
- Record an **estimate** when a task is created and **actual hours** as work happens (`hours`, or `log --hours` naming exactly one task). The Hours view compares them per task and per slice.
- `--add key=value` appends to a list (`prs`, `repos`, `requirements`, `evidence`, `figma`, or any list field) without resending it.
- The task's detail panel shows all of this with its tests, bugs, screenshots and a timeline of status changes and log entries. Put the task's requirements and acceptance notes in `--body`.

## Open questions and waiting on others

```bash
$R add questions --title "Do we need read receipts in v1?" --owner milan --waiting-on "Client" --task T006
$R update Q-4k2p --status answered --set answer="No, v2."
$R update T011 --waiting-on "Design (Anita)"        # any record can wait on someone
$R update T011 --waiting-on ""                      # clear it
```

When work stops on a question for someone else, record it instead of leaving it in chat. The dashboard's Decisions page lists open questions and everything waiting on others, grouped by who.

Statuses come from config. Defaults: tasks `todo → doing → done`, goals `active → achieved → dropped`. Run `$R types` to see the current ones before setting a status.

## Team and personal

- Tasks, goals, progress and decisions default to **team** (committed). Reviews and notes default to **personal**.
- Use `--personal` on `add` or `log` for anything the user calls private, a scratch idea, or only for them.
- `$R promote <id>` shares a personal record with the team; `$R unshare <id>` makes it private again. Only do this when the user asks.
- Team records must not depend on personal ones (teammates can't see them); `validate` flags this.
- After changing team records, remind the user that the changes are shared when they commit `.repostate/team/`. Do not commit unless asked.

## Working habits in a repo that has .repostate/

- **Start of a session:** run `$R list tasks --open` (and `--mine` if there are many people) to pick up where things stand.
- **Starting a task:** `$R start <id>`. If the work has no task yet and is more than a quick fix, add one.
- **End of a session or after a meaningful step:** `$R log "<one-line summary>" --details "<what changed, decisions, follow-ups>" --tasks <ids> --done <finished ids>`. Keep the summary short and factual.
- **New work discovered:** add it as a task instead of leaving it only in chat.
- Code reviews go through the **repostate-review** skill; architecture decisions through **repostate-architecture**; test cases, bugs, requirement traceability and screenshots through **repostate-qa**.

## Customizing

Teams change `.repostate/config.json` to rename statuses, add fields (`estimate_hours`, `client`), hide sections or add new record types such as `bugs` or `meetings`, which get their own dashboard page. Edit that file directly (it is the one file that is meant to be hand-edited), then run `$R validate`. The shapes are in `references/customize.md`. Record fields for each type are in `references/schema.md`.

## Exporting

`$R export --out docs/state` writes `tasks.md`, `goals.md`, `progress.md` and so on from the JSON, for people who want markdown. Add `--personal` to include private records.
