"""
CI/CD mode for navegador — non-interactive output, structured exit codes,
and GitHub Actions integration.

Exit code contract:
  0  — success, no issues
  1  — hard error (ingest failed, DB unreachable, schema corrupt)
  2  — warnings only (migration needed, zero symbols ingested)
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

# ── Exit codes ────────────────────────────────────────────────────────────────

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_WARN = 2


# ── CI environment detection ─────────────────────────────────────────────────

_CI_VARS = ("GITHUB_ACTIONS", "CI", "GITLAB_CI", "CIRCLECI", "JENKINS_URL")


def detect_ci() -> str | None:
    """
    Return the name of the detected CI environment, or None if not in CI.

    Checks environment variables in priority order:
      GITHUB_ACTIONS → "github_actions"
      CI             → "ci"
      GITLAB_CI      → "gitlab_ci"
      CIRCLECI       → "circleci"
      JENKINS_URL    → "jenkins"
    """
    if os.environ.get("GITHUB_ACTIONS"):
        return "github_actions"
    if os.environ.get("CI"):
        return "ci"
    if os.environ.get("GITLAB_CI"):
        return "gitlab_ci"
    if os.environ.get("CIRCLECI"):
        return "circleci"
    if os.environ.get("JENKINS_URL"):
        return "jenkins"
    return None


def is_ci() -> bool:
    """Return True if running inside any recognised CI environment."""
    return detect_ci() is not None


def is_github_actions() -> bool:
    """Return True if running inside GitHub Actions specifically."""
    return detect_ci() == "github_actions"


# ── Reporter ──────────────────────────────────────────────────────────────────


@dataclass
class CICDReporter:
    """
    Machine-readable reporter for CI/CD pipelines.

    Collects errors and warnings during a command run, then emits either
    plain JSON (all CI systems) or GitHub Actions annotations + step summaries
    (when GITHUB_ACTIONS is set).

    Usage::

        reporter = CICDReporter()
        reporter.add_error("ingest failed: <reason>")
        reporter.add_warning("no Python files found")
        reporter.emit(data={"files": 0})
        sys.exit(reporter.exit_code())
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ── Collecting ────────────────────────────────────────────────────────────

    def add_error(self, message: str) -> None:
        """Record a hard error (will produce exit code 1)."""
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        """Record a warning (will produce exit code 2 when no errors)."""
        self.warnings.append(message)

    # ── Exit code ─────────────────────────────────────────────────────────────

    def exit_code(self) -> int:
        """
        Return the appropriate process exit code.

          0  — no errors, no warnings
          1  — at least one error
          2  — warnings only
        """
        if self.errors:
            return EXIT_ERROR
        if self.warnings:
            return EXIT_WARN
        return EXIT_SUCCESS

    # ── Output ────────────────────────────────────────────────────────────────

    def emit(self, data: dict[str, Any] | None = None, *, file=None) -> None:
        """
        Emit the report to stdout (or *file*).

        In GitHub Actions this also:
          - Prints ``::error`` / ``::warning`` annotations for each issue.
          - Writes a Markdown step summary to $GITHUB_STEP_SUMMARY.

        In all environments it prints a JSON envelope::

            {
              "status": "success" | "error" | "warning",
              "errors": [...],
              "warnings": [...],
              "data": {...}
            }
        """
        if file is None:
            file = sys.stdout

        payload = self._build_payload(data)

        if is_github_actions():
            self._emit_github_annotations(file=file)
            self._write_github_step_summary(payload)

        print(json.dumps(payload, indent=2), file=file)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _status_str(self) -> str:
        code = self.exit_code()
        if code == EXIT_ERROR:
            return "error"
        if code == EXIT_WARN:
            return "warning"
        return "success"

    def _build_payload(self, data: dict[str, Any] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self._status_str(),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
        if data is not None:
            payload["data"] = data
        return payload

    def _emit_github_annotations(self, *, file=None) -> None:
        """Print GitHub Actions workflow commands for each issue."""
        if file is None:
            file = sys.stdout
        for err in self.errors:
            print(f"::error::{err}", file=file)
        for warn in self.warnings:
            print(f"::warning::{warn}", file=file)

    def _write_github_step_summary(self, payload: dict[str, Any]) -> None:
        """Append a Markdown summary to $GITHUB_STEP_SUMMARY if set."""
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not summary_path:
            return

        lines: list[str] = []
        status_icon = {"success": "✅", "warning": "⚠️", "error": "❌"}.get(payload["status"], "ℹ️")
        lines.append(f"## Navegador — {status_icon} {payload['status'].capitalize()}\n")

        if payload.get("data"):
            lines.append("### Stats\n")
            lines.append("| Key | Value |")
            lines.append("| --- | --- |")
            for k, v in payload["data"].items():
                lines.append(f"| {k} | {v} |")
            lines.append("")

        if payload["errors"]:
            lines.append("### Errors\n")
            for e in payload["errors"]:
                lines.append(f"- {e}")
            lines.append("")

        if payload["warnings"]:
            lines.append("### Warnings\n")
            for w in payload["warnings"]:
                lines.append(f"- {w}")
            lines.append("")

        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            pass  # Non-fatal — summary write failure must never break a pipeline
