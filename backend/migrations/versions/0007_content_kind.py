"""``documents.content_kind`` — har vi lovteksten, eller kun metadata?

Baggrund
========
En diagnostik af produktionskorpusset viste, at kun 973 af 3.411 maritime
dokumenter indeholder paragraftegnet ``§``. Den nærliggende forklaring
var, at parseren tabte teksten. Kontrol direkte mod kilden viste noget
andet: ELI-XML for fx ``A18650999930`` og ``B19300001605`` svarer HTTP
200 med et ``<Dokument>``, der kun har ``<Meta>``. Kilden HAR ikke
fuldtekst for de dokumenter.

De to tilfælde kræver hver sin handling, og indtil nu kunne de ikke
skelnes i databasen:

* ``metadata_only`` — kilden har ingen tekst. Genimport hjælper ikke.
* ``text_without_paragraph_sign`` — der er tekst, men ingen paragraffer.
  Her ER en genimport eller en parserrettelse relevant.
* ``full_text`` — tekst med paragraffer. Kun disse kan bære
  anvendelighedsregler.
* ``empty`` — ingen tekst gemt overhovedet.

Eksisterende rækker
===================
Kolonnen er nullable og fyldes her med en deterministisk vurdering af den
tekst, der allerede ligger i den aktuelle version. Bemærk begrænsningen:
``metadata_only`` kan IKKE afgøres ud fra gemt tekst alene, fordi den
gamle parser skrev metadatateksten ind i indholdsfeltet. De rækker lander
derfor i ``text_without_paragraph_sign`` og kan først skelnes efter en
genhentning fra kilden. Efter migrationen::

    python -m app.cli content status     # fordelingen
    python -m app.cli content classify   # kør vurderingen igen

Revision ID: 0007_content_kind
Revises: 0006_applicability
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_content_kind"
down_revision = "0006_applicability"
branch_labels = None
depends_on = None


#: Deterministisk vurdering af allerede gemt tekst. Bevidst holdt i ren
#: SQL med en korreleret underforespørgsel, så den virker ens på
#: PostgreSQL og SQLite uden at læse hele korpusset ind i Python.
_BACKFILL = """
UPDATE documents
   SET content_kind = COALESCE((
        SELECT CASE
                 WHEN v.content IS NULL OR TRIM(v.content) = '' THEN 'empty'
                 WHEN v.content LIKE '%§%' THEN 'full_text'
                 ELSE 'text_without_paragraph_sign'
               END
          FROM document_versions v
         WHERE v.id = documents.current_version_id
   ), 'empty')
 WHERE content_kind IS NULL
"""


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("content_kind", sa.String(length=48), nullable=True))

    op.create_index("ix_documents_content_kind", "documents", ["content_kind"])

    # Driften skal kunne se fordelingen umiddelbart efter opgraderingen,
    # ikke først efter næste import.
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_index("ix_documents_content_kind", table_name="documents")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("content_kind")
