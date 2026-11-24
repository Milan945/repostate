#!/usr/bin/env python3
"""repostate: keep a repo's tasks, goals, progress, reviews and decisions as JSON.

Each type is one JSON file, .repostate/<scope>/<type>.json, holding every record
of that type, one record per line. Scope is "team" (committed to git) or
"personal" (gitignored). The viewer,
.repostate/index.html, reads .repostate/data.js, which `build` regenerates
after every write.

Standard library only. Python 3.8+. Run `repostate.py --help` for commands.
"""

import argparse
import datetime as _dt
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import webbrowser

VERSION = "0.3.2"
STATE_DIR = ".repostate"
SCOPES = ("team", "personal")
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
TYPE_ALIASES = {"tests": "testcases", "test": "testcases", "defects": "bugs", "defect": "bugs",
                "reqs": "requirements", "req": "requirements"}

BUILTIN_TYPES = {
    "tasks": {
        "prefix": "T", "label": "Tasks", "view": "board", "scope": "team",
        "statuses": ["todo", "doing", "done"], "done": ["done"],
        "fields": {
            "slice": {"type": "string"}, "estimate_hours": {"type": "number"},
            "actual_hours": {"type": "number"}, "requirements": {"type": "list"},
            "repos": {"type": "list"}, "prs": {"type": "list"},
            "evidence": {"type": "list"}, "figma": {"type": "list"},
        },
    },
    "goals": {
        "prefix": "G", "label": "Goals", "view": "cards", "scope": "team",
        "statuses": ["active", "achieved", "dropped"], "done": ["achieved"],
    },
    "progress": {
        "prefix": "P", "label": "Progress", "view": "timeline", "scope": "team",
        "statuses": [], "done": [],
    },
    "decisions": {
        "prefix": "D", "label": "Decisions", "view": "cards", "scope": "team",
        "statuses": ["proposed", "accepted", "superseded", "rejected"], "done": ["accepted"],
        "fields": {"owner": {"type": "string"}, "waiting_on": {"type": "string"}},
    },
    "testcases": {
        "prefix": "TC", "label": "Tests", "view": "table", "scope": "team",
        "statuses": ["not_run", "pass", "pass_with_question", "fail", "blocked"],
        "done": ["pass", "pass_with_question"],
        "fields": {"task": {"type": "string"}, "kind": {"type": "string"}, "bug": {"type": "string"},
                   "requirements": {"type": "list"}, "evidence": {"type": "list"}},
    },
    "bugs": {
        "prefix": "B", "label": "Bugs", "view": "table", "scope": "team",
        "statuses": ["open", "fixing", "fixed", "wontfix"], "done": ["fixed", "wontfix"],
        "fields": {"task": {"type": "string"}, "testcase": {"type": "string"},
                   "severity": {"type": "enum", "options": ["low", "medium", "high", "critical"]},
                   "fix_commit": {"type": "string"}, "evidence": {"type": "list"}},
    },
    "requirements": {
        "prefix": "REQ", "label": "Requirements", "view": "table", "scope": "team",
        "statuses": [], "done": [],
    },
    "questions": {
        "prefix": "Q", "label": "Open questions", "view": "table", "scope": "team",
        "statuses": ["open", "answered"], "done": ["answered"],
        "fields": {"owner": {"type": "string"}, "waiting_on": {"type": "string"},
                   "task": {"type": "string"}, "answer": {"type": "string"}},
    },
    "reviews": {
        "prefix": "R", "label": "Code reviews", "view": "list", "scope": "personal",
        "statuses": ["open", "closed"], "done": ["closed"],
    },
    "notes": {
        "prefix": "N", "label": "Notes", "view": "list", "scope": "personal",
        "statuses": [], "done": [],
    },
}

DEFAULT_CONFIG = {
    "version": 1,
    "project": {"name": "", "accent": "#2563EB"},
    "priorities": ["low", "medium", "high", "urgent"],
    "types": {},
    "hidden": [],
    "reviews": {"archive_merged": True},
    "evidence": {"dirs": ["screenshots", "ui-review"]},
    "slices": {},
}

SEVERITIES = ["blocking", "major", "minor", "note"]
FINDING_STATUSES = ["open", "fixed", "wontfix"]
REVIEW_SCOPES = ["branch", "session", "worktree", "commits", "working"]

GITIGNORE = """# repostate: private and generated files stay out of git
personal/
config.local.json
data.js
.lock
"""


GITATTRIBUTES = """# repostate: merge type files record by record (enabled per clone by `repostate.py hooks`)
team/*.json merge=repostate
team/archive/*.json merge=repostate
"""

MERGE_DRIVER = ('PY=python3; command -v python3 >/dev/null 2>&1 || PY=python; '
                '"$PY" %s/repostate.py merge-file %%O %%A %%B' % STATE_DIR)


class StateError(Exception):
    pass


# --------------------------------------------------------------------------
# small helpers

def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git(args, cwd, check=False):
    try:
        out = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True)
    except OSError:
        if check:
            raise StateError("git is not installed")
        return ""
    if out.returncode != 0:
        if check:
            raise StateError("git %s failed: %s" % (" ".join(args), out.stderr.strip()))
        return ""
    return out.stdout.strip()


