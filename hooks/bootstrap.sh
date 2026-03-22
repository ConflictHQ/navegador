#!/usr/bin/env bash
# navegador bootstrap — install, initialise, and ingest a project
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ConflictHQ/navegador/main/hooks/bootstrap.sh | bash
#   # or locally:
#   bash hooks/bootstrap.sh [--repo owner/repo] [--wiki] [--agent claude|gemini|openai]

set -euo pipefail

NAV_DB="${NAVEGADOR_DB:-.navegador/graph.db}"
REPO_PATH="${REPO_PATH:-.}"
GITHUB_REPO="${GITHUB_REPO:-}"
INSTALL_AGENT="${INSTALL_AGENT:-}"
INGEST_WIKI=false

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --repo)   GITHUB_REPO="$2"; shift 2 ;;
    --wiki)   INGEST_WIKI=true; shift ;;
    --agent)  INSTALL_AGENT="$2"; shift 2 ;;
    --db)     NAV_DB="$2"; shift 2 ;;
    *)        echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Navegador bootstrap"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Install ───────────────────────────────────────────────────────────────────
if ! command -v navegador &>/dev/null; then
  echo "→ Installing navegador..."
  pip install "navegador[sqlite]" --quiet
else
  echo "→ navegador $(navegador --version 2>&1 | head -1) already installed"
fi

# ── Initialise DB directory ───────────────────────────────────────────────────
mkdir -p "$(dirname "$NAV_DB")"
echo "→ Graph DB: $NAV_DB"

# ── Ingest code ───────────────────────────────────────────────────────────────
echo "→ Ingesting code from $REPO_PATH ..."
navegador --db "$NAV_DB" ingest "$REPO_PATH" --json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  files={d['files']} functions={d['functions']} classes={d['classes']} edges={d['edges']}\")"

# ── Ingest wiki ───────────────────────────────────────────────────────────────
if [[ "$INGEST_WIKI" == "true" && -n "$GITHUB_REPO" ]]; then
  echo "→ Ingesting GitHub wiki for $GITHUB_REPO ..."
  navegador --db "$NAV_DB" wiki ingest --repo "$GITHUB_REPO" ${GITHUB_TOKEN:+--token "$GITHUB_TOKEN"} || true
fi

# ── Install agent hook ────────────────────────────────────────────────────────
HOOK_SRC_BASE="https://raw.githubusercontent.com/ConflictHQ/navegador/main/hooks"

install_claude_hook() {
  mkdir -p .claude/hooks
  curl -fsSL "$HOOK_SRC_BASE/claude-hook.py" -o .claude/hooks/navegador.py
  chmod +x .claude/hooks/navegador.py

  SETTINGS=".claude/settings.json"
  if [[ ! -f "$SETTINGS" ]]; then
    cat > "$SETTINGS" <<'JSON'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "python3 .claude/hooks/navegador.py" }]
      }
    ]
  }
}
JSON
    echo "  Created $SETTINGS"
  else
    echo "  $SETTINGS exists — add the hook manually (see .claude/hooks/navegador.py)"
  fi
}

install_gemini_hook() {
  mkdir -p .gemini/hooks
  curl -fsSL "$HOOK_SRC_BASE/gemini-hook.py" -o .gemini/hooks/navegador.py
  chmod +x .gemini/hooks/navegador.py
  echo "  Add to GEMINI.md: python3 .gemini/hooks/navegador.py <tool> <file>"
}

install_openai_hook() {
  curl -fsSL "$HOOK_SRC_BASE/openai-hook.py" -o navegador-openai.py
  chmod +x navegador-openai.py
  echo "  Register tool schemas from hooks/openai-tools.json with your assistant"
}

case "$INSTALL_AGENT" in
  claude) echo "→ Installing Claude Code hook..."; install_claude_hook ;;
  gemini) echo "→ Installing Gemini CLI hook...";  install_gemini_hook ;;
  openai) echo "→ Installing OpenAI hook...";      install_openai_hook ;;
  "")     ;;
  *)      echo "Unknown agent: $INSTALL_AGENT (use claude|gemini|openai)" ;;
esac

# ── Stats ─────────────────────────────────────────────────────────────────────
echo ""
echo "→ Graph stats:"
navegador --db "$NAV_DB" stats 2>/dev/null || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Done. Quick start:"
echo "   navegador search \"your query\""
echo "   navegador explain MyClass"
echo "   navegador stats"
echo "   navegador add concept \"Payment\" --domain billing"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
