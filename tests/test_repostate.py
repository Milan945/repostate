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

    def group(self, scope, rtype, archived=False):
        path = self.path(scope, *((["archive"] if archived else []) + [rtype + ".json"]))
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            return {r["id"]: r for r in json.load(fh)["records"]}

    def stored(self, scope, rtype, rid, archived=False):
        return rid in self.group(scope, rtype, archived)

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
        self.assertIn(".repostate/team/tasks.json", status)
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
        self.assertTrue(self.stored("personal", "notes", n["id"]))
        self.run_ok("promote", n["id"])
        self.assertTrue(self.stored("team", "notes", n["id"]))
        self.assertFalse(self.stored("personal", "notes", n["id"]))
        self.run_ok("unshare", n["id"])
        self.assertTrue(self.stored("personal", "notes", n["id"]))

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
        path = self.path("team", "tasks.json")
        with open(path) as fh:
            data = json.load(fh)
        data["records"][0]["status"] = "weird"
        data["records"][0]["goal"] = "G-missing"
        data["records"].append(dict(data["records"][0]))
        with open(path, "w") as fh:
            json.dump(data, fh)
        with open(self.path("team", "goals.json"), "w") as fh:
            fh.write("{not json")
        out = self.run_cli("validate")
        self.assertEqual(out.returncode, 1)
        for text in ("weird", "G-missing", "appears twice", "unreadable"):
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
        self.assertTrue(self.stored("personal", "tasks", t["id"]))


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
        self.assertFalse(os.path.exists(self.path("team", "tasks.json")))
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
        self.assertTrue(self.stored("personal", "reviews", review["id"]))
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
        self.assertTrue(self.stored("team", "reviews", shared["id"]))

    def test_archive_merged_branch(self):
        self.make_branch()
        review = self.run_json("review", "new", "--base", "main")
        git(self.dir, "checkout", "-q", "main")
        git(self.dir, "merge", "-q", "--no-ff", "-m", "merge", "feat/x")
        self.assertIn(review["id"], self.run_ok("archive", "--dry-run"))
        self.assertTrue(self.stored("personal", "reviews", review["id"]))
        self.run_ok("archive", "--into", "main")
        self.assertTrue(self.stored("personal", "reviews", review["id"], archived=True))
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



