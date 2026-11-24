#!/bin/sh
# Install the repostate skills into an agent's skills folder.
#
#   ./install.sh                 # Claude Code, for all your projects (~/.claude/skills)
#   ./install.sh --project       # Claude Code, this project only (./.claude/skills)
#   ./install.sh --agents        # agents that read ~/.agents/skills
#   ./install.sh --dir PATH      # any other skills folder
#
# Or without cloning:
#   curl -fsSL https://raw.githubusercontent.com/Milan945/repostate/main/install.sh | sh
set -eu

REPO_URL="https://github.com/Milan945/repostate"
TARGET="$HOME/.claude/skills"

while [ $# -gt 0 ]; do
  case "$1" in
    --project) TARGET="$(pwd)/.claude/skills" ;;
    --agents) TARGET="$HOME/.agents/skills" ;;
    --dir) shift; TARGET="$1" ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

SRC=""
if [ -f "$(dirname "$0")/skills/repostate/SKILL.md" ]; then
  SRC="$(cd "$(dirname "$0")" && pwd)/skills"
else
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  echo "Downloading repostate..."
  git clone --depth 1 --quiet "$REPO_URL" "$TMP/repostate"
  SRC="$TMP/repostate/skills"
fi

mkdir -p "$TARGET"
for skill in repostate repostate-review repostate-architecture repostate-qa; do
  rm -rf "${TARGET:?}/$skill"
  cp -R "$SRC/$skill" "$TARGET/$skill"
  echo "installed $skill -> $TARGET/$skill"
done

echo
echo "Done. In any repo, ask your agent to \"set up repostate\", or run:"
echo "  python3 \"$TARGET/repostate/scripts/repostate.py\" init"
