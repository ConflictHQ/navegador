"""
Native FalkorDB server lifecycle — install, run, and inspect a shared graph
server without Docker.

A centralized server is what makes navegador worth using across many repos: one
resident in-memory graph that every project and every agent queries, instead of
each agent re-reading the disk in each checkout.

Layout under ``~/.navegador`` (override with ``NAVEGADOR_HOME``)::

    lib/falkordb.so     the FalkorDB Redis module for this platform
    redis.conf          generated server configuration
    data/               dump.rdb + appendonlydir  (the graphs)
    logs/falkordb.log   server log
    falkordb.pid        pid file
    install.json        what was installed, and from where

The server is a stock ``redis-server`` with the FalkorDB module loaded; nothing
is compiled locally. Modules are the official prebuilt releases from
https://github.com/FalkorDB/FalkorDB/releases.
"""

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: Module release pinned by this navegador version. Overridable per install.
DEFAULT_FALKORDB_VERSION = "4.20.3"
RELEASE_URL = "https://github.com/FalkorDB/FalkorDB/releases/download/v{version}/{asset}"

SERVICE_LABEL = "dev.navegador.falkordb"
DEFAULT_PORT = 6379


class ServerError(RuntimeError):
    """Raised when a server operation cannot be completed."""


# ── Platform ──────────────────────────────────────────────────────────────────


def module_asset_name(system: str | None = None, machine: str | None = None) -> str:
    """
    Name of the prebuilt FalkorDB module asset for a platform.

    Raises ServerError on platforms FalkorDB publishes no module for — notably
    native Windows, which has no build at all (use WSL2).
    """
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arm = machine in ("arm64", "aarch64")

    if system == "darwin":
        if not arm:
            raise ServerError(
                "FalkorDB publishes no macOS x86_64 module. Use an arm64 (Apple "
                "Silicon) machine, or run a Linux server and point "
                "NAVEGADOR_REDIS_URL at it."
            )
        return "falkordb-macos-arm64v8.so"

    if system == "linux":
        musl = _is_musl()
        if arm:
            return "falkordb-alpine-arm64v8.so" if musl else "falkordb-arm64v8.so"
        return "falkordb-alpine-x64.so" if musl else "falkordb-x64.so"

    raise ServerError(
        f"No prebuilt FalkorDB module for {system}/{machine}. "
        "On Windows, run navegador under WSL2 and install the server there."
    )


def _is_musl() -> bool:
    """True on musl-based Linux (Alpine), which needs the alpine module build."""
    try:
        out = subprocess.run(["ldd", "--version"], capture_output=True, text=True, timeout=5)
        return "musl" in (out.stdout + out.stderr).lower()
    except (OSError, subprocess.SubprocessError):
        return False


def find_redis_server() -> str:
    """
    Locate a ``redis-server`` binary to host the module.

    Prefers one on PATH; falls back to the copy bundled with redislite, which is
    always present when the embedded backend is installed.
    """
    found = shutil.which("redis-server")
    if found:
        return found

    try:
        import redislite  # type: ignore[import]

        bundled = Path(redislite.__file__).parent / "bin" / "redis-server"
        if bundled.is_file():
            return str(bundled)
    except ImportError:
        pass

    raise ServerError(
        "No redis-server binary found. Install Redis first:\n"
        "  macOS:  brew install redis\n"
        "  Debian: sudo apt install redis-server\n"
        "The FalkorDB module is loaded into a stock redis-server; navegador "
        "does not build one."
    )


# ── Paths ─────────────────────────────────────────────────────────────────────


def server_home() -> Path:
    """Root directory for the managed server (``NAVEGADOR_HOME`` or ~/.navegador)."""
    override = os.environ.get("NAVEGADOR_HOME", "")
    return Path(override).expanduser() if override else Path.home() / ".navegador"


@dataclass
class ServerPaths:
    home: Path
    lib: Path = field(init=False)
    module: Path = field(init=False)
    data: Path = field(init=False)
    logs: Path = field(init=False)
    config: Path = field(init=False)
    pidfile: Path = field(init=False)
    socket: Path = field(init=False)
    manifest: Path = field(init=False)

    def __post_init__(self) -> None:
        self.lib = self.home / "lib"
        self.module = self.lib / "falkordb.so"
        self.data = self.home / "data"
        self.logs = self.home / "logs"
        self.config = self.home / "redis.conf"
        self.pidfile = self.home / "falkordb.pid"
        self.socket = self.home / "falkordb.sock"
        self.manifest = self.home / "install.json"

    def mkdirs(self) -> None:
        for d in (self.home, self.lib, self.data, self.logs):
            d.mkdir(parents=True, exist_ok=True)


def paths() -> ServerPaths:
    return ServerPaths(server_home())


# ── Module download ───────────────────────────────────────────────────────────


