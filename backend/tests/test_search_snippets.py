"""Uddrag på resultatlisten: korrekte også dybt i store love, uden fuld foldning.

Tre påstande:

1. **Dybe træf.** Et søgeord, der først står efter ``SNIPPET_SCAN_LIMIT``,
   skal stadig stå i resultatets uddrag — via den matchende paragraf, som
   findes i hele loven på den cachede foldede tekst.
2. **Positioner.** ``fold`` ændrer tekstens længde ("æ" → "ae", blød
   bindestreg → ""). Et uddrag skal klippes om fundet i kildeteksten, ikke
   om den position, fundet havde i den foldede tekst.
3. **Cache.** Parset struktur og foldet tekst caches pr. version under
   ``(version_id, content_hash)`` — ikke under selve teksten — og er
   afgrænset i samlet størrelse.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timezone

import pytest

from app.core.text import fold, make_snippet, normalize_whitespace
from app.services.search import SearchQuery, get_search_backend
from app.services.search.backends import SNIPPET_SCAN_LIMIT
from app.services.search.paragraphs import (
    _cache,
    _VersionCache,
    clear_structure_cache,
    locate_paragraphs,
)

TERM = "skumslukningsanlæg"
FILLER = "".join(
    f"§ {i}. Skibet skal være forsynet med det udstyr, som fremgår af bilag {i}.\n\n"
    for i in range(1, 400)
)
DEEP = f"§ 400. Fartøjet skal have et fast {TERM} i maskinrummet.\n\n"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_structure_cache()
    yield
    clear_structure_cache()


def _store(session, source_id: str, content: str, *, content_hash: str | None = None):
    from app.models import Document, DocumentVersion

    document = Document(
        source="test",
        source_id=source_id,
        title=f"Bekendtgørelse om udstyr i skibe {source_id}",
        display_title=f"Bekendtgørelse om udstyr i skibe {source_id}",
        status="Gældende",
        document_type="Bekendtgørelse",
        published_date=date(2020, 1, 1),
        is_maritime=True,
        maritime_score=95,
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        content=content,
        content_hash=content_hash or f"{document.id:064d}",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(version)
    session.flush()
    document.current_version_id = version.id
    document.search_text = fold(normalize_whitespace(content))
    document.search_title = fold(document.title)
    session.commit()
    return document


# ---------------------------------------------------------------------------
# 1. Dybe træf
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["lexical", "hybrid"])
def test_dybt_traef_giver_uddrag_med_soegeordet(session, mode):
    content = FILLER + DEEP
    assert content.find(TERM) > SNIPPET_SCAN_LIMIT
    _store(session, "DEEP-1", content)

    backend = get_search_backend(session, mode)
    hit = next(
        h for h in backend.search(session, SearchQuery(q=TERM, mode=mode)).hits
        if h.document.source_id == "DEEP-1"
    )
    assert TERM in hit.snippet
    assert hit.paragraph is not None and hit.paragraph.legal_path == "§ 400"
    assert not hit.snippet.startswith("§ 1.")


def test_uden_paragraffer_bruges_det_afgraensede_dokumentuddrag(session):
    """Et dokument uden paragrafstruktur får fortsat et uddrag — fra vinduet."""
    content = "Indledning uden paragraffer. Fartøjet skal have " + TERM + " om bord."
    _store(session, "FLAT-1", content)
    hit = get_search_backend(session, "lexical").search(
        session, SearchQuery(q=TERM, mode="lexical")
    ).hits[0]
    assert hit.paragraph is None
    assert TERM in hit.snippet


def test_gennemsyn_uden_soegeord_viser_dokumentets_begyndelse(session):
    _store(session, "BROWSE-1", FILLER + DEEP)
    hit = get_search_backend(session, "lexical").search(session, SearchQuery(q=None)).hits[0]
    assert hit.snippet.startswith("§ 1. Skibet skal være forsynet")


# ---------------------------------------------------------------------------
# 2. Positioner i kilde- og foldet tekst
# ---------------------------------------------------------------------------


def test_mange_aeoeaa_foer_traeffet_flytter_ikke_vinduet():
    snippet = make_snippet("æøå " * 3000 + TERM + " slut.", [TERM])
    assert TERM in snippet


def test_bloede_bindestreger_goer_den_foldede_tekst_kortere():
    """Blød bindestreg foldes til "", så den foldede tekst bliver *kortere*."""
    text = ("ski­bs­fø­rer " * 2000) + TERM
    assert TERM in make_snippet(text, [TERM])


def test_dekomponerede_tegn_normaliseres_foer_klipning():
    decomposed = "å" * 4000  # "å" som a + kombinerende ring
    snippet = make_snippet(decomposed + " " + TERM, [TERM])
    assert TERM in snippet


def test_tegn_som_lower_forlaenger_haandteres():
    """"İ".lower() er to tegn. Ingen særregel; omregningen er generel."""
    snippet = make_snippet("İ" * 3000 + " " + TERM, [TERM])
    assert TERM in snippet


def test_tilfaeldig_blanding_af_laengdeaendrende_tegn():
    alphabet = list("abcdefg ") + ["æ", "Ø", "å", "ü", "­", "é", "İ", "å", "—"]
    rng = random.Random(846)
    for _ in range(200):
        prefix = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 3000)))
        suffix = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 500)))
        snippet = make_snippet(f"{prefix} {TERM} {suffix}", [TERM])
        assert TERM in snippet, prefix[-40:]


def test_soegeord_med_aeoeaa_findes_som_skrevet():
    text = "x " * 5000 + "Søfartsstyrelsen fører tilsyn."
    assert "Søfartsstyrelsen" in make_snippet(text, ["soefartsstyrelsen"])
    assert "Søfartsstyrelsen" in make_snippet(text, ["Søfartsstyrelsen"])


# ---------------------------------------------------------------------------
# 3. Cache
# ---------------------------------------------------------------------------


def test_cachen_noegles_paa_version_og_hash_ikke_tekst(session):
    document = _store(session, "CACHE-1", FILLER + DEEP)
    locate_paragraphs(session, [document], [TERM])
    keys = list(_cache._entries)
    assert keys == [(document.current_version_id, document.current_version.content_hash)]
    assert all(isinstance(part, (int, str)) and len(str(part)) <= 64 for part in keys[0])


def test_samme_version_parses_og_foldes_kun_en_gang(session, monkeypatch):
    import app.services.search.paragraphs as paragraphs

    calls = {"parse": 0}
    real_parse = paragraphs.parse_legal_structure

    def counting_parse(content):
        calls["parse"] += 1
        return real_parse(content)

    monkeypatch.setattr(paragraphs, "parse_legal_structure", counting_parse)
    document = _store(session, "CACHE-2", FILLER + DEEP)
    for _ in range(3):
        locate_paragraphs(session, [document], [TERM])
    assert calls["parse"] == 1
    first = _cache.folded(document.current_version)
    assert _cache.folded(document.current_version) is first


def test_ny_indholdshash_giver_ny_parsning(session):
    document = _store(session, "CACHE-3", FILLER + DEEP)
    locate_paragraphs(session, [document], [TERM])
    document.current_version.content_hash = "f" * 64
    locate_paragraphs(session, [document], [TERM])
    assert len(_cache) == 2


def test_cachen_er_afgraenset_i_samlet_stoerrelse():
    class Version:
        def __init__(self, vid: int, content: str) -> None:
            self.id, self.content, self.content_hash = vid, content, f"{vid:064d}"

    cache = _VersionCache(max_entries=100, budget_chars=10_000)
    for vid in range(1, 21):
        cache.get(Version(vid, "§ 1. Tekst. " * 300))  # ~3.600 tegn
    assert cache.size <= 10_000
    assert len(cache) == 2
    assert (20, f"{20:064d}") in cache._entries  # den nyeste overlever


# ---------------------------------------------------------------------------
# 4. Regressioner
# ---------------------------------------------------------------------------


def test_uddrag_dannes_kun_for_de_valgte_paragraffer(session, monkeypatch):
    import app.services.search.paragraphs as paragraphs

    calls = {"n": 0}
    real = paragraphs.make_snippet

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(paragraphs, "make_snippet", counting)
    document = _store(session, "SEL-1", FILLER)  # "bilag" står i alle 399 paragraffer
    found = locate_paragraphs(session, [document], ["bilag"], per_document=3)
    assert len(found[document.id]) == 3
    assert calls["n"] == 3


def test_paragrafvalg_er_uaendret(session):
    """Samme rangering som før: højest score, ved lighed den første paragraf."""
    document = _store(session, "SEL-2", FILLER + DEEP)
    found = locate_paragraphs(session, [document], ["bilag"], per_document=2)
    assert [hit.legal_path for hit in found[document.id]] == ["§ 1", "§ 2"]
    deep = locate_paragraphs(session, [document], [TERM])
    assert deep[document.id][0].legal_path == "§ 400"
    assert TERM in deep[document.id][0].snippet