def deep_merge(base, extra):
    result = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def write_text_atomic(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def coerce(value, field_def=None):
    """Turn a command-line string into the value the field expects."""
    ftype = (field_def or {}).get("type")
    if ftype == "list":
        return [v.strip() for v in value.split(",") if v.strip()]
    if ftype == "string":
        return value
    if ftype == "number" or ftype is None:
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
        if ftype == "number":
            raise StateError("expected a number, got %r" % value)
    if ftype == "bool" or ftype is None:
        if value.lower() in ("true", "yes"):
            return True
        if value.lower() in ("false", "no"):
            return False
        if ftype == "bool":
            raise StateError("expected true or false, got %r" % value)
    if value.lower() == "null":
        return None
    return value


def split_list(values):
    out = []
    for v in values or []:
        out.extend(x.strip() for x in v.split(",") if x.strip())
    return out


# --------------------------------------------------------------------------
# the store

class Store:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.dir = os.path.join(self.root, STATE_DIR)
        self._config = None

    # ---- setup
    @classmethod
    def locate(cls, root=None):
        if root:
            return cls(root)
        top = git(["rev-parse", "--show-toplevel"], os.getcwd())
        return cls(top or os.getcwd())

    def exists(self):
        return os.path.isfile(os.path.join(self.dir, "config.json"))

    def require(self):
        if not self.exists():
            raise StateError("no %s/ here. Run `repostate.py init` in the repo root first." % STATE_DIR)

    # ---- config
    @property
    def config(self):
        if self._config is None:
            cfg = DEFAULT_CONFIG
            for name in ("config.json", "config.local.json"):
                path = os.path.join(self.dir, name)
                if os.path.isfile(path):
                    try:
                        cfg = deep_merge(cfg, read_json(path))
                    except ValueError as exc:
                        raise StateError("%s is not valid JSON: %s" % (path, exc))
            self._config = cfg
        return self._config

    def types(self):
        result = {}
        for name, spec in BUILTIN_TYPES.items():
            result[name] = deep_merge(spec, self.config.get("types", {}).get(name, {}))
        for name, spec in self.config.get("types", {}).items():
            if name not in result:
                base = {"prefix": name[:1].upper(), "label": name.replace("_", " ").title(),
                        "view": "table", "scope": "team", "statuses": [], "done": []}
                result[name] = deep_merge(base, spec)
        for spec in result.values():
            statuses = spec.get("statuses") or []
            done = [d for d in (spec.get("done") or []) if d in statuses]
            spec["done"] = done or statuses[-1:]
        return result

    def type_spec(self, rtype):
        types = self.types()
        rtype = TYPE_ALIASES.get(rtype, rtype) if rtype not in types else rtype
        if rtype not in types:
            singular = {k.rstrip("s"): k for k in types}
            if rtype in singular:
                return singular[rtype], types[singular[rtype]]
            raise StateError("unknown type %r. Known types: %s. Add new ones under \"types\" in config.json."
                             % (rtype, ", ".join(sorted(types))))
        return rtype, types[rtype]

    # ---- identity and git context
    def me(self):
        return {
            "name": git(["config", "user.name"], self.root),
            "email": git(["config", "user.email"], self.root),
        }

    def author(self):
        m = self.me()
        return m["email"] or m["name"] or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

    def branch(self):
        return git(["rev-parse", "--abbrev-ref", "HEAD"], self.root)

    def head(self):
        return git(["rev-parse", "--short", "HEAD"], self.root)

    # ---- locking: an O_EXCL lock file works on every OS
    def lock(self):
        path = os.path.join(self.dir, ".lock")
        store = self

        class _Lock:
            def __enter__(self_inner):
                deadline = time.time() + 15
                while True:
                    try:
                        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.write(fd, str(os.getpid()).encode())
                        os.close(fd)
                        return store
                    except FileExistsError:
                        try:
                            if time.time() - os.path.getmtime(path) > 30:
                                os.remove(path)
                                continue
                        except OSError:
                            continue
                        if time.time() > deadline:
                            raise StateError("another repostate command is still writing (%s)" % path)
                        time.sleep(0.05)

            def __exit__(self_inner, *exc):
                try:
                    os.remove(path)
                except OSError:
                    pass
                return False

        return _Lock()

    # ---- records: one file per type, one record per line
    def type_path(self, scope, rtype, archived=False):
        parts = [self.dir, scope] + (["archive"] if archived else []) + [rtype + ".json"]
        return os.path.join(*parts)

    def legacy_dir(self, scope, rtype, archived=False):
        """Folder used before 0.3, when every record was its own file."""
        return self.type_path(scope, rtype, archived)[:-5]

    def groups(self):
        """Every (scope, type, archived) that has a type file or a legacy folder."""
        found = []
        for scope in SCOPES:
            for archived in (False, True):
                base = os.path.join(self.dir, scope, "archive") if archived else os.path.join(self.dir, scope)
                if not os.path.isdir(base):
                    continue
                names = set()
                for name in os.listdir(base):
                    full = os.path.join(base, name)
                    if name.endswith(".json") and os.path.isfile(full):
                        names.add(name[:-5])
                    elif os.path.isdir(full) and name != "archive" and not name.startswith("."):
                        names.add(name)
                found.extend((scope, name, archived) for name in sorted(names))
        return found

    def read_group(self, scope, rtype, archived=False, errors=None):
        """All records of one type in one scope, as {lowercase id: record}."""
        records = {}

        def note(path, text):
            if errors is not None:
                errors.append("%s: %s" % (os.path.relpath(path, self.root).replace(os.sep, "/"), text))

        def take(rec, path):
            if not isinstance(rec, dict) or not rec.get("id"):
                note(path, "a record is not an object with an id")
                return
            key = str(rec["id"]).lower()
            old = records.get(key)
            if old is not None and (old.get("updated_at") or "") > (rec.get("updated_at") or ""):
                return
            records[key] = rec

        path = self.type_path(scope, rtype, archived)
        if os.path.isfile(path):
            try:
                data = read_json(path)
                items = data.get("records") if isinstance(data, dict) else data
                if not isinstance(items, list):
                    raise ValueError("no \"records\" list")
                seen = set()
                for rec in items:
                    if isinstance(rec, dict) and str(rec.get("id", "")).lower() in seen:
                        note(path, "id %s appears twice" % rec.get("id"))
                    seen.add(str(rec.get("id", "")).lower() if isinstance(rec, dict) else None)
                    take(rec, path)
            except (ValueError, OSError) as exc:
                note(path, "unreadable (%s)" % exc)
        folder = self.legacy_dir(scope, rtype, archived)
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".json"):
                    try:
                        take(read_json(os.path.join(folder, name)), os.path.join(folder, name))
                    except (ValueError, OSError) as exc:
                        note(os.path.join(folder, name), "unreadable (%s)" % exc)
        return records

    def write_group(self, scope, rtype, archived, records):
        """Write one type file (one record per line, sorted by id) and drop the legacy folder."""
        path = self.type_path(scope, rtype, archived)
        rows = [json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False)
                for _, r in sorted(records.items())]
        if rows:
            write_text_atomic(path, '{"type": %s, "records": [\n%s\n]}\n' % (json.dumps(rtype), ",\n".join(rows)))
        elif os.path.isfile(path):
            os.remove(path)
        folder = self.legacy_dir(scope, rtype, archived)
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if name.endswith(".json"):
                    os.remove(os.path.join(folder, name))
            try:
                os.rmdir(folder)
            except OSError:
                pass

    def migrate(self):
        """Fold folders of one-file-per-record (pre 0.3) into one file per type."""
        moved = 0
        for scope, rtype, archived in self.groups():
            if os.path.isdir(self.legacy_dir(scope, rtype, archived)):
                records = self.read_group(scope, rtype, archived)
                self.write_group(scope, rtype, archived, records)
                moved += len(records)
        return moved

    def iter_records(self, errors=None):
        for scope, rtype, archived in self.groups():
            for rec in self.read_group(scope, rtype, archived, errors).values():
                yield scope, rtype, archived, rec

    def taken_ids(self):
        return {str(rec["id"]).lower() for _, _, _, rec in self.iter_records()}

    def all_records(self, errors=None):
        records = []
        for scope, rtype, archived, rec in self.iter_records(errors):
            rec["_scope"] = scope
            rec["_type"] = rtype
            rec["_archived"] = archived
            rec["_path"] = os.path.relpath(self.type_path(scope, rtype, archived), self.root).replace(os.sep, "/")
            records.append(rec)
        return records

    def find(self, rid):
        rid = rid.strip().lower()
        for scope, rtype, archived, rec in self.iter_records():
            if str(rec["id"]).lower() == rid:
                return scope, rtype, archived, rec
        raise StateError("no record with id %s" % rid)

    def load(self, rid):
        scope, rtype, archived, rec = self.find(rid)
        return rec, scope, rtype, archived, self.type_path(scope, rtype, archived)

    def remove(self, rid, scope, rtype, archived=False):
        records = self.read_group(scope, rtype, archived)
        records.pop(str(rid).lower(), None)
        self.write_group(scope, rtype, archived, records)

    def new_id(self, prefix, spec=None):
        taken = self.taken_ids()
        if (spec or {}).get("ids") == "sequential":
            width = int(spec.get("id_width") or 3)
            pattern = re.compile(r"^%s-?0*(\d+)$" % re.escape(prefix.lower()))
            numbers = [int(m.group(1)) for m in (pattern.match(t) for t in taken) if m]
            return "%s%0*d" % (prefix, width, max(numbers or [0]) + 1)
        rng = random.SystemRandom()
        for length in (4, 5, 6, 8):
            for _ in range(50):
                rid = "%s-%s" % (prefix, "".join(rng.choice(ID_ALPHABET) for _ in range(length)))
                if rid.lower() not in taken:
                    return rid
        raise StateError("could not find a free id")

    def save(self, rec, scope, rtype, archived=False):
        records = self.read_group(scope, rtype, archived)
        records[str(rec["id"]).lower()] = rec
        self.write_group(scope, rtype, archived, records)

    # ---- validation of a record against its type
    def check_record(self, rec, rtype, spec, ids=None):
        problems = []
        if not rec.get("id"):
            problems.append("missing id")
        if rtype != "progress" and not rec.get("title"):
            problems.append("missing title")
        if rtype == "progress" and not rec.get("summary"):
            problems.append("missing summary")
        statuses = spec.get("statuses") or []
        if statuses and rec.get("status") not in statuses:
            problems.append("status %r is not one of %s" % (rec.get("status"), ", ".join(statuses)))
        prios = self.config.get("priorities") or []
        if rec.get("priority") is not None and prios and rec.get("priority") not in prios:
            problems.append("priority %r is not one of %s" % (rec.get("priority"), ", ".join(prios)))
        for name, fdef in (spec.get("fields") or {}).items():
            if name not in rec or rec[name] is None:
                if fdef.get("required"):
                    problems.append("missing required field %s" % name)
                continue
            value = rec[name]
            ftype = fdef.get("type")
            if ftype == "number" and not isinstance(value, (int, float)):
                problems.append("%s should be a number" % name)
            if ftype == "bool" and not isinstance(value, bool):
                problems.append("%s should be true or false" % name)
            if ftype == "list" and not isinstance(value, list):
                problems.append("%s should be a list" % name)
            if ftype == "enum" and value not in (fdef.get("options") or []):
                problems.append("%s %r is not one of %s" % (name, value, ", ".join(fdef.get("options") or [])))
        if rtype == "reviews":
            for f in rec.get("findings") or []:
                if f.get("severity") not in SEVERITIES:
                    problems.append("finding %s: severity %r is not one of %s"
                                    % (f.get("id"), f.get("severity"), ", ".join(SEVERITIES)))
        if ids is not None:
            refs = []
            for key in ("goal", "task", "testcase", "bug"):
                if rec.get(key):
                    refs.append(rec[key])
            for key in ("depends_on", "tasks", "related"):
                refs.extend(rec.get(key) or [])
            if rec.get("parent"):
                refs.append(rec["parent"])
            for ref in refs:
                if isinstance(ref, str) and ref.lower() not in ids:
                    problems.append("links to %s, which does not exist" % ref)
        return problems

    # ---- the generated bundle the viewer reads
    def web_url(self):
        """https URL of the origin remote, without credentials, for PR and commit links."""
        url = git(["config", "--get", "remote.origin.url"], self.root)
        m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
        if not m:
            m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", url)
        return "https://%s/%s" % (m.group(1), m.group(2)) if m else ""

    def scan_evidence(self, records):
        """Images under the evidence folders, matched to records by an id in the path."""
        dirs = (self.config.get("evidence") or {}).get("dirs") or []
        ids = sorted({str(r["id"]).lower() for r in records if r.get("id")}, key=len, reverse=True)
        pattern = re.compile(r"(?<![a-z0-9])(%s)(?![a-z0-9])" % "|".join(re.escape(i) for i in ids)) if ids else None
        found = []
        for folder in dirs:
            base = os.path.join(self.root, folder)
            if not os.path.isdir(base):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    if not name.lower().endswith(IMAGE_EXT):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, name), self.root).replace(os.sep, "/")
                    lower = name.lower()
                    label = ""
                    for word in ("before", "after"):
                        if re.search(r"(?<![a-z])%s(?![a-z])" % word, lower):
                            label = word
                    found.append({"path": rel, "label": label,
                                  "ids": sorted(set(pattern.findall(rel.lower()))) if pattern else []})
                    if len(found) >= 3000:
                        return found
        return found

    def build(self):
        records = self.all_records()
        branches = {}
        for rec in records:
            scope = rec.get("scope") if rec.get("_type") == "reviews" else None
            if isinstance(scope, dict) and scope.get("branch"):
                b = scope["branch"]
                if b not in branches:
                    branches[b] = git(["rev-parse", "--short", "--verify", "--quiet", b], self.root) or None
        default_branch = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], self.root)
        default_branch = default_branch.split("/", 1)[1] if "/" in default_branch else (default_branch or "main")
        data = {
            "generator": "repostate %s" % VERSION,
            "generated_at": now_iso(),
            "config": self.config,
            "types": self.types(),
            "git": {
                "repo": os.path.basename(self.root),
                "branch": self.branch(),
                "head": self.head(),
                "default_branch": default_branch,
                "branches": branches,
                "me": self.me(),
                "web": self.web_url(),
            },
            "evidence": self.scan_evidence(records),
            "records": records,
        }
        text = "/* generated by repostate; do not edit, do not commit */\nwindow.REPOSTATE = %s;\n" % json.dumps(
            data, ensure_ascii=False, indent=None, separators=(",", ":"))
        write_text_atomic(os.path.join(self.dir, "data.js"), text)
        return data


# --------------------------------------------------------------------------
# shared command logic

def apply_fields(rec, spec, sets):
    fields = spec.get("fields") or {}
    for item in sets or []:
        if "=" not in item:
            raise StateError("--set expects key=value, got %r" % item)
        key, value = item.split("=", 1)
        key = key.strip()
        if key in ("id", "created_at", "author") or key.startswith("_"):
            raise StateError("%s cannot be set by hand" % key)
        rec[key] = coerce(value, fields.get(key))


def merge_json_arg(rec, raw):
    if not raw:
        return
    text = sys.stdin.read() if raw == "-" else raw
    if os.path.isfile(raw):
        with open(raw, "r", encoding="utf-8") as fh:
            text = fh.read()
    try:
        extra = json.loads(text)
    except ValueError as exc:
        raise StateError("--json is not valid JSON: %s" % exc)
    if not isinstance(extra, dict):
        raise StateError("--json must be a JSON object")
    for key, value in extra.items():
        if key in ("id", "created_at") or key.startswith("_"):
            continue
        rec[key] = value


SIMPLE_FLAGS = [("title", "title"), ("status", "status"), ("priority", "priority"), ("goal", "goal"),
                ("assignee", "assignee"), ("parent", "parent"), ("body", "body"), ("summary", "summary"),
                ("task", "task"), ("slice", "slice"), ("estimate", "estimate_hours"),
                ("actual", "actual_hours"), ("severity", "severity"), ("kind", "kind"),
                ("owner", "owner"), ("waiting_on", "waiting_on")]


def apply_adds(rec, spec, adds):
    """--add key=value: append to a list field without resending the whole list."""
    for item in adds or []:
        if "=" not in item:
            raise StateError("--add expects key=value, got %r" % item)
        key, value = item.split("=", 1)
        key = key.strip()
        current = rec.get(key) or []
        if not isinstance(current, list):
            raise StateError("%s is not a list, use --set" % key)
        rec[key] = current + [v.strip() for v in value.split(",") if v.strip() and v.strip() not in current]


def apply_simple_flags(rec, args, clearing):
    for flag, key in SIMPLE_FLAGS:
        value = getattr(args, flag, None)
        if value is None or (value == "" and not clearing):
            continue
        if key in ("severity", "kind") and isinstance(value, str):
            value = value.strip().lower()
        rec[key] = None if value == "" else value


def check_links(store, rec):
    """Refuse links to records that do not exist."""
    ids = store.taken_ids()
    refs = [(k, rec.get(k)) for k in ("goal", "parent", "task", "testcase", "bug")]
    refs += [("depends_on", d) for d in rec.get("depends_on") or []]
    for key, ref in refs:
        if ref and str(ref).lower() not in ids:
            raise StateError("%s: %s does not exist" % (key, ref))


