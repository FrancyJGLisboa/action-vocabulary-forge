#!/usr/bin/env bash
# Install the decision-system-forge skill into every agent CLI found on this
# machine. Claude Code, Codex CLI, Copilot CLI and Gemini CLI all use the same
# layout: <config>/skills/<name>/SKILL.md. ~/.agents/skills is the shared
# canonical location this user already keeps.
#
# Default is a symlink so the clone stays the single point of truth.
#   ./install.sh            symlink (recommended)
#   ./install.sh --copy     copy instead
#   ./install.sh --uninstall
set -euo pipefail

NAME="decision-system-forge"
LEGACY_NAME="action-vocabulary-forge"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---link}"

TARGETS=(
  "$HOME/.agents/skills:agents"
  "$HOME/.claude/skills:Claude Code"
  "$HOME/.codex/skills:Codex CLI"
  "$HOME/.copilot/skills:Copilot CLI"
  "$HOME/.gemini/skills:Gemini CLI"
)

for entry in "${TARGETS[@]}"; do
  dir="${entry%%:*}"
  label="${entry#*:}"
  parent="$(dirname "$dir")"

  if [ ! -d "$parent" ]; then
    printf '%-14s skipped (%s not present)\n' "$label" "$parent"
    continue
  fi

  mkdir -p "$dir"
  dest="$dir/$NAME"
  legacy_dest="$dir/$LEGACY_NAME"

  # Remove only the legacy symlink installed by this project. Never delete a
  # user-maintained directory that happens to use the old name.
  if [ -L "$legacy_dest" ]; then
    rm "$legacy_dest"
  fi

  case "$MODE" in
    --uninstall)
      rm -rf "$dest"
      printf '%-14s removed\n' "$label"
      ;;
    --copy)
      rm -rf "$dest"
      cp -R "$SRC" "$dest"
      printf '%-14s copied  -> %s\n' "$label" "$dest"
      ;;
    *)
      rm -rf "$dest"
      ln -s "$SRC" "$dest"
      printf '%-14s linked  -> %s\n' "$label" "$dest"
      ;;
  esac
done
