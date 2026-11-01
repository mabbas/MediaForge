import asyncio
import os
import subprocess
import asyncpg

dump_path = "deploy/backups/grabitdown_pre_step04b2b.sql"
os.makedirs("deploy/backups", exist_ok=True)

print("1. Dumping grabitdown...")
res = subprocess.run(["docker", "exec", "postgres", "pg_dump", "-U", "postgres", "-d", "grabitdown"], capture_output=True)
if res.returncode != 0:
    print("DUMP FAILED:", res.stderr)
    exit(1)
with open(dump_path, "wb") as f:
    f.write(res.stdout)
size = os.path.getsize(dump_path)
print(f"   Dump created: {dump_path} ({size:,} bytes)")

PG_HOST = "127.0.0.1"
PG_PORT = 55432
PG_USER = "postgres"
PG_PASS = "P2pi7C:lT8z=xJfMRqEd:Lki5nE1/i/!"

async def get_pg_conn(db="postgres"):
    return await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, database=db
    )

print("2. Creating disposable database grabitdown_step04b2b_verify...")
async def test_restore():
    conn = await get_pg_conn("postgres")
    await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'grabitdown_step04b2b_verify';")
    await conn.execute("DROP DATABASE IF EXISTS grabitdown_step04b2b_verify;")
    await conn.execute("CREATE DATABASE grabitdown_step04b2b_verify OWNER postgres;")
    await conn.close()

    print("3. Restoring dump into grabitdown_step04b2b_verify...")
    with open(dump_path, "rb") as f:
        r = subprocess.run(["docker", "exec", "-i", "postgres", "psql", "-U", "postgres", "-d", "grabitdown_step04b2b_verify"], stdin=f, capture_output=True)
        if r.returncode != 0:
            print("RESTORE FAILED:", r.stderr)
            exit(1)

    print("4. Verifying restored database...")
    conn = await get_pg_conn("grabitdown_step04b2b_verify")
    head = await conn.fetchval("SELECT version_num FROM gid.alembic_version;")
    tables = await conn.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gid';")
    plans = await conn.fetchval("SELECT count(*) FROM gid.commercial_plans;")
    tenants = await conn.fetchval("SELECT count(*) FROM gid.tenants;")
    print(f"   Verified Alembic head: {head}")
    print(f"   Tables in gid: {tables}, plans: {plans}, tenants: {tenants}")
    assert head == "c3d4e5f6a7b8"
    assert plans == 60
    assert tenants == 855
    await conn.close()

    print("5. Dropping disposable database...")
    conn = await get_pg_conn("postgres")
    await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'grabitdown_step04b2b_verify';")
    await conn.execute("DROP DATABASE IF EXISTS grabitdown_step04b2b_verify;")
    await conn.close()
    print("   Backup and restoration verified successfully!")

asyncio.run(test_restore())