def record_status_change(store, rec, spec, old):
    new = rec.get("status")
    stamp = now_iso()
    if new in (spec.get("done") or []):
        rec.setdefault("done_at", stamp)
    else:
        rec.pop("done_at", None)
    if new != old:
        history = rec.get("history") or []
        history.append({"at": stamp, "by": store.author(), "field": "status", "from": old, "to": new})
        rec["history"] = history[-200:]


def add_hours(store, rec, hours, note=None):
    rec["actual_hours"] = round((rec.get("actual_hours") or 0) + hours, 2)
    entry = {"at": now_iso(), "by": store.author(), "field": "hours", "add": hours}
    if note:
        entry["note"] = note
    rec["history"] = ((rec.get("history") or []) + [entry])[-200:]
    rec["updated_at"] = entry["at"]


def create_record(store, rtype, fields, scope=None, rid=None, check=True):
    """Create a record of rtype from a dict. Returns (rec, scope)."""
    rtype, spec = store.type_spec(rtype)
    scope = scope or spec.get("scope", "team")
    if scope not in SCOPES:
        raise StateError("scope must be team or personal")
    if rid:
        rid = str(rid).strip()
        if not ID_RE.match(rid):
            raise StateError("id %r is not usable: letters, digits, dot, dash and underscore, up to 40 characters" % rid)
        try:
            store.find(rid)
        except StateError:
            pass
        else:
            raise StateError("id %s is already used" % rid)
    rec = {"id": rid or store.new_id(spec["prefix"], spec)}
    if spec.get("statuses"):
        rec["status"] = spec["statuses"][0]
    for key, value in fields.items():
        if value is not None and value != [] and not key.startswith("_") and key != "id":
            rec[key] = value
    rec.setdefault("author", store.author())
    stamp = now_iso()
    rec.setdefault("created_at", stamp)
    rec["updated_at"] = stamp
    if spec.get("statuses") and rec.get("status") in spec.get("done", []):
        rec.setdefault("done_at", stamp)
    problems = store.check_record(rec, rtype, spec)
    if problems:
        raise StateError("; ".join(problems))
    if check:
        check_links(store, rec)
    store.save(rec, scope, rtype)
    return rec, scope


def finish(store, args, payload, text):
    store.build()
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif text:
        print(text)


# --------------------------------------------------------------------------
# commands

def asset_path(*parts):
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.path.join(here, "..", "assets"), here):
        path = os.path.join(base, *parts)
        if os.path.exists(path):
            return os.path.normpath(path)
    return None


def cmd_init(store, args):
    first = not store.exists()
    os.makedirs(store.dir, exist_ok=True)
    for scope in SCOPES:
        os.makedirs(os.path.join(store.dir, scope), exist_ok=True)
    keep = os.path.join(store.dir, "team", ".gitkeep")
    if not os.path.exists(keep):
        write_text_atomic(keep, "")
    if first:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        cfg["project"]["name"] = args.name or os.path.basename(store.root)
        write_json_atomic(os.path.join(store.dir, "config.json"), cfg)
    write_text_atomic(os.path.join(store.dir, ".gitignore"), GITIGNORE)
    write_text_atomic(os.path.join(store.dir, ".gitattributes"), GITATTRIBUTES)
    migrated = store.migrate()

    copied = []
    me = os.path.abspath(__file__)
    target = os.path.join(store.dir, "repostate.py")
    if os.path.normcase(me) != os.path.normcase(os.path.abspath(target)):
        shutil.copyfile(me, target)
        copied.append("repostate.py")
    for name in ("index.html", "README.md"):
        src = asset_path(name if name != "README.md" else "STATE_README.md")
        dst = os.path.join(store.dir, name)
        if src and os.path.normcase(src) != os.path.normcase(os.path.abspath(dst)):
            shutil.copyfile(src, dst)
            copied.append(name)

    notes = []
    if args.hooks:
        notes.append(install_hooks(store))
    if args.actions:
        src = asset_path("github", "repostate-pr.yml")
        if src:
            dst = os.path.join(store.root, ".github", "workflows", "repostate-pr.yml")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            notes.append("added .github/workflows/repostate-pr.yml (posts a summary on each pull request)")

    found = find_markdown_candidates(store.root)
    store._config = None
    store.build()
    lines = ["%s %s/ in %s" % ("Created" if first else "Updated", STATE_DIR, store.root)]
    if copied:
        lines.append("copied: " + ", ".join(copied))
    lines.extend(notes)
    if migrated:
        lines.append("converted %d records to one file per type" % migrated)
    if found:
        lines.append("markdown files that can be imported: " + ", ".join(found))
        lines.append("preview with: python3 %s/repostate.py import %s --dry-run" % (STATE_DIR, " ".join(found)))
    lines.append("open the dashboard: %s/index.html" % STATE_DIR)
    finish(store, args, {"created": first, "copied": copied, "importable": found}, "\n".join(lines))


def find_markdown_candidates(root):
    pattern = re.compile(r"(task|todo|backlog|progress|changelog|worklog|journal|goal|roadmap|milestone|decision|adr|plan"
                         r"|test|defect|bug|question|requirement|hub)", re.I)
    found = []
    for base in (root, os.path.join(root, "docs"), os.path.join(root, ".claude")):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.lower().endswith(".md") and pattern.search(name) and name.lower() != "changelog.md":
                found.append(os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/"))
    return found


HOOK_MARK_START = "# >>> repostate >>>"
HOOK_MARK_END = "# <<< repostate <<<"
HOOK_BODY = """%s
# rebuild the dashboard data after git changes the working tree
if [ -f "%s/repostate.py" ]; then
  PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
  "$PY" "%s/repostate.py" build --quiet >/dev/null 2>&1 || true
fi
%s
""" % (HOOK_MARK_START, STATE_DIR, STATE_DIR, HOOK_MARK_END)


def install_hooks(store):
    hooks_dir = git(["rev-parse", "--git-path", "hooks"], store.root)
    if not hooks_dir:
        return "not a git repository, so no hooks were installed"
    if not os.path.isabs(hooks_dir):
        hooks_dir = os.path.join(store.root, hooks_dir)
    os.makedirs(hooks_dir, exist_ok=True)
    git(["config", "merge.repostate.name", "repostate record merge"], store.root)
    git(["config", "merge.repostate.driver", MERGE_DRIVER], store.root)
    done = []
    for name in ("post-merge", "post-checkout", "post-rewrite"):
        path = os.path.join(hooks_dir, name)
        text = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            if HOOK_MARK_START in text:
                continue
        if not text:
            text = "#!/bin/sh\n"
        text = text.rstrip("\n") + "\n\n" + HOOK_BODY
        write_text_atomic(path, text)
        os.chmod(path, 0o755)
        done.append(name)
    if not done:
        return "git hooks already installed"
    return "installed git hooks (%s): the dashboard rebuilds after pull, checkout and merge" % ", ".join(done)


def cmd_hooks(store, args):
    store.require()
    print(install_hooks(store))


def cmd_add(store, args):
    store.require()
    rtype, spec = store.type_spec(args.type)
    if getattr(args, "result", None):
        args.status = args.result
    rec = {}
    merge_json_arg(rec, args.json_data)
    apply_fields(rec, spec, args.set)
    apply_adds(rec, spec, args.add)
    apply_simple_flags(rec, args, clearing=False)
    if split_list(args.tags):
        rec["tags"] = split_list(args.tags)
    if split_list(args.depends):
        rec["depends_on"] = split_list(args.depends)
    if rtype == "progress":
        if rec.get("title") and not rec.get("summary"):
            rec["summary"] = rec.pop("title")
        rec.setdefault("branch", store.branch() or None)
        rec.setdefault("commit", store.head() or None)
    scope = "personal" if args.personal else ("team" if args.team else None)
    with store.lock():
        rec, scope = create_record(store, rtype, rec, scope, rid=args.id)
    label = rec.get("title") or rec.get("summary")
    finish(store, args, rec, "%s  %s  [%s, %s]" % (rec["id"], label, rtype, scope))


def cmd_update(store, args):
    store.require()
    if getattr(args, "result", None):
        args.status = args.result
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.id)
        rtype, spec = store.type_spec(rtype)
        old_status = rec.get("status")
        merge_json_arg(rec, getattr(args, "json_data", None))
        apply_fields(rec, spec, getattr(args, "set", None))
        apply_adds(rec, spec, getattr(args, "add", None))
        apply_simple_flags(rec, args, clearing=True)
        if getattr(args, "tags", None) is not None:
            rec["tags"] = split_list(args.tags)
        if getattr(args, "add_tag", None):
            rec["tags"] = sorted(set(rec.get("tags") or []) | set(split_list(args.add_tag)))
        if getattr(args, "depends", None) is not None:
            rec["depends_on"] = split_list(args.depends)
        if getattr(args, "add_depends", None):
            deps = rec.get("depends_on") or []
            rec["depends_on"] = deps + [d for d in split_list(args.add_depends) if d not in deps]
        for key in getattr(args, "unset", None) or []:
            rec.pop(key, None)
        rec = {k: v for k, v in rec.items() if v is not None}
        check_links(store, rec)
        record_status_change(store, rec, spec, old_status)
        rec["updated_at"] = now_iso()
        problems = store.check_record(rec, rtype, spec)
        if problems:
            raise StateError("; ".join(problems))
        store.save(rec, scope, rtype, archived)
    finish(store, args, rec, "%s updated (%s)" % (rec["id"], rec.get("status") or rtype))


def cmd_set_status(status):
    def run(store, args):
        store.require()
        rec, _, rtype, _, _ = store.load(args.id)
        _, spec = store.type_spec(rtype)
        target = status
        statuses = spec.get("statuses") or []
        if status == "done":
            target = (spec.get("done") or statuses[-1:] or ["done"])[0]
        elif status == "start":
            target = statuses[1] if len(statuses) > 2 else (statuses[0] if statuses else None)
        if not target:
            raise StateError("%s records have no status" % rtype)
        args.status = target
        cmd_update(store, args)
    return run


def cmd_hours(store, args):
    store.require()
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.id)
        add_hours(store, rec, args.hours, args.note)
        store.save(rec, scope, rtype, archived)
    finish(store, args, rec, "%s: %s h actual%s" % (
        rec["id"], rec["actual_hours"],
        (" of %s h estimated" % rec["estimate_hours"]) if rec.get("estimate_hours") else ""))


