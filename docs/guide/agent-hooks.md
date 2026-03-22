# Agent Hooks

Agent hooks keep the navegador graph in sync as AI coding agents work. Without hooks, the graph goes stale the moment an agent edits a file. With hooks, the graph is re-ingested automatically after every file write, and architectural decisions in `DECISIONS.md` are synced into the knowledge layer.

---

## Why hooks

A stale graph gives wrong answers. If an agent adds a new function and then asks `navegador function` about a caller, the graph needs to reflect the edit. Hooks solve this by triggering `navegador ingest` on the modified files immediately after the agent writes them.

Hooks also enforce the habit: every agent session starts with the graph as ground truth, not a stale snapshot.

---

## Claude Code

### Install

The hook file lives at `.claude/hooks/claude-hook.py`. Bootstrap installs it automatically:

```bash
./bootstrap.sh --repo owner/repo --agent claude
```

To install manually, copy `hooks/claude-hook.py` from the navegador repo into your project's `.claude/hooks/` directory.

### settings.json config

In your project's `.claude/settings.json`, register the hook:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/hooks/claude-hook.py"
          }
        ]
      }
    ]
  }
}
```

### What the hook does

On every `Write`, `Edit`, or `MultiEdit` tool call:

1. Reads the list of modified file paths from the tool result
2. Runs `navegador ingest` scoped to those files (fast incremental update)
3. Checks for changes to `DECISIONS.md` — if found, syncs any new ADR entries into the graph as `Decision` nodes
4. Logs a one-line summary to stderr (visible in Claude's tool output)

---

## Gemini CLI

### Install

The hook file lives at `.gemini/hooks/gemini-hook.py`. Bootstrap installs it automatically:

```bash
./bootstrap.sh --repo owner/repo --agent gemini
```

To install manually, copy `hooks/gemini-hook.py` from the navegador repo into your project's `.gemini/hooks/` directory.

### GEMINI.md config

Add to your project's `GEMINI.md`:

```markdown
## Tool Hooks

After writing or editing any source file, run:
```
python .gemini/hooks/gemini-hook.py <file_path>
```

This keeps the navegador knowledge graph in sync. The graph is your source of truth for code structure and project decisions.
```

The Gemini CLI does not have a declarative hook registry like Claude. The `GEMINI.md` instruction tells the model to call the hook script explicitly as a tool after file writes.

---

## OpenAI

OpenAI agents use a dispatcher script and a tool definition JSON file.

### Install

```bash
./bootstrap.sh --repo owner/repo --agent openai
```

This places:
- `openai-hook.py` — dispatcher script
- `openai-tools.json` — tool schema for the OpenAI function-calling API

### openai-tools.json

The tool schema exposes navegador commands as callable functions:

```json
[
  {
    "type": "function",
    "function": {
      "name": "navegador_explain",
      "description": "Look up any code or knowledge node by name",
      "parameters": {
        "type": "object",
        "properties": {
          "name": { "type": "string", "description": "Node name to explain" },
          "file": { "type": "string", "description": "Optional file path to disambiguate" }
        },
        "required": ["name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "navegador_ingest",
      "description": "Re-ingest a file or directory into the knowledge graph",
      "parameters": {
        "type": "object",
        "properties": {
          "path": { "type": "string", "description": "File or directory path to ingest" }
        },
        "required": ["path"]
      }
    }
  }
]
```

### Dispatcher script

`openai-hook.py` receives function call JSON on stdin and dispatches to `navegador` CLI commands:

```python
# openai-hook.py dispatches tool calls to the navegador CLI
# usage: echo '{"name": "navegador_explain", "arguments": {"name": "AuthService"}}' | python openai-hook.py
```

Register `openai-tools.json` in your OpenAI assistant configuration and point function call handling at `openai-hook.py`.

---

## NAVEGADOR.md template

Drop a `NAVEGADOR.md` in your project root so agents know the graph exists and how to use it. Example template:

```markdown
# Navegador Knowledge Graph

This project has a navegador knowledge graph at `.navegador/navegador.db`.

## Before editing code

Run the relevant context command first:

```bash
navegador context <file>           # full file context
navegador function <name>          # function + call graph
navegador class <name>             # class + hierarchy
navegador explain <name>           # anything by name
```

## Before adding new patterns

Check if a concept or rule already exists:

```bash
navegador search "<topic>" --all
navegador domain <domain-name>
```

## After editing code

The agent hook re-ingests automatically. If you disabled hooks, run:

```bash
navegador ingest ./src --clear
```

## Key domains

- **Payments** — payment processing, billing, idempotency rules
- **Auth** — authentication, session management, permissions
- **Infrastructure** — deployment, database, caching decisions
```

---

## Bootstrap reference

```bash
./bootstrap.sh [options]
```

| Option | Description |
|---|---|
| `--repo owner/repo` | GitHub repo to clone and ingest |
| `--wiki` | Also ingest the GitHub wiki |
| `--agent claude` | Install Claude Code hook + settings.json config |
| `--agent gemini` | Install Gemini CLI hook + GEMINI.md instruction |
| `--agent openai` | Install openai-hook.py + openai-tools.json |
| `--db <path>` | Custom database path (default: `.navegador/navegador.db`) |
