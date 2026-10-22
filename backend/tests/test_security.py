"""Test af adgangskontrol og rate limiting.

Testene her svarer på ét spørgsmål: hvad kan en person, der finder
adressen på tjenesten, gøre uden legitimation? Svaret skal være
"læse lovtekst" — og intet andet.
"""

from __future__ import annotations

import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.request_limits import SlidingWindowLimiter
from app.core.security import (
    AdminAuthNotConfiguredError,
    require_admin,
    verify_admin_auth,
)

#: Sat af conftest.py, som pytest importerer før testmodulerne.
TEST_ADMIN_TOKEN = os.environ["ADMIN_API_TOKEN"]

# Enhver rute, der enten skriver til databasen eller afslører drift.
# Listen er bevidst udtømmende: en ny rute i imports.py skal give en
# fejlende test her, hvis nogen kommer til at flytte den ud af den
# beskyttede router.
ADMIN_ENDPOINTS = [
    ("GET", "/api/admin/session"),
    ("GET", "/api/stats"),
    ("GET", "/api/import/runs"),
    ("GET", "/api/import/runs/1"),
    ("GET", "/api/embeddings/status"),
    ("GET", "/api/search/queries"),
    ("GET", "/api/search/related?q=skib"),
    ("POST", "/api/import/run"),
    ("POST", "/api/embeddings/run"),
]

# Det almindelige publikum skal kunne bruge alt dette uden legitimation.
PUBLIC_ENDPOINTS = [
    "/health",
    "/api/search?q=skib",
    "/api/documents",
    "/api/categories",
    "/api/facets",
]


def _call(client: TestClient, method: str, path: str, **kwargs):
    if method == "POST":
        return client.post(path, json={}, **kwargs)
    return client.get(path, **kwargs)


# ---------------------------------------------------------------------------
# Administratortoken
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), ADMIN_ENDPOINTS)
def test_driftsendepunkter_afvises_uden_token(public_api_client, method: str, path: str):
    response = _call(public_api_client, method, path)
    assert response.status_code == 401, f"{method} {path} var åbent"
    assert response.json()["detail"]


@pytest.mark.parametrize(("method", "path"), ADMIN_ENDPOINTS)
def test_driftsendepunkter_afvises_med_forkert_token(public_api_client, method: str, path: str):
    response = _call(
        public_api_client, method, path, headers={"Authorization": "Bearer forkert-token"}
    )
    assert response.status_code == 401, f"{method} {path} accepterede et forkert token"


def test_afvisning_oplyser_hvilken_godkendelse_der_mangler(public_api_client):
    """401 uden WWW-Authenticate er et ufuldstændigt svar."""
    response = public_api_client.get("/api/stats")
    assert response.status_code == 401
    assert "bearer" in response.headers.get("WWW-Authenticate", "").lower()


def test_forkert_skema_afvises(public_api_client):
    """Tokenet skal sendes som Bearer, ikke som Basic."""
    response = public_api_client.get(
        "/api/stats", headers={"Authorization": f"Basic {TEST_ADMIN_TOKEN}"}
    )
    assert response.status_code == 401


