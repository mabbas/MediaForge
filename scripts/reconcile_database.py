import asyncio
import asyncpg
import re

DEV_PG_PORT = 55432
DEV_DB = "grabitdown"
DEV_USER = "postgres"
DEV_PASS = "P2pi7C:lT8z=xJfMRqEd:Lki5nE1/i/!"

async def main():
    conn = await asyncpg.connect(
        host="127.0.0.1", port=DEV_PG_PORT, user=DEV_USER, password=DEV_PASS, database=DEV_DB
    )
    try:
        print("=== 1. ALL 23 TABLES: EXACT COUNT VS STAT ESTIMATE ===")
        rows = await conn.fetch("""
            SELECT
                t.table_name,
                s.n_live_tup AS stat_estimate
            FROM information_schema.tables t
            LEFT JOIN pg_stat_user_tables s ON s.schemaname = t.table_schema AND s.relname = t.table_name
            WHERE t.table_schema = 'gid' AND t.table_type = 'BASE TABLE'
            ORDER BY t.table_name;
        """)
        for r in rows:
            t = r['table_name']
            exact = await conn.fetchval(f'SELECT count(*) FROM gid."{t}"')
            stat = r['stat_estimate']
            diff = (stat - exact) if stat is not None else "N/A"
            flag = " [DISCREPANCY]" if diff != 0 and diff != "N/A" else ""
            print(f"  {t:35}: exact={exact:>5}, stat_estimate={str(stat):>5}, diff={str(diff):>3}{flag}")

        print("\n=== 2. LEGACY / GRANDFATHERED ACCOUNTS AUDIT ===")
        # Check grandfathered legacy_active accounts
        legacy_subs = await conn.fetch("""
            SELECT id, tenant_id, status, plan_id, created_at
            FROM gid.tenant_subscriptions
            WHERE status = 'legacy_active'
            ORDER BY created_at;
        """)
        print(f"Total 'legacy_active' subscriptions: {len(legacy_subs)}")
        for s in legacy_subs:
            print(f"  Sub {s['id']}: tenant={s['tenant_id']}, status={s['status']}, plan_id={s['plan_id']}")

        # Check tenant types / distribution
        tenants_total = await conn.fetchval("SELECT count(*) FROM gid.tenants")
        print(f"\nTotal tenants in DB: {tenants_total}")

        # Let's inspect tenants breakdown
        print("\n=== 3. TENANTS BREAKDOWN BY PREFIX / CREATION ===")
        sample_tenants = await conn.fetch("""
            SELECT
                CASE
                    WHEN id LIKE 'tenant-stage-%' THEN 'staging-test'
                    WHEN id LIKE 'tnt_%' THEN 'tnt-test'
                    WHEN id LIKE 'test_%' THEN 'test-prefix'
                    WHEN length(id) = 36 AND id ~ '^[0-9a-f-]+$' THEN 'standard-uuid'
                    ELSE 'other'
                END as category,
                count(*) as cnt
            FROM gid.tenants
            GROUP BY 1
            ORDER BY 2 DESC;
        """)
        for cat in sample_tenants:
            print(f"  {cat['category']:20}: {cat['cnt']}")

        # Check subscription statuses
        print("\n=== 4. TENANT SUBSCRIPTIONS BY STATUS ===")
        sub_statuses = await conn.fetch("""
            SELECT status, count(*)
            FROM gid.tenant_subscriptions
            GROUP BY status
            ORDER BY count(*) DESC;
        """)
        for ss in sub_statuses:
            print(f"  {ss['status']:25}: {ss['count']}")

        # Check foreign keys and referential integrity
        print("\n=== 5. REFERENTIAL INTEGRITY AUDIT ===")
        orphaned_subs = await conn.fetchval("""
            SELECT count(*) FROM gid.tenant_subscriptions s
            LEFT JOIN gid.tenants t ON s.tenant_id = t.id
            WHERE t.id IS NULL;
        """)
        print(f"Orphaned tenant_subscriptions (missing tenant): {orphaned_subs}")

        orphaned_plans = await conn.fetchval("""
            SELECT count(*) FROM gid.tenant_subscriptions s
            LEFT JOIN gid.commercial_plans p ON s.plan_id = p.id
            WHERE p.id IS NULL;
        """)
        print(f"Orphaned tenant_subscriptions (missing plan): {orphaned_plans}")

        orphaned_entitlements = await conn.fetchval("""
            SELECT count(*) FROM gid.commercial_plan_entitlements e
            LEFT JOIN gid.commercial_plans p ON e.plan_id = p.id
            WHERE p.id IS NULL;
        """)
        print(f"Orphaned commercial_plan_entitlements (missing plan): {orphaned_entitlements}")

        orphaned_audit = await conn.fetchval("""
            SELECT count(*) FROM gid.commercial_audit_logs a
            LEFT JOIN gid.tenants t ON a.tenant_id = t.id
            WHERE a.tenant_id IS NOT NULL AND t.id IS NULL;
        """)
        print(f"Orphaned commercial_audit_logs (missing tenant): {orphaned_audit}")

        # Check primary key match between backup and live DB for tenants, commercial_plans, audit_logs
        print("\n=== 6. BACKUP PRIMARY KEYS VS LIVE DB ===")
        with open("deploy/backups/grabitdown_pre_step04b2c.sql", "r", encoding="utf-16") as f:
            backup_text = f.read()

        for tbl, pk_col in [("tenants", "id"), ("commercial_plans", "id"), ("commercial_audit_logs", "id")]:
            m = re.search(r"COPY gid\." + tbl + r" \((.*?)\) FROM stdin;\n(.*?)\n\\\.", backup_text, re.DOTALL)
            if m:
                cols = [c.strip() for c in m.group(1).split(",")]
                pk_idx = cols.index(pk_col)
                backup_pks = set()
                for line in m.group(2).split("\n"):
                    if line.strip():
                        parts = line.split("\t")
                        if len(parts) > pk_idx:
                            backup_pks.add(parts[pk_idx])

                live_pks = set(r[pk_col] for r in await conn.fetch(f'SELECT "{pk_col}" FROM gid."{tbl}"'))

                in_backup_not_live = backup_pks - live_pks
                in_live_not_backup = live_pks - backup_pks
                print(f"Table '{tbl}':")
                print(f"  Backup PK count: {len(backup_pks)}")
                print(f"  Live PK count:   {len(live_pks)}")
                print(f"  In backup but missing from live: {len(in_backup_not_live)}")
                print(f"  In live but not in backup:       {len(in_live_not_backup)}")
                assert len(in_backup_not_live) == 0, f"Missing rows from live: {in_backup_not_live}"
                assert len(in_live_not_backup) == 0, f"Extra rows in live: {in_live_not_backup}"
                print(f"  --> 100% EXACT PK MATCH BETWEEN BACKUP AND LIVE DB!")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