def download_module(
    dest: Path, version: str = DEFAULT_FALKORDB_VERSION, expected_sha256: str = ""
) -> str:
    """
    Download the FalkorDB module for this platform to *dest*.

    Returns the sha256 of the downloaded file. When *expected_sha256* is given
    and does not match, the download is discarded and ServerError raised.

    The file is made executable — Redis refuses to load a module without the
    execute bit, reporting only "does not have execute permissions".
    """
    asset = module_asset_name()
    url = RELEASE_URL.format(version=version, asset=asset)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".download")

    try:
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
            digest = hashlib.sha256()
            with tmp.open("wb") as fh:
                while chunk := response.read(1 << 20):
                    fh.write(chunk)
                    digest.update(chunk)
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise ServerError(
            f"Could not download {asset} for FalkorDB v{version} (HTTP {e.code}).\n"
            f"  {url}\n"
            "Check the version exists, or pass --version with a published release."
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise ServerError(f"Could not download {asset}: {e}") from e

    sha = digest.hexdigest()
    if expected_sha256 and sha != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise ServerError(
            f"Checksum mismatch for {asset}:\n  expected {expected_sha256}\n  got      {sha}"
        )

    tmp.replace(dest)
    dest.chmod(0o755)
    return sha


# ── Configuration ─────────────────────────────────────────────────────────────


def render_config(
    p: ServerPaths,
    port: int = DEFAULT_PORT,
    bind: str = "127.0.0.1",
    maxmemory_mb: int = 0,
    appendonly: bool = True,
) -> str:
    """
    Render a redis.conf tuned for AST/knowledge graph workloads.

    Notes on the choices:

    - ``maxmemory-policy noeviction`` is not negotiable for a graph server.
      Under any eviction policy Redis would silently drop keys under memory
      pressure, which for a graph means losing part of it rather than shedding
      cache. Failing writes loudly is the correct behaviour.
    - ``maxmemory 0`` (unlimited) by default: FalkorDB holds graph data in
      module-owned memory that a maxmemory cap does not govern anyway, so a cap
      gives false reassurance. Set one only to bound the Redis keyspace itself.
    - Both AOF and RDB are on. Graphs are expensive to rebuild — a re-ingest of
      a large workspace is minutes of CPU — so durability is worth the disk.
    - A unix socket is exposed alongside TCP; local clients that use it skip the
      TCP stack entirely, which matters when many agents query concurrently.
    """
    lines = [
        "# Generated by: navegador server install",
        "# Edit freely — 'navegador server install --force' will overwrite it.",
        "",
        f"port {port}",
        f"bind {bind}",
        "protected-mode yes",
        "",
        "# Local clients should prefer this socket over TCP.",
        f"unixsocket {p.socket}",
        "unixsocketperm 700",
        "",
        f"dir {p.data}",
        "dbfilename dump.rdb",
        f"logfile {p.logs / 'falkordb.log'}",
        f"pidfile {p.pidfile}",
        "daemonize no",
        "",
        "# Durability: graphs cost minutes of CPU to rebuild.",
        f"appendonly {'yes' if appendonly else 'no'}",
        "appendfsync everysec",
        "save 900 1 300 10 60 10000",
        "",
        "# Never evict: eviction would corrupt a graph, not shed a cache.",
        f"maxmemory {maxmemory_mb}mb" if maxmemory_mb else "maxmemory 0",
        "maxmemory-policy noeviction",
        "",
        f"loadmodule {p.module}",
        "",
    ]
    return "\n".join(lines)


def write_config(p: ServerPaths, **kwargs) -> Path:
    p.mkdirs()
    p.config.write_text(render_config(p, **kwargs), encoding="utf-8")
    return p.config


# ── Service integration ───────────────────────────────────────────────────────


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "navegador-falkordb.service"


def render_launch_agent(redis_server: str, p: ServerPaths) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{SERVICE_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{redis_server}</string>
        <string>{p.config}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{p.home}</string>
    <key>StandardOutPath</key>
    <string>{p.logs / "launchd.out.log"}</string>
    <key>StandardErrorPath</key>
    <string>{p.logs / "launchd.err.log"}</string>
</dict>
</plist>
"""


def render_systemd_unit(redis_server: str, p: ServerPaths) -> str:
    return f"""[Unit]
Description=Navegador FalkorDB graph server
After=network.target

[Service]
Type=simple
ExecStart={redis_server} {p.config}
WorkingDirectory={p.home}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def install_service(redis_server: str, p: ServerPaths) -> Path:
    """Install a start-at-login service definition for the current platform."""
    if sys.platform == "darwin":
        target = launch_agent_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_launch_agent(redis_server, p), encoding="utf-8")
        return target

    target = systemd_unit_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_systemd_unit(redis_server, p), encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    return target


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ServerError(
            f"{' '.join(cmd)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def start_service() -> str:
    """Start the server via the platform service manager."""
    if sys.platform == "darwin":
        plist = launch_agent_path()
        if not plist.is_file():
            raise ServerError("Service not installed. Run: navegador server install")
        uid = os.getuid()
        # bootstrap is the modern spelling; load -w still works on older macOS.
        result = _run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], check=False)
        if result.returncode != 0:
            if "already bootstrapped" in (result.stderr + result.stdout).lower():
                return "already running"
            _run(["launchctl", "load", "-w", str(plist)])
        return "started"

    _run(["systemctl", "--user", "enable", "--now", "navegador-falkordb.service"])
    return "started"


