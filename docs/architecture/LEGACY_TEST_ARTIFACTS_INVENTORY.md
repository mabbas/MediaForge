# MediaForge — Legacy Test Artifacts Inventory and Proposed Cleanup Plan

This document provides a factual, read-only inventory of test-created artifacts identified within the development PostgreSQL database (`grabitdown`, schema `gid`) as audited during **STEP 04-B2-A (TASK 4 & TASK 5)**.

> [!IMPORTANT]
> In accordance with the mandate of STEP 04-B2-A, **no destructive cleanup has been performed on the authoritative development database**. This inventory document outlines the provenance of each test artifact and proposes a controlled cleanup procedure that requires explicit operator authorization.

---

## 1. Executive Summary & Inventory Counts

As of October 30, 2026, the database contains both authoritative baseline commercial entities and accumulated test entities created by previous unit/integration test runs that lacked database isolation:

| Entity Category | Authoritative Baseline | Test-Created Artifacts | Total in Database | Provenance / Origin |
| :--- | :---: | :---: | :---: | :--- |
| **Commercial Plans** | **6** | **54** | **60** | Unit test runs in `test_step04a1`, `test_step04a1_fixa`, `test_step04a3` |
| **Tenants** | **496** | **359** | **855** | Automated unit tests (`test_*`, `tnt_*`) and browser acceptance scripts |
| **Subscriptions** | **496** | **286** | **782** | Subscriptions created for test tenants |
| **Privileged Users** | **1** (Root Owner) | **40** (Test Admins) | **41** | `test_step04a3` tests inserting `role='system_admin'` on test tenants |
| **Scheduled Transitions**| **0** (Customer) | **196** (Test) | **196** | Transitions created by scheduler tests and test simulations |
| **Audit Log Events** | Preserved | Accumulated | **867** | Immutable records of test mutations |

---

## 2. Detailed Breakdown of Test Artifacts

### 2.1 Commercial Plans (54 Test Plans)
The 6 core platform plans are:
1. `plan-free-v1` (Active Baseline Free)
2. `plan-trial-v1` (Active Pro 14-Day Trial)
3. `plan-creator-monthly-v1` (Retired V1)
4. `plan-pro-monthly-v1` (Retired V1)
5. `plan-creator-monthly-v2` (Draft V2, unapproved pricing)
6. `plan-pro-monthly-v2` (Draft V2, unapproved pricing)

The remaining 54 plans have IDs matching test patterns:
- `plan_draft_*`: Created by draft plan validation tests.
- `plan_ret_*`: Created by retired plan rejection tests.
- `plan_app_*`: Created by plan assignment and upgrade tests.
- `plan-test-*` / `plan_upgrade_*`: Created by transition scheduling tests.

**Customer Impact Audit**: Exactly **0 baseline customer tenants** are subscribed to any of these 54 test plans.

### 2.2 Privileged Users (40 Test System Admins)
- **Authoritative Platform Root Owner**:
  - User ID: `00000000-0000-0000-0000-000000000002`
  - Email: `system@grabitdown.local`
  - Tenant ID: `00000000-0000-0000-0000-000000000001` (Platform Root)
  - Role: `owner` (system-level authority)
- **Test-Created System Admins (40 users)**:
  - Format: `u_<8_hex_chars>` with emails matching `admin_<6_hex_chars>@ex.com`.
  - Roles: `role = 'system_admin'`.
  - Tenant Associations: Associated exclusively with test tenants (`test_pub_*`, `test_upg_*`, `test_audit_corr_*`, `test_override_prec_*`).
  - Provenance: Created in `test_step04a3_subscription_lifecycle.py` tests 6, 7, 22, and 23.
  - Risk Assessment: Because these accounts are tied to synthetic test tenants and use dummy passwords, they cannot authenticate via the Next.js Super Admin portal (which strictly authenticates against tenant `default`), but their presence in `gid.users` clutters database audits.

### 2.3 Test Tenants (359 Tenants) & Subscriptions (286 Subscriptions)
- Pattern: IDs starting with `test_` or `tnt_`, slugs starting with `test_`, `t-`, `tgt-`, or `own-`.
- Zero active production traffic or customer data.

---

## 3. Root Cause Analysis

Previous test milestones defined a local fixture in test files:
```python
PG_URL = "postgresql+asyncpg://gid_app:...@127.0.0.1:55432/grabitdown"

@pytest.fixture
def pg_session_factory():
    engine = create_async_engine(PG_URL, ...)
    ...
```
Because `PG_URL` directly connected to the development database `grabitdown` without checking for a dedicated test database, any test that committed records (`session.commit()`) without explicit cleanup persisted those records into `grabitdown`.

---

## 4. Test Isolation Architecture Implemented in STEP 04-B2-A

To permanently halt test artifact accumulation in `grabitdown`:
1. **Dedicated Disposable Test Database (`grabitdown_test`)**:
   All future test runs and browser acceptance executions target `grabitdown_test`.
2. **Strict Test Database Identity Verification**:
   The central `pg_session_factory` fixture enforces that the connected database name must contain `_test` or be explicitly confirmed via `ALLOW_AUTHORITATIVE_DEV_TEST=1`. Destructive operations against `grabitdown` are rejected.
3. **Safe Teardown Guarantee**:
   All test entities created in `grabitdown_test` are isolated from production customer data.

---

## 5. Proposed Cleanup Plan for `grabitdown` (Requiring Operator Approval)

When authorized by platform operators, the following controlled SQL script can be executed against `grabitdown` to restore it to pristine baseline state:

```sql
-- STEP 1: Verify database identity
DO $$
BEGIN
    IF current_database() != 'grabitdown' THEN
        RAISE EXCEPTION 'Safety check failed: unexpected database %', current_database();
    END IF;
END $$;

BEGIN;

-- STEP 2: Delete test scheduled transitions
DELETE FROM gid.scheduled_subscription_transitions
WHERE tenant_id LIKE 'test_%' OR tenant_id LIKE 'tnt_%';

-- STEP 3: Delete test entitlement overrides
DELETE FROM gid.tenant_entitlement_overrides
WHERE tenant_id LIKE 'test_%' OR tenant_id LIKE 'tnt_%';

-- STEP 4: Delete test subscriptions
DELETE FROM gid.tenant_subscriptions
WHERE tenant_id LIKE 'test_%' OR tenant_id LIKE 'tnt_%';

-- STEP 5: Delete test users (including the 40 test admins)
DELETE FROM gid.users
WHERE tenant_id LIKE 'test_%' OR tenant_id LIKE 'tnt_%';

-- STEP 6: Delete test tenants
DELETE FROM gid.tenants
WHERE id LIKE 'test_%' OR id LIKE 'tnt_%'
   OR slug LIKE 'tgt-%' OR slug LIKE 'own-%' OR slug LIKE 't-%';

-- STEP 7: Delete test plans (preserving the 6 core plans)
DELETE FROM gid.commercial_plan_entitlements
WHERE plan_id NOT IN (
    'plan-free-v1', 'plan-trial-v1', 'plan-creator-monthly-v1',
    'plan-pro-monthly-v1', 'plan-creator-monthly-v2', 'plan-pro-monthly-v2'
);

DELETE FROM gid.commercial_plans
WHERE id NOT IN (
    'plan-free-v1', 'plan-trial-v1', 'plan-creator-monthly-v1',
    'plan-pro-monthly-v1', 'plan-creator-monthly-v2', 'plan-pro-monthly-v2'
);

COMMIT;
```

> [!NOTE]
> The above script is purely a proposal. In accordance with STEP 04-B2-A instructions, it has NOT been run. The development database `grabitdown` remains completely preserved.
