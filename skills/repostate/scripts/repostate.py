#!/usr/bin/env python3
"""repostate: keep a repo's tasks, goals, progress, reviews and decisions as JSON.

Every record is one JSON file under .repostate/<scope>/<type>/<id>.json, where
scope is "team" (committed to git) or "personal" (gitignored). The viewer,
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

VERSION = "0.1.0"
STATE_DIR = ".repostate"
SCOPES = ("team", "personal")
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"

BUILTIN_TYPES = {
    "tasks": {
        "prefix": "T", "label": "Tasks", "view": "board", "scope": "team",
        "statuses": ["todo", "doing", "done"], "done": ["done"],
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
        "prefix": "D", "label": "Architecture", "view": "cards", "scope": "team",
        "statuses": ["proposed", "accepted", "superseded", "rejected"], "done": ["accepted"],
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
            if spec.get("statuses") and not spec.get("done"):
                spec["done"] = [spec["statuses"][-1]]
        return result

    def type_spec(self, rtype):
        types = self.types()
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

    # ---- records
    def record_path(self, scope, rtype, rid, archived=False):
        parts = [self.dir, scope]
        if archived:
            parts.append("archive")
        parts += [rtype, rid + ".json"]
        return os.path.join(*parts)

    def iter_files(self):
        """Yield (scope, type, archived, path) for every record file."""
        for scope in SCOPES:
            base = os.path.join(self.dir, scope)
            if not os.path.isdir(base):
                continue
            for archived, root in ((False, base), (True, os.path.join(base, "archive"))):
                if not os.path.isdir(root):
                    continue
                for rtype in sorted(os.listdir(root)):
                    tdir = os.path.join(root, rtype)
                    if rtype == "archive" or not os.path.isdir(tdir):
                        continue
                    for name in sorted(os.listdir(tdir)):
                        if name.endswith(".json"):
                            yield scope, rtype, archived, os.path.join(tdir, name)

    def all_records(self, errors=None):
        records = []
        for scope, rtype, archived, path in self.iter_files():
            try:
                rec = read_json(path)
            except (ValueError, OSError) as exc:
                if errors is not None:
                    errors.append("%s: unreadable (%s)" % (os.path.relpath(path, self.root), exc))
                continue
            if not isinstance(rec, dict):
                if errors is not None:
                    errors.append("%s: not a JSON object" % os.path.relpath(path, self.root))
                continue
            rec["_scope"] = scope
            rec["_type"] = rtype
            rec["_archived"] = archived
            rec["_path"] = os.path.relpath(path, self.root).replace(os.sep, "/")
            records.append(rec)
        return records

    def find(self, rid):
        rid = rid.strip()
        for scope, rtype, archived, path in self.iter_files():
            if os.path.basename(path)[:-5].lower() == rid.lower():
                return scope, rtype, archived, path
        raise StateError("no record with id %s" % rid)

    def load(self, rid):
        scope, rtype, archived, path = self.find(rid)
        return read_json(path), scope, rtype, archived, path

    def new_id(self, prefix):
        taken = {os.path.basename(p)[:-5].lower() for _, _, _, p in self.iter_files()}
        rng = random.SystemRandom()
        for length in (4, 5, 6, 8):
            for _ in range(50):
                rid = "%s-%s" % (prefix, "".join(rng.choice(ID_ALPHABET) for _ in range(length)))
                if rid.lower() not in taken:
                    return rid
        raise StateError("could not find a free id")

    def save(self, rec, scope, rtype, archived=False):
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        write_json_atomic(self.record_path(scope, rtype, clean["id"], archived), clean)

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
            if rec.get("goal"):
                refs.append(rec["goal"])
            for key in ("depends_on", "tasks", "related"):
                refs.extend(rec.get(key) or [])
            if rec.get("parent"):
                refs.append(rec["parent"])
            for ref in refs:
                if isinstance(ref, str) and ref.lower() not in ids:
                    problems.append("links to %s, which does not exist" % ref)
        return problems

    # ---- the generated bundle the viewer reads
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
            },
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


def create_record(store, rtype, fields, scope=None, quiet=False):
    """Create a record of rtype from a dict. Returns (rec, scope)."""
    rtype, spec = store.type_spec(rtype)
    scope = scope or spec.get("scope", "team")
    if scope not in SCOPES:
        raise StateError("scope must be team or personal")
    rec = {"id": store.new_id(spec["prefix"])}
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
    if found:
        lines.append("markdown files that can be imported: " + ", ".join(found))
        lines.append("preview with: python3 %s/repostate.py import %s --dry-run" % (STATE_DIR, " ".join(found)))
    lines.append("open the dashboard: %s/index.html" % STATE_DIR)
    finish(store, args, {"created": first, "copied": copied, "importable": found}, "\n".join(lines))


def find_markdown_candidates(root):
    pattern = re.compile(r"(task|todo|backlog|progress|changelog|worklog|journal|goal|roadmap|milestone|decision|adr|plan)", re.I)
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
    fields = {
        "title": args.title,
        "status": args.status,
        "priority": args.priority,
        "goal": args.goal,
        "assignee": args.assignee,
        "parent": args.parent,
        "body": args.body,
        "tags": split_list(args.tags),
        "depends_on": split_list(args.depends),
    }
    if rtype == "progress" and args.title and not args.summary:
        fields["summary"] = fields.pop("title")
    if args.summary:
        fields["summary"] = args.summary
    rec = {}
    merge_json_arg(rec, args.json_data)
    apply_fields(rec, spec, args.set)
    for k, v in fields.items():
        if v not in (None, []):
            rec[k] = v
    if rtype == "progress":
        rec.setdefault("branch", store.branch() or None)
        rec.setdefault("commit", store.head() or None)
    scope = "personal" if args.personal else ("team" if args.team else None)
    with store.lock():
        rec, scope = create_record(store, rtype, rec, scope)
    label = rec.get("title") or rec.get("summary")
    finish(store, args, rec, "%s  %s  [%s, %s]" % (rec["id"], label, rtype, scope))


def cmd_update(store, args):
    store.require()
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.id)
        rtype, spec = store.type_spec(rtype)
        merge_json_arg(rec, args.json_data)
        apply_fields(rec, spec, args.set)
        for key in ("title", "status", "priority", "goal", "assignee", "parent", "body", "summary"):
            value = getattr(args, key, None)
            if value is not None:
                rec[key] = None if value == "" else value
        if args.tags is not None:
            rec["tags"] = split_list(args.tags)
        if args.add_tag:
            rec["tags"] = sorted(set(rec.get("tags") or []) | set(split_list(args.add_tag)))
        if args.depends is not None:
            rec["depends_on"] = split_list(args.depends)
        if args.add_depends:
            deps = rec.get("depends_on") or []
            rec["depends_on"] = deps + [d for d in split_list(args.add_depends) if d not in deps]
        for key in args.unset or []:
            rec.pop(key, None)
        rec = {k: v for k, v in rec.items() if v is not None}
        done = spec.get("done") or []
        if rec.get("status") in done:
            rec.setdefault("done_at", now_iso())
        elif "done_at" in rec:
            rec.pop("done_at")
        rec["updated_at"] = now_iso()
        problems = store.check_record(rec, rtype, spec)
        if problems:
            raise StateError("; ".join(problems))
        store.save(rec, scope, rtype, archived)
    finish(store, args, rec, "%s updated (%s)" % (rec["id"], rec.get("status") or rtype))


def cmd_set_status(status):
    def run(store, args):
        args.set, args.json_data, args.tags, args.add_tag, args.depends = None, None, None, None, None
        args.add_depends, args.unset = None, None
        for key in ("title", "priority", "goal", "assignee", "parent", "body", "summary"):
            setattr(args, key, None)
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


def cmd_log(store, args):
    store.require()
    rec = {"summary": args.summary}
    if args.details:
        rec["details"] = args.details
    if args.tasks:
        rec["tasks"] = split_list(args.tasks)
    if args.files:
        rec["files"] = split_list(args.files)
    if args.session:
        rec["session"] = args.session
    rec["branch"] = store.branch() or None
    rec["commit"] = store.head() or None
    scope = "personal" if args.personal else None
    with store.lock():
        rec, scope = create_record(store, "progress", rec, scope)
        closed = []
        for tid in split_list(args.done):
            task, tscope, ttype, tarch, _ = store.load(tid)
            _, tspec = store.type_spec(ttype)
            task["status"] = (tspec.get("done") or ["done"])[0]
            task["done_at"] = task["updated_at"] = now_iso()
            store.save(task, tscope, ttype, tarch)
            closed.append(task["id"])
    extra = (" and marked done: " + ", ".join(closed)) if closed else ""
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
    rec["_scope"], rec["_type"], rec["_path"] = scope, rtype, os.path.relpath(path, store.root)
    print(json.dumps(rec, indent=2, ensure_ascii=False))


def move_record(store, args, to_scope):
    store.require()
    with store.lock():
        rec, scope, rtype, archived, path = store.load(args.id)
        if scope == to_scope:
            raise StateError("%s is already %s" % (rec["id"], to_scope))
        rec["updated_at"] = now_iso()
        store.save(rec, to_scope, rtype, archived)
        os.remove(path)
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
        os.remove(path)
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
                path = os.path.join(store.root, rec["_path"])
                if not args.dry_run:
                    store.save(rec, rec["_scope"], "reviews", archived=True)
                    os.remove(path)
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
        name = os.path.basename(rec["_path"])[:-5].lower()
        if rid != name:
            errors.append("%s: id %r does not match its file name" % (rec["_path"], rec.get("id")))
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
    if args.since:
        changed = git(["diff", "--name-status", "%s...HEAD" % args.since, "--", "%s/team" % STATE_DIR], store.root)
        rows = []
        for line in changed.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[-1].endswith(".json"):
                continue
            rid = os.path.basename(parts[-1])[:-5]
            rec = by_id.get(rid)
            label = (rec.get("title") or rec.get("summary")) if rec else "(removed)"
            what = {"A": "added", "M": "updated", "D": "removed"}.get(parts[0][:1], "changed")
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


def cmd_import(store, args):
    store.require()
    plan = []
    for path in args.files:
        if not os.path.isfile(path):
            raise StateError("%s does not exist" % path)
        rtype = args.type or guess_type(path)
        if not rtype:
            raise StateError("can't tell what %s holds. Pass --type tasks|goals|progress|decisions" % path)
        rtype, spec = store.type_spec(rtype)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        rel = os.path.relpath(os.path.abspath(path), store.root).replace(os.sep, "/")
        if rtype == "progress":
            items = parse_progress_markdown(text, rel)
        elif rtype == "decisions":
            items = parse_decision_markdown(text, rel, spec)
        else:
            items = parse_list_markdown(text, rtype, spec, rel)
        plan.append((path, rtype, spec, items))

    existing = {}
    for rec in store.all_records():
        src = rec.get("source") or {}
        key = (src.get("file"), (rec.get("title") or rec.get("summary") or "").strip().lower())
        existing[key] = rec["id"]

    report = []
    created = []
    scope = "personal" if args.personal else None
    lock = store.lock() if not args.dry_run else None
    if lock:
        lock.__enter__()
    try:
        for path, rtype, spec, items in plan:
            new_ids = []
            skipped = 0
            for item in items:
                key = (item["source"]["file"], (item.get("title") or item.get("summary") or "").strip().lower())
                if key in existing:
                    skipped += 1
                    new_ids.append(existing[key])
                    continue
                parent_index = item.pop("_parent_index", None)
                if parent_index is not None and parent_index < len(new_ids) and new_ids[parent_index]:
                    item["parent"] = new_ids[parent_index]
                if args.dry_run:
                    new_ids.append(None)
                    continue
                rec, _ = create_record(store, rtype, item, scope)
                new_ids.append(rec["id"])
                created.append(rec["id"])
                existing[key] = rec["id"]
            report.append({"file": path, "type": rtype, "found": len(items), "skipped": skipped,
                           "items": items})
    finally:
        if lock:
            lock.__exit__(None, None, None)

    if args.json:
        print(json.dumps({"dry_run": args.dry_run, "created": created, "files": report}, indent=2,
                         ensure_ascii=False))
        if not args.dry_run:
            store.build()
        return
    for entry in report:
        new = entry["found"] - entry["skipped"]
        print("%s → %d %s (%d new, %d already imported)" % (entry["file"], entry["found"], entry["type"],
                                                            new, entry["skipped"]))
        for item in entry["items"][: args.limit]:
            label = item.get("title") or item.get("summary")
            extra = [item.get("status"), item.get("priority"), item.get("group"),
                     ("#" + " #".join(item["tags"])) if item.get("tags") else None,
                     ("@" + item["assignee"]) if item.get("assignee") else None,
                     ("due " + item["due"]) if item.get("due") else None,
                     (item.get("created_at") or "")[:10] or None]
            print("   - %s  (%s)" % (label, ", ".join(x for x in extra if x)))
        if len(entry["items"]) > args.limit:
            print("   … and %d more" % (len(entry["items"]) - args.limit))
    if args.dry_run:
        print("\nDry run: nothing was written. Run again without --dry-run to import.")
    else:
        store.build()
        print("\nImported %d records. The original markdown files were not changed." % len(created))


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
        sp.add_argument("--json-data", "--data", dest="json_data", metavar="JSON",
                        help="a JSON object of fields, a path to one, or - for stdin")
        if creating:
            g = sp.add_mutually_exclusive_group()
            g.add_argument("--personal", action="store_true", help="keep it private (gitignored)")
            g.add_argument("--team", action="store_true", help="share it with the team (committed)")

    sp = cmd("add", cmd_add, "add a record: add tasks --title ...")
    sp.add_argument("type", help="tasks, goals, progress, decisions, reviews, notes or a custom type")
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

    sp = cmd("log", cmd_log, "add a progress entry for this session")
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

    sp = cmd("import", cmd_import, "import tasks.md, progress.md, goals.md or ADR files")
    sp.add_argument("files", nargs="+")
    sp.add_argument("--type", help="force the type instead of guessing from the file name")
    sp.add_argument("--dry-run", action="store_true", help="show what would be created, write nothing")
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
    cmd("hooks", cmd_hooks, "install git hooks that rebuild the dashboard")
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
