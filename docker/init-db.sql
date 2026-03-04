-- Database initialization — creates schemas and extensions for multi-tenant support.

-- Enable useful extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create application schema (keeps our tables separate from public schema)
CREATE SCHEMA IF NOT EXISTS gid;

-- Grant permissions
GRANT ALL ON SCHEMA gid TO gid_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA gid GRANT ALL ON TABLES TO gid_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA gid GRANT ALL ON SEQUENCES TO gid_user;