class TestIdsAndTaskFields(RepoCase):
    def test_custom_ids(self):
        t = self.run_json("add", "tasks", "--id", "T001", "--title", "first")
        self.assertEqual(t["id"], "T001")
        self.assertTrue(self.stored("team", "tasks", "T001"))
        self.assertEqual(self.run_cli("add", "tasks", "--id", "t001", "--title", "again").returncode, 1)
        self.assertEqual(self.run_cli("add", "tasks", "--id", "../x", "--title", "bad").returncode, 1)
        self.assertEqual(self.run_json("show", "t001")["title"], "first")

    def test_sequential_ids(self):
        with open(self.path("config.json")) as fh:
            cfg = json.load(fh)
        cfg["types"] = {"tasks": {"ids": "sequential"}}
        with open(self.path("config.json"), "w") as fh:
            json.dump(cfg, fh)
        self.run_ok("add", "tasks", "--id", "T007", "--title", "seven")
        self.assertEqual(self.run_json("add", "tasks", "--title", "next")["id"], "T008")
        self.assertEqual(self.run_json("add", "tasks", "--title", "next")["id"], "T009")

    def test_slice_estimate_lists_and_links(self):
        self.run_ok("add", "tasks", "--id", "T001", "--title", "api")
        t = self.run_json("add", "tasks", "--id", "T002", "--title", "screen", "--slice", "A", "--estimate", "8",
                          "--depends", "T001", "--add", "requirements=MSG-01,MSG-02", "--add", "prs=#14")
        self.assertEqual((t["slice"], t["estimate_hours"], t["requirements"], t["prs"]),
                         ("A", 8.0, ["MSG-01", "MSG-02"], ["#14"]))
        t = self.run_json("update", "T002", "--add", "prs=#15", "--add", "prs=#14", "--add", "figma=https://figma.com/x")
        self.assertEqual(t["prs"], ["#14", "#15"])
        self.assertEqual(t["figma"], ["https://figma.com/x"])
        self.assertEqual(self.run_cli("add", "tasks", "--title", "x", "--depends", "T999").returncode, 1)
        self.assertEqual(self.run_cli("update", "T002", "--goal", "G-none").returncode, 1)

    def test_hours_and_history(self):
        self.run_ok("add", "tasks", "--id", "T001", "--title", "api", "--estimate", "6")
        self.run_ok("add", "tasks", "--id", "T002", "--title", "ui")
        self.assertEqual(self.run_json("hours", "T001", "2.5", "--note", "wiring")["actual_hours"], 2.5)
        self.run_ok("log", "More work", "--tasks", "T001", "--hours", "1.5")
        self.run_ok("start", "T001")
        t = self.run_json("done", "T001")
        self.assertEqual(t["actual_hours"], 4.0)
        fields = [(h["field"], h.get("to"), h.get("add")) for h in t["history"]]
        self.assertEqual(fields, [("hours", None, 2.5), ("hours", None, 1.5), ("status", "doing", None),
                                  ("status", "done", None)])
        out = self.run_ok("log", "Both", "--tasks", "T001,T002", "--hours", "2")
        self.assertIn("kept on the entry only", out)
        self.assertEqual(self.run_json("show", "T001")["actual_hours"], 4.0)
        entry = self.run_json("log", "Finish ui", "--done", "T002", "--hours", "1")
        self.assertEqual(entry["tasks"], ["T002"])
        t2 = self.run_json("show", "T002")
        self.assertEqual((t2["status"], t2["actual_hours"]), ("done", 1.0))

    def test_waiting_on_and_questions(self):
        q = self.run_json("add", "questions", "--title", "Read receipts?", "--owner", "milan", "--waiting-on", "Client")
        self.assertTrue(q["id"].startswith("Q-"))
        self.assertEqual((q["status"], q["waiting_on"]), ("open", "Client"))
        self.assertNotIn("waiting_on", self.run_json("update", q["id"], "--waiting-on", ""))
        self.assertEqual(self.run_json("done", q["id"])["status"], "answered")


class TestQA(RepoCase):
    def test_testcases_and_bugs(self):
        self.run_ok("add", "tasks", "--id", "T003", "--title", "badge")
        tc = self.run_json("add", "tests", "--id", "TC-014", "--title", "caps at 99+", "--task", "T003", "--kind", "UI")
        self.assertEqual((tc["status"], tc["kind"], tc["task"]), ("not_run", "ui", "T003"))
        self.assertEqual(self.run_cli("update", "TC-014", "--result", "maybe").returncode, 1)
        bug = self.run_json("add", "bugs", "--id", "BUG-007", "--title", "shows 100", "--task", "T003",
                            "--severity", "Medium", "--set", "testcase=TC-014")
        self.assertEqual((bug["status"], bug["severity"], bug["testcase"]), ("open", "medium", "TC-014"))
        self.assertEqual(self.run_cli("add", "bugs", "--title", "x", "--severity", "enormous").returncode, 1)
        self.assertEqual(self.run_cli("add", "bugs", "--title", "x", "--task", "T404").returncode, 1)
        self.assertEqual(self.run_json("update", "TC-014", "--result", "fail", "--set", "bug=BUG-007")["bug"], "BUG-007")
        fixed = self.run_json("update", "BUG-007", "--status", "fixed", "--set", "fix_commit=1234567")
        self.assertEqual(fixed["fix_commit"], "1234567")
        self.assertIn("done_at", fixed)
        self.assertIn("done_at", self.run_json("update", "TC-014", "--result", "pass_with_question"))
        summary = self.run_ok("summary")
        self.assertIn("**Tests:** pass with question 1", summary)
        self.assertIn("**Bugs:** fixed 1", summary)
        self.assertIn("no problems", self.run_ok("validate"))

    def test_requirement_ids_are_checked_once_listed(self):
        self.run_ok("add", "tasks", "--id", "T001", "--title", "x", "--add", "requirements=MSG-01,MSG-02")
        self.assertIn("no problems", self.run_ok("validate"))
        self.run_ok("add", "requirements", "--id", "MSG-01", "--title", "List")
        out = self.run_cli("validate")
        self.assertEqual(out.returncode, 1)
        self.assertIn("MSG-02 is not in the requirements list", out.stdout)

    def test_evidence_scan_and_web_link(self):
        git(self.dir, "remote", "add", "origin", "https://user:secret@github.com/acme/app.git")
        self.run_ok("add", "tasks", "--id", "T002", "--title", "screen")
        self.run_ok("add", "tasks", "--id", "T0021", "--title", "other")
        for rel in ("screenshots/T002-before.png", "ui-review/T002/after-fix.PNG", "screenshots/unrelated.png",
                    "screenshots/notes.txt", "elsewhere/T002.png"):
            self.write(rel, "x")
        self.run_ok("build")
        d = self.data()
        self.assertEqual(d["git"]["web"], "https://github.com/acme/app")
        self.assertNotIn("secret", json.dumps(d))
        found = {e["path"]: (e["ids"], e["label"]) for e in d["evidence"]}
        self.assertEqual(found, {"screenshots/T002-before.png": (["t002"], "before"),
                                 "screenshots/unrelated.png": ([], ""),
                                 "ui-review/T002/after-fix.PNG": (["t002"], "after")})


