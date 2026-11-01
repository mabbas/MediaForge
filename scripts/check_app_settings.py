import asyncio
import asyncpg
import json

async def main():
    configs = [
        ("DEV grabitdown", 55432, "P2pi7C:lT8z=xJfMRqEd:Lki5nE1/i/!", "grabitdown"),
        ("STAGING grabitdown_staging", 55433, "9da305315347c99ce75a135cab199772c964c76e413ddbd0c0bc22f336053be8", "grabitdown_staging")
    ]
    for label, port, pwd, dbname in configs:
        conn = await asyncpg.connect(host="127.0.0.1", port=port, user="postgres", password=pwd, database=dbname)
        try:
            rows = await conn.fetch("SELECT key, value FROM gid.app_settings ORDER BY key")
            print(f"=== {label} ===")
            for r in rows:
                print(f"  {r['key']}: {r['value']}")
        finally:
            await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
