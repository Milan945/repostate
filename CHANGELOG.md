# Changelog

## 0.3.1 - 2026-11-24

- Live demo on GitHub Pages, built from the example project in `examples/messaging-app`.
- Demo walkthrough at the top of the README.
- Dashboard: the task detail header no longer has the hours bar drawn over it while scrolling.
- Dashboard: the overview leaves out the Goals panel when a project has no goals, and keeps up to six summary tiles on one row.
- Releases are published automatically when a version tag is pushed.

## 0.3.0 - 2026-11-24

- **One JSON file per type** (`team/tasks.json`, `team/testcases.json`, …) with one record per line, instead of one file per record.
- `init` converts repos set up with earlier versions; the old folders are still read until then.
- A git merge driver, turned on by `repostate.py hooks`, merges two branches record by record.
- `summary --since` compares records, so pull request comments work across the layout change.

## 0.2.0 - 2026-11-24

- New built-in types: **test cases**, **bugs**, **requirements** and **open questions**.
- Tasks gain slice, estimate and actual hours, requirements, repos, pull requests, Figma and evidence links, and a history of status changes.
- Custom ids (`--id T001`) and optional sequential ids; `hours` command and `log --hours`.
- Import of markdown **tables** (tasks, tests, defects, decisions, questions, requirements), keeping their ids; `--update` refreshes records from changed rows.
- Dashboard: task detail panel, test matrix and pass-rate bars, bugs per task, slice board with "ready to start", dependency graph with the critical path, estimate against actual hours, evidence gallery, requirement traceability, decisions with open questions and "waiting on others".
- New `repostate-qa` skill.

## 0.1.0 - 2026-11-24

- First release: the `repostate`, `repostate-review` and `repostate-architecture` skills, the CLI, markdown import and export, the static dashboard, team and personal scopes, the Claude Code plugin and the pull request summary Action.