def cmd_log(store, args):
    store.require()
    rec = {"summary": args.summary}
    if args.details:
        rec["details"] = args.details
    touched = split_list(args.tasks)
    finished = split_list(args.done)
    if touched or finished:
        rec["tasks"] = touched + [t for t in finished if t not in touched]
    if args.files:
        rec["files"] = split_list(args.files)
    if args.session:
        rec["session"] = args.session
    if args.hours:
        rec["hours"] = args.hours
    rec["branch"] = store.branch() or None
    rec["commit"] = store.head() or None
    scope = "personal" if args.personal else None
    with store.lock():
        rec, scope = create_record(store, "progress", rec, scope, check=False)
        closed = []
        booked = None
        for tid in rec.get("tasks") or []:
            task, tscope, ttype, tarch, _ = store.load(tid)
            _, tspec = store.type_spec(ttype)
            changed = False
            if args.hours and len(rec["tasks"]) == 1:
                add_hours(store, task, args.hours, rec["summary"])
                booked = task["id"]
                changed = True
            if tid in finished:
                old = task.get("status")
                task["status"] = (tspec.get("done") or ["done"])[0]
                record_status_change(store, task, tspec, old)
                task["updated_at"] = now_iso()
                closed.append(task["id"])
                changed = True
            if changed:
                store.save(task, tscope, ttype, tarch)
    extra = (" and marked done: " + ", ".join(closed)) if closed else ""
    if booked:
        extra += "; %s h booked on %s" % (args.hours, booked)
    elif args.hours:
        extra += "; %s h kept on the entry only (name exactly one task to book them)" % args.hours
    finish(store, args, rec, "%s logged%s" % (rec["id"], extra))


def cmd_list(store, args):
    store.require()
    records = store.all_records()
    if args.type:
        rtype, _ = store.type_spec(args.type)
        records = [r for r in records if r["_type"] == rtype]
    if not args.archived:
        records = [r for r in records if not r["_archived"]]
    if args.scope:
        records = [r for r in records if r["_scope"] == args.scope]
    if args.status:
        wanted = set(split_list([args.status]))
        records = [r for r in records if r.get("status") in wanted]
    if args.open:
        types = store.types()
        records = [r for r in records if r.get("status") not in (types.get(r["_type"], {}).get("done") or [])]
    if args.mine:
        me = store.me()
        mine = {x for x in (me["email"], me["name"]) if x}
        records = [r for r in records if r.get("assignee") in mine or r.get("author") in mine]
    if args.query:
        q = args.query.lower()
        records = [r for r in records if q in json.dumps(r, ensure_ascii=False).lower()]
    records.sort(key=sort_key, reverse=args.type == "progress")
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return
    if not records:
        print("nothing found")
        return
    for r in records:
        label = r.get("title") or r.get("summary") or ""
        bits = [r.get("status") or "", r.get("priority") or "", r["_scope"]]
        print("%-9s %-11s %s  (%s)" % (r["id"], r["_type"], label, ", ".join(b for b in bits if b)))


def cmd_show(store, args):
    store.require()
    rec, scope, rtype, archived, path = store.load(args.id)
    rec["_scope"], rec["_type"], rec["_path"] = scope, rtype, os.path.relpath(path, store.root).replace(os.sep, "/")
    print(json.dumps(rec, indent=2, ensure_ascii=False))


def move_record(store, args, to_scope):
    store.require()
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.id)
        if scope == to_scope:
            raise StateError("%s is already %s" % (rec["id"], to_scope))
        rec["updated_at"] = now_iso()
        store.save(rec, to_scope, rtype, archived)
        store.remove(rec["id"], scope, rtype, archived)
    where = "it will be shared with your next commit" if to_scope == "team" else "it is now private to this machine"
    finish(store, args, rec, "%s moved to %s: %s" % (rec["id"], to_scope, where))


def cmd_promote(store, args):
    move_record(store, args, "team")


def cmd_unshare(store, args):
    move_record(store, args, "personal")


def cmd_delete(store, args):
    store.require()
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.id)
        store.remove(rec["id"], scope, rtype, archived)
    finish(store, args, {"deleted": rec["id"]}, "%s deleted" % rec["id"])


# ---- reviews

def cmd_review_new(store, args):
    store.require()
    rec = {}
    merge_json_arg(rec, args.json_data)
    scope_info = dict(rec.get("scope") or {})
    kind = args.scope or scope_info.get("type") or "branch"
    if kind not in REVIEW_SCOPES:
        raise StateError("--scope must be one of %s" % ", ".join(REVIEW_SCOPES))
    scope_info["type"] = kind
    branch = store.branch()
    scope_info.setdefault("branch", branch or None)
    scope_info["head_sha"] = scope_info.get("head_sha") or store.head() or None
    scope_info.setdefault("worktree", os.path.basename(store.root))
    if kind in ("branch", "worktree"):
        base = args.base or scope_info.get("base") or store.build().get("git", {}).get("default_branch") or "main"
        scope_info["base"] = base
        mb = git(["merge-base", base, "HEAD"], store.root)
        scope_info["base_sha"] = mb[:7] if mb else None
        if mb:
            files = git(["diff", "--name-only", mb, "HEAD"], store.root)
            scope_info["files_changed"] = len([f for f in files.splitlines() if f.strip()])
    if kind == "commits":
        if not args.range and not scope_info.get("range"):
            raise StateError("--scope commits needs --range A..B")
        scope_info["range"] = args.range or scope_info.get("range")
    if kind == "session":
        scope_info["session"] = args.session or scope_info.get("session") or os.environ.get("CLAUDE_SESSION_ID")
    if kind == "working":
        files = git(["status", "--porcelain"], store.root)
        scope_info["files_changed"] = len([f for f in files.splitlines() if f.strip()])
    rec["scope"] = {k: v for k, v in scope_info.items() if v is not None}
    if args.title:
        rec["title"] = args.title
    if not rec.get("title"):
        rec["title"] = "Review of %s" % (scope_info.get("branch") or kind)
    if args.summary:
        rec["summary"] = args.summary
    findings = []
    for i, f in enumerate(rec.get("findings") or [], 1):
        f = dict(f)
        f.setdefault("id", "F%d" % i)
        f.setdefault("status", "open")
        f.setdefault("severity", "minor")
        findings.append(f)
    rec["findings"] = findings
    scope = "team" if args.team else None
    with store.lock():
        rec, scope = create_record(store, "reviews", rec, scope)
    finish(store, args, rec, "%s  %s  [%d findings, %s]" % (rec["id"], rec["title"], len(findings), scope))


def cmd_review_finding(store, args):
    store.require()
    if args.severity not in SEVERITIES:
        raise StateError("--severity must be one of %s" % ", ".join(SEVERITIES))
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.review)
        if rtype != "reviews":
            raise StateError("%s is not a review" % args.review)
        findings = rec.setdefault("findings", [])
        nums = [int(f["id"][1:]) for f in findings if re.fullmatch(r"F\d+", str(f.get("id", "")))]
        finding = {"id": "F%d" % (max(nums or [0]) + 1), "severity": args.severity, "title": args.title,
                   "status": "open"}
        for key in ("file", "body", "suggestion", "task"):
            if getattr(args, key):
                finding[key] = getattr(args, key)
        if args.line:
            finding["line"] = args.line
        findings.append(finding)
        rec["updated_at"] = now_iso()
        store.save(rec, scope, rtype, archived)
    finish(store, args, finding, "%s %s added to %s" % (finding["id"], args.severity, rec["id"]))


def cmd_review_resolve(store, args):
    store.require()
    if args.status not in FINDING_STATUSES:
        raise StateError("--status must be one of %s" % ", ".join(FINDING_STATUSES))
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.review)
        hit = [f for f in rec.get("findings") or [] if str(f.get("id")).lower() == args.finding.lower()]
        if not hit:
            raise StateError("%s has no finding %s" % (rec["id"], args.finding))
        hit[0]["status"] = args.status
        if args.note:
            hit[0]["resolution"] = args.note
        rec["updated_at"] = now_iso()
        store.save(rec, scope, rtype, archived)
    finish(store, args, hit[0], "%s %s is now %s" % (rec["id"], hit[0]["id"], args.status))


def cmd_review_link(store, args):
    store.require()
    with store.lock():
        task = store.load(args.task)[0]
        rec, scope, rtype, archived, path = store.load(args.review)
        hit = [f for f in rec.get("findings") or [] if str(f.get("id")).lower() == args.finding.lower()]
        if not hit:
            raise StateError("%s has no finding %s" % (rec["id"], args.finding))
        hit[0]["task"] = task["id"]
        rec["updated_at"] = now_iso()
        store.save(rec, scope, rtype, archived)
    finish(store, args, hit[0], "%s %s is tracked by %s" % (rec["id"], hit[0]["id"], task["id"]))


def cmd_archive(store, args):
    """Archive reviews whose branch was merged into the default branch or deleted."""
    store.require()
    data = store.build()
    default = args.into or data["git"]["default_branch"]
    moved = []
    with store.lock():
        for rec in store.all_records():
            if rec["_type"] != "reviews" or rec["_archived"]:
                continue
            branch = (rec.get("scope") or {}).get("branch")
            if not branch or branch == default:
                continue
            exists = git(["rev-parse", "--verify", "--quiet", branch], store.root)
            merged = exists and subprocess.run(
                ["git", "merge-base", "--is-ancestor", branch, default], cwd=store.root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if (not exists) or merged:
                if not args.dry_run:
                    store.save(rec, rec["_scope"], "reviews", archived=True)
                    store.remove(rec["id"], rec["_scope"], "reviews", False)
                moved.append("%s (%s %s)" % (rec["id"], branch, "deleted" if not exists else "merged"))
    verb = "would archive" if args.dry_run else "archived"
    finish(store, args, {"archived": moved}, ("%s: %s" % (verb, ", ".join(moved))) if moved else "nothing to archive")


# ---- build, validate, summary, open

def cmd_build(store, args):
    store.require()
    data = store.build()
    if not args.quiet:
        print("built %s/data.js (%d records)" % (STATE_DIR, len(data["records"])))


def cmd_validate(store, args):
    store.require()
    errors = []
    records = store.all_records(errors)
    types = store.types()
    seen = {}
    for rec in records:
        rid = str(rec.get("id", "")).lower()
        if rid in seen:
            errors.append("%s: id also used by %s" % (rec["_path"], seen[rid]))
        seen[rid] = rec["_path"]
    ids = set(seen)
    for rec in records:
        spec = types.get(rec["_type"])
        if spec is None:
            errors.append("%s: folder %r is not a known type (add it to config.json \"types\")"
                          % (rec["_path"], rec["_type"]))
            continue
        for problem in store.check_record(rec, rec["_type"], spec, ids):
            errors.append("%s: %s" % (rec["_path"], problem))
        if rec["_scope"] == "team":
            for key in rec.get("depends_on") or []:
                other = next((r for r in records if str(r.get("id")).lower() == str(key).lower()), None)
                if other and other["_scope"] == "personal":
                    errors.append("%s: depends on %s, which is personal and invisible to the team"
                                  % (rec["_path"], key))
    known_reqs = {str(r.get("id", "")).lower() for r in records if r["_type"] == "requirements"}
    if known_reqs:
        for rec in records:
            for req in rec.get("requirements") or []:
                if str(req).lower() not in known_reqs:
                    errors.append("%s: requirement %s is not in the requirements list" % (rec["_path"], req))
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, "records": len(records)}, indent=2))
    elif errors:
        print("%d problem(s):" % len(errors))
        for e in errors:
            print("  - " + e)
    else:
        print("ok: %d records, no problems" % len(records))
    if errors:
        sys.exit(1)


