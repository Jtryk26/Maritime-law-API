"""Produktionskonfiguration: standardværdier og boolsk parsing.

Et produktionsmiljø (ENVIRONMENT=production, SOURCE_CLIENT=production)
skal kunne starte uden at ENABLE_ADMIN_API, RUN_MIGRATIONS_ON_STARTUP og
EXPOSE_API_DOCS sættes eksplicit. Værdierne sættes her som miljøvariabler,
præcis som Docker Compose gør, og `.env` læses ikke.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, validate_production_invariants
from app.main import create_app

FLAGS = ("ENABLE_ADMIN_API", "RUN_MIGRATIONS_ON_STARTUP", "EXPOSE_API_DOCS")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    # conftest.py sætter EXPOSE_API_DOCS og ENABLE_ADMIN_API til true for
    # resten af suiten; her skal standardværdierne gælde.
    for name in (*FLAGS, "ENVIRONMENT", "SOURCE_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_production_starts_with_defaults(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SOURCE_CLIENT", "production")
    clean_env.setenv("ENVIRONMENT", "production")
    settings = Settings(_env_file=None)

    assert settings.source_client == "production"
    assert settings.is_production
    assert settings.enable_admin_api is False
    assert settings.run_migrations_on_startup is False
    assert settings.expose_api_docs is False
    validate_production_invariants(settings)
    create_app(settings)


def test_development_defaults(clean_env: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)

    assert settings.enable_admin_api is False
    assert settings.run_migrations_on_startup is False
    assert settings.expose_api_docs is True


def test_explicit_docs_in_production_is_still_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ENVIRONMENT", "production")
    clean_env.setenv("EXPOSE_API_DOCS", "true")

    with pytest.raises(RuntimeError, match="EXPOSE_API_DOCS=true"):
        validate_production_invariants(Settings(_env_file=None))


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("raw", ["false", "False", "FALSE", "0", "", "  ", "no", "off"])
def test_false_like_values_are_false(clean_env: pytest.MonkeyPatch, flag: str, raw: str) -> None:
    clean_env.setenv("ENVIRONMENT", "production")
    clean_env.setenv(flag, raw)
    settings = Settings(_env_file=None)

    assert getattr(settings, flag.lower()) is False
    validate_production_invariants(settings)


@pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "on"])
def test_true_like_values_are_true(clean_env: pytest.MonkeyPatch, raw: str) -> None:
    clean_env.setenv("ENABLE_ADMIN_API", raw)

    assert Settings(_env_file=None).enable_admin_api is True


@pytest.mark.parametrize("raw", ["ja", "2", "truee"])
def test_unknown_bool_values_are_rejected(clean_env: pytest.MonkeyPatch, raw: str) -> None:
    clean_env.setenv("ENABLE_ADMIN_API", raw)

    with pytest.raises(ValidationError, match="Ugyldig boolsk værdi"):
        Settings(_env_file=None)
