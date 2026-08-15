"""Test af søgeloggen.

Loggen er det, brugerens ønske om at "vektorisere de spørgsmål der går
gennem søgemaskinen" bliver til: hver søgning gemmes én gang med sin
egen vektor, så systemet kan vise beslægtede søgninger og — vigtigere —
hvilke søgninger der aldrig får svar.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import SearchQueryLog
from app.services.search import QueryLogService, normalize_query


class TestNormalisering:
    def test_danske_tegn_foldes(self):
        assert normalize_query("Søulykke") == normalize_query("soeulykke")

    def test_mellemrum_og_store_bogstaver_er_ligegyldige(self):
        assert normalize_query("  BRAND   passagerskib ") == normalize_query("brand passagerskib")


class TestRegistrering:
    def test_soegning_gemmes_med_vektor(self, session, embedding_provider):
        service = QueryLogService(session, embedding_provider)
        entry = service.record("brandsikkerhed på passagerskibe", result_count=3, mode="hybrid")

        assert entry is not None
        assert entry.occurrences == 1
        assert entry.last_result_count == 3
        assert entry.last_mode == "hybrid"
        assert entry.embedding is not None
        assert entry.embedding_model == embedding_provider.info.model

    def test_gentagelse_giver_ikke_en_ny_raekke(self, session, embedding_provider):
        service = QueryLogService(session, embedding_provider)
        service.record("redningsmidler", result_count=2, mode="hybrid")
        service.record("Redningsmidler", result_count=5, mode="lexical")

        assert session.scalar(select(func.count()).select_from(SearchQueryLog)) == 1

        entry = session.scalars(select(SearchQueryLog)).one()
        assert entry.occurrences == 2
        assert entry.last_result_count == 5
        assert entry.last_mode == "lexical"
        # Den oprindelige skrivemåde bevares.
        assert entry.query_text == "redningsmidler"

    def test_bedste_antal_traef_huskes(self, session, embedding_provider):
        """Adskiller 'findes ikke' fra 'gav ingenting på grund af et filter'."""
        service = QueryLogService(session, embedding_provider)
        service.record("lodspligt", result_count=4, mode="hybrid")
        service.record("lodspligt", result_count=0, mode="hybrid")

        entry = session.scalars(select(SearchQueryLog)).one()
        assert entry.last_result_count == 0
        assert entry.best_result_count == 4
        assert entry.had_no_results is False

    def test_meget_korte_soegninger_springes_over(self, session, embedding_provider):
        """Halvskrevne ord er støj, ikke data."""
        service = QueryLogService(session, embedding_provider)
        assert service.record("a", result_count=0, mode="hybrid") is None
        assert session.scalar(select(func.count()).select_from(SearchQueryLog)) == 0

    def test_logning_kan_slaas_fra(self, session, embedding_provider, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setenv("SEARCH_QUERY_LOG_ENABLED", "false")
        get_settings.cache_clear()
        try:
            service = QueryLogService(session, embedding_provider)
            assert service.record("skib", result_count=1, mode="hybrid") is None
        finally:
            get_settings.cache_clear()

    def test_fejl_i_loggen_rammer_ikke_kalderen(self, session):
        """Et resultat må aldrig gå tabt, fordi en logpost ikke kunne skrives."""

        class BrokenProvider:
            from app.services.embedding import HashingEmbeddingProvider as _H

            info = _H(dimensions=8).info

            def embed_query(self, text):  # noqa: ARG002
                raise RuntimeError("model nede")

            def embed_passages(self, texts):  # noqa: ARG002
                raise RuntimeError("model nede")

        service = QueryLogService(session, BrokenProvider())
        entry = service.record("skibssikkerhed", result_count=1, mode="hybrid")

        # Posten gemmes stadig — kun vektoren mangler.
        assert entry is not None
        assert entry.embedding is None


class TestAflaesning:
    def _populate(self, session, provider) -> QueryLogService:
        service = QueryLogService(session, provider)
        service.record("brand på passagerskib", result_count=3, mode="hybrid")
        service.record("brand på passagerskib", result_count=3, mode="hybrid")
        service.record("brand om bord på passagerskib", result_count=2, mode="hybrid")
        service.record("kommunale biblioteker", result_count=0, mode="hybrid")
        service.record("dagtilbud og daginstitutioner", result_count=0, mode="hybrid")
        return service

    def test_hyppigste_soegninger(self, session, embedding_provider):
        service = self._populate(session, embedding_provider)
        popular = service.popular(limit=3)

        assert popular
        assert popular[0].query_text == "brand på passagerskib"
        assert popular[0].occurrences == 2

    def test_soegninger_uden_resultat(self, session, embedding_provider):
        """Den vigtigste liste: hvad materialet eller ordvalget mangler."""
        service = self._populate(session, embedding_provider)
        empty = service.without_results(limit=10)

        texts = {e.query_text for e in empty}
        assert "kommunale biblioteker" in texts
        assert "brand på passagerskib" not in texts

    def test_relaterede_soegninger_findes_paa_lighed(self, session, embedding_provider):
        service = self._populate(session, embedding_provider)
        related = service.related("brand på passagerskib", limit=5)

        assert related, "der skulle findes mindst én beslægtet søgning"
        # Søgningen selv er ikke sit eget forslag.
        assert all(r.query != "brand på passagerskib" for r in related)
        # Den nærmeste skal være den om det samme emne, ikke bibliotekerne.
        assert related[0].query == "brand om bord på passagerskib"

    def test_relaterede_soegninger_sorteres_faldende(self, session, embedding_provider):
        service = self._populate(session, embedding_provider)
        related = service.related("brand passagerskib", limit=10)
        similarities = [r.similarity for r in related]
        assert similarities == sorted(similarities, reverse=True)

    def test_tom_log_giver_ingen_forslag(self, session, embedding_provider):
        service = QueryLogService(session, embedding_provider)
        assert service.related("skib", limit=5) == []

    def test_noegletal(self, session, embedding_provider):
        service = self._populate(session, embedding_provider)
        stats = service.stats()

        assert stats["distinct_queries"] == 4
        assert stats["total_searches"] == 5
        assert stats["queries_without_results"] == 2
        assert stats["vectorized_queries"] == 4

    def test_ingen_personoplysninger_i_modellen(self):
        """Loggen skal kunne svare på HVAD der søges — ikke på HVEM.

        Testen er et værn: tilføjer nogen senere en bruger- eller
        IP-kolonne, skal det være et bevidst valg, ikke en glidning.
        """
        columns = set(SearchQueryLog.__table__.columns.keys())
        forbidden = {"user_id", "user", "ip", "ip_address", "session_id", "client_id"}
        assert columns & forbidden == set()