def records_at(store, ref):
    """Team records as they were at a git ref, {id: record}. Reads both file layouts."""
    found = {}
    names = git(["ls-tree", "-r", "--name-only", ref, "--", "%s/team" % STATE_DIR], store.root)
    for name in names.splitlines():
        parts = name.split("/")[2:]
        if not name.endswith(".json") or not parts or parts[0] == "archive" or len(parts) > 2:
            continue
        try:
            data = json.loads(git(["show", "%s:%s" % (ref, name)], store.root))
        except ValueError:
            continue
        items = data.get("records", []) if len(parts) == 1 and isinstance(data, dict) else [data]
        for rec in items:
            if isinstance(rec, dict) and rec.get("id"):
                found[rec["id"]] = rec
    return found


def parse_state_file(path):
    try:
        data = read_json(path)
    except (ValueError, OSError):
        return None, {}
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return None, {}
    return data.get("type"), {str(r["id"]).lower(): r for r in data["records"] if isinstance(r, dict) and r.get("id")}


def cmd_merge_file(store, args):
    """Git merge driver: three-way merge of a type file, record by record."""
    _, base = parse_state_file(args.base)
    rtype, ours = parse_state_file(args.ours)
    rtype2, theirs = parse_state_file(args.theirs)
    if rtype is None or rtype2 is None:
        sys.exit(1)
    merged = {}
    for key in set(base) | set(ours) | set(theirs):
        o, a, b = base.get(key), ours.get(key), theirs.get(key)
        if a == b or b == o:
            pick = a
        elif a == o:
            pick = b
        elif a is None or b is None:
            pick = a or b
        else:
            pick = a if (a.get("updated_at") or "") >= (b.get("updated_at") or "") else b
        if pick is not None:
            merged[key] = pick
    rows = [json.dumps(r, ensure_ascii=False) for _, r in sorted(merged.items())]
    write_text_atomic(args.ours, '{"type": %s, "records": [\n%s\n]}\n' % (json.dumps(rtype), ",\n".join(rows)))


def cmd_summary(store, args):
    """Markdown summary of team data, for pull request comments."""
    store.require()
    types = store.types()
    records = [r for r in store.all_records() if r["_scope"] == "team" and not r["_archived"]]
    by_id = {r["id"]: r for r in records}
    lines = ["## %s: project state" % (store.config["project"].get("name") or os.path.basename(store.root)), ""]
    tasks = [r for r in records if r["_type"] == "tasks"]
    if tasks:
        counts = {}
        for t in tasks:
            counts[t.get("status")] = counts.get(t.get("status"), 0) + 1
        order = types["tasks"]["statuses"]
        lines.append("**Tasks:** " + " · ".join("%s %d" % (s, counts.get(s, 0)) for s in order if counts.get(s)))
    goals = [g for g in records if g["_type"] == "goals" and g.get("status") == "active"]
    for g in goals:
        linked = [t for t in tasks if t.get("goal") == g["id"]]
        done = len([t for t in linked if t.get("status") in types["tasks"]["done"]])
        lines.append("- Goal **%s**: %d of %d tasks done" % (g["title"], done, len(linked)))
    for rtype, name in (("testcases", "Tests"), ("bugs", "Bugs")):
        group = [r for r in records if r["_type"] == rtype]
        if group:
            counts = {}
            for r in group:
                counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
            lines.append("**%s:** " % name + " · ".join(
                "%s %d" % (str(st).replace("_", " "), counts[st]) for st in types[rtype]["statuses"] if counts.get(st)))
    if args.since:
        before = records_at(store, git(["merge-base", args.since, "HEAD"], store.root) or args.since)
        rows = []
        for rid in sorted(set(before) | set(by_id)):
            old, rec = before.get(rid), by_id.get(rid)
            new = {k: v for k, v in rec.items() if not k.startswith("_")} if rec else None
            if old == new:
                continue
            label = (rec or old).get("title") or (rec or old).get("summary")
            what = "added" if old is None else ("removed" if rec is None else "updated")
            status = (" → %s" % rec.get("status")) if rec and rec.get("status") else ""
            rows.append("| %s | %s | %s%s |" % (rid, label, what, status))
        lines += ["", "### Changed in this branch", ""]
        if rows:
            lines += ["| ID | Item | Change |", "|---|---|---|"] + rows
        else:
            lines.append("No project-state changes in this branch.")
    reviews = [r for r in records if r["_type"] == "reviews"]
    open_findings = [(r, f) for r in reviews for f in r.get("findings") or [] if f.get("status") == "open"]
    if open_findings:
        blocking = len([1 for _, f in open_findings if f.get("severity") == "blocking"])
        lines += ["", "**Open review findings:** %d (%d blocking)" % (len(open_findings), blocking)]
    lines += ["", "<sub>Generated by repostate. Open `.repostate/index.html` for the full dashboard.</sub>"]
    print("\n".join(lines))


def cmd_open(store, args):
    store.require()
    store.build()
    path = os.path.join(store.dir, "index.html")
    url = "file://" + path.replace(os.sep, "/") if os.sep == "/" else "file:///" + path.replace(os.sep, "/")
    print(url)
    if not args.print_only:
        webbrowser.open(url)


def cmd_types(store, args):
    store.require()
    types = store.types()
    if args.json:
        print(json.dumps(types, indent=2))
        return
    for name, spec in types.items():
        statuses = " → ".join(spec.get("statuses") or []) or "(no status)"
        print("%-10s %s-  default %-8s %s" % (name, spec["prefix"], spec.get("scope", "team"), statuses))


# --------------------------------------------------------------------------
# markdown import and export

CHECKBOX = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+\[([ xX~\-/])\]\s+(.*)$")
BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
STATUS_WORDS = [
    (re.compile(r"\b(done|complete[d]?|finished|shipped|closed)\b", re.I), "done"),
    (re.compile(r"\b(in[ -]?progress|doing|wip|current|active|ongoing|started)\b", re.I), "doing"),
    (re.compile(r"\b(blocked|waiting|on hold)\b", re.I), "blocked"),
    (re.compile(r"\b(todo|to do|backlog|next|planned|upcoming|later|open)\b", re.I), "todo"),
]
PRIORITY_RE = [
    (re.compile(r"(?:^|\s)(?:\[?P0\]?|!urgent|\(urgent\)|🔥)(?=\s|$)", re.I), "urgent"),
    (re.compile(r"(?:^|\s)(?:\[?P1\]?|!high|\(high\))(?=\s|$)", re.I), "high"),
    (re.compile(r"(?:^|\s)(?:\[?P2\]?|!medium|\(medium\))(?=\s|$)", re.I), "medium"),
    (re.compile(r"(?:^|\s)(?:\[?P3\]?|!low|\(low\))(?=\s|$)", re.I), "low"),
]
TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")
ASSIGNEE_RE = re.compile(r"(?:^|\s)@([\w.\-]+(?:@[\w.\-]+)?)")
DUE_RE = re.compile(r"(?:^|\s)(?:due|by)[: ]\s*(\d{4}-\d{2}-\d{2})", re.I)


def guess_type(path):
    name = os.path.basename(path).lower()
    if re.search(r"(^|[^a-z])(tests?|test[-_ ]?cases?|qa)([^a-z]|$)", name):
        return "testcases"
    if re.search(r"(bug|defect)", name):
        return "bugs"
    if re.search(r"question", name):
        return "questions"
    if re.search(r"requirement|(^|[^a-z])reqs?([^a-z]|$)", name):
        return "requirements"
    if re.search(r"(task|todo|backlog|plan)", name):
        return "tasks"
    if re.search(r"(progress|worklog|journal|log|changelog|history)", name):
        return "progress"
    if re.search(r"(goal|roadmap|milestone|okr)", name):
        return "goals"
    if re.search(r"(adr|decision|architecture)", name) or re.match(r"\d{3,4}-", name):
        return "decisions"
    return None


def heading_status(text):
    for regex, status in STATUS_WORDS:
        if regex.search(text):
            return status
    return None


def map_status(status, spec):
    """Map an import status word onto the statuses this type actually has."""
    statuses = spec.get("statuses") or []
    if not statuses:
        return None
    if status in statuses:
        return status
    done = (spec.get("done") or statuses[-1:])[0]
    if status == "done":
        return done
    if status == "blocked" and "blocked" in statuses:
        return "blocked"
    if status == "doing":
        active_words = ("doing", "in_progress", "in-progress", "in progress", "started", "active", "wip", "review")
        for s in statuses:
            if s.lower() in active_words:
                return s
    return statuses[0]


def parse_inline(text):
    meta = {}
    for regex, prio in PRIORITY_RE:
        if regex.search(text):
            meta["priority"] = prio
            text = regex.sub(" ", text)
            break
    tags = TAG_RE.findall(text)
    if tags:
        meta["tags"] = tags
        text = TAG_RE.sub(" ", text)
    m = DUE_RE.search(text)
    if m:
        meta["due"] = m.group(1)
        text = DUE_RE.sub(" ", text)
    who = ASSIGNEE_RE.findall(text)
    if who:
        meta["assignee"] = who[0]
        text = ASSIGNEE_RE.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—:")
    text = re.sub(r"^\*\*(.+?)\*\*", r"\1", text)
    meta["title"] = text.strip()
    return meta


def parse_list_markdown(text, rtype, spec, relpath):
    """Tasks or goals from checkbox lists, bullets and headings."""
    items = []
    group = None
    section_status = None
    stack = []  # (indent, item)
    in_code = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip():
            continue
        h = HEADING.match(line)
        if h:
            title = h.group(2).strip()
            level = len(h.group(1))
            if level == 1 and not items:
                continue
            group = title
            section_status = heading_status(title)
            stack = []
            continue
        cb = CHECKBOX.match(line)
        bl = None if cb else BULLET.match(line)
        if not cb and not bl:
            if stack and line.startswith((" ", "\t")):
                parent = stack[-1][1]
                parent["body"] = (parent.get("body", "") + "\n" + line.strip()).strip()
            continue
        indent = len((cb or bl).group(1).replace("\t", "    "))
        if cb:
            mark = cb.group(2).lower()
            raw = cb.group(3)
            status = "done" if mark == "x" else ("doing" if mark in ("~", "/", "-") else (section_status or "todo"))
            if mark == " " and (section_status == "done" or (section_status == "doing" and indent > 0)):
                status = "todo"
        else:
            raw = bl.group(2)
            status = section_status or "todo"
            strike = re.match(r"^~~(.+)~~$", raw.strip())
            if strike:
                raw, status = strike.group(1), "done"
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack and not cb and rtype == "goals":
            parent = stack[-1][1]
            parent["body"] = (parent.get("body", "") + "\n- " + raw.strip()).strip()
            continue
        meta = parse_inline(raw)
        if not meta.get("title"):
            continue
        item = {"title": meta.pop("title"), "status": map_status(status, spec)}
        item.update(meta)
        if status == "blocked" and "blocked" not in (spec.get("statuses") or []):
            item["tags"] = sorted(set(item.get("tags") or []) | {"blocked"})
        if group:
            item["group"] = group
        item["source"] = {"file": relpath, "line": lineno}
        if stack:
            item["_parent_index"] = items.index(stack[-1][1])
        items.append(item)
        stack.append((indent, item))
    return items


