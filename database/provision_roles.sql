-- =============================================================================
-- Database Least Privilege Provisioning Script for Maritim Lovdatabase
-- =============================================================================
-- Kør som databaseejer/superbruger mod PostgreSQL databasen:
--   psql -U maritim -d maritim -f database/provision_roles.sql
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'maritim_runtime') THEN
        CREATE ROLE maritim_runtime WITH LOGIN PASSWORD 'skal_erstattes_med_staerkt_kodeord';
    END IF;
END
$$;

-- 1. Forbindelses- og skemarettigheder
GRANT CONNECT ON DATABASE maritim TO maritim_runtime;
GRANT USAGE ON SCHEMA public TO maritim_runtime;

-- 2. Forhindr enhver oprettelse eller sletning af tabeller/funktioner i skemaet
REVOKE CREATE ON SCHEMA public FROM maritim_runtime;

-- 3. Ren læseadgang til samtlige tabeller (SELECT)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO maritim_runtime;

-- 4. Fratag udtrykkeligt alle sekvensrettigheder (forhindrer nextval() tilstandsændring)
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM maritim_runtime;

-- 5. Standardprivilegier for fremtidige tabeller oprettet af ejeren
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO maritim_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM maritim_runtime;
