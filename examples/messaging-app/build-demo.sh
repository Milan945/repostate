#!/bin/sh
# Builds a complete example .repostate from the markdown in this folder.
#   sh examples/messaging-app/build-demo.sh /tmp/demo
# The result is a small git repo with .repostate/ ready to open in a browser.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
CLI="$HERE/../../skills/repostate/scripts/repostate.py"
OUT="${1:?usage: build-demo.sh OUTPUT_DIR}"
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python

rm -rf "$OUT"; mkdir -p "$OUT"
cp -R "$HERE/hub.md" "$HERE/progress.md" "$HERE/screenshots" "$OUT/"
cd "$OUT"
git init -q -b main
git config user.email "demo@example.com"; git config user.name "Demo"
git remote add origin https://github.com/example/messaging-app.git
git add -A; git commit -qm "example project"

"$PY" "$CLI" init --name "Example: messaging app rebuild" >/dev/null
R="$PY .repostate/repostate.py"
"$PY" - <<'PY'
import json
p = ".repostate/config.json"; c = json.load(open(p))
c["slices"] = {"A": "Conversation list", "B": "Threads", "C": "Attachments", "D1": "Profile view",
               "D2": "Profile edit", "E1": "Notifications"}
json.dump(c, open(p, "w"), indent=2)
PY
$R import hub.md progress.md >/dev/null
$R update T002 --body "Shows the user's conversations, newest first, with avatar, name, last message and time. Names longer than the row truncate with an ellipsis." \
  --add figma=https://www.figma.com/design/EXAMPLE/Messaging?node-id=12-4 --add repos=example/messaging-app,example/messaging-api >/dev/null
$R update T003 --add figma=https://www.figma.com/design/EXAMPLE/Messaging?node-id=14-2 >/dev/null
$R update T004 --assignee priya --add repos=example/messaging-api >/dev/null
$R hours T004 1.5 --note "pairing on cursor pagination" >/dev/null
$R log "Thread API returns the first page" --tasks T004 --hours 1.5 --details "Cursor pagination; 404 for threads the user is not in still to do." >/dev/null
$R add bugs --id BUG-003 --title "Thread API returns 500 on an empty conversation" --task T004 --severity critical --set testcase=TC-008 >/dev/null
$R update TC-008 --set bug=BUG-003 --set status_note="Blocked by BUG-003" >/dev/null
$R update T005 --waiting-on "Design (Anita)" >/dev/null
$R add goals --id G1 --title "Slice A ready for beta users" --set target_date=2026-11-28 --body "Conversation list, refresh and unread badge, with every UI test passing." >/dev/null
$R add goals --id G2 --title "Messaging MVP for the client demo" --set target_date=2026-12-15 --body "Slices A to C: list, threads, sending and attachments." >/dev/null
for t in T001 T002 T003; do $R update $t --goal G1 >/dev/null; done
for t in T004 T005 T006 T007 T008; do $R update $t --goal G2 >/dev/null; done
$R validate >/dev/null
$R build --quiet
echo "built $OUT/.repostate (open $OUT/.repostate/index.html)"
