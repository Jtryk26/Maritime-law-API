"""Test for PostgreSQL mindsteprivilegie-provisionering og rettighedshåndhævelse."""

import argparse
import os
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import text

from app.cli import cmd_provision_runtime_role


def test_provision_runtime_role_requires_password(capsys, monkeypatch):
    """Kommandoen afviser kørsel uden runtime password."""
    monkeypatch.delenv("POSTGRES_RUNTIME_PASSWORD", raising=False)
    args = argparse.Namespace(user="maritim_runtime", password=None)
    result = cmd_provision_runtime_role(args)
    assert result == 1
    captured = capsys.readouterr()
    assert "Fejl: Runtime-kodeord skal angives" in captured.err


def test_provision_runtime_role_generates_strict_grants():
    """Verificerer at de udførte SQL-sætninger giver strengt begrænsede rettigheder via psycopg.sql."""
    mock_session = MagicMock()
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind

    executed_queries = []

    mock_cursor = MagicMock()
    mock_raw_conn = MagicMock()
    mock_raw_conn.cursor.return_value.__enter__.return_value = mock_cursor

    def fake_cursor_execute(query, *args, **kwargs):
        executed_queries.append(repr(query))

    mock_cursor.execute.side_effect = fake_cursor_execute
    mock_session.connection.return_value.connection = mock_raw_conn

    def fake_execute(clause, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.scalar.return_value = None
        return mock_res

    mock_session.execute.side_effect = fake_execute

    with patch("app.cli.session_scope") as mock_scope:
        mock_scope.return_value.__enter__.return_value = mock_session
        args = argparse.Namespace(user="maritim_runtime", password="test_secret_pass_123")
        res = cmd_provision_runtime_role(args)
        assert res == 0

    full_ast = "\n".join(executed_queries)

    # 1. Bekræft at Identifier og Literal anvendes sikkert
    assert "Identifier('maritim_runtime')" in full_ast
    assert "Literal('test_secret_pass_123')" in full_ast

    # 2. Bekræft at USAGE tildeles på public, men CREATE fratages
    assert "GRANT USAGE ON SCHEMA public TO" in full_ast
    assert "REVOKE CREATE ON SCHEMA public FROM" in full_ast

    # 3. Bekræft at SELECT tildeles, men INGEN modifikationer
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO" in full_ast
    assert "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM" in full_ast
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO" in full_ast
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM" in full_ast

    # 4. Bekræft at mutating privilegier ALDRIG tildeles
    assert "GRANT INSERT" not in full_ast
    assert "GRANT UPDATE" not in full_ast
    assert "GRANT DELETE" not in full_ast
    assert "GRANT ALL" not in full_ast


def test_provision_runtime_role_from_env_and_special_chars(monkeypatch):
    """Kommandoen læser sikkert fra miljøvariabel og escaper specialtegn i kodeord."""
    special_pass = "P@ss'w0rd!#%&\"123"
    monkeypatch.setenv("POSTGRES_RUNTIME_PASSWORD", special_pass)
    mock_session = MagicMock()
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind

    executed_queries = []

    mock_cursor = MagicMock()
    mock_raw_conn = MagicMock()
    mock_raw_conn.cursor.return_value.__enter__.return_value = mock_cursor

    def fake_cursor_execute(query, *args, **kwargs):
        executed_queries.append(repr(query))

    mock_cursor.execute.side_effect = fake_cursor_execute
    mock_session.connection.return_value.connection = mock_raw_conn

    def fake_execute(clause, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.scalar.return_value = None
        return mock_res

    mock_session.execute.side_effect = fake_execute

    with patch("app.cli.session_scope") as mock_scope:
        mock_scope.return_value.__enter__.return_value = mock_session
        args = argparse.Namespace(user="maritim_runtime", password=None)
        res = cmd_provision_runtime_role(args)
        assert res == 0

    full_ast = "\n".join(executed_queries)
    assert f"Literal({special_pass!r})" in full_ast




def test_provision_runtime_role_rejects_malformed_username(capsys):
    """Ugyldige brugernavne med SQL-injektionsforsøg afvises."""
    mock_session = MagicMock()
    with patch("app.cli.session_scope") as mock_scope:
        mock_scope.return_value.__enter__.return_value = mock_session
        args = argparse.Namespace(user="bad_user; DROP TABLE documents;", password="some_password")
        res = cmd_provision_runtime_role(args)
        assert res == 1

    captured = capsys.readouterr()
    assert "Ugyldigt rollenavn" in captured.err


def test_build_database_url_encodes_special_characters():
    """SQLAlchemy URL.create håndterer specialtegn i kodeord sikkert."""
    from sqlalchemy.engine import make_url
    from app.core.config import build_database_url

    complex_pass = "P@ss'w0rd!#%&\"123"
    url_str = build_database_url(
        user="maritim_runtime",
        password=complex_pass,
        host="db",
        port=5432,
        dbname="maritim",
    )

    # Bekræft at make_url parser URL'en korrekt og gendanner det oprindelige rå kodeord
    parsed = make_url(url_str)
    assert parsed.username == "maritim_runtime"
    assert parsed.password == complex_pass
    assert parsed.host == "db"
    assert parsed.port == 5432
    assert parsed.database == "maritim"


def test_settings_constructs_database_url_from_complex_password():
    """Settings samler og encoder automatisk URL fra separate POSTGRES_* variable."""
    from sqlalchemy.engine import make_url
    from app.core.config import Settings

    complex_pass = "P@ss'w0rd!#%&\"123"
    settings = Settings(
        postgres_runtime_user="maritim_runtime",
        postgres_runtime_password=complex_pass,
        postgres_host="db",
        postgres_port=5432,
        postgres_db="maritim",
        database_url=None,
    )

    parsed = make_url(settings.database_url)
    assert parsed.username == "maritim_runtime"
    assert parsed.password == complex_pass
    assert parsed.host == "db"
    assert parsed.port == 5432
    assert parsed.database == "maritim"



def test_provision_runtime_role_idempotent_execution():
    """Gentagen kørsel af rollestyring opdaterer sikkert uden fejl."""
    mock_session = MagicMock()
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind

    # Simuler første kørsel (findes ikke) -> anden kørsel (findes)
    mock_session.execute.return_value.scalar.side_effect = [None, 1]

    with patch("app.cli.session_scope") as mock_scope:
        mock_scope.return_value.__enter__.return_value = mock_session
        args = argparse.Namespace(user="maritim_runtime", password="secure_password_abc")

        # 1. Oprettelse
        res1 = cmd_provision_runtime_role(args)
        assert res1 == 0

        # 2. Opdatering (idempotens)
        res2 = cmd_provision_runtime_role(args)
        assert res2 == 0