class TestTableImport(RepoCase):
    HUB = """\
        # Hub

        ## Tasks

        | ID | Task | Slice | Status | Depends on | Est dev h | Actual h | Requirements | PR |
        |----|------|-------|--------|------------|-----------|----------|--------------|----|
        | T001 | List API | A | ✅ Done | — | 6 | 7 | MSG-01 | #12 |
        | T002 | List screen | A | In progress | T001 | 8h | | MSG-01, MSG-02 | [#14](https://github.com/acme/app/pull/14) |
        | T003 | Edit profile | D2 | Blocked | T002 | ~8 | | PD-03 | |

        ## Test cases

        | TC | Scenario | Task | Kind | Result | Bug |
        |----|----------|------|------|--------|-----|
        | TC-001 | Newest first | T001 | API | Pass | |
        | TC-002 | Empty state | T002 | UI | Pass with question: is the copy final? | |
        | TC-003 | Long names | T002 | UI | ❌ Fail | BUG-001 |
        | TC-004 | Offline | T002 | UI | Blocked | |
        | TC-005 | Tablet | T002 | UI | Not run | |
        | TC-006 | Odd one | T002 | UI | Flaky on CI | |

        ## Defects

        | ID | Defect | Severity | Status | Task | Test case | Fix commit |
        |----|--------|----------|--------|------|-----------|------------|
        | BUG-001 | Names overflow | Major | Fixed | T002 | TC-003 | a1b2c3d |

        ## Decisions

        | ID | Decision | Owner | Status |
        |----|----------|-------|--------|
        | DEC-01 | Cursor pagination | Milan | Decided |
        | DEC-02 | 25 MB limit | Priya | Pending |

        ## Open questions

        | ID | Question | Owner | Waiting on |
        |----|----------|-------|------------|
        | Q-01 | Is the copy final? | Milan | Design |

        ## Requirements

        | Req | Requirement |
        |-----|-------------|
        | MSG-01 | See conversations |
        | MSG-02 | Pull to refresh |
        | PD-03 | Edit profile |
        """

    def test_hub_tables(self):
        self.write("hub.md", self.HUB)
        preview = self.run_json("import", "hub.md", "--dry-run")
        self.assertEqual({f["type"]: f["found"] for f in preview["files"]},
                         {"tasks": 3, "testcases": 6, "bugs": 1, "decisions": 2, "questions": 1, "requirements": 3})
        self.assertEqual(self.run_json("list"), [])
        self.assertEqual(len(self.run_json("import", "hub.md")["created"]), 16)

        t1, t2, t3 = (self.run_json("show", i) for i in ("T001", "T002", "T003"))
        self.assertEqual((t1["status"], t1["slice"], t1["estimate_hours"], t1["actual_hours"], t1["prs"]),
                         ("done", "A", 6, 7, ["#12"]))
        self.assertNotIn("depends_on", t1)
        self.assertEqual((t2["status"], t2["depends_on"], t2["estimate_hours"], t2["requirements"]),
                         ("doing", ["T001"], 8, ["MSG-01", "MSG-02"]))
        self.assertEqual(t2["prs"], ["https://github.com/acme/app/pull/14"])
        self.assertEqual((t3["status"], t3["tags"], t3["estimate_hours"]), ("todo", ["blocked"], 8))

        results = {r["id"]: r for r in self.run_json("list", "testcases")}
        self.assertEqual([results["TC-00%d" % i]["status"] for i in range(1, 7)],
                         ["pass", "pass_with_question", "fail", "blocked", "not_run", "not_run"])
        self.assertIn("copy final", results["TC-002"]["status_note"])
        self.assertEqual(results["TC-006"]["status_note"], "Flaky on CI")
        self.assertEqual((results["TC-003"]["bug"], results["TC-003"]["kind"], results["TC-003"]["task"]),
                         ("BUG-001", "ui", "T002"))

        bug = self.run_json("show", "BUG-001")
        self.assertEqual((bug["status"], bug["severity"], bug["testcase"], bug["fix_commit"]),
                         ("fixed", "high", "TC-003", "a1b2c3d"))
        decisions = {d["id"]: d for d in self.run_json("list", "decisions")}
        self.assertEqual((decisions["DEC-01"]["status"], decisions["DEC-01"]["owner"]), ("accepted", "Milan"))
        self.assertEqual(decisions["DEC-02"]["status"], "proposed")
        q = self.run_json("show", "Q-01")
        self.assertEqual((q["status"], q["waiting_on"]), ("open", "Design"))
        self.assertEqual(self.run_json("show", "MSG-02")["title"], "Pull to refresh")
        self.assertIn("no problems", self.run_ok("validate"))

        again = self.run_json("import", "hub.md")
        self.assertEqual((again["created"], again["updated"]), ([], []))

    def test_update_refreshes_from_changed_rows(self):
        self.write("hub.md", self.HUB)
        self.run_ok("import", "hub.md")
        with open(os.path.join(self.dir, "hub.md"), encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("| T002 | List screen | A | In progress |", "| T002 | List screen | A | Done |")
        text = text.replace("| TC-005 | Tablet | T002 | UI | Not run |", "| TC-005 | Tablet | T002 | UI | Pass |")
        with open(os.path.join(self.dir, "hub.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        self.assertEqual(self.run_json("import", "hub.md")["updated"], [])
        self.assertEqual(self.run_json("show", "T002")["status"], "doing")
        self.assertEqual(sorted(self.run_json("import", "hub.md", "--update")["updated"]), ["T002", "TC-005"])
        t2 = self.run_json("show", "T002")
        self.assertEqual(t2["status"], "done")
        self.assertEqual(t2["history"][-1]["to"], "done")
        self.assertEqual(self.run_json("show", "TC-005")["status"], "pass")

    def test_table_type_from_file_name_and_export(self):
        self.write("docs/defects.md", """\
            | # | Title | Sev | State |
            |---|-------|-----|-------|
            | 1 | Crash on open | S1 | Open |
            | 2 | Typo | trivial | Won't fix |
            """)
        self.run_ok("import", "docs/defects.md")
        bugs = {b["id"]: b for b in self.run_json("list", "bugs")}
        self.assertEqual((bugs["B1"]["severity"], bugs["B1"]["status"]), ("critical", "open"))
        self.assertEqual((bugs["B2"]["severity"], bugs["B2"]["status"]), ("low", "wontfix"))
        out = self.run_ok("export", "--type", "bugs")
        self.assertIn("| B1 | Crash on open | open | critical |", out)

    def test_checkbox_file_still_imports_as_list(self):
        self.write("tasks.md", """\
            ## Todo
            - [ ] Parent
              - [x] Child
            """)
        self.run_ok("import", "tasks.md")
        tasks = {t["title"]: t for t in self.run_json("list", "tasks")}
        self.assertEqual(tasks["Child"]["parent"], tasks["Parent"]["id"])



class TestOneFilePerType(RepoCase):
    def test_layout_is_one_line_per_record(self):
        for i in range(3):
            self.run_ok("add", "tasks", "--title", "t%d" % i)
        self.run_ok("add", "testcases", "--title", "c")
        self.assertEqual(sorted(n for n in os.listdir(self.path("team")) if n.endswith(".json")),
                         ["tasks.json", "testcases.json"])
        with open(self.path("team", "tasks.json"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], '{"type": "tasks", "records": [')
        ids = list(self.group("team", "tasks"))
        self.assertEqual(ids, sorted(ids, key=str.lower))

    def test_last_record_removed_drops_the_file(self):
        t = self.run_json("add", "tasks", "--title", "only")
        self.run_ok("delete", t["id"])
        self.assertFalse(os.path.exists(self.path("team", "tasks.json")))

    def test_old_folders_are_read_and_converted(self):
        os.makedirs(self.path("team", "tasks"))
        os.makedirs(self.path("personal", "archive", "reviews"))
        old = {"id": "T-old1", "title": "from 0.2", "status": "todo", "created_at": "2026-11-01T00:00:00Z"}
        with open(self.path("team", "tasks", "T-old1.json"), "w") as fh:
            json.dump(old, fh)
        with open(self.path("team", "tasks", "T-old2.json"), "w") as fh:
            json.dump(dict(old, id="T-old2"), fh)
        with open(self.path("personal", "archive", "reviews", "R-old.json"), "w") as fh:
            json.dump({"id": "R-old", "title": "r", "status": "closed", "findings": []}, fh)
        self.assertEqual(len(self.run_json("list", "tasks")), 2)
        self.assertEqual(self.run_json("done", "T-old1")["status"], "done")
        self.assertFalse(os.path.exists(self.path("team", "tasks")))
        self.assertEqual(set(self.group("team", "tasks")), {"T-old1", "T-old2"})
        self.assertIn("converted 1 records", self.run_ok("init"))
        self.assertFalse(os.path.exists(self.path("personal", "archive", "reviews")))
        self.assertTrue(self.stored("personal", "reviews", "R-old", archived=True))
        self.assertIn("no problems", self.run_ok("validate"))

    def test_summary_since_across_the_layout_change(self):
        os.makedirs(self.path("team", "tasks"))
        with open(self.path("team", "tasks", "T-old1.json"), "w") as fh:
            json.dump({"id": "T-old1", "title": "kept", "status": "todo"}, fh)
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "old layout")
        git(self.dir, "checkout", "-q", "-b", "feat/z")
        self.run_ok("init")
        self.run_ok("add", "tasks", "--id", "T-new", "--title", "fresh")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "new layout")
        out = self.run_ok("summary", "--since", "main")
        self.assertIn("| T-new | fresh | added", out)
        self.assertNotIn("T-old1", out)

    def test_branches_merge_record_by_record(self):
        self.run_ok("hooks")
        self.run_ok("add", "tasks", "--id", "T001", "--title", "one")
        self.run_ok("add", "tasks", "--id", "T002", "--title", "two")
        git(self.dir, "add", "-A")
        git(self.dir, "commit", "-qm", "base")
        git(self.dir, "checkout", "-q", "-b", "feat/a")
        self.run_ok("add", "tasks", "--id", "T003", "--title", "three")
        self.run_ok("done", "T001")
        git(self.dir, "commit", "-qam", "a")
        git(self.dir, "checkout", "-q", "main")
        self.run_ok("add", "tasks", "--id", "T004", "--title", "four")
        self.run_ok("start", "T002")
        git(self.dir, "commit", "-qam", "b")
        git(self.dir, "merge", "-q", "-m", "merge", "feat/a")
        tasks = self.group("team", "tasks")
        self.assertEqual({k: v["status"] for k, v in tasks.items()},
                         {"T001": "done", "T002": "doing", "T003": "todo", "T004": "todo"})
        self.assertIn("no problems", self.run_ok("validate"))


if __name__ == "__main__":
    unittest.main()
