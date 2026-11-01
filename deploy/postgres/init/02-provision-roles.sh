#!/bin/bash
set -euo pipefail

# ==============================================================================
# MediaForge / GrabItDown — PostgreSQL Runtime Role Provisioning Script
# Executed automatically after 01-init-database.sh on first cluster init.
# Provisions the least-privilege runtime role 'gid_app' (DML only, no DDL).
# ==============================================================================

DB_NAME="${POSTGRES_DB:-grabitdown}"
MIGRATION_USER="${GID_DB_USER:-gid_user}"
APP_USER="${GID_APP_USER:-gid_app}"
APP_PASS="${GID_APP_PASSWORD:?Application runtime password GID_APP_PASSWORD must be set}"
APP_SCHEMA="${GID_DB_SCHEMA:-gid}"
ADMIN_USER="${POSTGRES_USER:-postgres}"

echo ">>> [MediaForge] Provisioning runtime role '${APP_USER}' on database '${DB_NAME}'..."

psql -v ON_ERROR_STOP=1 --username "${ADMIN_USER}" --dbname "${DB_NAME}" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${APP_USER}') THEN
            CREATE ROLE "${APP_USER}" WITH LOGIN PASSWORD '${APP_PASS}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
        ELSE
            ALTER ROLE "${APP_USER}" WITH PASSWORD '${APP_PASS}';
        END IF;
    END
    \$\$;

    -- Revoke database-level creation privileges
    REVOKE ALL ON DATABASE "${DB_NAME}" FROM "${APP_USER}";
    GRANT CONNECT, TEMPORARY ON DATABASE "${DB_NAME}" TO "${APP_USER}";

    -- Ensure public schema is locked down
    REVOKE ALL ON SCHEMA public FROM "${APP_USER}";

    -- Grant USAGE on application schema (allows object resolution, denies DDL creation)
    GRANT USAGE ON SCHEMA "${APP_SCHEMA}" TO "${APP_USER}";
    REVOKE CREATE ON SCHEMA "${APP_SCHEMA}" FROM "${APP_USER}";

    -- Grant DML privileges on existing tables
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "${APP_SCHEMA}" TO "${APP_USER}";

    -- Grant sequence privileges
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "${APP_SCHEMA}" TO "${APP_USER}";

    -- Configure default privileges for future objects created by the migration role
    ALTER DEFAULT PRIVILEGES FOR ROLE "${MIGRATION_USER}" IN SCHEMA "${APP_SCHEMA}"
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "${APP_USER}";
    ALTER DEFAULT PRIVILEGES FOR ROLE "${MIGRATION_USER}" IN SCHEMA "${APP_SCHEMA}"
        GRANT USAGE, SELECT ON SEQUENCES TO "${APP_USER}";

    -- Set default search path
    ALTER ROLE "${APP_USER}" SET search_path TO "${APP_SCHEMA}", public;
EOSQL

echo ">>> [MediaForge] Runtime role '${APP_USER}' successfully configured with least-privilege DML access."
