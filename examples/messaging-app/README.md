# Example: messaging app rebuild

A small, made-up project used for the [live demo](https://milan945.github.io/repostate/) and the README walkthrough. It starts the way many projects do: a hand-kept markdown file.

- [`hub.md`](hub.md): tables of tasks (with slices, dependencies, estimates and requirements), test cases, defects, decisions, open questions and requirements.
- [`progress.md`](progress.md): a dated progress log.
- [`screenshots/`](screenshots): before and after screenshots, named after the task or bug they belong to.

[`build-demo.sh`](build-demo.sh) turns it into a repostate project: it imports both markdown files in one go, then adds a few things an agent would record during the work (hours, goals, a new bug, who a task is waiting on).

```bash
sh examples/messaging-app/build-demo.sh /tmp/demo
# open /tmp/demo/.repostate/index.html
```

To preview what the importer reads from `hub.md` in your own setup:

```bash
python3 .repostate/repostate.py import hub.md --dry-run
```
