"""End-to-end tests for repostate.py. Run: python -m unittest discover -s tests -v"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skills", "repostate", "scripts", "repostate.py")


def git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="repostate-test-")
        git(self.dir, "init", "-q")
        git(self.dir, "config", "user.email", "dev@example.com")
        git(self.dir, "config", "user.name", "Dev")
        git(self.dir, "config", "commit.gpgsign", "false")
        git(self.dir, "checkout", "-q", "-b", "main")
        self.write("README.md", "hello\n")
        git(self.dir, "add", ".")
        git(self.dir, "commit", "-qm", "init")
        self.run_ok("init", "--name", "Test project")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # helpers
    def run_cli(self, *args, stdin=None):
        proc = subprocess.run([sys.executable, SCRIPT, "--root", self.dir] + list(args), cwd=self.dir,
                              input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, encoding="utf-8")
        return proc

    def run_ok(self, *args, stdin=None):
        proc = self.run_cli(*args, stdin=stdin)
        self.assertEqual(proc.returncode, 0, "command %s failed:\n%s\n%s" % (args, proc.stdout, proc.stderr))
        return proc.stdout

    def run_json(self, *args, stdin=None):
        return json.loads(self.run_ok(*(list(args) + ["--json"]), stdin=stdin))

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path) or self.dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(text))
        return path

    def path(self, *parts):
        return os.path.join(self.dir, ".repostate", *parts)

    def data(self):
        with open(self.path("data.js"), encoding="utf-8") as fh:
            text = fh.read()
        return json.loads(text[text.index("=") + 1:].strip().rstrip(";"))


class TestInit(RepoCase):
    def test_layout(self):
        for name in ("config.json", ".gitignore", "repostate.py", "index.html", "README.md", "data.js"):
            self.assertTrue(os.path.exists(self.path(name)), name)
        with open(self.path(".gitignore")) as fh:
            ignore = fh.read()
        for line in ("personal/", "config.local.json", "data.js"):
            self.assertIn(line, ignore)
        with open(self.path("config.json")) as fh:
            self.assertEqual(json.load(fh)["project"]["name"], "Test project")

    def test_personal_is_ignored_by_git(self):
        self.run_ok("add", "tasks", "--title", "secret", "--personal")
        self.run_ok("add", "tasks", "--title", "shared")
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.dir,
                                stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertIn(".repostate/team/tasks/", status)
        self.assertNotIn("personal", status)
        self.assertNotIn("data.js", status)

    def test_reinit_keeps_data_and_config(self):
        self.run_ok("add", "goals", "--title", "Ship it")
        with open(self.path("config.json")) as fh:
            cfg = json.load(fh)
        cfg["project"]["accent"] = "#0F766E"
        with open(self.path("config.json"), "w") as fh:
            json.dump(cfg, fh)
        self.run_ok("init")
        with open(self.path("config.json")) as fh:
            self.assertEqual(json.load(fh)["project"]["accent"], "#0F766E")
        self.assertEqual(len(self.run_json("list", "goals")), 1)

    def test_vendored_copy_runs(self):
        proc = subprocess.run([sys.executable, self.path("repostate.py"), "list"], cwd=self.dir,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestRecords(RepoCase):
    def test_task_lifecycle(self):
        goal = self.run_json("add", "goals", "--title", "Retry safety")
        task = self.run_json("add", "tasks", "--title", "Unique index", "--priority", "high",
                             "--goal", goal["id"], "--tags", "db,payments")
        self.assertTrue(task["id"].startswith("T-"))
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["tags"], ["db", "payments"])
        self.assertEqual(task["author"], "dev@example.com")
        self.assertEqual(self.run_json("start", task["id"])["status"], "doing")
        done = self.run_json("done", task["id"])
        self.assertEqual(done["status"], "done")
        self.assertIn("done_at", done)
        reopened = self.run_json("update", task["id"], "--status", "todo")
        self.assertNotIn("done_at", reopened)

    def test_ids_are_unique(self):
        ids = {self.run_json("add", "tasks", "--title", "t%d" % i)["id"] for i in range(30)}
        self.assertEqual(len(ids), 30)

    def test_rejects_bad_status_and_priority(self):
        bad = self.run_cli("add", "tasks", "--title", "x", "--status", "nope")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("status", bad.stderr)
        bad = self.run_cli("add", "tasks", "--title", "x", "--priority", "huge")
        self.assertEqual(bad.returncode, 1)

    def test_update_fields_and_dependencies(self):
        a = self.run_json("add", "tasks", "--title", "A")
        b = self.run_json("add", "tasks", "--title", "B", "--set", "estimate_hours=3")
        self.assertEqual(b["estimate_hours"], 3)
        b = self.run_json("update", b["id"], "--add-depends", a["id"], "--add-tag", "x", "--unset", "estimate_hours")
        self.assertEqual(b["depends_on"], [a["id"]])
        self.assertEqual(b["tags"], ["x"])
        self.assertNotIn("estimate_hours", b)

    def test_log_marks_tasks_done(self):
        t = self.run_json("add", "tasks", "--title", "A")
        entry = self.run_json("log", "Did A", "--done", t["id"], "--details", "two files")
        self.assertEqual(entry["summary"], "Did A")
        self.assertEqual(entry["branch"], "main")
        self.assertEqual(self.run_json("show", t["id"])["status"], "done")

    def test_promote_and_unshare(self):
        n = self.run_json("add", "notes", "--title", "idea")
        self.assertTrue(os.path.exists(self.path("personal", "notes", n["id"] + ".json")))
        self.run_ok("promote", n["id"])
        self.assertTrue(os.path.exists(self.path("team", "notes", n["id"] + ".json")))
        self.assertFalse(os.path.exists(self.path("personal", "notes", n["id"] + ".json")))
        self.run_ok("unshare", n["id"])
        self.assertTrue(os.path.exists(self.path("personal", "notes", n["id"] + ".json")))

    def test_list_filters(self):
        self.run_ok("add", "tasks", "--title", "open one")
        d = self.run_json("add", "tasks", "--title", "finished")
        self.run_ok("done", d["id"])
        self.run_ok("add", "tasks", "--title", "mine", "--personal")
        self.assertEqual(len(self.run_json("list", "tasks", "--open")), 2)
        self.assertEqual(len(self.run_json("list", "tasks", "--scope", "personal")), 1)
        self.assertEqual(len(self.run_json("list", "-q", "finish")), 1)

    def test_singular_type_names(self):
        self.assertTrue(self.run_json("add", "task", "--title", "x")["id"].startswith("T-"))

    def test_delete(self):
        t = self.run_json("add", "tasks", "--title", "x")
        self.run_ok("delete", t["id"])
        self.assertEqual(self.run_cli("show", t["id"]).returncode, 1)


class TestValidate(RepoCase):
    def test_clean(self):
        self.run_ok("add", "tasks", "--title", "x")
        self.assertIn("no problems", self.run_ok("validate"))

    def test_detects_problems(self):
        t = self.run_json("add", "tasks", "--title", "x")
        path = self.path("team", "tasks", t["id"] + ".json")
        with open(path) as fh:
            rec = json.load(fh)
        rec["status"] = "weird"
        rec["goal"] = "G-missing"
        with open(path, "w") as fh:
            json.dump(rec, fh)
        with open(self.path("team", "tasks", "T-copy.json"), "w") as fh:
            json.dump(dict(rec, id="T-other"), fh)
        with open(self.path("team", "tasks", "T-broken.json"), "w") as fh:
            fh.write("{not json")
        out = self.run_cli("validate")
        self.assertEqual(out.returncode, 1)
        for text in ("weird", "G-missing", "does not match its file name", "unreadable"):
            self.assertIn(text, out.stdout)

    def test_team_depending_on_personal(self):
        p = self.run_json("add", "tasks", "--title", "private", "--personal")
        self.run_ok("add", "tasks", "--title", "shared", "--depends", p["id"])
        out = self.run_cli("validate")
        self.assertEqual(out.returncode, 1)
        self.assertIn("personal", out.stdout)


class TestConfig(RepoCase):
    def set_config(self, extra):
        with open(self.path("config.json")) as fh:
            cfg = json.load(fh)
        cfg.update(extra)
        with open(self.path("config.json"), "w") as fh:
            json.dump(cfg, fh)

    def test_custom_type_and_fields(self):
        self.set_config({"types": {
            "tasks": {"statuses": ["backlog", "todo", "doing", "review", "done"],
                      "fields": {"estimate_hours": {"type": "number"}, "billable": {"type": "bool"}}},
            "bugs": {"prefix": "B", "label": "Bugs", "view": "board", "statuses": ["new", "fixing", "fixed"],
                     "fields": {"severity": {"type": "enum", "options": ["low", "high"], "required": True}}}}})
        t = self.run_json("add", "tasks", "--title", "x", "--set", "estimate_hours=2.5", "--set", "billable=yes")
        self.assertEqual(t["status"], "backlog")
        self.assertEqual(t["estimate_hours"], 2.5)
        self.assertIs(t["billable"], True)
        self.assertEqual(self.run_cli("add", "tasks", "--title", "y", "--set", "estimate_hours=lots").returncode, 1)
        self.assertEqual(self.run_json("start", t["id"])["status"], "todo")
        self.assertEqual(self.run_cli("add", "bugs", "--title", "crash").returncode, 1)
        bug = self.run_json("add", "bugs", "--title", "crash", "--set", "severity=high")
        self.assertTrue(bug["id"].startswith("B-"))
        self.assertEqual(self.run_json("done", bug["id"])["status"], "fixed")
        self.assertIn("bugs", self.data()["types"])

    def test_local_config_overrides(self):
        with open(self.path("config.local.json"), "w") as fh:
            json.dump({"types": {"tasks": {"scope": "personal"}}}, fh)
        t = self.run_json("add", "tasks", "--title", "x")
        self.assertTrue(os.path.exists(self.path("personal", "tasks", t["id"] + ".json")))


class TestImport(RepoCase):
    TASKS = """\
        # Tasks

        ## In Progress
        - [ ] Return stored response @priya #payments P1
          - [ ] Write the lookup
          - [x] Add the migration
        - [~] Batch inventory checks !medium

        ## Todo
        - [ ] Move webhook retries due: 2026-12-05
        - Alert on duplicates

        ## Blocked
        - [ ] Split webhook handling

        ## Done
        - [x] Unique index
        - ~~Profile hot path~~
        """

    def test_tasks(self):
        self.write("tasks.md", self.TASKS)
        preview = self.run_json("import", "tasks.md", "--dry-run")
        self.assertEqual(preview["created"], [])
        self.assertFalse(os.path.isdir(self.path("team", "tasks")) and os.listdir(self.path("team", "tasks")))
        self.run_ok("import", "tasks.md")
        tasks = {t["title"]: t for t in self.run_json("list", "tasks")}
        self.assertEqual(len(tasks), 9)
        first = tasks["Return stored response"]
        self.assertEqual((first["status"], first["priority"], first["assignee"], first["tags"]),
                         ("doing", "high", "priya", ["payments"]))
        self.assertEqual(tasks["Write the lookup"]["parent"], first["id"])
        self.assertEqual(tasks["Write the lookup"]["status"], "todo")
        self.assertEqual(tasks["Add the migration"]["status"], "done")
        self.assertEqual(tasks["Batch inventory checks"]["status"], "doing")
        self.assertEqual(tasks["Move webhook retries"]["due"], "2026-12-05")
        self.assertEqual(tasks["Alert on duplicates"]["status"], "todo")
        self.assertIn("blocked", tasks["Split webhook handling"]["tags"])
        self.assertEqual(tasks["Profile hot path"]["status"], "done")
        self.assertEqual(tasks["Unique index"]["source"], {"file": "tasks.md", "line": 17})
        again = self.run_json("import", "tasks.md")
        self.assertEqual(again["created"], [])
        self.assertIn("no problems", self.run_ok("validate"))

    def test_progress_goals_and_adr(self):
        self.write("progress.md", """\
            ## 2026-11-23
            - Added unique index
              - tests pass
            ## 2026-11-22 — Webhooks
            - Proposed worker
            - 2026-11-20: Migrated lookups
            """)
        self.write("goals.md", """\
            # Goals
            - [ ] Safe retries
              - no double charges
            - [x] Fast checkout
            """)
        self.write("docs/adr/0007-keys-in-postgres.md", """\
            # 7. Keys in Postgres

            Status: Accepted

            ## Context
            Keys must survive a flush.

            ## Decision
            Store them in Postgres.

            ## Consequences
            One more table.
            """)
        self.run_ok("import", "progress.md", "goals.md", "docs/adr/0007-keys-in-postgres.md")
        progress = {p["summary"]: p for p in self.run_json("list", "progress")}
        self.assertEqual(progress["Added unique index"]["created_at"][:10], "2026-11-23")
        self.assertEqual(progress["Added unique index"]["details"], "- tests pass")
        self.assertEqual(progress["Proposed worker"]["group"], "Webhooks")
        self.assertEqual(progress["Migrated lookups"]["created_at"][:10], "2026-11-20")
        goals = {g["title"]: g for g in self.run_json("list", "goals")}
        self.assertEqual(goals["Safe retries"]["status"], "active")
        self.assertIn("no double charges", goals["Safe retries"]["body"])
        self.assertEqual(goals["Fast checkout"]["status"], "achieved")
        (adr,) = self.run_json("list", "decisions")
        self.assertEqual((adr["title"], adr["status"]), ("Keys in Postgres", "accepted"))
        self.assertEqual(adr["decision"], "Store them in Postgres.")

    def test_unknown_file_needs_type(self):
        self.write("notes-from-call.md", "- something\n")
        self.assertEqual(self.run_cli("import", "notes-from-call.md").returncode, 1)
        self.run_ok("import", "notes-from-call.md", "--type", "tasks")

    def test_export_round_trip(self):
        self.write("tasks.md", self.TASKS)
        self.run_ok("import", "tasks.md")
        out = self.run_ok("export", "--type", "tasks")
        self.assertIn("## Doing", out)
        self.assertIn("- [x] Unique index", out)
        self.run_ok("export", "--out", "exported")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "exported", "tasks.md")))


class TestReviews(RepoCase):
    def make_branch(self):
        git(self.dir, "checkout", "-q", "-b", "feat/x")
        self.write("app.py", "print(1)\n")
        git(self.dir, "add", "app.py")
        git(self.dir, "commit", "-qm", "feat")

    def test_branch_review_flow(self):
        self.make_branch()
        review = self.run_json("review", "new", "--base", "main", "--data", "-", stdin=json.dumps({
            "title": "Review", "summary": "ok",
            "findings": [{"severity": "blocking", "file": "app.py", "line": 1, "title": "prints"},
                         {"severity": "minor", "title": "naming"}]}))
        self.assertTrue(review["id"].startswith("R-"))
        scope = review["scope"]
        self.assertEqual((scope["type"], scope["branch"], scope["base"], scope["files_changed"]),
                         ("branch", "feat/x", "main", 1))
        self.assertTrue(os.path.exists(self.path("personal", "reviews", review["id"] + ".json")))
        self.assertEqual([f["id"] for f in review["findings"]], ["F1", "F2"])
        f3 = self.run_json("review", "finding", review["id"], "--severity", "note", "--title", "later")
        self.assertEqual(f3["id"], "F3")
        task = self.run_json("add", "tasks", "--title", "fix prints")
        self.assertEqual(self.run_json("review", "link", review["id"], "F1", task["id"])["task"], task["id"])
        self.assertEqual(self.run_json("review", "resolve", review["id"], "F1")["status"], "fixed")
        self.assertEqual(self.run_cli("review", "finding", review["id"], "--severity", "huge", "--title", "x").returncode, 2)
        built = self.data()
        self.assertIn("feat/x", built["git"]["branches"])

    def test_other_scopes(self):
        self.assertEqual(self.run_json("review", "new", "--scope", "session", "--session", "abc")["scope"]["session"], "abc")
        self.assertEqual(self.run_cli("review", "new", "--scope", "commits").returncode, 1)
        self.assertEqual(self.run_json("review", "new", "--scope", "commits", "--range", "a..b")["scope"]["range"], "a..b")
        shared = self.run_json("review", "new", "--scope", "working", "--team")
        self.assertTrue(os.path.exists(self.path("team", "reviews", shared["id"] + ".json")))

    def test_archive_merged_branch(self):
        self.make_branch()
        review = self.run_json("review", "new", "--base", "main")
        git(self.dir, "checkout", "-q", "main")
        git(self.dir, "merge", "-q", "--no-ff", "-m", "merge", "feat/x")
        self.assertIn(review["id"], self.run_ok("archive", "--dry-run"))
        self.assertTrue(os.path.exists(self.path("personal", "reviews", review["id"] + ".json")))
        self.run_ok("archive", "--into", "main")
        self.assertTrue(os.path.exists(self.path("personal", "archive", "reviews", review["id"] + ".json")))
        self.assertEqual(self.run_json("list", "reviews"), [])
        self.assertEqual(len(self.run_json("list", "reviews", "--archived")), 1)


class TestBuildAndSummary(RepoCase):
    def test_data_js(self):
        self.run_ok("add", "tasks", "--title", "x")
        self.run_ok("add", "notes", "--title", "private")
        d = self.data()
        self.assertEqual(len(d["records"]), 2)
        self.assertEqual({r["_scope"] for r in d["records"]}, {"team", "personal"})
        self.assertEqual(d["git"]["branch"], "main")
        self.assertEqual(d["git"]["me"]["email"], "dev@example.com")

    def test_summary_since(self):
        git(self.dir, "add", ".")
        git(self.dir, "commit", "-qm", "state")
        git(self.dir, "checkout", "-q", "-b", "feat/y")
        g = self.run_json("add", "goals", "--title", "Goal")
        t = self.run_json("add", "tasks", "--title", "New task", "--goal", g["id"])
        self.run_ok("add", "notes", "--title", "private note")
        git(self.dir, "add", ".")
        git(self.dir, "commit", "-qm", "work")
        out = self.run_ok("summary", "--since", "main")
        self.assertIn(t["id"], out)
        self.assertIn("added", out)
        self.assertIn("0 of 1 tasks done", out)
        self.assertNotIn("private note", out)

    def test_hooks_are_idempotent(self):
        self.run_ok("hooks")
        self.run_ok("hooks")
        with open(os.path.join(self.dir, ".git", "hooks", "post-merge")) as fh:
            self.assertEqual(fh.read().count(">>> repostate >>>"), 1)

    def test_actions_template(self):
        self.run_ok("init", "--actions")
        self.assertTrue(os.path.exists(os.path.join(self.dir, ".github", "workflows", "repostate-pr.yml")))


if __name__ == "__main__":
    unittest.main()
