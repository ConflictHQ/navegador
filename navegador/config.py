"""
Navegador storage configuration.

Storage is resolved from layered sources, highest priority first:

  1. Explicit argument       — ``--db`` / ``--redis-url`` on the CLI
  2. ``NAVEGADOR_REDIS_URL`` — env var, centralized backend
  3. ``NAVEGADOR_DB``        — env var, embedded backend at that path
  4. Project config          — ``.navegador/config.toml`` ``[storage]``, found by
                               walking up from the *target* path (the repo being
                               operated on), not just the current directory
  5. User config             — ``~/.config/navegador/config.toml`` ``[storage]``,
                               the machine-wide default for every project
  6. Default                 — embedded FalkorDB at ``.navegador/graph.db``

Every resolution carries a :attr:`StorageConfig.source` describing which of those
decided it, so commands can say where their backend came from instead of leaving
the user to guess.

Embedded (falkordblite) — the zero-infrastructure default:
  - DB lives at .navegador/graph.db inside the project
  - The file is a Redis RDB snapshot, NOT a SQLite database — sqlite3 cannot
    open it; the "sqlite" backend name is kept for config/API compatibility
  - Each developer has their own local graph
  - Re-ingest anytime: navegador ingest .

Centralized (Redis/FalkorDB) — multi-repo, multi-agent:
  - All agents read and write one shared in-memory graph
  - No staleness between agents or CI, and no per-agent disk scanning
  - Requires a Redis instance with the FalkorDB module loaded
    (``navegador server install`` sets one up natively)
"""

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_DB_PATH = ".navegador/graph.db"
DEFAULT_REDIS_URL = "redis://localhost:6379"

#: Backend names accepted in config.toml that mean "embedded falkordblite".
#: "sqlite" is the historical spelling and is kept working indefinitely.
_EMBEDDED_ALIASES = frozenset({"sqlite", "embedded", "falkordblite", "local", "file"})
_REDIS_ALIASES = frozenset({"redis", "falkordb", "central", "centralized"})

CONFIG_FILENAME = "config.toml"
NAV_DIRNAME = ".navegador"


class StorageResolutionError(RuntimeError):
    """Raised when a store cannot be opened, carrying the resolved config."""


@dataclass(frozen=True)
class StorageConfig:
    """A fully resolved storage decision, with the provenance that produced it."""

    backend: str  # "redis" | "embedded"
    redis_url: str = ""
    db_path: str = ""
    source: str = ""
    graph_name: str = ""

    @property
    def is_redis(self) -> bool:
        return self.backend == "redis"

    def describe(self) -> str:
        """
        One-line human description of the decision and its provenance.

        Square brackets are deliberately avoided — this string is printed
        through Rich, which would consume them as markup.
        """
        where = self.redis_url if self.is_redis else self.db_path
        graph = f" graph={self.graph_name}" if self.graph_name else ""
        return f"{where}{graph} ({self.backend}, from {self.source})"


# ── Config file discovery ─────────────────────────────────────────────────────


def user_config_path() -> Path:
    """
    Path to the machine-wide user config.

    Honours ``NAVEGADOR_CONFIG`` for an explicit override, otherwise
    ``$XDG_CONFIG_HOME/navegador/config.toml`` falling back to
    ``~/.config/navegador/config.toml``.
    """
    override = os.environ.get("NAVEGADOR_CONFIG", "")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "navegador" / CONFIG_FILENAME