def parse_progress_markdown(text, relpath):
    entries = []
    current_date = None
    current_heading = None
    last = None
    in_code = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip():
            continue
        h = HEADING.match(line)
        if h:
            title = h.group(2).strip()
            d = DATE_RE.search(title)
            if d:
                current_date = d.group(1)
                rest = title.replace(d.group(1), "").strip(" -–—:()")
                current_heading = rest or None
            else:
                current_heading = title
            last = None
            continue
        bl = BULLET.match(line) or CHECKBOX.match(line)
        if bl:
            indent = len(bl.group(1).replace("\t", "    "))
            raw = bl.group(bl.lastindex).strip()
            if indent > 0 and last is not None:
                last["details"] = (last.get("details", "") + "\n- " + raw).strip()
                continue
            d = DATE_RE.match(raw)
            date = current_date
            if d:
                date = d.group(1)
                raw = raw[len(d.group(1)):].strip(" -–—:")
            entry = {"summary": raw, "source": {"file": relpath, "line": lineno}}
            if date:
                entry["created_at"] = date + "T12:00:00Z"
            if current_heading:
                entry["group"] = current_heading
            entries.append(entry)
            last = entry
        elif last is not None:
            last["details"] = (last.get("details", "") + "\n" + line.strip()).strip()
        elif current_heading or current_date:
            entry = {"summary": line.strip(), "source": {"file": relpath, "line": lineno}}
            if current_date:
                entry["created_at"] = current_date + "T12:00:00Z"
            entries.append(entry)
            last = entry
    return entries


def parse_decision_markdown(text, relpath, spec):
    """One ADR-style file: # Title, Status, ## Context / Decision / Consequences."""
    rec = {"source": {"file": relpath, "line": 1}}
    section = None
    buf = {}
    for line in text.splitlines():
        h = HEADING.match(line)
        if h:
            title = h.group(2).strip()
            if len(h.group(1)) == 1 and "title" not in rec:
                rec["title"] = re.sub(r"^(ADR[- ]?)?\d+[.:\-\s]+", "", title, flags=re.I).strip() or title
                continue
            section = title.lower()
            continue
        m = re.match(r"^\s*\**status\**\s*[:\-]\s*\**(\w+)", line, re.I)
        if m and section in (None, "status"):
            rec["status"] = m.group(1).lower()
            continue
        if section:
            buf.setdefault(section, []).append(line)
    for key, lines in buf.items():
        body = "\n".join(lines).strip()
        if not body:
            continue
        if key.startswith("status") and "status" not in rec:
            rec["status"] = body.split()[0].strip("*").lower()
        elif key.startswith("context"):
            rec["context"] = body
        elif key.startswith("decision"):
            rec["decision"] = body
        elif key.startswith("consequence"):
            rec["consequences"] = body
        else:
            rec["body"] = (rec.get("body", "") + "\n\n### " + key.title() + "\n" + body).strip()
    rec.setdefault("title", os.path.splitext(os.path.basename(relpath))[0])
    statuses = spec.get("statuses") or []
    if rec.get("status") not in statuses:
        rec["status"] = statuses[0] if statuses else None
    return [rec]


TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
MD_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
EMPTY_CELLS = {"", "-", "--", "—", "–", "n/a", "na", "none", "tbd", "?", "nil"}
COLUMN_WORDS = {
    "id": ["id", "#", "no", "key", "ref"],
    "title": ["title", "name", "summary", "description", "desc", "scenario", "what"],
    "status": ["status", "state", "result", "outcome"],
    "priority": ["priority", "prio"],
    "severity": ["severity", "sev"],
    "assignee": ["assignee", "assigned to", "assigned", "who", "dev", "developer"],
    "owner": ["owner"],
    "slice": ["slice", "phase", "milestone", "epic", "group", "sprint"],
    "depends_on": ["depends on", "depends", "dependencies", "deps", "blocked by", "after", "prerequisites"],
    "estimate_hours": ["estimate", "est", "est h", "estimate h", "estimated hours", "estimate hours", "est hours",
                       "dev h", "est dev h", "estimated", "dev hours", "est dev hours", "estimated dev h"],
    "actual_hours": ["actual", "actual h", "actual hours", "spent", "actual dev h", "act h", "act", "actual dev hours"],
    "requirements": ["requirements", "requirement", "reqs", "req", "req ids", "requirement ids", "covers"],
    "task": ["task", "task id", "tasks"],
    "testcase": ["test case", "testcase", "tc", "test", "case", "test id", "tc id", "found by"],
    "bug": ["bug", "defect", "bug id", "defect id", "bugs", "defects"],
    "kind": ["kind", "type", "layer", "category"],
    "fix_commit": ["fix commit", "commit", "fix", "fixed in", "fix sha"],
    "prs": ["pr", "prs", "pull request", "pull requests", "mr"],
    "repos": ["repo", "repos", "repository", "repositories"],
    "evidence": ["evidence", "screenshot", "screenshots", "proof"],
    "figma": ["figma", "design", "designs"],
    "waiting_on": ["waiting on", "blocked on", "waiting for"],
    "answer": ["answer", "resolution"],
    "body": ["notes", "note", "details", "comment", "comments", "remarks"],
    "steps": ["steps"],
    "expected": ["expected", "expected result"],
    "due": ["due", "due date", "deadline"],
    "tags": ["tags", "labels"],
}
HEADER_TO_FIELD = {word: field for field, words in COLUMN_WORDS.items() for word in words}
TYPE_COLUMNS = {
    "tasks": {"task": "title", "tasks": "title", "task id": "id", "owner": "assignee"},
    "goals": {"goal": "title"},
    "testcases": {"test case": "title", "testcase": "title", "test": "title", "case": "title", "tc": "id",
                  "tc id": "id", "test id": "id", "case id": "id"},
    "bugs": {"bug": "title", "defect": "title", "issue": "title", "bug id": "id", "defect id": "id"},
    "requirements": {"requirement": "title", "req": "id", "req id": "id", "requirement id": "id"},
    "decisions": {"decision": "title", "decision id": "id"},
    "questions": {"question": "title", "open question": "title", "question id": "id"},
}
LINK_FIELDS = {"task": ("testcases", "bugs", "questions", "decisions"), "testcase": ("bugs",), "bug": ("testcases",)}
STATUS_SYNONYMS = {
    "testcases": [(r"pass\w*\W*(with|w/)?\W*(a\s+)?(question|query|q\b|note|caveat)|pass\w*\s*\?", "pass_with_question"),
                  (r"\b(pass|passed|ok|green)\b", "pass"), (r"\b(fail|failed|red)\b", "fail"),
                  (r"\bblock", "blocked"), (r"\b(not run|not_run|pending|todo|skipped|not tested|untested)\b", "not_run")],
    "bugs": [(r"won.?t\s*fix|wontfix|invalid|duplicate|not a bug", "wontfix"),
             (r"\b(fixed|closed|resolved|done|verified)\b", "fixed"),
             (r"\b(fixing|in progress|wip|doing)\b", "fixing"), (r"\b(open|new|reported|todo)\b", "open")],
    "decisions": [(r"supersed", "superseded"), (r"reject|declin", "rejected"),
                  (r"\b(accepted|decided|approved|agreed|done|final|closed)\b", "accepted"),
                  (r"\b(proposed|open|pending|draft|tbd)\b", "proposed")],
    "questions": [(r"\b(answered|resolved|closed|done)\b", "answered"), (r"\b(open|pending|asked|waiting)\b", "open")],
}
PRIORITY_WORDS = {"p0": "urgent", "p1": "high", "p2": "medium", "p3": "low", "critical": "urgent",
                  "blocker": "urgent", "normal": "medium", "med": "medium", "highest": "urgent", "lowest": "low"}
SEVERITY_WORDS = {"blocker": "critical", "s1": "critical", "major": "high", "s2": "high", "moderate": "medium",
                  "normal": "medium", "med": "medium", "s3": "medium", "minor": "low", "trivial": "low", "s4": "low"}


def split_row(line):
    inner = line.strip()[1:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def is_separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in cells)


def find_tables(text):
    """Yield (heading above the table, header cells, [(line number, cells)])."""
    lines = text.splitlines()
    heading, in_code, i = None, False, 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code = not in_code
        elif not in_code:
            h = HEADING.match(line)
            if h:
                heading = h.group(2).strip()
            elif (TABLE_ROW.match(line) and i + 1 < len(lines) and TABLE_ROW.match(lines[i + 1])
                  and is_separator(split_row(lines[i + 1]))):
                rows, j = [], i + 2
                while j < len(lines) and TABLE_ROW.match(lines[j]):
                    rows.append((j + 1, split_row(lines[j])))
                    j += 1
                yield heading, split_row(line), rows
                i = j
                continue
        i += 1


def norm_header(text):
    text = re.sub(r"[*_`]", "", MD_LINK.sub(r"\1", text)).lower()
    return re.sub(r"[^a-z0-9#]+", " ", text).strip()


def guess_table_type(heading, header):
    names = {norm_header(h) for h in header}
    if names & {"severity", "sev", "fix commit", "defect", "defect id", "bug id"}:
        return "bugs"
    if names & {"result", "outcome", "expected", "expected result", "scenario", "test case", "testcase", "tc", "tc id"}:
        return "testcases"
    if names & {"question", "open question", "waiting on", "answer"}:
        return "questions"
    if names & {"decision"}:
        return "decisions"
    if names & {"depends on", "depends", "dependencies", "deps", "estimate", "est", "est h", "dev h", "est dev h",
                "slice", "task", "assignee", "blocked by"}:
        return "tasks"
    if names & {"requirement", "req", "req id", "requirement id"}:
        return "requirements"
    for pattern, rtype in ((r"test|qa\b", "testcases"), (r"bug|defect", "bugs"), (r"question", "questions"),
                           (r"decision", "decisions"), (r"requirement", "requirements"),
                           (r"task|backlog|todo|slice|plan", "tasks"), (r"goal|milestone", "goals")):
        if heading and re.search(pattern, heading, re.I):
            return rtype
    return None


def map_columns(header, rtype):
    """Field name for each table column, or None to skip it."""
    names = [norm_header(h) for h in header]
    overrides = TYPE_COLUMNS.get(rtype, {})
    fields = []
    for name in names:
        field = overrides.get(name) or HEADER_TO_FIELD.get(name)
        if field in LINK_FIELDS and rtype not in LINK_FIELDS[field]:
            field = None
        if field is None and name:
            slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
            field = slug if slug and slug not in ("id", "author", "history", "source") else None
        fields.append(field)
    # "Task | Title" or "Test case | Scenario": the type-named column is the id when another column is the title
    if "id" not in fields and fields.count("title") > 1:
        for i, name in enumerate(names):
            if overrides.get(name) == "title":
                fields[i] = "id"
                break
    seen = set()
    for i, field in enumerate(fields):
        if field in seen:
            fields[i] = None
        elif field:
            seen.add(field)
    return fields


def cell_text(cell):
    cell = MD_LINK.sub(r"\1", cell)
    cell = re.sub(r"<br\s*/?>", "\n", cell)
    return re.sub(r"[*`]|~~", "", cell).strip()


def cell_ids(cell):
    return [t.lstrip("#@") for t in re.split(r"[,;\s]+", cell_text(cell)) if t.lower() not in EMPTY_CELLS]