def test_gyldigt_token_giver_adgang(api_client):
    response = api_client.get("/api/admin/session")
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_import_kraever_token(public_api_client):
    """Det alvorligste tilfælde: en fremmed må ikke kunne starte en import."""
    response = public_api_client.post("/api/import/run", json={"source_client": "fixture"})
    assert response.status_code == 401

    # Og intet blev skrevet.
    runs = public_api_client.get(
        "/api/import/runs", headers={"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    )
    assert runs.status_code == 200
    assert runs.json()["total"] == 0


@pytest.mark.parametrize("path", PUBLIC_ENDPOINTS)
def test_offentlige_endepunkter_er_stadig_aabne(public_api_client, path: str):
    """Adgangskontrollen må ikke ramme den søgende bruger."""
    response = public_api_client.get(path)
    assert response.status_code == 200, f"{path} kræver nu legitimation"


def test_dokumenter_kan_laeses_uden_token(api_client, public_api_client):
    """Et importeret dokument skal kunne læses af enhver."""
    created = api_client.post("/api/import/run", json={"source_client": "fixture"})
    assert created.status_code == 201

    listing = public_api_client.get("/api/documents")
    assert listing.status_code == 200
    document_id = listing.json()["items"][0]["id"]

    assert public_api_client.get(f"/api/documents/{document_id}").status_code == 200
    assert public_api_client.get(f"/api/documents/{document_id}/versions").status_code == 200


def test_uden_konfigureret_token_er_drift_lukket(public_api_client, monkeypatch):
    """Fail-closed: en glemt konfiguration åbner ikke endepunkterne.

    503 frem for 401, fordi fejlen ligger på serveren og ikke hos den,
    der kalder — og fordi svaret så kan skelnes i brugerfladen.
    """
    monkeypatch.setenv("ADMIN_API_TOKEN", "")
    get_settings.cache_clear()
    try:
        response = public_api_client.post("/api/import/run", json={"source_client": "fixture"})
        assert response.status_code == 503

        # Heller ikke med det rigtige token fra før.
        response = public_api_client.get(
            "/api/stats", headers={"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
        )
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Opstartskontrol
# ---------------------------------------------------------------------------


def test_produktion_uden_token_starter_ikke():
    settings = Settings(environment="production", admin_api_token="")
    with pytest.raises(AdminAuthNotConfiguredError):
        verify_admin_auth(settings)


def test_produktion_afviser_kort_token():
    settings = Settings(environment="production", admin_api_token="kort")
    with pytest.raises(AdminAuthNotConfiguredError):
        verify_admin_auth(settings)


def test_produktion_med_ordentligt_token_er_i_orden():
    settings = Settings(environment="production", admin_api_token="x" * 40)
    verify_admin_auth(settings)  # rejser ikke


def test_udvikling_uden_token_starter_alligevel():
    """En frisk udviklingsmaskine skal kunne køre uden opsætning."""
    verify_admin_auth(Settings(environment="development", admin_api_token=""))


def test_tomt_token_i_miljoeet_betyder_ikke_sat():
    """ADMIN_API_TOKEN= i .env må ikke blive til et token på nul tegn."""
    assert Settings(admin_api_token="   ").admin_api_token is None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_glidende_vindue_slipper_praecis_kvoten_igennem():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert [limiter.check("a", now=t).allowed for t in (0, 1, 2, 3)] == [
        True,
        True,
        True,
        False,
    ]


def test_kvoten_er_pr_klient():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    assert limiter.check("a", now=0).allowed is True
    assert limiter.check("b", now=0).allowed is True
    assert limiter.check("a", now=0).allowed is False


def test_kvoten_frigives_naar_vinduet_glider():
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10)
    assert limiter.check("a", now=0).allowed is True
    assert limiter.check("a", now=5).allowed is True
    assert limiter.check("a", now=9).allowed is False
    # Det første kald er nu faldet ud af vinduet.
    assert limiter.check("a", now=11).allowed is True


def test_retry_after_er_positiv():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    limiter.check("a", now=0)
    decision = limiter.check("a", now=1)
    assert decision.allowed is False
    assert decision.retry_after >= 1


def test_antal_huskede_klienter_har_loft():
    """Selve tælleren må ikke kunne bruges til at spise hukommelse."""
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60, max_keys=10)
    for index in range(500):
        limiter.check(f"klient-{index}", now=float(index))
    assert len(limiter._hits) <= 10  # noqa: SLF001 — netop loftet der testes


def _limited_app(settings: Settings) -> TestClient:
    """Lille applikation med samme middleware som den rigtige."""
    from app.api.middleware import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, settings=settings)

    @app.get("/api/search")
    def search() -> dict[str, str]:
        return {"ok": "søgning"}

    @app.get("/api/documents")
    def documents() -> dict[str, str]:
        return {"ok": "dokumenter"}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"ok": "health"}

    return TestClient(app)


