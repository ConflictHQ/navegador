#!/usr/bin/env python3
"""
Measure how much work an agent spends finding things (#187).

Navegador's thesis is that agents waste effort locating code, not reading it —
so the metric that matters is turns spent orienting, not bytes scanned. This
script freezes a baseline from real agent transcripts so the claim can be
tested after the targeting tools land, rather than asserted.

Usage::

    python scripts/retrieval_telemetry.py                     # human table
    python scripts/retrieval_telemetry.py --json out.json     # machine readable
    python scripts/retrieval_telemetry.py --baseline old.json # compare

Transcripts are Claude Code session logs: one JSON object per line under
``~/.claude/projects/<project>/<session>.jsonl``. Nothing here is specific to
navegador's own repo — point it at any project directory.

Two measurement decisions carry the whole report, and both are deliberate:

**Active time, not wall time.** Sessions are resumed across days, so wall
duration says more about the human's calendar than the agent's behaviour.
Active time sums the gaps between consecutive events that are shorter than
``--idle-threshold``. The median inter-event gap inside active time is a few
seconds, so the default of 120s is far above the working cadence and cuts
only genuine human-away pauses.

**A pipe is not a disk read.** ``Grep``/``Glob`` tool calls are almost never
used in practice — filesystem search goes through ``Bash``. But a large share
of Bash calls are ``git log | grep ...``, which filters another command's
stdout and never touches the filesystem. Counting every command containing
"grep" roughly triples the apparent read rate. Only a search binary in the
*first* pipeline position is counted as a read.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

# Commands that read the filesystem when they lead a pipeline.
SEARCH_COMMANDS = {"grep", "rg", "find", "fd", "cat", "ls", "head", "tail", "awk", "sed"}
# Tools that read the filesystem directly.
READ_TOOLS = {"Read", "Grep", "Glob"}
# Tools that represent productive work rather than orientation.
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

# Navegador's targeting surface, however it is reached: as MCP tools, or as
# CLI commands through Bash. A call counts as targeting when it hands back a
# set of places to look.
TARGETING_TOOLS = {"locate", "scope_for", "neighbourhood", "grep_code"}
TARGETING_CLI = ("navegador locate", "navegador scope", "navegador grep")

# Paths look like a/b/c.py — enough to pull candidates out of a tool result
# without depending on its exact JSON shape, which differs per tool.
PATH_IN_TEXT = re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,6}")


def parse_timestamp(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def leading_command(command: str) -> str | None:
    """
    The binary in the first pipeline position, or None if unparseable.

    ``git log | grep x`` returns "git": the grep filters stdout and never
    touches disk, so counting it as a read overstates retrieval badly.
    """
    first = command.split("|")[0].strip()
    if not first:
        return None
    try:
        tokens = shlex.split(first)
    except ValueError:
        return None  # unbalanced quotes; caller falls back
    for token in tokens:
        if "=" in token and not token.startswith("-"):
            continue  # leading VAR=value assignments
        return Path(token).name
    return None


def bash_paths(command: str) -> list[str]:
    """Non-flag arguments of the leading command, used for scope analysis."""
    first = command.split("|")[0].strip()
    try:
        tokens = shlex.split(first)
    except ValueError:
        return []
    return [t for t in tokens[2:] if not t.startswith("-")]


class ToolCall:
    __slots__ = ("name", "input", "timestamp", "is_read", "is_write", "paths", "uid", "result")

    def __init__(
        self, name: str, tool_input: dict, timestamp: datetime | None, uid: str = ""
    ) -> None:
        self.uid = uid
        self.result = ""
        self.name = name
        self.input = tool_input if isinstance(tool_input, dict) else {}
        self.timestamp = timestamp
        self.is_write = name in WRITE_TOOLS
        self.is_read, self.paths = self._classify()

    def _classify(self) -> tuple[bool, list[str]]:
        if self.name in READ_TOOLS:
            target = self.input.get("file_path") or self.input.get("path") or ""
            return True, [target] if target else []
        if self.name == "Bash":
            command = self.input.get("command", "")
            lead = leading_command(command)
            if lead in SEARCH_COMMANDS:
                # sed -i rewrites a file; it is a write wearing a read's clothes
                if lead == "sed" and "-i" in command.split():
                    return False, []
                return True, bash_paths(command)
        return False, []

    @property
    def is_targeting(self) -> bool:
        if any(t in self.name for t in TARGETING_TOOLS):
            return True
        if self.name == "Bash":
            command = self.input.get("command", "")
            return any(c in command for c in TARGETING_CLI)
        return False

    def offered_paths(self) -> set[str]:
        """Paths this call handed back — the scope the agent was given."""
        return set(PATH_IN_TEXT.findall(self.result or ""))

    def touches(self, path: str) -> bool:
        """
        True when this call refers to *path* as a file, not merely as a place.

        Substring matching is wrong here: ``grep -r foo src/`` would "touch"
        every file under src/, so a broad sweep would score as having found
        the target immediately and the metric would flatter any agent that
        greps widely. A candidate only counts when it looks like a file
        reference and resolves to the same one.
        """
        target = path.strip()
        if not target:
            return False
        for candidate in self.paths:
            if not candidate or not Path(candidate).suffix:
                continue  # a directory or a search pattern, not a file
            if candidate == target or target.endswith(candidate) or candidate.endswith(target):
                return True
        return False


class Session:
    """One transcript, reduced to the calls and the episode boundaries."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.project = path.parent.name
        self.calls: list[ToolCall] = []
        self.timestamps: list[datetime] = []
        # Index into self.calls where each user turn began.
        self.episode_starts: list[int] = []
        self._load()

    def _load(self) -> None:
        results: dict[str, str] = {}
        with self.path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stamp = parse_timestamp(event.get("timestamp", ""))
                if stamp is not None:
                    self.timestamps.append(stamp)
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                if event.get("type") == "user" and message.get("role") == "user":
                    content = message.get("content")
                    # A tool_result also arrives as a user message; only a real
                    # human turn starts a new episode.
                    if isinstance(content, str) or (
                        isinstance(content, list)
                        and not any(
                            isinstance(p, dict) and p.get("type") == "tool_result" for p in content
                        )
                    ):
                        self.episode_starts.append(len(self.calls))
                for part in message.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "tool_use":
                        self.calls.append(
                            ToolCall(
                                part.get("name", "?"),
                                part.get("input", {}),
                                stamp,
                                uid=part.get("id", ""),
                            )
                        )
                    elif isinstance(part, dict) and part.get("type") == "tool_result":
                        # The result is how we learn what a targeting call
                        # actually offered, which is the whole point of
                        # measuring precision rather than turn counts.
                        body = part.get("content")
                        if isinstance(body, list):
                            body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                        results[part.get("tool_use_id", "")] = str(body or "")

        for call in self.calls:
            if call.uid in results:
                call.result = results[call.uid]

    def active_minutes(self, idle_threshold: float) -> float:
        stamps = sorted(self.timestamps)
        total = 0.0
        for earlier, later in zip(stamps, stamps[1:]):
            gap = (later - earlier).total_seconds()
            if 0 <= gap < idle_threshold:
                total += gap
        return total / 60.0

    def episodes(self) -> list[list[ToolCall]]:
        if not self.calls:
            return []
        bounds = sorted(set(self.episode_starts + [0, len(self.calls)]))
        return [self.calls[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]

    # ── Thesis metrics ────────────────────────────────────────────────────

    def orientation_turns(self) -> list[int]:
        """
        Calls made before the first edit in each episode.

        The agent is looking rather than changing anything. If targeting works,
        this is the number that falls.
        """
        out = []
        for episode in self.episodes():
            for index, call in enumerate(episode):
                if call.is_write:
                    out.append(index)
                    break
        return out

    def turns_to_first_target(self) -> list[int]:
        """
        Calls before first touching a file the episode goes on to edit.

        Orientation turns can be gamed by an agent that edits early and badly.
        This asks a sharper question: how long until it laid hands on the file
        that actually mattered? Only files that were edited count, so reading
        for legitimate context is not scored as waste.
        """
        out = []
        for episode in self.episodes():
            edited = {
                call.input.get("file_path", "")
                for call in episode
                if call.is_write and call.input.get("file_path")
            }
            for target in edited:
                for index, call in enumerate(episode):
                    if call.touches(target) or call.input.get("file_path") == target:
                        out.append(index)
                        break
        return out

    def targeting_precision(self) -> list[tuple[int, bool]]:
        """
        For each targeting call: how many places it offered, and whether the
        file the episode went on to edit was among them.

        This is the measurement that needs no control group. Comparing turn
        counts across time periods is confounded by task difficulty and by
        whether the tools were used at all; asking "was the answer in the set
        we handed over" is a direct question about whether targeting works.

        Only episodes that end in an edit are scored — without an edit there
        is no ground truth about which file mattered.
        """
        scored: list[tuple[int, bool]] = []
        for episode in self.episodes():
            edited = {
                call.input.get("file_path", "")
                for call in episode
                if call.is_write and call.input.get("file_path")
            }
            if not edited:
                continue
            for call in episode:
                if not call.is_targeting:
                    continue
                offered = call.offered_paths()
                if not offered:
                    continue
                hit = any(
                    any(o.endswith(target) or target.endswith(o) for o in offered)
                    for target in edited
                )
                scored.append((len(offered), hit))
        return scored

    def reread_ratio(self) -> float | None:
        """Reads divided by distinct files read; 1.0 means nothing re-opened."""
        files = [
            call.input.get("file_path")
            for call in self.calls
            if call.name == "Read" and call.input.get("file_path")
        ]
        if not files:
            return None
        return len(files) / len(set(files))

    def scope_mix(self) -> Counter:
        """How narrowly searches are aimed — the 2026-08 baseline was 94.6% scoped."""
        mix: Counter = Counter()
        for call in self.calls:
            if not call.is_read:
                continue
            if call.name == "Read":
                mix["single file"] += 1
            elif not call.paths or call.paths == ["."]:
                mix["whole tree"] += 1
            elif len(call.paths) > 1:
                mix["file list"] += 1
            elif any(ch in call.paths[0] for ch in "*?["):
                mix["glob"] += 1
            elif Path(call.paths[0]).suffix:
                mix["single file"] += 1
            else:
                mix["subdirectory"] += 1
        return mix


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(q: float) -> float:
        return ordered[min(int(q * len(ordered)), len(ordered) - 1)]

    return {
        "n": len(ordered),
        "mean": round(statistics.fmean(ordered), 2),
        "p25": round(at(0.25), 2),
        "p50": round(statistics.median(ordered), 2),
        "p75": round(at(0.75), 2),
        "p90": round(at(0.90), 2),
    }


def collect(root: Path, min_calls: int, idle: float) -> tuple[list[Session], dict]:
    sessions = []
    for path in sorted(root.glob("*/*.jsonl")):
        if "subagents" in path.parts:
            continue  # no independent session semantics
        try:
            session = Session(path)
        except OSError:
            continue
        if len(session.calls) >= min_calls and session.active_minutes(idle) >= 1:
            sessions.append(session)

    orientation: list[float] = []
    precision: list[tuple[int, bool]] = []
    to_target: list[float] = []
    rereads: list[float] = []
    read_rate: list[float] = []
    calls_per_session: list[float] = []
    tools: Counter = Counter()
    scope: Counter = Counter()

    for session in sessions:
        minutes = session.active_minutes(idle)
        reads = sum(1 for c in session.calls if c.is_read)
        orientation.extend(session.orientation_turns())
        precision.extend(session.targeting_precision())
        to_target.extend(session.turns_to_first_target())
        ratio = session.reread_ratio()
        if ratio is not None:
            rereads.append(ratio)
        if minutes > 0:
            read_rate.append(reads / minutes)
        calls_per_session.append(len(session.calls))
        tools.update(c.name for c in session.calls)
        scope.update(session.scope_mix())

    total_scope = sum(scope.values()) or 1
    hits = sum(1 for _, hit in precision if hit)
    targeting = {
        "calls_scored": len(precision),
        "hit_rate": round(hits / len(precision), 3) if precision else None,
        "median_scope_size": (
            round(statistics.median([n for n, _ in precision]), 1) if precision else None
        ),
    }
    return sessions, {
        "sample": {
            "sessions": len(sessions),
            "projects": len({s.project for s in sessions}),
            "tool_calls": sum(len(s.calls) for s in sessions),
            "idle_threshold_s": idle,
        },
        "orientation_turns": percentiles(orientation),
        "turns_to_first_target": percentiles(to_target),
        "reread_ratio": percentiles(rereads),
        "fs_reads_per_active_minute": percentiles(read_rate),
        "tool_calls_per_session": percentiles(calls_per_session),
        "scope_mix_pct": {k: round(100 * v / total_scope, 1) for k, v in scope.most_common()},
        "targeting": targeting,
        "top_tools": dict(tools.most_common(8)),
    }


def render(report: dict, baseline: dict | None) -> str:
    lines = []
    sample = report["sample"]
    lines.append(
        f"{sample['sessions']} sessions across {sample['projects']} projects, "
        f"{sample['tool_calls']:,} tool calls "
        f"(idle threshold {sample['idle_threshold_s']:.0f}s)"
    )
    lines.append("")
    header = f"{'metric':<32}{'p25':>9}{'p50':>9}{'p75':>9}{'p90':>9}"
    if baseline:
        header += f"{'p50 was':>11}{'delta':>9}"
    lines.append(header)
    lines.append("-" * len(header))

    for key in (
        "turns_to_first_target",
        "orientation_turns",
        "reread_ratio",
        "fs_reads_per_active_minute",
        "tool_calls_per_session",
    ):
        stats = report.get(key)
        if not stats:
            continue
        row = f"{key:<32}{stats['p25']:>9}{stats['p50']:>9}{stats['p75']:>9}{stats['p90']:>9}"
        if baseline and (was := baseline.get(key, {}).get("p50")) is not None:
            delta = stats["p50"] - was
            row += f"{was:>11}{delta:>+9.2f}"
        lines.append(row)

    lines.append("")
    lines.append(
        "search scope:  " + "  ".join(f"{k} {v}%" for k, v in report["scope_mix_pct"].items())
    )

    targeting = report.get("targeting") or {}
    lines.append("")
    if not targeting.get("calls_scored"):
        lines.append(
            "targeting:     no scored calls yet — needs episodes where a targeting tool\n"
            "               was used and the session went on to edit a file"
        )
    else:
        lines.append(
            f"targeting:     hit rate {targeting['hit_rate']:.1%} "
            f"over {targeting['calls_scored']} calls "
            f"(median {targeting['median_scope_size']:.0f} places offered)"
        )
        lines.append(
            "               = how often the file the agent went on to edit was in\n"
            "                 the scope it was handed"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--transcripts",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Directory of <project>/<session>.jsonl transcripts",
    )
    parser.add_argument("--json", type=Path, help="Write the report as JSON")
    parser.add_argument("--baseline", type=Path, help="Earlier JSON report to compare against")
    parser.add_argument("--min-calls", type=int, default=10, help="Ignore trivial sessions")
    parser.add_argument(
        "--idle-threshold",
        type=float,
        default=120.0,
        help="Gaps longer than this are the human being away, not the agent working",
    )
    args = parser.parse_args()

    if not args.transcripts.is_dir():
        parser.error(f"no transcripts at {args.transcripts}")

    _, report = collect(args.transcripts, args.min_calls, args.idle_threshold)
    if not report["sample"]["sessions"]:
        print("No sessions matched.")
        return 1

    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    print(render(report, baseline))

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
