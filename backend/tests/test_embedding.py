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

        # Det præcise antal afhænger af hvor §-grænserne falder — snittet
        # lægges på strukturen frem for på en tegntælling, så et stykke
        # gerne må blive større end `target_chars` for at ramme en
        # paragraf. Det der skal gælde, er at teksten BLIVER delt.
        assert len(chunks) >= 2
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


class TestChunkIntegritet:
    """Værn mod at chunkeren taber eller dublerer lovtekst.

    Testene her er skrevet efter at to fejl blev fundet ved netop den
    slags kontrol:

    * En for kort forløber blev båret videre som en STRENG og derefter
      sat sammen med det næste stykke — som på grund af overlap begyndte
      inde i den. Samme passage stod dermed to gange i samme stykke,
      skåret midt over.
    * Et for kort stykke blev føjet til det forrige ved at lægge
      strengene sammen. Med overlap var de to ikke naboer, og resultatet
      var et stykke, der ikke fandtes i kilden.

    Begge var usynlige i en almindelig gennemlæsning af outputtet og
    ville have forgiftet vektorerne uden at nogen bemærkede det. Derfor
    kontrolleres invarianterne direkte.
    """

    CONFIGS = [
        ChunkingConfig(),
        ChunkingConfig(target_chars=300, max_chars=450, overlap_chars=50, min_chars=80),
        ChunkingConfig(target_chars=120, max_chars=180, overlap_chars=30, min_chars=40),
        ChunkingConfig(target_chars=400, max_chars=600, overlap_chars=0, min_chars=100),
    ]

    def _fixture_texts(self) -> list[str]:
        import json
        from pathlib import Path as _Path

        root = _Path(__file__).resolve().parents[2]
        payload = json.loads(
            (root / "data" / "fixtures" / "documents.json").read_text(encoding="utf-8")
        )
        return [d["content"] for d in payload["documents"] if d.get("content")]

    def _normalised(self, text: str) -> str:
        """Præcis den kæde chunkeren selv kører teksten gennem."""
        from app.core.text import strip_html
        from app.services.embedding.chunking import normalize_whitespace_preserving_breaks

        return normalize_whitespace_preserving_breaks(strip_html(text))

    @pytest.mark.parametrize("config_index", range(4))
    def test_hvert_stykke_er_et_sammenhaengende_udsnit(self, config_index):
        """`content` skal være præcis `tekst[char_start:char_end]`.

        Holder den invariant, kan et stykke hverken indeholde dubleret
        tekst eller tekst i forkert rækkefølge.
        """
        config = self.CONFIGS[config_index]
        for text in self._fixture_texts():
            normalised = self._normalised(text)
            for chunk in chunk_document(text, config):
                assert chunk.content == normalised[chunk.char_start : chunk.char_end].strip(), (
                    f"stykke {chunk.index} er ikke et udsnit af kilden"
                )

    @pytest.mark.parametrize("config_index", range(4))
    def test_ingen_lovtekst_gaar_tabt(self, config_index):
        """Hvert eneste tegn skal være dækket af mindst ét stykke.

        Tabt lovtekst er den værste fejl chunkeren kan lave: paragraffen
        findes ikke, søgningen svarer alligevel, og ingen opdager det.
        """
        config = self.CONFIGS[config_index]
        for text in self._fixture_texts():
            normalised = self._normalised(text)
            covered = bytearray(len(normalised))
            for chunk in chunk_document(text, config):
                for position in range(chunk.char_start, min(chunk.char_end, len(normalised))):
                    covered[position] = 1

            lost = [
                index
                for index, character in enumerate(normalised)
                if not covered[index] and not character.isspace()
            ]
            assert not lost, (
                f"{len(lost)} tegn er ikke dækket, første ved {lost[0]}: "
                f"{normalised[max(0, lost[0] - 40):lost[0] + 40]!r}"
            )

    def test_stykker_kommer_i_raekkefoelge(self):
        for text in self._fixture_texts():
            for config in self.CONFIGS:
                chunks = chunk_document(text, config)
                starts = [c.char_start for c in chunks]
                assert starts == sorted(starts)
                assert [c.index for c in chunks] == list(range(len(chunks)))


