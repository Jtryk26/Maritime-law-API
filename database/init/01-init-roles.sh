#!/bin/bash
set -e

# =============================================================================
# Database Least Privilege Setup for Maritim Lovdatabase
# =============================================================================
# Ejer ($POSTGRES_USER / maritim) opretter skema og tabeller ved migrationer.
# Runtime-brugeren (maritim_runtime) anvendes af den offentlige web-backend og har
# udelukkende SELECT-rettigheder på produktionsdata uden sekvensmutation.
# =============================================================================

RUNTIME_USER="${POSTGRES_RUNTIME_USER:-maritim_runtime}"
RUNTIME_PASS="${POSTGRES_RUNTIME_PASSWORD:-maritim_runtime_pass}"
DB_NAME="${POSTGRES_DB:-maritim}"

echo "Configuring least privilege role: $RUNTIME_USER on $DB_NAME..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB_NAME" <<-EOSQL
    -- Opret runtime-rollen, hvis den ikke findes
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$RUNTIME_USER') THEN
            CREATE ROLE $RUNTIME_USER WITH LOGIN PASSWORD '$RUNTIME_PASS';
        ELSE
            ALTER ROLE $RUNTIME_USER WITH PASSWORD '$RUNTIME_PASS';
        END IF;
    END
    \$\$;

    -- Forbindelses- og skema-rettigheder
    GRANT CONNECT ON DATABASE "$DB_NAME" TO "$RUNTIME_USER";
    GRANT USAGE ON SCHEMA public TO "$RUNTIME_USER";

    -- Skrivebeskyttelse: Fratag eksplicit rettigheder til at oprette eller ændre tabeller i skemaet
    REVOKE CREATE ON SCHEMA public FROM "$RUNTIME_USER";

    -- Læserettigheder på eksisterende tabeller
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO "$RUNTIME_USER";

    -- Fratag udtrykkeligt alle sekvensrettigheder (forhindrer nextval()-tilstandsændring)
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "$RUNTIME_USER";

    -- Læserettigheder på alle fremtidige tabeller oprettet af ejeren
    ALTER DEFAULT PRIVILEGES FOR ROLE "$POSTGRES_USER" IN SCHEMA public GRANT SELECT ON TABLES TO "$RUNTIME_USER";
    ALTER DEFAULT PRIVILEGES FOR ROLE "$POSTGRES_USER" IN SCHEMA public REVOKE ALL ON SEQUENCES FROM "$RUNTIME_USER";
EOSQL

echo "Role $RUNTIME_USER configured successfully with strict read-only permissions."
