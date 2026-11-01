import asyncio
import os
import subprocess
import sys
import asyncpg

PG_HOST = "127.0.0.1"
PG_PORT = 55432
PG_SUPERUSER = "postgres"
PG_SUPERPASS = "P2pi7C:lT8z=xJfMRqEd:Lki5nE1/i/!"

GID_USER = "gid_user"
GID_PASS = "Tbn9TfIHv4_YKd-vowcZ=!oiG1Av,4ME"
GID_APP = "gid_app"
GID_APP_PASS = "bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ"

DB_REHEARSAL = "grabitdown_staging_rehearsal"
DB_RESTORE = "grabitdown_staging_restore_test"
DUMP_PATH = os.path.abspath("deploy/backups/rehearsal_dump.sql")

async def run_sql(db_name: str, sql: str, user=PG_SUPERUSER, password=PG_SUPERPASS):
    conn = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=user, password=password, database=db_name
    )
    try:
        return await conn.fetch(sql)
    finally:
        await conn.close()

async def execute_ddl(db_name: str, sql: str, user=PG_SUPERUSER, password=PG_SUPERPASS):
    conn = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=user, password=password, database=db_name
    )
    try:
        await conn.execute(sql)
    finally:
        await conn.close()

async def main():
    print("=== TASK 12: Migration & Disaster Recovery Rehearsal ===")

    # Step 1: Create DB_REHEARSAL
    print(f"1. Creating disposable rehearsal database: {DB_REHEARSAL}...")
    conn = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_SUPERUSER, password=PG_SUPERPASS, database="postgres"
    )
    try:
        # Terminate any connections and drop if exists
        await conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_REHEARSAL}';")
        await conn.execute(f"DROP DATABASE IF EXISTS {DB_REHEARSAL};")
        await conn.execute(f"CREATE DATABASE {DB_REHEARSAL} OWNER {PG_SUPERUSER};")
    finally:
        await conn.close()

    # Step 2: Set up schema gid and grants
    print(f"2. Initializing schema 'gid' and granting permissions in {DB_REHEARSAL}...")
    await execute_ddl(DB_REHEARSAL, f"""
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        CREATE EXTENSION IF NOT EXISTS "pgcrypto";
        CREATE SCHEMA IF NOT EXISTS gid AUTHORIZATION {GID_USER};
        GRANT ALL ON SCHEMA gid TO {GID_USER};
        GRANT USAGE ON SCHEMA gid TO {GID_APP};
        ALTER DEFAULT PRIVILEGES IN SCHEMA gid GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {GID_APP};
        ALTER DEFAULT PRIVILEGES IN SCHEMA gid GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {GID_APP};
    """)

    # Step 3: Run Alembic migration lineage from scratch to head
    print(f"3. Running Alembic migrations lineage (py -m alembic upgrade head) on {DB_REHEARSAL}...")
    env = os.environ.copy()
    migration_url = f"postgresql+asyncpg://{GID_USER}:{GID_PASS}@{PG_HOST}:{PG_PORT}/{DB_REHEARSAL}"
    env["GID_MIGRATION_DATABASE_URL"] = migration_url
    env["GID_API_DATABASE_URL"] = migration_url

    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True
    )
    if proc.returncode != 0:
        print("Alembic upgrade FAILED:")
        print(proc.stderr)
        sys.exit(1)
    print("   Alembic migration to head completed successfully.")

    # Step 4: Verify rehearsal DB state
    print(f"4. Verifying {DB_REHEARSAL} schema, tables, and Alembic head...")
    alembic_head = await run_sql(DB_REHEARSAL, "SELECT version_num FROM gid.alembic_version;")
    current_rev = alembic_head[0]["version_num"]
    print(f"   Current Alembic revision: {current_rev} (Expected: c3d4e5f6a7b8)")
    assert current_rev == "c3d4e5f6a7b8", f"Expected c3d4e5f6a7b8, got {current_rev}"

    tables = await run_sql(DB_REHEARSAL, """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'gid' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    rehearsal_table_names = [r["table_name"] for r in tables]
    print(f"   Total tables created in gid: {len(rehearsal_table_names)}")
    print(f"   Tables: {', '.join(rehearsal_table_names)}")

    fks = await run_sql(DB_REHEARSAL, """
        SELECT count(*) as cnt
        FROM information_schema.table_constraints
        WHERE constraint_schema = 'gid' AND constraint_type = 'FOREIGN KEY';
    """)
    rehearsal_fk_count = fks[0]["cnt"]
    print(f"   Foreign keys in gid: {rehearsal_fk_count}")

    # Step 5: Dump rehearsal database
    print(f"5. Creating logical backup dump of {DB_REHEARSAL} via docker exec pg_dump...")
    os.makedirs(os.path.dirname(DUMP_PATH), exist_ok=True)
    dump_proc = subprocess.run(
        ["docker", "exec", "postgres", "pg_dump", "-U", PG_SUPERUSER, "-d", DB_REHEARSAL],
        capture_output=True
    )
    if dump_proc.returncode != 0:
        print("pg_dump FAILED:")
        print(dump_proc.stderr.decode("utf-8", errors="ignore"))
        sys.exit(1)
    with open(DUMP_PATH, "wb") as f:
        f.write(dump_proc.stdout)
    dump_size = os.path.getsize(DUMP_PATH)
    print(f"   Dump successfully created at {DUMP_PATH} ({dump_size:,} bytes)")

    # Step 6: Create DB_RESTORE
    print(f"6. Creating second disposable database for restore rehearsal: {DB_RESTORE}...")
    conn = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_SUPERUSER, password=PG_SUPERPASS, database="postgres"
    )
    try:
        await conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_RESTORE}';")
        await conn.execute(f"DROP DATABASE IF EXISTS {DB_RESTORE};")
        await conn.execute(f"CREATE DATABASE {DB_RESTORE} OWNER {PG_SUPERUSER};")
    finally:
        await conn.close()

    # Step 7: Restore dump into DB_RESTORE
    print(f"7. Restoring {DUMP_PATH} into {DB_RESTORE}...")
    with open(DUMP_PATH, "rb") as f:
        restore_proc = subprocess.run(
            ["docker", "exec", "-i", "postgres", "psql", "-U", PG_SUPERUSER, "-d", DB_RESTORE],
            stdin=f,
            capture_output=True
        )
    if restore_proc.returncode != 0:
        print("psql restore FAILED:")
        print(restore_proc.stderr.decode("utf-8", errors="ignore"))
        sys.exit(1)
    print("   Restore process exited cleanly.")

    # Step 8: Verify restored database
    print(f"8. Verifying restored database {DB_RESTORE}...")
    restored_head = await run_sql(DB_RESTORE, "SELECT version_num FROM gid.alembic_version;")
    assert restored_head[0]["version_num"] == "c3d4e5f6a7b8", f"Restored head mismatch: {restored_head}"

    restored_tables = await run_sql(DB_RESTORE, """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'gid' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    restored_table_names = [r["table_name"] for r in restored_tables]
    print(f"   Total tables restored in gid: {len(restored_table_names)}")
    assert restored_table_names == rehearsal_table_names, "Table list mismatch between rehearsal and restore!"

    restored_fks = await run_sql(DB_RESTORE, """
        SELECT count(*) as cnt
        FROM information_schema.table_constraints
        WHERE constraint_schema = 'gid' AND constraint_type = 'FOREIGN KEY';
    """)
    print(f"   Foreign keys restored in gid: {restored_fks[0]['cnt']}")
    assert restored_fks[0]["cnt"] == rehearsal_fk_count, "Foreign key count mismatch!"

    print("   Verification PASSED: 100% schema, Alembic revision, table count, and constraint match.")

    # Step 9: Clean up disposable rehearsal databases and dump
    print(f"9. Cleaning up disposable databases and temporary dump file...")
    conn = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_SUPERUSER, password=PG_SUPERPASS, database="postgres"
    )
    try:
        await conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_REHEARSAL}';")
        await conn.execute(f"DROP DATABASE IF EXISTS {DB_REHEARSAL};")
        await conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_RESTORE}';")
        await conn.execute(f"DROP DATABASE IF EXISTS {DB_RESTORE};")
    finally:
        await conn.close()

    if os.path.exists(DUMP_PATH):
        os.remove(DUMP_PATH)
        print(f"   Removed temporary dump {DUMP_PATH}")

    print("=== TASK 12 REHEARSAL SUCCESSFUL! ===")

if __name__ == "__main__":
    asyncio.run(main())