def find_project_config(start: str | Path | None = None) -> Path | None:
    """
    Locate the nearest ``.navegador/config.toml`` at or above *start*.

    Walks upward so that running from a subdirectory of a project — or naming a
    target repo elsewhere on disk — still finds that project's configuration.
    Returns None when no project config exists above *start*.
    """
    try:
        current = Path(start or ".").resolve()
    except OSError:
        return None

    # A file target (e.g. a graph path) resolves against its containing directory.
    if current.is_file():
        current = current.parent

    for directory in (current, *current.parents):
        candidate = directory / NAV_DIRNAME / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def read_config(path: str | Path) -> dict:
    """Parse a TOML config file, returning {} when absent or unreadable."""
    p = Path(path)
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def storage_from_file(path: Path, label: str) -> StorageConfig | None:
    """Build a StorageConfig from a config file's ``[storage]`` table."""
    storage = read_config(path).get("storage")
    if not isinstance(storage, dict):
        return None

    backend = str(storage.get("backend", "")).strip().lower()
    source = f"{label} {path}"
    # On a shared server every project would otherwise read the same default
    # graph; `graph` is how a project addresses its own namespace.
    graph_name = str(storage.get("graph", "")).strip()

    if backend in _REDIS_ALIASES:
        url = str(storage.get("redis_url", "")).strip() or DEFAULT_REDIS_URL
        return StorageConfig(backend="redis", redis_url=url, source=source, graph_name=graph_name)

    if backend in _EMBEDDED_ALIASES:
        db_path = str(storage.get("db_path", "")).strip() or DEFAULT_DB_PATH
        # Relative db_path is relative to the project root (config.toml's
        # grandparent), so it stays correct from any working directory.
        candidate = Path(db_path)
        if not candidate.is_absolute():
            candidate = path.parent.parent / candidate
        return StorageConfig(
            backend="embedded", db_path=str(candidate), source=source, graph_name=graph_name
        )

    # A [storage] table with an unrecognised or missing backend is not a
    # decision — fall through to the next layer rather than guessing.
    return None


@dataclass(frozen=True)
class LLMConfig:
    """A resolved LLM provider selection, with the layer that produced it."""

    provider: str = ""
    model: str = ""
    source: str = "default"

    def describe(self) -> str:
        return f"{self.provider or '(auto)'}/{self.model or '(default)'} (from {self.source})"


def resolve_llm(
    provider: str | None = None,
    model: str | None = None,
    target: str | Path | None = None,
) -> LLMConfig:
    """
    Resolve which LLM provider and model to use, and record why.

    Layered exactly like storage: explicit flags, then environment, then the
    project's ``[llm]`` table, then the user's. `navegador init` has always
    written ``[llm] provider`` and ``model``, and nothing read them (#164).
    """
    if provider or model:
        return LLMConfig(provider=provider or "", model=model or "", source="command line")

    env_provider = os.environ.get("NAVEGADOR_LLM_PROVIDER", "").strip()
    env_model = os.environ.get("NAVEGADOR_LLM_MODEL", "").strip()
    if env_provider or env_model:
        return LLMConfig(provider=env_provider, model=env_model, source="environment")

    for path, label in (
        (find_project_config(target), "project config"),
        (user_config_path(), "user config"),
    ):
        if not path or not Path(path).is_file():
            continue
        section = read_config(path).get("llm")
        if not isinstance(section, dict):
            continue
        name = str(section.get("provider", "")).strip()
        model_id = str(section.get("model", "")).strip()
        if name or model_id:
            return LLMConfig(provider=name, model=model_id, source=f"{label} {path}")

    return LLMConfig()


# ── Resolution ────────────────────────────────────────────────────────────────


def _configured_graph_name(target: str | Path | None) -> str:
    """
    The graph name a config file declares, regardless of which layer wins.

    Read separately from the connection so that overriding the server does not
    silently drop the namespace (#178). Project config first, then the
    machine-wide user config.
    """
    for path in (find_project_config(target), user_config_path()):
        if not path or not Path(path).is_file():
            continue
        storage = read_config(Path(path)).get("storage")
        if isinstance(storage, dict):
            name = str(storage.get("graph", "")).strip()
            if name:
                return name
    return ""