class TestLovadresse:
    """Kapitel OG paragraf skal følge med stykket ind i vektoren."""

    LOVTEKST = (
        "Kapitel 3\n"
        "Skibets drift\n\n"
        "§ 12. Rederiet skal sikre, at skibet er forsvarligt bemandet, og at "
        "besætningen er instrueret i sine opgaver. Skibsføreren fører tilsyn med, "
        "at instruktionerne følges, og at der føres journal over afholdte øvelser.\n\n"
        "Stk. 2. Reglerne i stk. 1 gælder ikke for lastskibe under 500 "
        "bruttotonnage i national fart.\n\n"
        "Kapitel 4\n"
        "Certifikater\n\n"
        "§ 1. Skibet skal have gyldigt sikkerhedscertifikat."
    )

    def _chunks(self):
        return chunk_document(
            self.LOVTEKST,
            ChunkingConfig(target_chars=260, max_chars=330, overlap_chars=60, min_chars=60),
        )

    def test_undtagelsen_holdes_helst_sammen_med_reglen(self):
        """Det bedste udfald: undtagelsen og reglen i samme stykke.

        Snittene søges på §-grænser før alt andet, så en paragraf med to
        stykker holdes samlet, hvis den kan være i ét stykke.
        """
        chunks = self._chunks()
        with_rule = [c for c in chunks if "§ 12." in c.content and "Stk. 2" in c.content]
        assert with_rule, "reglen og dens undtagelse blev unødigt adskilt"

    def test_adskilt_undtagelse_baerer_stadig_sin_paragraf(self):
        """Kernen i det hele.

        Kan paragraffen ikke være i ét stykke, havner undtagelsen for
        sig. Et stykke der alene siger "Stk. 2. Reglerne i stk. 1 gælder
        ikke for lastskibe under 500 BT" er ubrugeligt uden at vide
        HVILKEN regel der undtages — så skal paragraffen stå på stykket.
        """
        chunks = chunk_document(
            self.LOVTEKST,
            # Tvinger et snit inde i § 12.
            ChunkingConfig(target_chars=150, max_chars=190, overlap_chars=30, min_chars=40),
        )
        orphans = [c for c in chunks if "Stk. 2" in c.content and "§ 12." not in c.content]
        assert orphans, "konfigurationen delte ikke paragraffen — testen måler intet"

        for chunk in orphans:
            assert chunk.paragraph == "§ 12", chunk.content[:80]
            assert chunk.chapter == "Kapitel 3"
            assert "§ 12" in chunk.embedding_text("Lov om sikkerhed til søs")

    def test_nyt_kapitel_nulstiller_paragraffen(self):
        """§ 1 i kapitel 4 er ikke en fortsættelse af § 12 i kapitel 3."""
        from app.services.embedding.chunking import _context_at

        position = self.LOVTEKST.index("Kapitel 4")
        chapter, paragraph, _ = _context_at(self.LOVTEKST, position + len("Kapitel 4"))
        assert chapter == "Kapitel 4"
        assert paragraph is None

    def test_stykke_der_begynder_paa_en_paragraf_arver_ikke_den_forrige(self):
        """Fejlen der lå her: mønstret er forankret i linjestarten, så et
        snit lagt præcis på paragraffen faldt uden for skanningen, og
        stykket blev mærket med den FORRIGE paragraf."""
        text = "§ 11. Første regel om noget.\n§ 12. Anden regel om noget andet."
        chunks = chunk_document(
            text, ChunkingConfig(target_chars=28, max_chars=32, overlap_chars=0, min_chars=10)
        )
        opener = next((c for c in chunks if c.content.startswith("§ 12")), None)
        if opener is not None:
            assert opener.paragraph == "§ 12"

    def test_lovadresse_saettes_foran_den_vektoriserede_tekst(self):
        chunk = self._chunks()[0]
        embedded = chunk.embedding_text("Lov om sikkerhed til søs", "33")

        assert "Lov om sikkerhed til søs" in embedded
        assert "nr. 33" in embedded
        assert chunk.legal_path in embedded
        # Præfikset må ikke havne i selve lovteksten.
        assert "nr. 33" not in chunk.content
