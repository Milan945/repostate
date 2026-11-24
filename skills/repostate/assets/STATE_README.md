# .repostate

This folder holds the project's tasks, goals, progress log, test cases, bugs, code reviews and decisions as JSON. It is managed by [repostate](https://github.com/Milan945/repostate).

## See the dashboard

```bash
python3 .repostate/repostate.py open      # builds the data and opens the dashboard
```

Or run `python3 .repostate/repostate.py build`, then open `.repostate/index.html` in a browser. No server and no install are needed. Python 3.8 or newer is the only requirement.

To have the dashboard rebuild itself after every `git pull`, checkout or merge, run this once per clone:

```bash
python3 .repostate/repostate.py hooks
```

## What is shared and what is private

| Folder | In git? | Who sees it |
|---|---|---|
| `team/` | Committed | Everyone with access to this repo |
| `personal/` | Gitignored | Only you, on this machine |
| `config.json` | Committed | Team settings: statuses, custom fields, custom types |
| `config.local.json` | Gitignored | Your own overrides |
| `data.js` | Gitignored | Generated for the dashboard |

Move a record between them with `repostate.py promote <id>` (personal → team) or `repostate.py unshare <id>`.

## Editing

Agents and people should change records through `repostate.py` (`add`, `update`, `done`, `log`, `review …`) rather than by hand, so ids stay unique and files stay valid. Run `python3 .repostate/repostate.py --help` for every command, and `validate` to check the files after a manual edit or a merge.
