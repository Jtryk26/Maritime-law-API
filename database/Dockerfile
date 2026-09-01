# PostgreSQL 16 med pgvector og den locale den eksisterende databaseklynge
# blev oprettet med. Uden den afviser PostgreSQL postgresql.conf ved opstart.
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends locales \
    && sed -i 's/^# *\(da_DK.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen da_DK.UTF-8 \
    && locale -a | grep -qx 'da_DK.utf8' \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=da_DK.utf8

COPY database/init/ /docker-entrypoint-initdb.d/
RUN chmod +x /docker-entrypoint-initdb.d/*.sh || true
