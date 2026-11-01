"""Live PostgreSQL Transaction Rollback Retention Verification.

Tests Task 3 requirements against the live PostgreSQL database:
1. Starts a database transaction.
2. Inserts a synthetic expired customer artifact (60 days old).
3. Inserts a synthetic dependent artifact referencing it as parent.
4. Inserts a standalone expired customer artifact (60 days old).
5. Runs retention preview with retention_days=30:
   - Proves referenced parent is PROTECTED (referenced_source_protected).
   - Proves standalone expired artifact is marked CLEANUP CANDIDATE (expired_retention_candidate).
6. Runs retention preview with default retain-forever (retention_days=-1):
   - Proves standalone expired artifact is RETAINED (0 eligible for cleanup).
7. Rolls back the entire transaction.
8. Verifies zero rows persisted to live PostgreSQL database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from api.config import get_api_settings
from api.database.models import MediaArtifactDB
from api.services.retention_service import generate_retention_preview, DEFAULT_RETENTION_DAYS

TENANT_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"


async def run_live_rollback_test():
    settings = get_api_settings()
    db_url = settings.database_url
    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    now = datetime.utcnow()
    old_time = now - timedelta(days=60)

    print("=== Live PostgreSQL Transaction Rollback Retention Test ===")

    async with session_factory() as session:
        # Start transaction
        async with session.begin():
            # 1. Referenced Parent Artifact (60 days old)
            parent_art = MediaArtifactDB(
                id="synthetic-live-parent-01",
                tenant_id=TENANT_ID,
                user_id=USER_ID,
                artifact_type="clip",
                storage_path="clips/synthetic_parent_01.mp4",
                filename="synthetic_parent_01.mp4",
                file_size_bytes=50000,
                duration_seconds=10.0,
                status="ready",
                created_at=old_time,
            )
            # 2. Child Dependent Artifact referencing parent
            child_art = MediaArtifactDB(
                id="synthetic-live-child-02",
                tenant_id=TENANT_ID,
                user_id=USER_ID,
                artifact_type="merge",
                storage_path="merged/synthetic_child_02.mp4",
                filename="synthetic_child_02.mp4",
                file_size_bytes=100000,
                duration_seconds=20.0,
                status="ready",
                parent_artifact_id="synthetic-live-parent-01",
                input_sources=["clips/synthetic_parent_01.mp4"],
                created_at=now,
            )
            # 3. Standalone Expired Artifact (60 days old, unreferenced)
            standalone_art = MediaArtifactDB(
                id="synthetic-live-expired-03",
                tenant_id=TENANT_ID,
                user_id=USER_ID,
                artifact_type="clip",
                storage_path="clips/synthetic_standalone_03.mp4",
                filename="synthetic_standalone_03.mp4",
                file_size_bytes=75000,
                duration_seconds=15.0,
                status="ready",
                created_at=old_time,
            )
            session.add_all([parent_art, child_art, standalone_art])
            await session.flush()

            # Test A: Explicit 30-day retention evaluation inside active transaction
            preview_30d = await generate_retention_preview(
                db=session,
                storage_root=Path("/app/downloads"),
                retention_days=30,
                filter_tenant_id=TENANT_ID,
            )

            # Find evaluated items
            parent_item = next((it for it in preview_30d.items if it.artifact_id == "synthetic-live-parent-01"), None)
            child_item = next((it for it in preview_30d.items if it.artifact_id == "synthetic-live-child-02"), None)
            standalone_item = next((it for it in preview_30d.items if it.artifact_id == "synthetic-live-expired-03"), None)

            assert parent_item is not None, "Parent artifact not evaluated in preview"
            assert standalone_item is not None, "Standalone artifact not evaluated in preview"

            print("[Test A - Explicit 30-Day Window]:")
            print(f"  Parent Artifact:     classification={parent_item.classification}, action={parent_item.action}, protected={parent_item.is_protected}")
            print(f"  Standalone Expired:  classification={standalone_item.classification}, action={standalone_item.action}, eligible_cleanup={standalone_item.eligible_for_cleanup}")

            assert parent_item.classification == "referenced_source_protected", f"Expected referenced_source_protected, got {parent_item.classification}"
            assert parent_item.eligible_for_cleanup is False
            assert standalone_item.classification == "expired_retention_candidate", f"Expected expired_retention_candidate, got {standalone_item.classification}"
            assert standalone_item.eligible_for_cleanup is True

            # Test B: Default retain-forever policy (-1)
            preview_default = await generate_retention_preview(
                db=session,
                storage_root=Path("/app/downloads"),
                retention_days=DEFAULT_RETENTION_DAYS,  # -1
                filter_tenant_id=TENANT_ID,
            )
            standalone_default_item = next((it for it in preview_default.items if it.artifact_id == "synthetic-live-expired-03"), None)
            assert standalone_default_item is not None
            print("[Test B - Default Retain-Forever Window]:")
            print(f"  Standalone Expired:  classification={standalone_default_item.classification}, action={standalone_default_item.action}, eligible_cleanup={standalone_default_item.eligible_for_cleanup}")
            print(f"  Total Cleanup Count: {preview_default.eligible_for_cleanup_count}")

            assert standalone_default_item.classification == "customer_media_retained"
            assert standalone_default_item.eligible_for_cleanup is False
            assert preview_default.eligible_for_cleanup_count == 0

            # Explicit rollback
            await session.rollback()
            print("[Rollback]: Active transaction rolled back successfully.")

    # 4. Verify post-rollback invariance: records must NOT exist in PostgreSQL
    async with session_factory() as verify_session:
        res = await verify_session.execute(
            select(MediaArtifactDB).where(
                MediaArtifactDB.id.in_(["synthetic-live-parent-01", "synthetic-live-child-02", "synthetic-live-expired-03"])
            )
        )
        lingering = list(res.scalars().all())
        assert len(lingering) == 0, f"Rollback failed: {len(lingering)} records persisted in PostgreSQL"
        print(f"[Verification]: 0 lingering records in PostgreSQL `grabitdown`. Invariance confirmed 100%.")

    await engine.dispose()
    print("\n[RESULT: PASS] Live PostgreSQL transaction rollback retention test passed completely.")


if __name__ == "__main__":
    asyncio.run(run_live_rollback_test())
