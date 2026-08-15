"""Vektorprimitiver: pakning, normalisering og cosinus-lighed.

Ét sted for alt der handler om selve talrækkerne, så embedding-laget,
søgningen og søgeloggen behandler vektorer ens.

Lagringsformat
==============
En vektor gemmes portabelt som **float32 i little-endian**, altså
``4 * dim`` bytes i en BLOB-kolonne. Formatet er valgt frem for JSON
fordi det fylder omkring en fjerdedel og kan læses direkte ind i numpy
uden parsing. På PostgreSQL med pgvector findes den samme vektor
desuden i en ``vector``-kolonne, som er den der faktisk indekseres;
BLOB'en er sandheden, pgvector-kolonnen er et indeks over den.

Normalisering
=============
Alle vektorer L2-normaliseres **før** de gemmes. Så er cosinus-lighed
identisk med prikproduktet, hvilket både gør brute force-stien hurtigere
og betyder at pgvector's ``<=>`` (cosinus-afstand) og vores egen
udregning giver præcis samme rangering.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence

import numpy as np

__all__ = [
    "FLOAT_FORMAT",
    "normalize",
    "pack_vector",
    "unpack_vector",
    "unpack_matrix",
    "cosine_similarity",
    "to_pgvector_literal",
    "vector_dimensions",
]

#: numpy-dtypen der svarer til lagringsformatet. '<f4' = little-endian float32.
FLOAT_FORMAT = "<f4"


def normalize(vector: Sequence[float] | np.ndarray) -> np.ndarray:
    """L2-normaliserer en vektor.

    En nulvektor (kan opstå hvis et chunk kun indeholder tegnsætning)
    returneres uændret frem for at give division med nul. Den vil aldrig
    matche noget, hvilket er den rigtige opførsel.
    """
    array = np.asarray(vector, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(array))
    if norm == 0.0 or not np.isfinite(norm):
        return array
    return (array / norm).astype(np.float32)


def pack_vector(vector: Sequence[float] | np.ndarray) -> bytes:
    """Pakker en vektor til den portable BLOB-repræsentation.

    Normaliserer undervejs, så det er umuligt at komme til at gemme en
    unormaliseret vektor ved at gå uden om hjælperen.
    """
    return normalize(vector).astype(FLOAT_FORMAT).tobytes()


def unpack_vector(blob: bytes | memoryview | None) -> np.ndarray | None:
    """Læser én vektor tilbage. ``None`` hvis blob'en mangler eller er skæv."""
    if not blob:
        return None
    data = bytes(blob)
    if len(data) % 4 != 0:
        return None
    return np.frombuffer(data, dtype=FLOAT_FORMAT)


def unpack_matrix(blobs: Iterable[bytes | memoryview | None], dimensions: int) -> np.ndarray:
    """Læser mange vektorer ind som én (n, dim)-matrix.

    Rækker med forkert længde erstattes af nul, så de aldrig kan matche.
    Det sker i praksis kun hvis embedding-modellen er skiftet uden at
    indekset er bygget om — og da er en tavs nulrække bedre end en
    exception midt i en søgning, fordi `embed status` alligevel
    rapporterer uoverensstemmelsen.
    """
    rows: list[np.ndarray] = []
    zero = np.zeros(dimensions, dtype=np.float32)
    for blob in blobs:
        vector = unpack_vector(blob)
        if vector is None or vector.shape[0] != dimensions:
            rows.append(zero)
        else:
            rows.append(vector)
    if not rows:
        return np.zeros((0, dimensions), dtype=np.float32)
    return np.vstack(rows).astype(np.float32)


def cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Lighed mellem én normaliseret vektor og en matrix af normaliserede
    vektorer. Da alt er normaliseret er dette blot et prikprodukt."""
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    return matrix @ query.astype(np.float32)


def to_pgvector_literal(vector: Sequence[float] | np.ndarray) -> str:
    """pgvector's tekstform: ``[0.1,0.2,...]``.

    Bruges som bundet parameter med et eksplicit ``CAST(:v AS vector)``,
    så vi ikke behøver pgvector's Python-pakke for at kunne søge.
    """
    values = normalize(vector)
    return "[" + ",".join(f"{float(v):.7g}" for v in values) + "]"


def vector_dimensions(blob: bytes | memoryview | None) -> int:
    """Antal dimensioner i en pakket vektor. 0 hvis den mangler."""
    if not blob:
        return 0
    return len(bytes(blob)) // struct.calcsize("f")
