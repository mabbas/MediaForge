import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect("postgresql://gid_app:413d25fc25d349785642d26f616809a015fbd2ee136341c6f98f137fe0f2c0e1@127.0.0.1:55433/grabitdown_staging")
    plans = await conn.fetch("SELECT plan_code, name, status FROM gid.commercial_plans ORDER BY created_at;")
    print(f"Total plans in staging: {len(plans)}")
    for p in plans:
        print(f"  {p['plan_code']}: {p['name']} ({p['status']})")
    tenants = await conn.fetch("SELECT id, slug, name FROM gid.tenants;")
    print(f"Total tenants in staging: {len(tenants)}")
    for t in tenants:
        print(f"  {t['slug']}: {t['name']}")
    users = await conn.fetch("SELECT id, email, role FROM gid.users;")
    print(f"Total users in staging: {len(users)}")
    for u in users:
        print(f"  {u['email']}: {u['role']}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
