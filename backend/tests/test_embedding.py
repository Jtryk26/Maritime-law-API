"""Test af vektorprimitiver, chunking og embedding-udbydere.

Bemærk hvad der IKKE testes her: modelkvalitet. Testene kører med
hash-udbyderen, som er deterministisk og uden semantik, og en test der
påstod at "livbåd" ligner "redningsflåde" ville måle hash-støj frem for
sprogforståelse. Kvaliteten af den rigtige model hører til i en
evaluering mod et sæt kendte søgninger — ikke i en enhedstest.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.vectors import (
    cosine_similarity,
    normalize,
    pack_vector,
    to_pgvector_literal,
    unpack_matrix,
    unpack_vector,
    vector_dimensions,
)
from app.services.embedding import (
    ChunkingConfig,
    EmbeddingUnavailableError,
    HashingEmbeddingProvider,
    build_embedding_provider,
    chunk_document,
)


# ---------------------------------------------------------------------------
# Vektorprimitiver
# ---------------------------------------------------------------------------


class TestVektorer:
    def test_normalisering_giver_enhedslaengde(self):
        vector = normalize([3.0, 4.0])
        assert pytest.approx(float(np.linalg.norm(vector)), abs=1e-6) == 1.0

    def test_nulvektor_giver_ikke_division_med_nul(self):
        """Et stykke med kun tegnsætning kan give en nulvektor."""
        vector = normalize([0.0, 0.0, 0.0])
        assert np.all(vector == 0.0)
        assert np.isfinite(vector).all()

    def test_pakning_er_tabsfri_nok_og_normaliseret(self):
        original = [1.0, 2.0, 2.0]
        blob = pack_vector(original)
        restored = unpack_vector(blob)

        assert vector_dimensions(blob) == 3
        assert pytest.approx(float(np.linalg.norm(restored)), abs=1e-6) == 1.0
        # Retningen er bevaret, kun længden er sat til 1.
        assert pytest.approx(float(restored[1] / restored[0]), abs=1e-5) == 2.0

    def test_ugyldig_blob_giver_none_ikke_exception(self):
        assert unpack_vector(None) is None
        assert unpack_vector(b"") is None
        assert unpack_vector(b"abc") is None  # ikke deleligt med 4

    def test_raekker_med_forkert_laengde_bliver_nul(self):
        """Sker ved modelskifte uden genopbygning. Må ikke vælte en søgning."""
        good = pack_vector([1.0, 0.0, 0.0, 0.0])
        bad = pack_vector([1.0, 0.0])
        matrix = unpack_matrix([good, bad, None], dimensions=4)

        assert matrix.shape == (3, 4)
        assert matrix[0][0] == pytest.approx(1.0)
        assert np.all(matrix[1] == 0.0)
        assert np.all(matrix[2] == 0.0)

    def test_cosinus_paa_normaliserede_vektorer(self):
        query = normalize([1.0, 0.0])
        matrix = np.vstack([normalize([1.0, 0.0]), normalize([0.0, 1.0]), normalize([1.0, 1.0])])
        similarities = cosine_similarity(query, matrix)

        assert similarities[0] == pytest.approx(1.0, abs=1e-6)
        assert similarities[1] == pytest.approx(0.0, abs=1e-6)
        assert similarities[2] == pytest.approx(0.7071, abs=1e-3)

    def test_pgvector_litteral_er_normaliseret(self):
        literal = to_pgvector_literal([3.0, 4.0])
        assert literal.startswith("[") and literal.endswith("]")
        values = [float(v) for v in literal[1:-1].split(",")]
        assert pytest.approx(sum(v * v for v in values), abs=1e-5) == 1.0


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


LOVTEKST = """Kapitel 1
Anvendelsesområde

§ 1. Denne bekendtgørelse finder anvendelse på passagerskibe, der sejler i
indenrigsfart, og som er certificeret til at medføre flere end 12 passagerer.
Bekendtgørelsen gælder tillige for hurtigfærger uanset længde og for
lastskibe over 500 bruttotonnage i international fart.

Stk. 2. Reglerne i stk. 1 gælder ikke for fiskeskibe under 15 meter, for
fritidsfartøjer eller for skibe, der udelukkende anvendes til
myndighedsudøvelse i danske farvande.

§ 2. Skibsføreren har det overordnede ansvar for, at brandsikkerheden om
bord er i overensstemmelse med denne bekendtgørelse og med SOLAS kapitel
II-2. Rederiet skal sikre, at der findes en opdateret brandplan.