def stop_service() -> str:
    if sys.platform == "darwin":
        plist = launch_agent_path()
        uid = os.getuid()
        result = _run(["launchctl", "bootout", f"gui/{uid}/{SERVICE_LABEL}"], check=False)
        if result.returncode != 0 and plist.is_file():
            _run(["launchctl", "unload", "-w", str(plist)], check=False)
        return "stopped"

    _run(["systemctl", "--user", "disable", "--now", "navegador-falkordb.service"], check=False)
    return "stopped"


# ── Status ────────────────────────────────────────────────────────────────────


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def probe(url: str = "redis://localhost:6379") -> dict:
    """
    Inspect a running FalkorDB server.

    Returns a dict with ``reachable``, and when reachable: server/module
    versions, memory, graph names, and whether the graph module is present at
    all — a plain Redis answers PING happily and then fails every query.
    """
    info: dict = {"url": url, "reachable": False, "graph_module": False}
    try:
        import redis as redis_lib
    except ImportError:
        info["error"] = "redis client not installed (pip install redis)"
        return info

    try:
        client = redis_lib.Redis.from_url(url, socket_connect_timeout=2)
        server_info = client.info("server")
        info["reachable"] = True
        info["redis_version"] = server_info.get("redis_version", "")
        info["executable"] = server_info.get("executable", "")
        info["uptime_days"] = server_info.get("uptime_in_days")

        modules = client.module_list()
        for module in modules:
            name = module.get(b"name") or module.get("name")
            name = name.decode() if isinstance(name, bytes) else name
            if name == "graph":
                ver = module.get(b"ver") or module.get("ver")
                info["graph_module"] = True
                info["module_version"] = _decode_module_version(ver)

        mem = client.info("memory")
        info["used_memory_human"] = mem.get("used_memory_human", "")

        if info["graph_module"]:
            graphs = client.execute_command("GRAPH.LIST")
            info["graphs"] = sorted(g.decode() if isinstance(g, bytes) else str(g) for g in graphs)
    except Exception as e:  # noqa: BLE001 — any client error means "not usable"
        info["error"] = str(e)

    return info


def wait_until_ready(url: str, timeout: float = 15.0, interval: float = 0.3) -> dict:
    """
    Poll *url* until the graph module answers, or *timeout* elapses.

    The service manager returns as soon as the process is spawned, but FalkorDB
    takes a moment to initialise its thread pool and register the module. Without
    this, a freshly installed server reports itself unusable for a second or two.
    """
    deadline = time.monotonic() + timeout
    info: dict = {}
    while True:
        info = probe(url)
        if info.get("graph_module") or time.monotonic() >= deadline:
            return info
        time.sleep(interval)


def _decode_module_version(ver) -> str:
    """FalkorDB encodes 4.20.3 as the integer 42003."""
    try:
        n = int(ver)
    except (TypeError, ValueError):
        return str(ver)
    return f"{n // 10000}.{(n // 100) % 100}.{n % 100}"


def read_manifest(p: ServerPaths) -> dict:
    try:
        return json.loads(p.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_manifest(p: ServerPaths, data: dict) -> None:
    p.manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def install(
    version: str = DEFAULT_FALKORDB_VERSION,
    port: int = DEFAULT_PORT,
    bind: str = "127.0.0.1",
    maxmemory_mb: int = 0,
    force: bool = False,
    expected_sha256: str = "",
) -> dict:
    """
    Install the module, configuration, and service definition.

    Does not start the server — the caller decides, so that a port conflict can
    be reported before anything is launched.
    """
    p = paths()
    p.mkdirs()
    redis_server = find_redis_server()

    if p.module.is_file() and not force:
        sha = hashlib.sha256(p.module.read_bytes()).hexdigest()
        existing = read_manifest(p)
        installed_version = existing.get("falkordb_version", "unknown")
    else:
        sha = download_module(p.module, version=version, expected_sha256=expected_sha256)
        installed_version = version

    write_config(p, port=port, bind=bind, maxmemory_mb=maxmemory_mb)
    service = install_service(redis_server, p)

    manifest = {
        "falkordb_version": installed_version,
        "module_sha256": sha,
        "module_asset": module_asset_name(),
        "redis_server": redis_server,
        "port": port,
        "bind": bind,
        "config": str(p.config),
        "service": str(service),
        "data_dir": str(p.data),
    }
    write_manifest(p, manifest)
    return manifest


def uninstall(remove_data: bool = False) -> dict:
    """Stop the service and remove its definition. Data is kept unless asked."""
    p = paths()
    stop_service()

    removed = []
    for target in (launch_agent_path(), systemd_unit_path()):
        if target.is_file():
            target.unlink()
            removed.append(str(target))

    if remove_data and p.data.exists():
        shutil.rmtree(p.data)
        removed.append(str(p.data))

    return {"removed": removed, "data_removed": remove_data, "home": str(p.home)}
