import asyncio
import asyncpg
import json
import os

async def main():
    staging_pass = os.getenv("STAGING_PG_PASSWORD", "9da305315347c99ce75a135cab199772c964c76e413ddbd0c0bc22f336053be8")
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=55433,
        user="postgres",
        password=staging_pass,
        database="grabitdown_staging"
    )
    try:
        val = {
            "enabled": True,
            "paused": False,
            "rollout_mode": "allowlist_only",
            "tenant_allowlist": [],
            "poll_interval_seconds": 60,
            "description": "Commercial scheduled transition background processor configuration",
            "test_tenant_prefixes": ["test_", "qa_", "demo_"],
            "updated_by_user_id": None,
            "reason": "Restore allowlist_only mode with empty tenant_allowlist for staging release gate"
        }
        await conn.execute("UPDATE gid.app_settings SET value = $1 WHERE key = 'commercial:scheduler_config'", json.dumps(val))
        print("Updated staging commercial:scheduler_config to empty allowlist.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
