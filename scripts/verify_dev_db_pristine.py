"""Verify development database grabitdown remained untouched (Delta = 0)."""

import asyncio
import os
import asyncpg


DEV_PG_PORT = 55432
DEV_DB = "grabitdown"
DEV_USER = "postgres"
DEV_PASS = os.getenv("DEV_PG_PASSWORD", "P2pi7C:lT8z=xJfMRqEd:Lki5nE1/i/!")

async def check_dev_db():
    print(f"Connecting to development database {DEV_DB} on port {DEV_PG_PORT}...")
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=DEV_PG_PORT,
        user=DEV_USER,
        password=DEV_PASS,
        database=DEV_DB,
    )
    try:
        tables = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'gid' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
            """
        )
        print(f"Total tables in schema 'gid': {len(tables)}")
        total_rows = 0
        counts = {}
        for r in tables:
            t = r["table_name"]
            cnt = await conn.fetchval(f'SELECT count(*) FROM gid."{t}"')
            counts[t] = cnt
            total_rows += cnt
            print(f"  {t:35}: {cnt:>6} rows")

        print(f"\nTotal rows across all {len(tables)} tables: {total_rows}")

        # Check Alembic version
        alembic_v = await conn.fetchval("SELECT version_num FROM gid.alembic_version")
        print(f"Alembic revision: {alembic_v}")
        assert alembic_v == "c3d4e5f6a7b8", f"Unexpected alembic version in dev DB: {alembic_v}"

        # Commercial plans count
        print(f"Commercial plans: {counts.get('commercial_plans')}")
        print(f"Tenants count:    {counts.get('tenants')}")

        # Historical test accounts inventory
        test_tenants = await conn.fetch(
            "SELECT id, name, slug FROM gid.tenants WHERE id LIKE 'tnt_test_%' OR name LIKE '%Test%' LIMIT 10"
        )
        print(f"\nSample historical test accounts in dev DB: {len(test_tenants)} returned")
        for tt in test_tenants[:5]:
            print(f"  - {tt['id']}: {tt['name']} ({tt['slug']})")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(check_dev_db())