def cell_links(cell):
    urls = [m.group(2) for m in MD_LINK.finditer(cell)]
    rest = re.sub(r"[*`<>]", " ", MD_LINK.sub(" ", cell))
    return urls + [t for t in re.split(r"[,;\s]+", rest) if t.lower() not in EMPTY_CELLS]


def cell_hours(cell):
    m = re.search(r"\d+(?:\.\d+)?", cell_text(cell))
    if not m:
        return None
    value = float(m.group(0))
    return int(value) if value == int(value) else value


def normalise_status(raw, rtype, spec):
    """Map a table cell onto one of the type's statuses. Returns (status, note or None)."""
    statuses = spec.get("statuses") or []
    if not statuses:
        return None, None
    text = cell_text(raw)
    done_word = "pass" if rtype == "testcases" else "done"
    for mark, word in (("✅", done_word), ("☑", done_word), ("✔", done_word), ("🟢", done_word), ("❌", "fail"),
                       ("🚧", "in progress"), ("🔄", "in progress"), ("🟡", "in progress"), ("⏳", "todo"),
                       ("⬜", "todo"), ("🔴", "blocked"), ("⛔", "blocked"), ("❓", " question")):
        text = text.replace(mark, " %s " % word)
    low = re.sub(r"\s+", " ", text.replace("️", "")).strip().lower()
    if low in EMPTY_CELLS:
        return statuses[0], None
    slug = re.sub(r"[^a-z0-9]+", "_", low).strip("_")
    if slug in statuses:
        return slug, None
    for pattern, status in STATUS_SYNONYMS.get(rtype, []):
        if status in statuses and re.search(pattern, low):
            note = text.strip() if status == "pass_with_question" and re.search(r"[:(?]", text) else None
            return status, note
    if rtype not in STATUS_SYNONYMS:
        word = heading_status(low)
        if word:
            return map_status(word, spec), None
    return statuses[0], cell_text(raw)


def parse_table(store, header, rows, rtype, spec, relpath):
    fields = map_columns(header, rtype)
    priorities = store.config.get("priorities") or []
    severities = ((spec.get("fields") or {}).get("severity") or {}).get("options") or []
    items = []
    for lineno, cells in rows:
        item = {}
        for field, cell in zip(fields, cells):
            if not field or cell_text(cell).lower() in EMPTY_CELLS and field != "status":
                continue
            text = cell_text(cell)
            if field == "id":
                rid = text.split()[0] if text.split() else ""
                item["_id"] = (spec["prefix"] + rid) if rid.isdigit() else rid
            elif field == "status":
                status, note = normalise_status(cell, rtype, spec)
                if status:
                    item["status"] = status
                if note:
                    item["status_note"] = note
                if (rtype in ("tasks", "goals") and status != "blocked"
                        and re.search(r"block|on hold|waiting", cell_text(cell), re.I)):
                    item["tags"] = sorted(set(item.get("tags") or []) | {"blocked"})
            elif field == "priority":
                value = PRIORITY_WORDS.get(text.lower(), text.lower())
                item["priority" if value in priorities else "priority_note"] = value
            elif field == "severity":
                value = SEVERITY_WORDS.get(text.lower(), text.lower())
                item["severity" if (not severities or value in severities) else "severity_note"] = value
            elif field in ("depends_on", "requirements", "tags"):
                item[field] = cell_ids(cell)
            elif field in ("task", "testcase", "bug"):
                ids = cell_ids(cell)
                if ids:
                    item[field] = ids[0]
                if len(ids) > 1:
                    item["related"] = (item.get("related") or []) + ids[1:]
            elif field in ("estimate_hours", "actual_hours"):
                hours = cell_hours(cell)
                if hours is not None:
                    item[field] = hours
            elif field in ("prs", "repos", "evidence", "figma"):
                item[field] = cell_links(cell)
            elif field in ("assignee", "owner", "waiting_on"):
                item[field] = text.lstrip("@")
            elif field == "kind":
                item[field] = text.lower()
            elif field == "fix_commit":
                links = cell_links(cell)
                item[field] = links[0] if links else text
            else:
                item[field] = text
        if not item.get("title") and item.get("_id"):
            item["title"] = item["_id"]
        if not item.get("title"):
            continue
        if rtype == "progress":
            item["summary"] = item.pop("title")
        item["source"] = {"file": relpath, "line": lineno}
        items.append(item)
    return items


def update_from_import(store, rid, item):
    """Refresh an already imported record from its table row. Returns True when something changed."""
    rec, scope, rtype, archived, path = store.load(rid)
    rtype, spec = store.type_spec(rtype)
    old_status = rec.get("status")
    changed = False
    for key, value in item.items():
        if key.startswith("_") or key == "source" or value in (None, "", []):
            continue
        if rec.get(key) != value:
            rec[key] = value
            changed = True
    if changed:
        record_status_change(store, rec, spec, old_status)
        rec["updated_at"] = now_iso()
        problems = store.check_record(rec, rtype, spec)
        if problems:
            raise StateError("%s: %s" % (rid, "; ".join(problems)))
        store.save(rec, scope, rtype, archived)
    return changed


def cmd_import(store, args):
    store.require()
    forced = store.type_spec(args.type)[0] if args.type else None
    plan = []
    for path in args.files:
        if not os.path.isfile(path):
            raise StateError("%s does not exist" % path)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(store.root)).replace(os.sep, "/")
        file_type = forced or guess_type(path)
        groups = {}
        tables = list(find_tables(text))
        for heading, header, rows in tables:
            ttype = forced or guess_table_type(heading, header) or file_type
            if not ttype:
                continue
            ttype, tspec = store.type_spec(ttype)
            groups.setdefault(ttype, []).extend(parse_table(store, header, rows, ttype, tspec, rel))
        has_checkbox = any(CHECKBOX.match(line) for line in text.splitlines())
        if file_type and (not tables or (has_checkbox and file_type in ("tasks", "goals"))):
            ftype, fspec = store.type_spec(file_type)
            if ftype == "progress":
                items = parse_progress_markdown(text, rel)
            elif ftype == "decisions":
                items = parse_decision_markdown(text, rel, fspec)
            else:
                items = parse_list_markdown(text, ftype, fspec, rel)
                for item in items:
                    if "_parent_index" in item:
                        item["_parent"] = items[item.pop("_parent_index")]
            groups.setdefault(ftype, []).extend(items)
        if not groups:
            raise StateError("can't tell what %s holds. Pass --type tasks|goals|progress|decisions|testcases|bugs|"
                             "requirements|questions" % path)
        for rtype, items in groups.items():
            plan.append((path, rtype, store.type_spec(rtype)[1], items))

    existing_src, existing_ids = {}, {}
    for rec in store.all_records():
        src = rec.get("source") or {}
        existing_src[(src.get("file"), (rec.get("title") or rec.get("summary") or "").strip().lower())] = rec["id"]
        existing_ids[str(rec["id"]).lower()] = rec["id"]

    report, created, updated = [], [], []
    scope = "personal" if args.personal else None
    lock = store.lock() if not args.dry_run else None
    if lock:
        lock.__enter__()
    try:
        for path, rtype, spec, items in plan:
            skipped = refreshed = 0
            for item in items:
                rid = item.pop("_id", None)
                if rid and not ID_RE.match(rid):
                    rid = None
                parent = item.pop("_parent", None)
                if parent is not None and parent.get("_new_id"):
                    item["parent"] = parent["_new_id"]
                key = (item["source"]["file"], (item.get("title") or item.get("summary") or "").strip().lower())
                hit = existing_ids.get(rid.lower()) if rid else existing_src.get(key)
                if hit:
                    item["_new_id"] = hit
                    if rid and args.update:
                        if args.dry_run or update_from_import(store, hit, item):
                            refreshed += 1
                            updated.append(hit)
                        else:
                            skipped += 1
                    else:
                        skipped += 1
                    continue
                if args.dry_run:
                    if rid:
                        existing_ids[rid.lower()] = rid
                        item["_new_id"] = rid
                    continue
                rec, _ = create_record(store, rtype, item, scope, rid=rid, check=False)
                item["_new_id"] = rec["id"]
                created.append(rec["id"])
                existing_src[key] = rec["id"]
                existing_ids[rec["id"].lower()] = rec["id"]
            shown = []
            for item in items:
                clean = {k: v for k, v in item.items() if not k.startswith("_")}
                if item.get("_new_id"):
                    clean["id"] = item["_new_id"]
                shown.append(clean)
            report.append({"file": path, "type": rtype, "found": len(items), "skipped": skipped,
                           "updated": refreshed, "items": shown})
    finally:
        if lock:
            lock.__exit__(None, None, None)

    if args.json:
        print(json.dumps({"dry_run": args.dry_run, "created": created, "updated": updated, "files": report},
                         indent=2, ensure_ascii=False))
        if not args.dry_run:
            store.build()
        return
    for entry in report:
        new = entry["found"] - entry["skipped"] - entry["updated"]
        print("%s → %d %s (%d new, %d updated, %d already imported)" % (
            entry["file"], entry["found"], entry["type"], new, entry["updated"], entry["skipped"]))
        for item in entry["items"][: args.limit]:
            label = item.get("title") or item.get("summary")
            extra = [item.get("status"), item.get("priority"), item.get("severity"), item.get("slice") or item.get("group"),
                     ("task " + item["task"]) if item.get("task") else None,
                     ("after " + ",".join(item["depends_on"])) if item.get("depends_on") else None,
                     ("%sh" % item["estimate_hours"]) if item.get("estimate_hours") is not None else None,
                     ("#" + " #".join(item["tags"])) if item.get("tags") else None,
                     ("@" + item["assignee"]) if item.get("assignee") else None,
                     ("due " + item["due"]) if item.get("due") else None,
                     (item.get("created_at") or "")[:10] or None]
            print("   - %s%s  (%s)" % ((item["id"] + "  ") if item.get("id") else "", label,
                                       ", ".join(str(x) for x in extra if x)))
        if len(entry["items"]) > args.limit:
            print("   … and %d more" % (len(entry["items"]) - args.limit))
    if args.dry_run:
        print("\nDry run: nothing was written. Run again without --dry-run to import.")
    else:
        store.build()
        print("\nImported %d new records%s. The original markdown files were not changed." % (
            len(created), (", refreshed %d" % len(updated)) if updated else ""))


def sort_key(rec):
    """Creation order; records created in the same second keep their source order."""
    return (rec.get("created_at") or "", (rec.get("source") or {}).get("line") or 0, rec.get("id") or "")


