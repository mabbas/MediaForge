#!/bin/bash
set -euo pipefail

# ==============================================================================
# MediaForge / GrabItDown — PostgreSQL Initialization Script
# Executed automatically by the official postgres entrypoint on first cluster init.
# ==============================================================================

DB_NAME="${POSTGRES_DB:-grabitdown}"
APP_USER="${GID_DB_USER:-gid_user}"
APP_PASS="${GID_DB_PASSWORD:?Application database password GID_DB_PASSWORD must be set}"
APP_SCHEMA="${GID_DB_SCHEMA:-gid}"
ADMIN_USER="${POSTGRES_USER:-postgres}"

echo ">>> [MediaForge] Initializing PostgreSQL cluster..."
echo ">>> [MediaForge] Target Database: ${DB_NAME}"
echo ">>> [MediaForge] Application User: ${APP_USER}"
echo ">>> [MediaForge] Application Schema: ${APP_SCHEMA}"

# 1. Create Application Role (Non-Superuser) if not exists
echo ">>> [MediaForge] Creating or updating application role '${APP_USER}'..."
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
EOSQL

# 2. Grant Database connection privileges
echo ">>> [MediaForge] Granting database connect permissions..."
psql -v ON_ERROR_STOP=1 --username "${ADMIN_USER}" --dbname "${DB_NAME}" <<-EOSQL
    GRANT CONNECT, TEMPORARY ON DATABASE "${DB_NAME}" TO "${APP_USER}";
EOSQL

# 3. Create required extensions
echo ">>> [MediaForge] Enabling uuid-ossp and pgcrypto extensions..."
psql -v ON_ERROR_STOP=1 --username "${ADMIN_USER}" --dbname "${DB_NAME}" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";
EOSQL

# 4. Create and authorize application schema
echo ">>> [MediaForge] Configuring schema '${APP_SCHEMA}'..."
psql -v ON_ERROR_STOP=1 --username "${ADMIN_USER}" --dbname "${DB_NAME}" <<-EOSQL
    CREATE SCHEMA IF NOT EXISTS "${APP_SCHEMA}" AUTHORIZATION "${APP_USER}";
    GRANT USAGE, CREATE ON SCHEMA "${APP_SCHEMA}" TO "${APP_USER}";

    -- Ensure future objects created in schema are accessible to application role
    ALTER DEFAULT PRIVILEGES IN SCHEMA "${APP_SCHEMA}" GRANT ALL ON TABLES TO "${APP_USER}";
    ALTER DEFAULT PRIVILEGES IN SCHEMA "${APP_SCHEMA}" GRANT ALL ON SEQUENCES TO "${APP_USER}";
    ALTER DEFAULT PRIVILEGES IN SCHEMA "${APP_SCHEMA}" GRANT ALL ON FUNCTIONS TO "${APP_USER}";
    ALTER DEFAULT PRIVILEGES IN SCHEMA "${APP_SCHEMA}" GRANT ALL ON TYPES TO "${APP_USER}";
EOSQL

echo ">>> [MediaForge] PostgreSQL initialization complete. No business tables created."
