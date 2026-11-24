---
name: repostate-qa
description: Track QA in .repostate/ - test cases with results (pass, pass with question, fail, blocked, not run), bugs linked to tasks and test cases, requirement traceability (requirement to task to test), and screenshot evidence per task - so the repostate dashboard shows a test matrix, pass rates, open and fixed bug counts, coverage gaps and before/after screenshots. Use when the user runs or records tests, reports or fixes a bug or defect, asks about test coverage or untested requirements, wants UI before/after screenshots attached to a task, or wants to import a test-case table or defects table from markdown.
---

# repostate-qa

QA data lives beside tasks as four record types. Never write the files by hand; use the CLI, `python3 .repostate/repostate.py` (shown as `$R`). If `.repostate/` does not exist, set it up with the **repostate** skill first.

| Type | Default ids | Links | Status values |
|---|---|---|---|
| `testcases` | `TC-…` | `task`, `bug`, `requirements` | `not_run`, `pass`, `pass_with_question`, `fail`, `blocked` |
| `bugs` | `B-…` | `task`, `testcase`, `fix_commit` | `open`, `fixing`, `fixed`, `wontfix` |
| `requirements` | your own, e.g. `MSG-01` | linked from tasks and test cases | none |
| evidence | image files | matched to a record by its id in the path | |

Pass `--id` to use the project's own numbering (`--id TC-014`, `--id BUG-007`, `--id MSG-03`).

## Test cases

```bash
$R add testcases --id TC-014 --title "Badge caps at 99+" --task T003 --kind ui \
   --set expected="Shows 99+ for 100 or more unread" --add requirements=MSG-03
$R update TC-014 --result pass
$R update TC-014 --result pass_with_question --set status_note="Is 99+ right for tablets too?"
$R update TC-014 --result fail --set bug=BUG-007
$R list testcases --status fail,blocked
```

- `--kind` is free text; use `ui` or `api` unless the project has its own kinds.
- Use `pass_with_question` when the test passes but something needs a human answer; put the question in `status_note`, and add it as an open question (`$R add questions --title "…" --task T003 --waiting-on "Design"`) if someone else must answer.
- Use `blocked` when the test could not be run, and say why in `status_note`.
- After a test run, update every case you ran, then log it: `$R log "Ran slice A UI tests: 5 pass, 2 fail" --tasks T002`.

## Bugs

```bash
$R add bugs --id BUG-007 --title "Badge shows 100 instead of 99+" --task T003 --severity medium \
   --set testcase=TC-014 --body "Steps: open a conversation list with 100 unread…"
$R update TC-014 --result fail --set bug=BUG-007      # point the failing test at it
$R start BUG-007                                      # open -> fixing
$R update BUG-007 --status fixed --set fix_commit=a1b2c3d
$R update TC-014 --result pass                        # after re-testing
```

Severity is `low`, `medium`, `high` or `critical`. A bug found by a test should carry both `task` and `testcase`. When a fix lands, record `fix_commit` (short or full hash; the dashboard links it when the repo is on GitHub or GitLab) and re-run the test that found it.

## Requirements and traceability

Tasks and test cases name the requirements they cover:

```bash
$R add requirements --id MSG-03 --title "Unread count is visible"
$R update T003 --add requirements=MSG-03
$R update TC-014 --add requirements=MSG-03
```

A test case with no `requirements` of its own is counted against its task's requirements (shown as "via task"). The dashboard's **Traceability** page shows each requirement with its tasks and tests and one of: `verified`, `in progress`, `failing`, `no test`, `no task`. It also lists tests and tasks that have no requirement. Requirement records are optional: ids used on tasks still appear in the matrix. Once requirement records exist, `validate` flags any id that is not in the list.

To answer "what is untested?": `$R list requirements --json`, `$R list tasks --json` and `$R list testcases --json`, or open the Traceability page and choose "Gaps only".

## Evidence (screenshots)

The dashboard picks up images automatically from `screenshots/` and `ui-review/` (change the folders with `evidence.dirs` in `config.json`). An image belongs to a record when the record's id appears in the file name or a folder name:

```
screenshots/T002-before.png      -> task T002, labelled "before"
screenshots/T002-after.png       -> task T002, labelled "after"
ui-review/T002/long-names.png    -> task T002
screenshots/BUG-007-repro.png    -> bug BUG-007
```

For UI changes, save a before and an after screenshot with those words in the names. For anything stored elsewhere, attach a link: `$R update T002 --add evidence=https://…`. Run `$R build` after adding files so the dashboard sees them. Images show on the task's detail panel and on the **Evidence** page; images that match no record are listed there so they can be renamed.

## Importing existing tables

Hand-kept tables import directly; each table's type is recognised from its columns or the heading above it:

```bash
$R import docs/hub.md --dry-run        # preview: tasks, test cases, defects, decisions, questions, requirements
$R import docs/hub.md                  # ids in the table are kept (T001, TC-014, BUG-007)
$R import docs/hub.md --update         # later: refresh existing records from changed rows
```

`--update` makes the markdown row win for every column it has, so use it only while the markdown file is still the source of truth. Column names the importer understands are listed in the repostate skill's `references/import.md`. Results such as "Pass", "✅", "Pass with question: …", "Failed", "Blocked" and "Not run" are mapped to the five statuses; anything it cannot map becomes `not_run` with the original text kept in `status_note`.

## Reporting to the user

After recording QA work, summarise in chat: tests run and their results, new bugs with severity, and anything waiting on someone. Point to the dashboard's Tests, Bugs and Traceability pages for detail.