def cmd_export(store, args):
    store.require()
    types = store.types()
    records = [r for r in store.all_records() if not r["_archived"]]
    if not args.personal:
        records = [r for r in records if r["_scope"] == "team"]
    wanted = [store.type_spec(t)[0] for t in split_list(args.type)] if args.type else list(types)
    out = {}
    for rtype in wanted:
        recs = [r for r in records if r["_type"] == rtype]
        if not recs:
            continue
        spec = types[rtype]
        lines = ["# %s" % spec["label"]]
        if rtype == "progress":
            recs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
            day = None
            for r in recs:
                d = (r.get("created_at") or "")[:10]
                if d != day:
                    lines += ["", "## %s" % d, ""]
                    day = d
                lines.append("- %s" % r.get("summary"))
                for dl in (r.get("details") or "").splitlines():
                    lines.append("  " + (dl if dl.startswith("- ") else "- " + dl))
        elif spec.get("view") == "table":
            keys = [k for k in (spec.get("fields") or {}) if any(r.get(k) not in (None, "", []) for r in recs)]
            head = ["ID", "Title"] + (["Status"] if spec.get("statuses") else []) + [k.replace("_", " ").title() for k in keys]
            lines += ["", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            for r in sorted(recs, key=sort_key):
                row = [r["id"], r.get("title") or ""] + ([r.get("status") or ""] if spec.get("statuses") else [])
                row += [", ".join(map(str, r[k])) if isinstance(r.get(k), list) else ("" if r.get(k) is None else r[k])
                        for k in keys]
                lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |")
        elif spec.get("statuses"):
            for status in spec["statuses"]:
                group = [r for r in recs if r.get("status") == status]
                if not group:
                    continue
                lines += ["", "## %s" % status.title(), ""]
                done = status in (spec.get("done") or [])
                for r in sorted(group, key=sort_key):
                    meta = [r["id"]]
                    if r.get("priority"):
                        meta.append(r["priority"])
                    meta += ["#" + t for t in r.get("tags") or []]
                    if r.get("assignee"):
                        meta.append("@" + r["assignee"])
                    box = "- [x]" if done else "- [ ]"
                    lines.append("%s %s  (%s)" % (box, r.get("title"), " ".join(meta)))
        else:
            for r in recs:
                lines.append("- %s (%s)" % (r.get("title") or r.get("summary"), r["id"]))
        out[rtype] = "\n".join(lines).strip() + "\n"
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for rtype, text in out.items():
            write_text_atomic(os.path.join(args.out, rtype + ".md"), text)
        print("wrote %s" % ", ".join(os.path.join(args.out, t + ".md") for t in out))
    else:
        print("\n\n".join(out.values()).rstrip())


# --------------------------------------------------------------------------
# argument parsing

def build_parser():
    p = argparse.ArgumentParser(prog="repostate", description="Project state as JSON inside the repo.")
    p.add_argument("--root", help="repo root (default: the git top level, else the current directory)")
    p.add_argument("--version", action="version", version="repostate " + VERSION)
    sub = p.add_subparsers(dest="command", metavar="command")

    def cmd(name, func, help_text, aliases=()):
        sp = sub.add_parser(name, help=help_text, aliases=list(aliases))
        sp.set_defaults(func=func)
        sp.add_argument("--json", action="store_true", help="print machine-readable JSON")
        return sp

    sp = cmd("init", cmd_init, "create or upgrade .repostate/ in this repo")
    sp.add_argument("--name", help="project name shown in the dashboard")
    sp.add_argument("--hooks", action="store_true", help="install git hooks that rebuild the dashboard after pulls")
    sp.add_argument("--actions", action="store_true", help="add a GitHub Action that posts a summary on PRs")

    def record_opts(sp, creating):
        sp.add_argument("--title")
        sp.add_argument("--summary", help="progress entries: what happened")
        sp.add_argument("--status")
        sp.add_argument("--priority")
        sp.add_argument("--goal", help="goal id this belongs to")
        sp.add_argument("--assignee")
        sp.add_argument("--parent", help="parent task id")
        sp.add_argument("--body", help="longer description (markdown)")
        sp.add_argument("--tags", action="append", help="comma-separated tags")
        sp.add_argument("--depends", action="append", help="comma-separated ids this waits on")
        sp.add_argument("--set", action="append", metavar="KEY=VALUE", help="any other field (repeatable)")
        sp.add_argument("--add", action="append", metavar="KEY=VALUE",
                        help="append to a list field: prs, repos, requirements, evidence, figma (repeatable)")
        sp.add_argument("--task", help="task this test case, bug or question belongs to")
        sp.add_argument("--result", help="test cases: same as --status (pass, pass_with_question, fail, blocked, not_run)")
        sp.add_argument("--slice", help="tasks: slice or phase, e.g. A or D2")
        sp.add_argument("--estimate", type=float, help="tasks: estimated hours")
        sp.add_argument("--actual", type=float, help="tasks: actual hours so far (use the hours command to add)")
        sp.add_argument("--severity", help="bugs: low, medium, high or critical")
        sp.add_argument("--kind", help="test cases: ui, api, ...")
        sp.add_argument("--owner", help="decisions and questions: who owns it")
        sp.add_argument("--waiting-on", dest="waiting_on", help="who or what this is waiting on")
        sp.add_argument("--json-data", "--data", dest="json_data", metavar="JSON",
                        help="a JSON object of fields, a path to one, or - for stdin")
        if creating:
            g = sp.add_mutually_exclusive_group()
            g.add_argument("--personal", action="store_true", help="keep it private (gitignored)")
            g.add_argument("--team", action="store_true", help="share it with the team (committed)")

    sp = cmd("add", cmd_add, "add a record: add tasks --title ...")
    sp.add_argument("type", help="tasks, goals, progress, decisions, testcases, bugs, requirements, questions, "
                                 "reviews, notes or a custom type")
    sp.add_argument("--id", help="use this id (e.g. T001, MSG-01) instead of a generated one")
    record_opts(sp, True)

    sp = cmd("update", cmd_update, "change fields of a record")
    sp.add_argument("id")
    record_opts(sp, False)
    sp.add_argument("--add-tag", action="append")
    sp.add_argument("--add-depends", action="append")
    sp.add_argument("--unset", action="append", metavar="KEY", help="remove a field")

    for name, status, text in (("done", "done", "mark a record done"),
                               ("start", "start", "mark a task as in progress")):
        sp = cmd(name, cmd_set_status(status), text)
        sp.add_argument("id")

    sp = cmd("hours", cmd_hours, "add actual hours to a task: hours T001 2.5")
    sp.add_argument("id")
    sp.add_argument("hours", type=float)
    sp.add_argument("--note")

    sp = cmd("log", cmd_log, "add a progress entry for this session")
    sp.add_argument("--hours", type=float, help="hours spent; booked on the task when exactly one is named")
    sp.add_argument("summary")
    sp.add_argument("--details")
    sp.add_argument("--tasks", action="append", help="task ids this work touched")
    sp.add_argument("--done", action="append", help="task ids to mark done at the same time")
    sp.add_argument("--files", action="append", help="files changed")
    sp.add_argument("--session", help="agent session id")
    sp.add_argument("--personal", action="store_true")

    sp = cmd("list", cmd_list, "list records", aliases=("ls",))
    sp.add_argument("type", nargs="?")
    sp.add_argument("--status")
    sp.add_argument("--open", action="store_true", help="hide finished items")
    sp.add_argument("--scope", choices=SCOPES)
    sp.add_argument("--mine", action="store_true", help="assigned to or written by me")
    sp.add_argument("--archived", action="store_true", help="include archived reviews")
    sp.add_argument("-q", "--query", help="text search")

    sp = cmd("show", cmd_show, "print one record")
    sp.add_argument("id")

    sp = cmd("promote", cmd_promote, "move a personal record to the team (shared)", aliases=("share",))
    sp.add_argument("id")
    sp = cmd("unshare", cmd_unshare, "move a team record back to personal")
    sp.add_argument("id")
    sp = cmd("delete", cmd_delete, "delete a record", aliases=("rm",))
    sp.add_argument("id")

    rv = sub.add_parser("review", help="code reviews scoped to a branch, session, worktree or commits")
    rsub = rv.add_subparsers(dest="review_command", metavar="action")
    sp = rsub.add_parser("new", help="start a review (git details are filled in)")
    sp.set_defaults(func=cmd_review_new)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--title")
    sp.add_argument("--summary")
    sp.add_argument("--scope", choices=REVIEW_SCOPES, help="what was reviewed (default branch)")
    sp.add_argument("--base", help="branch compared against (default: the repo's default branch)")
    sp.add_argument("--range", help="commit range for --scope commits, e.g. a1b2c3..HEAD")
    sp.add_argument("--session", help="session id for --scope session")
    sp.add_argument("--team", action="store_true", help="share it now instead of keeping it personal")
    sp.add_argument("--json-data", "--data", dest="json_data", metavar="JSON",
                    help="the whole review as JSON (title, summary, findings), a path, or - for stdin")
    sp = rsub.add_parser("finding", help="add a finding to a review")
    sp.set_defaults(func=cmd_review_finding)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("review")
    sp.add_argument("--severity", required=True, choices=SEVERITIES)
    sp.add_argument("--title", required=True)
    sp.add_argument("--file")
    sp.add_argument("--line", type=int)
    sp.add_argument("--body")
    sp.add_argument("--suggestion")
    sp.add_argument("--task", help="task id that will fix it")
    sp = rsub.add_parser("resolve", help="mark a finding fixed, wontfix or open")
    sp.set_defaults(func=cmd_review_resolve)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("review")
    sp.add_argument("finding")
    sp.add_argument("--status", default="fixed", choices=FINDING_STATUSES)
    sp.add_argument("--note")

    sp = rsub.add_parser("link", help="link a finding to the task that will fix it")
    sp.set_defaults(func=cmd_review_link)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("review")
    sp.add_argument("finding")
    sp.add_argument("task")

    sp = cmd("archive", cmd_archive, "archive reviews of merged or deleted branches")
    sp.add_argument("--into", help="branch to treat as merged into (default: the repo's default branch)")
    sp.add_argument("--dry-run", action="store_true")

    sp = cmd("import", cmd_import, "import markdown: task lists, tables of tasks, tests, bugs, decisions, ADRs")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--type", help="force the type instead of guessing from the file name")
    sp.add_argument("--dry-run", action="store_true", help="show what would be created, write nothing")
    sp.add_argument("--update", action="store_true",
                    help="refresh records that already exist from table rows with the same id")
    sp.add_argument("--personal", action="store_true")
    sp.add_argument("--limit", type=int, default=25, help="items to preview per file")

    sp = cmd("export", cmd_export, "write records back out as markdown")
    sp.add_argument("--type", action="append")
    sp.add_argument("--out", help="folder to write <type>.md files into (default: print)")
    sp.add_argument("--personal", action="store_true", help="include personal records")

    sp = cmd("build", cmd_build, "regenerate .repostate/data.js for the dashboard")
    sp.add_argument("--quiet", action="store_true")
    cmd("validate", cmd_validate, "check every record for problems")
    sp = cmd("summary", cmd_summary, "markdown summary of team data (for PR comments)")
    sp.add_argument("--since", help="also list records changed since this ref, e.g. origin/main")
    sp = cmd("open", cmd_open, "build and open the dashboard in a browser")
    sp.add_argument("--print-only", action="store_true", help="print the file URL without opening it")
    cmd("types", cmd_types, "list record types and their statuses")
    cmd("hooks", cmd_hooks, "install git hooks (rebuild the dashboard after pulls) and the record merge driver")
    sp = cmd("merge-file", cmd_merge_file, "git merge driver for type files (used by git, not by hand)")
    sp.add_argument("base")
    sp.add_argument("ours")
    sp.add_argument("theirs")
    return p


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        store = Store.locate(args.root)
        args.func(store, args)
    except StateError as exc:
        print("repostate: %s" % exc, file=sys.stderr)
        return 1
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