Kapitel 2
Brandslukningsudstyr

§ 3. Der skal forefindes fast installerede brandslukningsanlæg i
maskinrummet, i lastrum og i rum, hvor der opbevares brandfarlige væsker.
Anlæggene skal efterses mindst én gang årligt af en godkendt virksomhed.
"""


class TestChunking:
    def test_tom_tekst_giver_ingen_stykker(self):
        assert chunk_document("") == []
        assert chunk_document("   \n  ") == []

    def test_lovtekst_deles_i_flere_stykker(self):
        config = ChunkingConfig(target_chars=300, max_chars=500, overlap_chars=40, min_chars=40)
        chunks = chunk_document(LOVTEKST, config)

        assert len(chunks) >= 3
        assert all(chunk.content.strip() for chunk in chunks)
        # Indeks er fortløbende fra nul.
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_snit_falder_paa_paragraf_eller_kapitel(self):
        """Det er hele pointen: et stykke skal helst være én bestemmelse."""
        config = ChunkingConfig(target_chars=300, max_chars=500, overlap_chars=0, min_chars=40)
        chunks = chunk_document(LOVTEKST, config)

        starts = [c.content.lstrip()[:12] for c in chunks[1:]]
        assert any(s.startswith(("§", "Kapitel", "Stk.")) for s in starts), starts

    def test_overskrift_foelger_med_stykket(self):
        config = ChunkingConfig(target_chars=250, max_chars=400, overlap_chars=0, min_chars=40)
        chunks = chunk_document(LOVTEKST, config)
        headings = [c.heading for c in chunks if c.heading]

        assert headings, "mindst ét stykke skal kende sin overskrift"
        assert any(h.startswith(("§", "Kapitel")) for h in headings)

    def test_overlap_deles_mellem_nabostykker(self):
        config = ChunkingConfig(target_chars=250, max_chars=350, overlap_chars=60, min_chars=30)
        chunks = chunk_document(LOVTEKST, config)

        assert len(chunks) >= 2
        # Næste stykke begynder før det forrige sluttede.
        assert chunks[1].char_start < chunks[0].char_end

    def test_loft_pr_dokument_overholdes(self):
        config = ChunkingConfig(
            target_chars=50, max_chars=80, overlap_chars=0, min_chars=10, max_per_document=4
        )
        chunks = chunk_document(LOVTEKST * 5, config)
        assert len(chunks) == 4

    def test_meget_kort_hale_lagges_til_forrige_stykke(self):
        config = ChunkingConfig(target_chars=200, max_chars=260, overlap_chars=0, min_chars=150)
        chunks = chunk_document(LOVTEKST, config)
        assert all(len(c.content) >= 100 for c in chunks[:-1])

    def test_tekst_uden_struktur_deles_stadig(self):
        """Bilag og tabeller har ingen paragraffer. Det må ikke give uendelig løkke."""
        config = ChunkingConfig(target_chars=100, max_chars=150, overlap_chars=20, min_chars=20)
        chunks = chunk_document("abcdefghij " * 200, config)

        assert len(chunks) > 1
        assert all(len(c.content) <= 200 for c in chunks)

    def test_kontekstpraefiks_indeholder_titel_og_overskrift(self):
        chunks = chunk_document(LOVTEKST, ChunkingConfig(target_chars=300, max_chars=500))
        text = chunks[0].embedding_text("Bekendtgørelse om brandsikkerhed")

        assert "Bekendtgørelse om brandsikkerhed" in text
        # Selve indholdet er uændret — præfikset er kun til modellen.
        assert chunks[0].content in text
        assert "Bekendtgørelse om brandsikkerhed" not in chunks[0].content


# ---------------------------------------------------------------------------
# Udbydere
# ---------------------------------------------------------------------------


class TestHashUdbyder:
    def test_er_deterministisk(self):
        provider = HashingEmbeddingProvider(dimensions=64)
        first = provider.embed_query("brandsikkerhed på passagerskibe")
        second = provider.embed_query("brandsikkerhed på passagerskibe")
        assert np.allclose(first, second)

    def test_vektorer_er_normaliserede_og_har_rigtig_laengde(self):
        provider = HashingEmbeddingProvider(dimensions=64)
        vectors = provider.embed_passages(["skib", "havn", "redningsflåde"])

        assert vectors.shape == (3, 64)
        for vector in vectors:
            assert pytest.approx(float(np.linalg.norm(vector)), abs=1e-5) == 1.0

    def test_faelles_ord_giver_hoejere_lighed_end_ingen(self):
        """Det eneste hash-udbyderen kan — og alt rørføringen behøver."""
        provider = HashingEmbeddingProvider(dimensions=256)
        query = provider.embed_query("brand på passagerskib")
        related = provider.embed_query("brand om bord på passagerskib")
        unrelated = provider.embed_query("kommunale biblioteker og dagtilbud")

        assert float(query @ related) > float(query @ unrelated)

    def test_foldning_goer_danske_tegn_ligegyldige(self):
        provider = HashingEmbeddingProvider(dimensions=128)
        with_danish = provider.embed_query("søulykke")
        folded = provider.embed_query("soeulykke")
        assert np.allclose(with_danish, folded)

    def test_tom_tekst_giver_nulvektor_der_ikke_matcher(self):
        provider = HashingEmbeddingProvider(dimensions=32)
        vector = provider.embed_query("")
        assert np.all(vector == 0.0)

    def test_markerer_sig_selv_som_ikke_semantisk(self):
        """Brugerfladen skal kunne se det. Ellers ville den love for meget."""
        assert HashingEmbeddingProvider(dimensions=32).info.semantic is False


class TestUdbydervalg:
    def test_ukendt_udbyder_afvises(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setenv("EMBEDDING_PROVIDER", "magi")
        get_settings.cache_clear()
        try:
            with pytest.raises(EmbeddingUnavailableError, match="Ukendt"):
                build_embedding_provider()
        finally:
            get_settings.cache_clear()

    def test_slaaet_fra_giver_forklarende_fejl_ikke_tavs_fallback(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
        get_settings.cache_clear()
        try:
            with pytest.raises(EmbeddingUnavailableError, match="slået fra"):
                build_embedding_provider()
        finally:
            get_settings.cache_clear()

    def test_api_udbyder_uden_url_fejler_frem_for_at_gaette(self, monkeypatch):
        """Et gættet endpoint ville ligne en færdig integration."""
        from app.core.config import get_settings

        monkeypatch.setenv("EMBEDDING_PROVIDER", "api")
        monkeypatch.delenv("EMBEDDING_API_URL", raising=False)
        get_settings.cache_clear()
        try:
            with pytest.raises(EmbeddingUnavailableError, match="EMBEDDING_API_URL"):
                build_embedding_provider()
        finally:
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Den lokale udbyders adapterlogik
# ---------------------------------------------------------------------------


class _FakeSentenceTransformer:
    """Står i stedet for den rigtige model.

    Modellen kan ikke hentes i et lukket testmiljø, og at hente 500 MB i
    en enhedstest ville være forkert uanset. Det vi kan og skal afprøve,
    er vores egen adapter: at præfikserne sættes på, at dimensionen
    kontrolleres, og at en model der ikke kan indlæses giver en forklarende
    fejl frem for en ImportError langt inde i en søgning.
    """

    #: Sidste tekster modellen fik. Testene læser den.
    last_inputs: list[str] = []

    def __init__(self, model_name, device=None, cache_folder=None, dimensions=4):
        self.model_name = model_name
        self.device = device
        self.cache_folder = cache_folder
        self._dimensions = dimensions

    def get_sentence_embedding_dimension(self):
        return self._dimensions

    def encode(self, texts, **kwargs):
        type(self).last_inputs = list(texts)
        rows = []
        for index, text in enumerate(texts):
            row = np.zeros(self._dimensions, dtype=np.float32)
            row[index % self._dimensions] = 1.0
            row[(len(text) or 1) % self._dimensions] += 0.5
            rows.append(row / np.linalg.norm(row))
        return np.vstack(rows)


@pytest.fixture()
def fake_sentence_transformers(monkeypatch):
    """Lægger et stand-in for `sentence_transformers` i sys.modules."""
    import sys
    import types

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = _FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    _FakeSentenceTransformer.last_inputs = []
    return module


class TestLokalUdbyder:
    def _provider(self, **kwargs):
        from app.services.embedding import LocalEmbeddingProvider

        defaults = dict(
            model_name="intfloat/multilingual-e5-small",
            dimensions=4,
            query_prefix="query: ",
            passage_prefix="passage: ",
        )
        defaults.update(kwargs)
        return LocalEmbeddingProvider(**defaults)

    def test_e5_praefikser_saettes_paa(self, fake_sentence_transformers):
        """Uden præfikserne falder E5's kvalitet mærkbart."""
        provider = self._provider()

        provider.embed_query("brandsikkerhed")
        assert _FakeSentenceTransformer.last_inputs == ["query: brandsikkerhed"]

        provider.embed_passages(["§ 1. Skibet skal ..."])
        assert _FakeSentenceTransformer.last_inputs == ["passage: § 1. Skibet skal ..."]

    def test_forkert_dimension_opdages_ved_indlaesning(self, fake_sentence_transformers):
        """En konfigurationsfejl, ikke en driftsfejl: alle gemte vektorer
        ville blive ubrugelige sammen med de nye."""
        from app.services.embedding import EmbeddingDimensionError

        provider = self._provider(dimensions=999)
        with pytest.raises(EmbeddingDimensionError, match="999"):
            provider.warmup()

    def test_manglende_pakke_giver_forklarende_fejl(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)

        provider = self._provider()
        with pytest.raises(EmbeddingUnavailableError, match="requirements-embedding"):
            provider.warmup()

    def test_e5_faar_en_foreslaaet_graense_ukendte_modeller_faar_ingen(
        self, fake_sentence_transformers
    ):
        """Et gæt på en ukendt models skala ville kassere alt eller intet."""
        assert self._provider().info.suggested_min_similarity > 0.0
        assert self._provider(model_name="en-ukendt/model").info.suggested_min_similarity == 0.0

    def test_modellen_indlaeses_kun_en_gang(self, fake_sentence_transformers):
        provider = self._provider()
        provider.embed_query("a")
        first = provider._model
        provider.embed_query("b")
        assert provider._model is first