def test_soegning_begraenses_og_svarer_429():
    client = _limited_app(
        Settings(
            rate_limit_enabled=True,
            rate_limit_search_per_minute=3,
            rate_limit_requests_per_minute=100,
        )
    )
    codes = [client.get("/api/search").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]

    blocked = client.get("/api/search")
    assert blocked.json()["error_type"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1


def test_soegning_og_oevrige_kald_har_hver_sin_kvote():
    client = _limited_app(
        Settings(
            rate_limit_enabled=True,
            rate_limit_search_per_minute=1,
            rate_limit_requests_per_minute=50,
        )
    )
    assert client.get("/api/search").status_code == 200
    assert client.get("/api/search").status_code == 429
    # Den opbrugte søgekvote må ikke lukke resten af API'et.
    assert client.get("/api/documents").status_code == 200


def test_health_begraenses_ikke():
    """Docker og overvågning skal kunne spørge uden at bruge af kvoten."""
    client = _limited_app(
        Settings(rate_limit_enabled=True, rate_limit_requests_per_minute=1)
    )
    assert [client.get("/health").status_code for _ in range(5)] == [200] * 5


def test_rate_limiting_kan_slaas_fra():
    client = _limited_app(
        Settings(rate_limit_enabled=False, rate_limit_search_per_minute=1)
    )
    assert [client.get("/api/search").status_code for _ in range(5)] == [200] * 5


def test_resterende_kvote_oplyses():
    client = _limited_app(
        Settings(rate_limit_enabled=True, rate_limit_search_per_minute=5)
    )
    response = client.get("/api/search")
    assert response.headers["X-RateLimit-Limit"] == "5"
    assert response.headers["X-RateLimit-Remaining"] == "4"


# ---------------------------------------------------------------------------
# Klientens adresse bag proxy
# ---------------------------------------------------------------------------


def test_forfalsket_afsenderadresse_ignoreres_uden_tillid_til_proxy():
    """Uden TRUST_PROXY_HEADERS må headere ikke kunne omgå kvoten.

    Ville vi stole på en vilkårlig X-Forwarded-For, kunne enhver klient
    skrive en ny adresse for hver forespørgsel og dermed have uendelig
    kvote.
    """
    client = _limited_app(
        Settings(
            rate_limit_enabled=True,
            rate_limit_search_per_minute=2,
            trust_proxy_headers=False,
        )
    )
    codes = [
        client.get("/api/search", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
        for i in range(4)
    ]
    assert codes == [200, 200, 429, 429]


def test_cloudflare_adresse_bruges_naar_proxy_er_betroet():
    """Bag tunnelen har alle brugere samme socket-adresse.

    Uden CF-Connecting-IP ville én persons søgninger lukke hele skolen
    ude af tjenesten.
    """
    client = _limited_app(
        Settings(
            rate_limit_enabled=True,
            rate_limit_search_per_minute=1,
            trust_proxy_headers=True,
        )
    )
    assert client.get("/api/search", headers={"CF-Connecting-IP": "203.0.113.7"}).status_code == 200
    assert client.get("/api/search", headers={"CF-Connecting-IP": "203.0.113.8"}).status_code == 200
    assert client.get("/api/search", headers={"CF-Connecting-IP": "203.0.113.7"}).status_code == 429


def test_dependency_kan_bruges_paa_en_enkelt_rute(public_api_client):
    """`require_admin` skal virke uden for imports-routeren.

    Det er sådan søgeloggen i documents.py er beskyttet.
    """
    app = FastAPI()

    @app.get("/hemmelig", dependencies=[Depends(require_admin)])
    def hemmelig() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/hemmelig").status_code == 401
    assert client.get(
        "/hemmelig", headers={"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}
    ).status_code == 200
