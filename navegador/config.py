"""
Navegador storage configuration.

Priority order for store selection:
  1. NAVEGADOR_REDIS_URL env var  → Redis/FalkorDB (centralized, multi-agent)
  2. NAVEGADOR_DB env var         → SQLite at that path
  3. Default                      → SQLite at .navegador/graph.db

Local (SQLite) — default:
  - DB lives at .navegador/graph.db inside the project
  - Zero infrastructure — single file, gitignored
  - Each developer has their own local graph
  - Re-ingest anytime: navegador ingest .

Centralized (Redis/FalkorDB) — production / multi-agent:
  - Set NAVEGADOR_REDIS_URL=redis://host:6379
  - All agents read/write the same shared graph
  - No staleness between agents or CI
  - Requires a Redis instance with the FalkorDB module loaded
"""

import os
from pathlib import Path

DEFAULT_DB_PATH = ".navegador/graph.db"


def get_store(db_path: str | None = None):
    """
    Return a GraphStore using the best available backend.

    Resolution order:
      1. Explicit db_path argument (used when --db flag is passed)
      2. NAVEGADOR_REDIS_URL env var  → Redis backend
      3. NAVEGADOR_DB env var         → SQLite at that path
      4. Default SQLite path
    """
    from navegador.graph import GraphStore

    # 1. Explicit path always means SQLite
    if db_path and db_path != DEFAULT_DB_PATH:
        return GraphStore.sqlite(db_path)

    # 2. Redis URL takes precedence over SQLite
    redis_url = os.environ.get("NAVEGADOR_REDIS_URL", "")
    if redis_url:
        return GraphStore.redis(redis_url)

    # 3. Explicit SQLite path via env
    env_db = os.environ.get("NAVEGADOR_DB", "")
    if env_db:
        return GraphStore.sqlite(env_db)

    # 4. Default SQLite path (or explicit --db default)
    return GraphStore.sqlite(db_path or DEFAULT_DB_PATH)


def init_project(project_dir: str | Path = ".") -> Path:
    """
    Initialise a .navegador/ directory in the project.

    Creates:
      .navegador/           — DB and config directory (should be gitignored)
      .navegador/.env.example — example env file showing config options
    """
    project_dir = Path(project_dir).resolve()
    nav_dir = project_dir / ".navegador"
    nav_dir.mkdir(parents=True, exist_ok=True)

    env_example = nav_dir / ".env.example"
    if not env_example.exists():
        env_example.write_text(
            "# Navegador storage configuration\n"
            "# Uncomment one of the following:\n\n"
            "# SQLite (default — local, zero infrastructure)\n"
            "# NAVEGADOR_DB=.navegador/graph.db\n\n"
            "# Redis/FalkorDB (centralized — production, multi-agent)\n"
            "# NAVEGADOR_REDIS_URL=redis://localhost:6379\n",
            encoding="utf-8",
        )

    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ".navegador/" not in content:
            with gitignore.open("a", encoding="utf-8") as f:
                f.write("\n# Navegador graph DB\n.navegador/\n")
    else:
        gitignore.write_text("# Navegador graph DB\n.navegador/\n", encoding="utf-8")

    return nav_dir