def resolve_storage(
    db_path: str | None = None,
    redis_url: str | None = None,
    target: str | Path | None = None,
    graph_name: str | None = None,
) -> StorageConfig:
    """
    Resolve which store to use, and record why.

    Args:
        db_path:   Explicit embedded-graph path (from ``--db``). Wins outright.
        redis_url: Explicit Redis URL (from ``--redis-url``). Wins outright.
        target:    The path being operated on — a repo root, a subdirectory, or a
                   graph file. Project config discovery walks up from here, so
                   naming a repo elsewhere on disk picks up *that* repo's config
                   rather than the caller's working directory.
        graph_name: Named graph within the store (from ``--graph``). Overrides
                   any configured name; on a shared server this is what keeps
                   each project in its own namespace.
    """
    override = (graph_name or os.environ.get("NAVEGADOR_GRAPH", "")).strip()
    configured = _configured_graph_name(target)

    def _with_graph(config: StorageConfig) -> StorageConfig:
        """
        Apply the namespace decision, which is separate from the connection one.

        ``redis_url`` says *which server*; ``graph`` says *which namespace on
        it*. Overriding the first must not discard the second. It used to:
        setting NAVEGADOR_REDIS_URL returned before project config was ever
        read, so a project with a configured graph landed on the default
        `navegador` graph instead of its own. For an MCP client that is
        invisible — the server starts, every tool answers, and the graph is
        empty (#178).

        Precedence: an explicit --graph or NAVEGADOR_GRAPH wins, then whatever
        the resolving layer itself carried, then the project's configured name.
        """
        chosen = override or config.graph_name or configured
        return replace(config, graph_name=chosen) if chosen else config

    # 1. Explicit arguments
    if redis_url:
        return _with_graph(
            StorageConfig(backend="redis", redis_url=redis_url, source="--redis-url")
        )
    if db_path:
        return _with_graph(StorageConfig(backend="embedded", db_path=db_path, source="--db"))

    # 2. Environment
    env_redis = os.environ.get("NAVEGADOR_REDIS_URL", "").strip()
    if env_redis:
        return _with_graph(
            StorageConfig(
                backend="redis", redis_url=env_redis, source="NAVEGADOR_REDIS_URL env var"
            )
        )
    env_db = os.environ.get("NAVEGADOR_DB", "").strip()
    if env_db:
        return _with_graph(
            StorageConfig(backend="embedded", db_path=env_db, source="NAVEGADOR_DB env var")
        )

    # 3. Project config, discovered from the target being operated on
    project_config = find_project_config(target)
    if project_config:
        resolved = storage_from_file(project_config, "project config")
        if resolved:
            return _with_graph(resolved)

    # 4. User config — the machine-wide default
    user_config = user_config_path()
    if user_config.is_file():
        resolved = storage_from_file(user_config, "user config")
        if resolved:
            return _with_graph(resolved)

    # 5. Default embedded store
    return _with_graph(StorageConfig(backend="embedded", db_path=DEFAULT_DB_PATH, source="default"))


def get_store(
    db_path: str | None = None,
    redis_url: str | None = None,
    target: str | Path | None = None,
    graph_name: str | None = None,
):
    """
    Return a GraphStore for the resolved backend.

    Raises:
        StorageResolutionError: when the resolved backend cannot be opened. The
            message names the backend, where it came from, and how to fix it —
            rather than surfacing a raw redis-py traceback.
    """
    return open_store(resolve_storage(db_path, redis_url, target, graph_name))


def open_store(config: StorageConfig):
    """Open the store described by *config*, converting failures into guidance."""
    from navegador.graph import GraphStore

    if config.is_redis:
        try:
            store = GraphStore.redis(config.redis_url)
            # On a shared server the default graph is common to every project;
            # a configured name is what keeps each in its own namespace.
            return store.with_graph(config.graph_name) if config.graph_name else store
        except ImportError:
            raise
        except Exception as e:
            raise StorageResolutionError(
                f"Cannot reach the FalkorDB server at {config.redis_url} "
                f"(selected by {config.source}).\n"
                f"  Underlying error: {e}\n"
                f"  Check it is running with:  navegador server status\n"
                f"  Start or install one with: navegador server install\n"
                f"  Or use the local graph instead: --db .navegador/graph.db"
            ) from e

    try:
        store = GraphStore.sqlite(config.db_path or DEFAULT_DB_PATH)
        return store.with_graph(config.graph_name) if config.graph_name else store
    except ImportError:
        # Missing dependency — GraphStore.sqlite already explains this well.
        raise
    except Exception as e:
        raise StorageResolutionError(
            f"Could not start the embedded graph at {config.db_path or DEFAULT_DB_PATH} "
            f"(selected by {config.source}).\n"
            f"  Underlying error: {e}\n"
            f"  The embedded backend launches a private FalkorDB process; this "
            f"usually means it could not create its socket or data directory.\n"
            f"  Run 'navegador init' here to create a project config, pass an "
            f"explicit --db path, or point at a shared server with "
            f"--redis-url {DEFAULT_REDIS_URL}."
        ) from e


