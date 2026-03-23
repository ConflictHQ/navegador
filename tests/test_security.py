"""Tests for navegador.security — sensitive content detection and redaction."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from navegador.security import REDACTED, SensitiveContentDetector, SensitiveMatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def detector():
    return SensitiveContentDetector()


# ---------------------------------------------------------------------------
# Pattern detection tests
# ---------------------------------------------------------------------------


class TestAPIKeyDetection:
    def test_aws_akia_key(self, detector):
        text = "key = AKIAIOSFODNN7EXAMPLE"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "aws_access_key" in names

    def test_aws_asia_key(self, detector):
        # ASIA prefix + exactly 16 uppercase alphanumeric chars = 20-char key
        text = "assume_role_key=ASIAIOSFODNN7EXAMPLE"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "aws_access_key" in names

    def test_github_token_ghp(self, detector):
        text = "GITHUB_TOKEN=ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456789012"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "github_token" in names

    def test_openai_sk_key(self, detector):
        text = 'api_key = "sk-abcdefghijklmnopqrstuvwxyz12345678901234567890"'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "api_key_sk" in names

    def test_generic_api_key_assignment(self, detector):
        text = 'API_KEY = "AbCdEfGhIjKlMnOpQrStUvWxYz123456"'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "api_key_assignment" in names

    def test_severity_is_high_for_aws_key(self, detector):
        text = "AKIAIOSFODNN7EXAMPLE"
        matches = detector.scan_content(text)
        assert any(m.severity == "high" for m in matches)

    def test_match_text_is_redacted(self, detector):
        text = "AKIAIOSFODNN7EXAMPLE"
        matches = detector.scan_content(text)
        assert all(m.match_text == REDACTED for m in matches)

    def test_line_number_is_correct(self, detector):
        text = "# header\nAKIAIOSFODNN7EXAMPLE\n# footer"
        matches = detector.scan_content(text)
        aws_matches = [m for m in matches if m.pattern_name == "aws_access_key"]
        assert len(aws_matches) >= 1
        assert aws_matches[0].line_number == 2


class TestPasswordDetection:
    def test_password_equals_string(self, detector):
        text = 'password = "super_s3cr3t_pass"'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "password_assignment" in names

    def test_passwd_variant(self, detector):
        text = "passwd = 'hunter2hunter2'"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "password_assignment" in names

    def test_secret_key_variant(self, detector):
        text = 'secret = "mysecretvalue123"'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "password_assignment" in names

    def test_severity_high(self, detector):
        text = 'password = "hunter2hunter2"'
        matches = detector.scan_content(text)
        pw = [m for m in matches if m.pattern_name == "password_assignment"]
        assert all(m.severity == "high" for m in pw)


class TestPrivateKeyDetection:
    def test_rsa_private_key_header(self, detector):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "private_key_pem" in names

    def test_generic_private_key_header(self, detector):
        text = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w...\n-----END PRIVATE KEY-----"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "private_key_pem" in names

    def test_openssh_private_key_header(self, detector):
        text = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1...\n-----END OPENSSH PRIVATE KEY-----"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "private_key_pem" in names

    def test_severity_high(self, detector):
        text = "-----BEGIN RSA PRIVATE KEY-----"
        matches = detector.scan_content(text)
        pk = [m for m in matches if m.pattern_name == "private_key_pem"]
        assert all(m.severity == "high" for m in pk)


class TestConnectionStringDetection:
    def test_postgres_with_credentials(self, detector):
        text = 'DATABASE_URL = "postgresql://admin:s3cret@db.example.com:5432/mydb"'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "connection_string" in names

    def test_mysql_with_credentials(self, detector):
        text = "conn = mysql://user:passw0rd@localhost/schema"
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "connection_string" in names

    def test_mongodb_with_credentials(self, detector):
        text = 'uri = "mongodb://root:secret123@mongo.host:27017/db"'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "connection_string" in names

    def test_mongodb_srv_with_credentials(self, detector):
        text = 'uri = "mongodb+srv://admin:password@cluster0.abc.mongodb.net/mydb"'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "connection_string" in names

    def test_severity_high(self, detector):
        text = "postgresql://admin:s3cret@db.example.com/mydb"
        matches = detector.scan_content(text)
        cs = [m for m in matches if m.pattern_name == "connection_string"]
        assert all(m.severity == "high" for m in cs)


class TestJWTDetection:
    def test_valid_jwt(self, detector):
        # A real-looking but fake JWT
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
        signature = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        jwt = f"{header}.{payload}.{signature}"
        text = f'Authorization: Bearer {jwt}'
        matches = detector.scan_content(text)
        names = [m.pattern_name for m in matches]
        assert "jwt_token" in names

    def test_severity_medium(self, detector):
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        sig = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"{header}.{payload}.{sig}"
        matches = detector.scan_content(text)
        jwt = [m for m in matches if m.pattern_name == "jwt_token"]
        assert all(m.severity == "medium" for m in jwt)


# ---------------------------------------------------------------------------
# Redaction tests
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_redact_aws_key(self, detector):
        text = "key = AKIAIOSFODNN7EXAMPLE"
        result = detector.redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert REDACTED in result

    def test_redact_password(self, detector):
        text = 'password = "hunter2hunter2"'
        result = detector.redact(text)
        assert "hunter2hunter2" not in result
        assert REDACTED in result

    def test_redact_pem_header(self, detector):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
        result = detector.redact(text)
        assert "-----BEGIN RSA PRIVATE KEY-----" not in result
        assert REDACTED in result

    def test_redact_connection_string(self, detector):
        text = "postgresql://admin:s3cret@db.example.com/mydb"
        result = detector.redact(text)
        assert "s3cret" not in result
        assert REDACTED in result

    def test_redact_jwt(self, detector):
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
        sig = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        jwt = f"{header}.{payload}.{sig}"
        result = detector.redact(jwt)
        assert jwt not in result
        assert REDACTED in result

    def test_redact_returns_unchanged_clean_text(self, detector):
        text = "def hello():\n    return 'world'\n"
        result = detector.redact(text)
        assert result == text

    def test_redact_multiple_secrets_in_one_string(self, detector):
        text = (
            "AKIAIOSFODNN7EXAMPLE\n"
            'password = "mysecretvalue"\n'
        )
        result = detector.redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "mysecretvalue" not in result


# ---------------------------------------------------------------------------
# scan_file tests
# ---------------------------------------------------------------------------


class TestScanFile:
    def test_scan_file_detects_secrets(self, detector, tmp_path):
        secret_file = tmp_path / "config.py"
        secret_file.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
        matches = detector.scan_file(secret_file)
        assert len(matches) >= 1
        assert any(m.pattern_name == "aws_access_key" for m in matches)

    def test_scan_file_clean_file(self, detector, tmp_path):
        clean_file = tmp_path / "utils.py"
        clean_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        matches = detector.scan_file(clean_file)
        assert matches == []

    def test_scan_file_missing_file_returns_empty(self, detector, tmp_path):
        missing = tmp_path / "does_not_exist.py"
        matches = detector.scan_file(missing)
        assert matches == []


# ---------------------------------------------------------------------------
# No false positives on clean code
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    CLEAN_SNIPPETS = [
        # Normal variable names
        "password_length = 12\npassword_complexity = True\n",
        # Password prompt (no literal value)
        "password = input('Enter password: ')\n",
        # Short strings (below minimum length threshold)
        "secret = 'abc'\n",
        # Postgres URL without credentials
        "DB_URL = 'postgresql://localhost/mydb'\n",
        # A function named after a key concept
        "def get_api_key_name():\n    return 'key_name'\n",
        # Normal assignment that looks vaguely like an env var
        "API_BASE_URL = 'https://api.example.com'\n",
        # JWT-shaped but too short / clearly not a real token
        "token = 'eyJ.x.y'\n",
    ]

    @pytest.mark.parametrize("snippet", CLEAN_SNIPPETS)
    def test_no_false_positive(self, detector, snippet):
        matches = detector.scan_content(snippet)
        assert matches == [], f"Unexpected match in: {snippet!r} → {matches}"


# ---------------------------------------------------------------------------
# SensitiveMatch dataclass
# ---------------------------------------------------------------------------


class TestSensitiveMatch:
    def test_fields(self):
        m = SensitiveMatch(
            pattern_name="aws_access_key",
            line_number=3,
            match_text=REDACTED,
            severity="high",
        )
        assert m.pattern_name == "aws_access_key"
        assert m.line_number == 3
        assert m.match_text == REDACTED
        assert m.severity == "high"


# ---------------------------------------------------------------------------
# CLI --redact flag
# ---------------------------------------------------------------------------


class TestCLIRedactFlag:
    def test_redact_flag_accepted(self):
        """--redact flag should be accepted by the ingest command without error."""
        from navegador.cli.commands import main

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=MagicMock()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 1, "functions": 2,
                                                           "classes": 0, "edges": 3, "skipped": 0}
                result = runner.invoke(main, ["ingest", "src", "--redact"])
                assert result.exit_code == 0

    def test_redact_flag_passes_to_ingester(self):
        """RepoIngester must be constructed with redact=True when --redact is given."""
        from navegador.cli.commands import main

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=MagicMock()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 0, "functions": 0,
                                                           "classes": 0, "edges": 0, "skipped": 0}
                runner.invoke(main, ["ingest", "src", "--redact"])
                MockRI.assert_called_once()
                _, kwargs = MockRI.call_args
                assert kwargs.get("redact") is True

    def test_no_redact_flag_defaults_false(self):
        """Without --redact, RepoIngester should be constructed with redact=False (default)."""
        from navegador.cli.commands import main

        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("src").mkdir()
            with patch("navegador.cli.commands._get_store", return_value=MagicMock()), \
                 patch("navegador.ingestion.RepoIngester") as MockRI:
                MockRI.return_value.ingest.return_value = {"files": 0, "functions": 0,
                                                           "classes": 0, "edges": 0, "skipped": 0}
                runner.invoke(main, ["ingest", "src"])
                MockRI.assert_called_once()
                _, kwargs = MockRI.call_args
                assert kwargs.get("redact", False) is False
