import asyncio
import asyncpg
from api.security.passwords import verify_password

async def main():
    conn = await asyncpg.connect('postgresql://postgres:9da305315347c99ce75a135cab199772c964c76e413ddbd0c0bc22f336053be8@127.0.0.1:55433/grabitdown_staging')
    rows = await conn.fetch("SELECT email, password_hash, role FROM gid.users WHERE role IN ('system_admin', 'admin', 'owner') ORDER BY id LIMIT 30")
    for r in rows:
        email = r['email']
        ph = r['password_hash']
        for p in ['AdminPass123!', 'Password123!', 'password123', 'admin', 'secret', 'bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ', 'BrowserAdmin123!']:
            if ph and verify_password(p, ph):
                print(f"User: {email} (role: {r['role']}) has password: {p}")
                await conn.close()
                return
    print("No sample match found in first 30 users")
    await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