# ---------------------------------------------------------------------------
# HTTP-udbyderen
# ---------------------------------------------------------------------------


class TestApiUdbyder:
    def _provider(self, handler, **kwargs):
        import httpx

        from app.services.embedding import ApiEmbeddingProvider

        client = httpx.Client(transport=httpx.MockTransport(handler))
        defaults = dict(
            url="https://embeddings.example/v1/embeddings",
            model_name="test-model",
            dimensions=3,
            client=client,
        )
        defaults.update(kwargs)
        return ApiEmbeddingProvider(**defaults)

    def test_laeser_openai_formet_svar(self):
        import httpx

        def handler(request):
            return httpx.Response(
                200, json={"data": [{"embedding": [1.0, 0.0, 0.0]}, {"embedding": [0.0, 2.0, 0.0]}]}
            )

        vectors = self._provider(handler).embed_passages(["a", "b"])
        assert vectors.shape == (2, 3)
        # Udbydere normaliserer ikke nødvendigvis selv.
        assert vectors[1][1] == pytest.approx(1.0)

    def test_laeser_ogsaa_selvhostet_format(self):
        import httpx

        def handler(request):
            return httpx.Response(200, json={"embeddings": [[0.0, 0.0, 3.0]]})

        vector = self._provider(handler).embed_query("skib")
        assert vector[2] == pytest.approx(1.0)

    def test_permanent_fejl_forsoeges_ikke_igen(self):
        import httpx

        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401, text="unauthorized")

        with pytest.raises(EmbeddingUnavailableError, match="401"):
            self._provider(handler).embed_query("skib")
        assert calls["n"] == 1, "en 401 bliver ikke bedre af at blive gentaget"

    def test_midlertidig_fejl_forsoeges_igen(self, monkeypatch):
        import httpx

        monkeypatch.setattr("app.services.embedding.remote.time.sleep", lambda _s: None)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0, 0.0]}]})

        vector = self._provider(handler, max_retries=3).embed_query("skib")
        assert calls["n"] == 3
        assert vector[0] == pytest.approx(1.0)

    def test_uventet_svarform_forklares(self):
        import httpx

        def handler(request):
            return httpx.Response(200, json={"resultat": "noget andet"})

        with pytest.raises(EmbeddingUnavailableError, match="Kunne ikke finde vektorer"):
            self._provider(handler).embed_query("skib")

    def test_forkert_dimension_afvises(self):
        import httpx

        from app.services.embedding import EmbeddingDimensionError

        def handler(request):
            return httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}]})

        with pytest.raises(EmbeddingDimensionError):
            self._provider(handler).embed_query("skib")
