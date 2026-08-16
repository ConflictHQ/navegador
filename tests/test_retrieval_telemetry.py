"""
Tests for the retrieval telemetry tool (#187).

The tool decides whether weeks of work on #184 and #188 are justified, so its
classification rules need to be right. Two are load-bearing and easy to get
wrong, and both are asserted here:

- a search binary downstream of a pipe filters stdout and is not a disk read
- long human-away pauses must not count as agent working time

Transcripts are synthesised rather than read from disk so the assertions are
about the logic, not about whoever's machine runs the suite.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from retrieval_telemetry import (  # noqa: E402
    Session,
    collect,
    leading_command,
    percentiles,
)

START = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


def tool_use(name, tool_input, offset_s):
    return {
        "type": "assistant",
        "timestamp": (START + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z"),
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": tool_input}],
        },
    }


def user_turn(text, offset_s):
    return {
        "type": "user",
        "timestamp": (START + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z"),
        "message": {"role": "user", "content": text},
    }


def tool_result(offset_s):
    """A tool result is also a user message — it must not start an episode."""
    return {
        "type": "user",
        "timestamp": (START + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z"),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}],
        },
    }


def write_session(directory, events, name="session.jsonl"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("\n".join(json.dumps(e) for e in events))
    return path


class TestPipelineClassification:
    """The rule that roughly triples the read rate when it is wrong."""

    @pytest.mark.parametrize(
        "command,expected",
        [
            ("grep -r foo .", "grep"),
            ("rg --files", "rg"),
            ("git log | grep foo", "git"),
            ("gh pr view 1 --json body | jq .body | head -20", "gh"),
            ("cat file.txt", "cat"),
            ("FOO=bar grep x .", "grep"),
            ("/usr/bin/find . -name x", "find"),
        ],
    )
    def test_leading_command(self, command, expected):
        assert leading_command(command) == expected

    def test_grep_after_pipe_is_not_a_read(self, tmp_path):
        events = [
            user_turn("go", 0),
            tool_use("Bash", {"command": "git log --oneline | grep fix"}, 1),
        ]
        session = Session(write_session(tmp_path / "p", events))
        assert [c.is_read for c in session.calls] == [False]

    def test_grep_leading_is_a_read(self, tmp_path):
        events = [user_turn("go", 0), tool_use("Bash", {"command": "grep -rn foo src/"}, 1)]
        session = Session(write_session(tmp_path / "p", events))
        assert [c.is_read for c in session.calls] == [True]

    def test_sed_in_place_is_not_a_read(self, tmp_path):
        events = [user_turn("go", 0), tool_use("Bash", {"command": "sed -i s/a/b/ f.py"}, 1)]
        session = Session(write_session(tmp_path / "p", events))
        assert [c.is_read for c in session.calls] == [False]

    def test_unparseable_command_does_not_crash(self, tmp_path):
        events = [user_turn("go", 0), tool_use("Bash", {"command": "grep 'unbalanced ."}, 1)]
        session = Session(write_session(tmp_path / "p", events))
        assert len(session.calls) == 1


class TestActiveTime:
    def test_idle_gap_is_excluded(self, tmp_path):
        events = [
            user_turn("go", 0),
            tool_use("Read", {"file_path": "a.py"}, 10),
            tool_use("Read", {"file_path": "b.py"}, 20),
            tool_use("Read", {"file_path": "c.py"}, 4000),  # an hour away
        ]
        session = Session(write_session(tmp_path / "p", events))
        minutes = session.active_minutes(120.0)
        assert minutes == pytest.approx(20 / 60, abs=0.01), (
            "the hour-long pause was counted as working time"
        )

    def test_wall_time_would_have_been_much_larger(self, tmp_path):
        events = [user_turn("go", 0), tool_use("Read", {"file_path": "a.py"}, 7200)]
        session = Session(write_session(tmp_path / "p", events))
        assert session.active_minutes(120.0) == 0


class TestEpisodes:
    def test_tool_result_does_not_start_an_episode(self, tmp_path):
        events = [
            user_turn("do the thing", 0),
            tool_use("Read", {"file_path": "a.py"}, 1),
            tool_result(2),
            tool_use("Edit", {"file_path": "a.py"}, 3),
        ]
        session = Session(write_session(tmp_path / "p", events))
        assert len(session.episodes()) == 1

    def test_second_user_turn_starts_a_new_episode(self, tmp_path):
        events = [
            user_turn("first", 0),
            tool_use("Read", {"file_path": "a.py"}, 1),
            user_turn("second", 2),
            tool_use("Read", {"file_path": "b.py"}, 3),
        ]
        session = Session(write_session(tmp_path / "p", events))
        assert len(session.episodes()) == 2


class TestThesisMetrics:
    def test_turns_to_first_target_counts_calls_before_touching_it(self, tmp_path):
        """Three misses, then the file that gets edited — index 3."""
        events = [
            user_turn("fix the bug", 0),
            tool_use("Bash", {"command": "grep -r wrong src/"}, 1),
            tool_use("Read", {"file_path": "src/decoy_a.py"}, 2),
            tool_use("Read", {"file_path": "src/decoy_b.py"}, 3),
            tool_use("Read", {"file_path": "src/real.py"}, 4),
            tool_use("Edit", {"file_path": "src/real.py"}, 5),
        ]
        session = Session(write_session(tmp_path / "p", events))
        assert session.turns_to_first_target() == [3]

    def test_files_read_but_never_edited_are_not_scored(self, tmp_path):
        """
        Reading for context is legitimate. Only files the episode actually
        edits count, or the metric would punish an agent for understanding.
        """
        events = [
            user_turn("go", 0),
            tool_use("Read", {"file_path": "context.py"}, 1),
            tool_use("Read", {"file_path": "target.py"}, 2),
            tool_use("Edit", {"file_path": "target.py"}, 3),
        ]
        session = Session(write_session(tmp_path / "p", events))
        assert session.turns_to_first_target() == [1]

    def test_orientation_turns_stop_at_first_edit(self, tmp_path):
        events = [
            user_turn("go", 0),
            tool_use("Read", {"file_path": "a.py"}, 1),
            tool_use("Read", {"file_path": "b.py"}, 2),
            tool_use("Edit", {"file_path": "b.py"}, 3),
            tool_use("Read", {"file_path": "c.py"}, 4),
        ]
        session = Session(write_session(tmp_path / "p", events))
        assert session.orientation_turns() == [2]

    def test_episode_with_no_edit_is_not_scored(self, tmp_path):
        """A question-answering turn has no target and should not dilute the median."""
        events = [user_turn("what does this do", 0), tool_use("Read", {"file_path": "a.py"}, 1)]
        session = Session(write_session(tmp_path / "p", events))
        assert session.orientation_turns() == []
        assert session.turns_to_first_target() == []

    def test_reread_ratio(self, tmp_path):
        events = [
            user_turn("go", 0),
            tool_use("Read", {"file_path": "a.py"}, 1),
            tool_use("Read", {"file_path": "b.py"}, 2),
            tool_use("Read", {"file_path": "a.py"}, 3),
        ]
        session = Session(write_session(tmp_path / "p", events))
        assert session.reread_ratio() == pytest.approx(1.5)

    def test_reread_ratio_is_none_without_reads(self, tmp_path):
        events = [user_turn("go", 0), tool_use("Bash", {"command": "ls"}, 1)]
        session = Session(write_session(tmp_path / "p", events))
        assert session.reread_ratio() is None


def busy_session(count=12, spacing=10):
    """A session long enough to clear the one-minute active-time floor."""
    return [user_turn("go", 0)] + [
        tool_use("Read", {"file_path": f"f{i}.py"}, (i + 1) * spacing) for i in range(count)
    ]


class TestCollect:
    def test_a_real_session_is_counted(self, tmp_path):
        write_session(tmp_path / "proj", busy_session())
        _, report = collect(tmp_path, min_calls=10, idle=120.0)
        assert report["sample"]["sessions"] == 1

    def test_subagent_transcripts_are_excluded(self, tmp_path):
        """Subagent runs have no independent session semantics."""
        write_session(tmp_path / "proj", busy_session())
        write_session(tmp_path / "subagents", busy_session(), name="sub.jsonl")

        _, report = collect(tmp_path, min_calls=10, idle=120.0)
        assert report["sample"]["sessions"] == 1

    def test_trivial_sessions_are_ignored(self, tmp_path):
        write_session(tmp_path / "proj", [user_turn("go", 0), tool_use("Read", {}, 1)])
        _, report = collect(tmp_path, min_calls=10, idle=120.0)
        assert report["sample"]["sessions"] == 0

    def test_session_below_the_active_time_floor_is_ignored(self, tmp_path):
        """
        Twelve calls in twelve seconds is a burst, not a working session, and
        would produce a wild per-minute rate.
        """
        write_session(tmp_path / "proj", busy_session(spacing=1))
        _, report = collect(tmp_path, min_calls=10, idle=120.0)
        assert report["sample"]["sessions"] == 0


class TestPercentiles:
    def test_empty(self):
        assert percentiles([]) == {}

    def test_shape(self):
        stats = percentiles([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        assert stats["n"] == 10
        assert stats["p50"] == pytest.approx(5.5)
