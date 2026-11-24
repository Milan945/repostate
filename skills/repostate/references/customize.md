# Customizing with config.json

`.repostate/config.json` is committed and applies to the whole team. `.repostate/config.local.json` has the same shape, is gitignored, and is merged on top for one person. Both are deep-merged over the defaults, so only write what you change. After editing, run `python3 .repostate/repostate.py validate`.

## Full example

```json
{
  "version": 1,
  "project": { "name": "Checkout API", "accent": "#0F766E" },
  "priorities": ["low", "medium", "high", "urgent"],
  "hidden": ["notes"],
  "types": {
    "tasks": {
      "statuses": ["backlog", "todo", "doing", "review", "done"],
      "done": ["done"],
      "fields": {
        "estimate_hours": { "type": "number" },
        "client": { "type": "enum", "options": ["acme", "globex"] },
        "billable": { "type": "bool" }
      }
    },
    "reviews": { "scope": "team" },
    "bugs": {
      "prefix": "B",
      "label": "Bugs",
      "view": "board",
      "scope": "team",
      "statuses": ["new", "confirmed", "fixing", "fixed"],
      "fields": {
        "severity": { "type": "enum", "options": ["low", "medium", "high", "critical"], "required": true },
        "reported_by": { "type": "string" }
      }
    },
    "meetings": { "prefix": "M", "label": "Meetings", "view": "timeline", "statuses": [] }
  }
}
```

## Keys

| Key | What it does |
|---|---|
| `project.name` | Title in the dashboard. |
| `project.accent` | Accent color, as a hex value. |
| `priorities` | Allowed `priority` values, lowest first. |
| `hidden` | Types to leave out of the dashboard navigation. Data is still kept. |
| `types.<name>.statuses` | Allowed statuses in order. The first is the default for new records. Empty list = no status. |
| `types.<name>.done` | Which statuses count as finished. Defaults to the last status. |
| `types.<name>.fields` | Extra fields: `type` is `string`, `number`, `bool`, `list`, `date` or `enum` (with `options`). `required: true` makes `add` refuse records without it. `--set key=value` converts values to the declared type. |
| `types.<name>.scope` | `team` or `personal`: where new records go when `add` gets neither `--team` nor `--personal`. |
| `types.<name>.label` | Name in the dashboard. |
| `types.<name>.prefix` | Id prefix for a new type (one or two capital letters). Do not change it for a type that already has records. |
| `types.<name>.view` | Dashboard layout for a new type: `board` (columns by status), `table`, `timeline` or `cards`. |

## Adding a type

Add it under `types`, then create records with `repostate.py add <name> --title …`. It gets its own page in the dashboard automatically. A fully custom layout for one type needs a change to `index.html`; the generic views cover most needs.

## Changing statuses on a type that already has records

Records with a status that is no longer listed fail `validate` and appear under a note on the board. Move them first (`update <id> --status <new>`), or keep the old status in the list until they are moved.