def default_graph_name(project_dir: str | Path) -> str:
    """
    Conventional graph name for a project on a shared server.

    Matches the ``navegador_<repo>`` namespacing used by federated workspace
    ingest and by graph migration, so a project addresses the same graph however
    it was populated.
    """
    return f"navegador_{Path(project_dir).resolve().name}"


def init_project(
    project_dir: str | Path = ".",
    storage: str = "sqlite",
    redis_url: str = "",
    graph_name: str = "",
    llm_provider: str = "",
    llm_model: str = "",
    cluster: bool = False,
    commit_graph: bool = False,
) -> Path:
    """
    Initialise a .navegador/ directory in the project.

    Creates:
      .navegador/              — DB and config directory
      .navegador/.env.example  — example env file showing config options
      .navegador/config.toml   — project-specific configuration

    Args:
        commit_graph: When True, the graph DB is committed to git — .navegador/
                      is NOT added to .gitignore and a .gitkeep is written so
                      the directory is tracked before the first ingest.
                      When False (default), .navegador/ is gitignored and the
                      graph is treated as a build artifact (rebuild with
                      ``navegador ingest .``).
    """
    project_dir = Path(project_dir).resolve()
    nav_dir = project_dir / NAV_DIRNAME
    nav_dir.mkdir(parents=True, exist_ok=True)

    env_example = nav_dir / ".env.example"
    if not env_example.exists():
        env_example.write_text(
            "# Navegador storage configuration\n"
            "# Uncomment one of the following:\n\n"
            "# Embedded FalkorDB (default — local, zero infrastructure)\n"
            "# The graph file is a Redis RDB snapshot, not SQLite\n"
            "# NAVEGADOR_DB=.navegador/graph.db\n\n"
            "# Redis/FalkorDB (centralized — production, multi-agent)\n"
            f"# NAVEGADOR_REDIS_URL={DEFAULT_REDIS_URL}\n",
            encoding="utf-8",
        )

    # Write config.toml
    config_path = nav_dir / CONFIG_FILENAME
    config_lines = [
        "# Navegador project configuration",
        "# Generated by: navegador init",
        "",
        "[storage]",
        '# "sqlite" = embedded FalkorDB via falkordblite (single local file).',
        "# The graph file is a Redis RDB snapshot — open it with navegador, not sqlite3.",
        '# "redis"  = shared FalkorDB server (navegador server install).',
        f'backend = "{storage}"',
    ]
    if storage == "redis":
        config_lines.append(f'redis_url = "{redis_url or DEFAULT_REDIS_URL}"')
        # Without a name every project on a shared server reads the same default
        # graph. Derive one from the project directory so they stay separate.
        config_lines += [
            "# Named graph on the shared server — keeps this project's graph",
            "# separate from every other project using the same server.",
            f'graph = "{graph_name or default_graph_name(project_dir)}"',
        ]
    else:
        config_lines.append(f'db_path = "{DEFAULT_DB_PATH}"')

    config_lines += [
        "",
        "[llm]",
        f'provider = "{llm_provider}"',
        f'model = "{llm_model}"',
        "",
        "[cluster]",
        f"enabled = {'true' if cluster else 'false'}",
        "# Shard eviction (federated workspaces) — ceilings for resident repo shards:",
        "# max_resident_shards = 4",
        "# max_shard_memory_mb = 512",
    ]
    config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")

    if commit_graph:
        # Track the directory in git but leave graph.db to be created by ingest
        gitkeep = nav_dir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")
    else:
        # Gitignore the whole directory — graph is a build artifact
        gitignore = project_dir / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            if ".navegador/" not in content:
                with gitignore.open("a", encoding="utf-8") as f:
                    f.write("\n# Navegador graph DB\n.navegador/\n")
        else:
            gitignore.write_text("# Navegador graph DB\n.navegador/\n", encoding="utf-8")

    return nav_dir
