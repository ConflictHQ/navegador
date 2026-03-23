"""
Sensitive content detection and redaction.

Scans source text for credentials, API keys, private keys, connection strings,
and other high-value secrets before they are persisted in the graph.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

REDACTED = "[REDACTED]"


@dataclass
class SensitiveMatch:
    """A single sensitive-content finding."""

    pattern_name: str
    line_number: int
    match_text: str  # the matched text — stored already-redacted
    severity: str  # "high" or "medium"


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------
# Each entry: (name, compiled-regex, severity)
# The regex must capture the full sensitive token (group 0 is what's replaced).

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # AWS access key IDs — 20-char uppercase alphanumeric starting with AKIA/ASIA/AROA
    (
        "aws_access_key",
        re.compile(r"(?<![A-Z0-9])(AKIA|ASIA|AROA)[A-Z0-9]{16}(?![A-Z0-9])"),
        "high",
    ),
    # AWS secret access key — 40-char base64-ish value that typically follows "aws_secret"
    (
        "aws_secret_key",
        re.compile(
            r'(?i)aws[_\-\s]*secret[_\-\s]*(?:access[_\-\s]*)?key\s*[=:]\s*["\']?([A-Za-z0-9/+]{40})["\']?'
        ),
        "high",
    ),
    # GitHub personal access tokens (classic ghp_ and fine-grained github_pat_)
    (
        "github_token",
        re.compile(r"(ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{80,})"),
        "high",
    ),
    # Generic "sk-" prefixed keys (OpenAI, Anthropic, Stripe, etc.)
    (
        "api_key_sk",
        re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}"),
        "high",
    ),
    # Generic API key / token assignment pattern
    (
        "api_key_assignment",
        re.compile(
            r'(?i)(?:api[_\-]?key|api[_\-]?token|access[_\-]?token|auth[_\-]?token)\s*[=:]\s*["\']([A-Za-z0-9\-_\.]{16,})["\']'
        ),
        "high",
    ),
    # Password in assignment
    (
        "password_assignment",
        re.compile(r'(?i)(?:password|passwd|secret)\s*[=:]\s*["\']([^"\']{4,})["\']'),
        "high",
    ),
    # PEM private key header
    (
        "private_key_pem",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        "high",
    ),
    # PostgreSQL / MySQL / MongoDB / Redis connection strings with credentials
    (
        "connection_string",
        re.compile(
            r'(?i)(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:@\s"\']+:[^@\s"\'@]+@[^\s"\']{4,}'
        ),
        "high",
    ),
    # JWT — three base64url segments separated by dots (ey… header is a strong signal)
    (
        "jwt_token",
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "medium",
    ),
]


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class SensitiveContentDetector:
    """
    Detect and redact sensitive content (credentials, tokens, keys) in source text.

    Usage::

        detector = SensitiveContentDetector()
        matches = detector.scan_content(text)
        clean   = detector.redact(text)
    """

    def scan_content(self, text: str) -> list[SensitiveMatch]:
        """
        Scan *text* for sensitive patterns.

        Returns a list of :class:`SensitiveMatch` objects, one per finding.
        The ``match_text`` field contains the matched string already rendered as
        ``[REDACTED]`` so callers never need to touch the raw secret.
        """
        findings: list[SensitiveMatch] = []

        for pattern_name, regex, severity in _PATTERNS:
            for m in regex.finditer(text):
                # Determine line number (1-based) by counting newlines before match start
                line_number = text.count("\n", 0, m.start()) + 1
                findings.append(
                    SensitiveMatch(
                        pattern_name=pattern_name,
                        line_number=line_number,
                        match_text=REDACTED,
                        severity=severity,
                    )
                )

        # Stable, deterministic order: by line then pattern name
        findings.sort(key=lambda f: (f.line_number, f.pattern_name))
        return findings

    def redact(self, text: str) -> str:
        """
        Return a copy of *text* with all sensitive patterns replaced by ``[REDACTED]``.
        """
        result = text
        for _pattern_name, regex, _severity in _PATTERNS:
            result = regex.sub(REDACTED, result)
        return result

    def scan_file(self, path: Path) -> list[SensitiveMatch]:
        """
        Convenience wrapper: read *path* and run :meth:`scan_content`.

        Returns an empty list if the file cannot be read.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return self.scan_content(text)
