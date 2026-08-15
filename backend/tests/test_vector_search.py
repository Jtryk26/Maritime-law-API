"""Test af det semantiske indeks, vektorsøgning og hybridsammensmeltning.

Testene kører med hash-udbyderen (se `conftest.database_url`). De
handler derfor om **rørføringen** — at chunks bliver skrevet, at
forældede vektorer opdages, at filtre gælder begge sider, at
sammensmeltningen rangerer som den skal — og ikke om modelkvalitet.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Document, DocumentChunk
from app.services.embedding import EmbeddingIndexer, HashingEmbeddingProvider
from app.services.search import (
    HybridSearchBackend,
    SearchQuery,
    VectorSearchBackend,
    get_lexical_backend,
    resolve_search_mode,
)


def _document(session, fragment: str) -> Document:
    document = session.scalars(
        select(Document).where(Document.title.ilike(f"%{fragment}%"))
    ).first()
    assert document is not None, f"fandt intet dokument med {fragment!r} i titlen"
    return document


# ---------------------------------------------------------------------------
# Indeksering
# ---------------------------------------------------------------------------


class TestIndeksering:
    def test_import_alene_vektoriserer_ikke(self, seeded_session, embedding_provider):
        """Vektorisering er bevidst adskilt fra importen.

        En import må ikke kunne fejle, fordi en model ikke kunne
        indlæses — lovteksten er det vigtige.
        """
        from app.services.categorization import KeywordCategorizationEngine
        from app.services.importer import ImportService
        from app.services.relevance import KeywordRelevanceEngine
        from app.services.retsinformation import FixtureRetsinformationClient

        ImportService(
            seeded_session,
            client=FixtureRetsinformationClient(revision=1),
            relevance_engine=KeywordRelevanceEngine(),
            categorization_engine=KeywordCategorizationEngine(),
        ).run()

        assert seeded_session.scalar(select(DocumentChunk.id)) is None

        indexer = EmbeddingIndexer(seeded_session, embedding_provider)
        assert indexer.pending_count() > 0

    def test_indeksering_skriver_stykker_med_vektorer(self, indexed_session, embedding_provider):
        chunks = indexed_session.scalars(select(DocumentChunk)).all()

        assert chunks, "der skulle være skrevet stykker"
        for chunk in chunks[:20]:
            assert chunk.embedding is not None
            assert chunk.embedding_model == embedding_provider.info.model
            assert chunk.embedding_dim == embedding_provider.info.dimensions
            assert len(chunk.embedding) == 4 * embedding_provider.info.dimensions

    def test_kun_maritime_dokumenter_vektoriseres_som_standard(self, indexed_session):
        """At vektorisere folkeskolebekendtgørelser koster tid uden gevinst."""
        rows = indexed_session.execute(
            select(Document.is_maritime).join(DocumentChunk, DocumentChunk.document_id == Document.id)
        ).all()
        assert rows
        assert all(is_maritime for (is_maritime,) in rows)

    def test_dokumentet_peger_paa_den_version_der_blev_vektoriseret(self, indexed_session):
        documents = indexed_session.scalars(
            select(Document).where(Document.chunk_count > 0)
        ).all()
        assert documents
        for document in documents:
            assert document.embedded_version_id == document.current_version_id
            assert document.embedded_at is not None

    def test_koersel_nummer_to_finder_intet_at_lave(self, indexed_session, embedding_provider):
        """Idempotens: uændret indhold giver ikke nye stykker."""
        indexer = EmbeddingIndexer(indexed_session, embedding_provider)
        before = indexed_session.scalar(select(DocumentChunk.id).limit(1))

        assert indexer.pending_count() == 0
        report = indexer.index_pending()

        assert report.documents_checked == 0
        assert report.chunks_written == 0
        assert indexed_session.scalar(select(DocumentChunk.id).limit(1)) == before

    def test_ny_version_goer_vektorerne_foraeldede(self, indexed_session, embedding_provider):
        """Ændres lovteksten, skal indekset følge med — ellers svarer
        søgningen på en tekst der ikke længere gælder."""
        from app.services.categorization import KeywordCategorizationEngine
        from app.services.importer import ImportService
        from app.services.relevance import KeywordRelevanceEngine
        from app.services.retsinformation import FixtureRetsinformationClient

        indexer = EmbeddingIndexer(indexed_session, embedding_provider)
        assert indexer.pending_count() == 0

        # Revision 2 af fixturen ændrer indholdet i mindst ét dokument.
        ImportService(
            indexed_session,
            client=FixtureRetsinformationClient(revision=2),
            relevance_engine=KeywordRelevanceEngine(),
            categorization_engine=KeywordCategorizationEngine(),
        ).run()

        assert indexer.pending_count() > 0

        report = indexer.index_pending()
        assert report.documents_embedded > 0
        assert indexer.pending_count() == 0

    def test_modelskifte_goer_alle_vektorer_foraeldede(self, indexed_session):
        """Et halvt indeks fra én model og et halvt fra en anden ville give
        resultater ingen kunne forklare."""
        other = HashingEmbeddingProvider(dimensions=64, model_name="hashing-v2")
        indexer = EmbeddingIndexer(indexed_session, other)

        embedded = indexed_session.scalar(
            select(Document.id).where(Document.chunk_count > 0).limit(1)
        )
        assert embedded is not None
        assert indexer.pending_count() > 0

    def test_reset_sletter_alt_og_bygger_forfra(self, indexed_session, embedding_provider):
        indexer = EmbeddingIndexer(indexed_session, embedding_provider)
        report = indexer.index_pending(reset=True)
        assert report.chunks_deleted > 0
        assert report.chunks_written > 0
        assert indexer.pending_count() == 0

    def test_daekning_rapporteres(self, indexed_session, embedding_provider):
        coverage = EmbeddingIndexer(indexed_session, embedding_provider).coverage()

        assert coverage["embedded_documents"] > 0
        assert coverage["chunks"] > 0
        assert coverage["pending_documents"] == 0
        assert coverage["coverage_pct"] == pytest.approx(100.0, abs=0.1)
        # Hash-udbyderen må aldrig præsentere sig som semantisk.
        assert coverage["semantic"] is False


# ---------------------------------------------------------------------------
# Vektorsøgning
# ---------------------------------------------------------------------------


class TestVektorsoegning:
    def _backend(self, provider) -> VectorSearchBackend:
        return VectorSearchBackend(provider)

    def test_finder_dokumenter_paa_vektorlighed(self, indexed_session, embedding_provider):
        backend = self._backend(embedding_provider)
        results = backend.search(indexed_session, SearchQuery(q="brand passagerskib"))

        assert results.total >= 1
        assert results.mode == "semantic"
        titles = [hit.document.title.lower() for hit in results.hits]
        assert any("brandsikkerhed" in t for t in titles), titles

    def test_hit_bærer_lighed_og_overskrift(self, indexed_session, embedding_provider):
        backend = self._backend(embedding_provider)
        results = backend.search(indexed_session, SearchQuery(q="redningsmidler om bord"))

        assert results.hits
        hit = results.hits[0]
        assert hit.match_source == "semantic"
        assert hit.semantic_score is not None
        assert 0.0 <= hit.semantic_score <= 1.0
        assert hit.lexical_rank is None
        assert hit.snippet

    def test_kun_bedste_stykke_taeller_pr_dokument(self, indexed_session, embedding_provider):
        """Ellers ville en lang lov med én relevant paragraf blive straffet."""
        backend = self._backend(embedding_provider)
        results = backend.search(indexed_session, SearchQuery(q="skib"))

        ids = [hit.document.id for hit in results.hits]
        assert len(ids) == len(set(ids)), "et dokument må kun optræde én gang"

    def test_filtre_gaelder_ogsaa_semantisk(self, indexed_session, embedding_provider):
        """Filtrene findes ét sted og skal virke ens på begge sider."""
        backend = self._backend(embedding_provider)
        results = backend.search(
            indexed_session,
            SearchQuery(q="skib sikkerhed", document_types=["Bekendtgørelse"]),
        )
        assert all(hit.document.document_type == "Bekendtgørelse" for hit in results.hits)

        none_expected = backend.search(
            indexed_session,
            SearchQuery(q="skib sikkerhed", document_types=["Findes-Ikke"]),
        )
        assert none_expected.total == 0

    def test_uden_soegestreng_falder_tilbage_med_besked(self, indexed_session, embedding_provider):
        """Der er intet at ligne uden en søgning — og brugeren får det at vide."""
        backend = self._backend(embedding_provider)
        results = backend.search(indexed_session, SearchQuery(q=None))

        assert results.mode == "lexical"
        assert results.notice

    def test_sideinddeling_over_kandidater(self, indexed_session, embedding_provider):
        backend = self._backend(embedding_provider)
        first = backend.search(indexed_session, SearchQuery(q="skib", page=1, page_size=2))
        second = backend.search(indexed_session, SearchQuery(q="skib", page=2, page_size=2))

        assert first.total == second.total
        if first.total > 2:
            assert {h.document.id for h in first.hits} & {h.document.id for h in second.hits} == set()

    def test_lignende_dokumenter(self, indexed_session, embedding_provider):
        backend = self._backend(embedding_provider)
        document = _document(indexed_session, "brandsikkerhed")

        matches = backend.similar_to_document(indexed_session, document.id, limit=5)

        assert all(m.document_id != document.id for m in matches), "må ikke ligne sig selv"
        assert all(0.0 <= m.similarity <= 1.0001 for m in matches)

    def test_lignende_dokumenter_for_uvektoriseret_giver_tom_liste(
        self, seeded_session, embedding_provider
    ):
        """Ikke en fejl — indekset er bare ikke bygget endnu."""
        backend = self._backend(embedding_provider)
        assert backend.similar_to_document(seeded_session, 1, limit=5) == []


# ---------------------------------------------------------------------------
# Hybrid
# ---------------------------------------------------------------------------


class TestHybrid:
    def _backend(self, session, provider) -> HybridSearchBackend:
        return HybridSearchBackend(get_lexical_backend(session), VectorSearchBackend(provider))

    def test_hybrid_finder_mindst_det_leksikalske_finder(
        self, indexed_session, embedding_provider
    ):
        """Sammensmeltningen må aldrig tabe et eksakt match på gulvet."""
        lexical = get_lexical_backend(indexed_session).search(
            indexed_session, SearchQuery(q="brand passagerskib", page_size=50)
        )
        hybrid = self._backend(indexed_session, embedding_provider).search(
            indexed_session, SearchQuery(q="brand passagerskib", page_size=50)
        )

        lexical_ids = {hit.document.id for hit in lexical.hits}
        hybrid_ids = {hit.document.id for hit in hybrid.hits}
        assert lexical_ids <= hybrid_ids

    def test_hybrid_markerer_hvordan_hvert_hit_blev_fundet(
        self, indexed_session, embedding_provider
    ):
        results = self._backend(indexed_session, embedding_provider).search(
            indexed_session, SearchQuery(q="brandsikkerhed passagerskibe", page_size=20)
        )

        assert results.mode == "hybrid"
        assert results.hits
        assert all(h.match_source in {"lexical", "semantic", "both"} for h in results.hits)
        # Mindst ét dokument skal være fundet af begge sider, ellers
        # smelter vi ikke rigtigt sammen.
        assert any(h.match_source == "both" for h in results.hits)

    def test_dokument_fundet_af_begge_rangerer_over_et_fundet_af_en(
        self, indexed_session, embedding_provider
    ):
        """Selve pointen med RRF: enighed mellem de to slags vejer tungt."""
        results = self._backend(indexed_session, embedding_provider).search(
            indexed_session, SearchQuery(q="brandsikkerhed passagerskibe", page_size=20)
        )
        sources = [hit.match_source for hit in results.hits]
        if "both" in sources and len(set(sources)) > 1:
            assert sources.index("both") < max(
                sources.index(s) for s in sources if s != "both"
            )

    def test_uden_soegestreng_er_hybrid_en_filtreret_liste(
        self, indexed_session, embedding_provider
    ):
        results = self._backend(indexed_session, embedding_provider).search(
            indexed_session, SearchQuery(q=None, is_maritime=True)
        )
        assert results.mode == "lexical"
        assert results.total > 0

    def test_semantisk_fejl_falder_tilbage_frem_for_at_vaelte(self, indexed_session):
        """En søgning må ikke gå ned, fordi modellen ikke kan indlæses."""

        class BrokenProvider:
            info = HashingEmbeddingProvider(dimensions=64).info

            def embed_query(self, text):  # noqa: ARG002
                raise RuntimeError("modellen er væk")

            def embed_passages(self, texts):  # noqa: ARG002
                raise RuntimeError("modellen er væk")

        backend = HybridSearchBackend(
            get_lexical_backend(indexed_session), VectorSearchBackend(BrokenProvider())
        )
        results = backend.search(indexed_session, SearchQuery(q="brand"))

        assert results.mode == "lexical"
        assert results.notice
        assert results.total >= 1


# ---------------------------------------------------------------------------
# Tilstandsvalg
# ---------------------------------------------------------------------------


class TestTilstandsvalg:
    def test_uden_vektorer_nedgraderes_der_med_besked(self, seeded_session):
        """Nedgraderingen må aldrig være tavs: en bruger, der tror der blev
        søgt på betydning, kan ellers fejlagtigt konkludere at der ingen
        regler findes om emnet."""
        mode, notice = resolve_search_mode(seeded_session, "hybrid")

        assert mode == "lexical"
        assert notice is not None
        assert "embed run" in notice

    def test_med_vektorer_leveres_den_oenskede_tilstand(self, indexed_session):
        mode, notice = resolve_search_mode(indexed_session, "hybrid")
        assert mode == "hybrid"
        assert notice is None

    def test_leksikalsk_spoerger_aldrig_efter_vektorer(self, seeded_session):
        mode, notice = resolve_search_mode(seeded_session, "lexical")
        assert (mode, notice) == ("lexical", None)

    def test_ukendt_tilstand_bliver_leksikalsk(self, indexed_session):
        mode, _ = resolve_search_mode(indexed_session, "telepati")
        assert mode == "lexical"
