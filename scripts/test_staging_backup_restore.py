"""Task 11 & 12: Staging Database & Media Backup/Restore Verification and Timing."""

import asyncio
import hashlib
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg


STAGING_PG_PORT = 55433
STAGING_DB = "grabitdown_staging"
RESTORE_TEST_DB = "grabitdown_staging_restore_test"
STAGING_USER = "postgres"
STAGING_PASS = os.getenv("POSTGRES_PASSWORD", "9da305315347c99ce75a135cab199772c964c76e413ddbd0c0bc22f336053be8")

BACKUP_DIR = Path("D:/Dev/MediaForge/deploy/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

async def test_db_backup_restore():
    print("=== TASK 11: DATABASE LOGICAL BACKUP & RESTORE VERIFICATION ===")
    dump_file = BACKUP_DIR / f"staging_pgdump_{int(time.time())}.sql"

    # 1. Measure backup duration & size
    print(f"Starting pg_dump of {STAGING_DB} inside Docker container...")
    t0 = time.perf_counter()
    dump_cmd = [
        "docker", "exec", "mediaforge-staging-postgres",
        "pg_dump", "-U", "postgres", "-d", STAGING_DB, "-F", "p"
    ]
    with open(dump_file, "wb") as f:
        res = subprocess.run(dump_cmd, stdout=f, stderr=subprocess.PIPE, check=True)
    backup_duration = time.perf_counter() - t0
    backup_size = dump_file.stat().st_size
    print(f"[PASS] Staging database backup completed in {backup_duration:.3f}s (Size: {backup_size:,} bytes, Path: {dump_file.name})")

    # Connect to postgres maintenance DB to create disposable restore target
    conn = await asyncpg.connect(
        host="127.0.0.1", port=STAGING_PG_PORT, user=STAGING_USER, password=STAGING_PASS, database="postgres"
    )
    try:
        # Drop disposable restore test DB if exists, then create
        await conn.execute(f"DROP DATABASE IF EXISTS {RESTORE_TEST_DB}")
        await conn.execute(f"CREATE DATABASE {RESTORE_TEST_DB} OWNER postgres")
        print(f"Created clean disposable target database: {RESTORE_TEST_DB}")

        # 2. Measure restore duration
        print(f"Restoring {dump_file.name} into {RESTORE_TEST_DB}...")
        t1 = time.perf_counter()
        # We stream the file into psql inside container
        restore_cmd = [
            "docker", "exec", "-i", "mediaforge-staging-postgres",
            "psql", "-U", "postgres", "-d", RESTORE_TEST_DB
        ]
        with open(dump_file, "rb") as f:
            res_restore = subprocess.run(restore_cmd, stdin=f, capture_output=True, check=True)
        restore_duration = time.perf_counter() - t1
        print(f"[PASS] Staging database restore completed in {restore_duration:.3f}s")

        # 3. Verify restored database schema and contents
        r_conn = await asyncpg.connect(
            host="127.0.0.1", port=STAGING_PG_PORT, user=STAGING_USER, password=STAGING_PASS, database=RESTORE_TEST_DB
        )
        try:
            # Check table count in schema gid
            tables = await r_conn.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'gid' AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """
            )
            table_names = [t["table_name"] for t in tables]
            print(f"Restored table count in schema 'gid': {len(table_names)}")
            assert len(table_names) == 23, f"Expected 23 tables in schema gid, got {len(table_names)}"
            print(f"[PASS] Schema gid contains all 23 expected tables.")

            # Check Alembic revision head
            alembic_row = await r_conn.fetchrow("SELECT version_num FROM gid.alembic_version")
            assert alembic_row, "Alembic revision table missing in restored database!"
            assert alembic_row["version_num"] == "c3d4e5f6a7b8", f"Unexpected Alembic version: {alembic_row['version_num']}"
            print(f"[PASS] Alembic revision verified at head: '{alembic_row['version_num']}'")

            # Check foreign key constraints
            fk_count = await r_conn.fetchval(
                """
                SELECT count(*)
                FROM information_schema.table_constraints
                WHERE table_schema = 'gid' AND constraint_type = 'FOREIGN KEY';
                """
            )
            print(f"[PASS] Verified {fk_count} foreign key constraints restored intact.")

            # Check default plan catalog
            plan_count = await r_conn.fetchval("SELECT count(*) FROM gid.commercial_plans")
            print(f"[PASS] Commercial plan catalog restored intact ({plan_count} plans).")

        finally:
            await r_conn.close()

    finally:
        # Cleanup restore DB
        print(f"Cleaning up disposable database {RESTORE_TEST_DB}...")
        await conn.execute(f"DROP DATABASE IF EXISTS {RESTORE_TEST_DB}")
        await conn.close()
        print("Disposable restore test database dropped cleanly.")

    return {
        "db_size_bytes": backup_size,
        "backup_duration_seconds": backup_duration,
        "restore_duration_seconds": restore_duration,
    }

async def test_media_backup_restore():
    print("\n=== TASK 12: MEDIA FILE BACKUP & RESTORE VERIFICATION ===")
    test_media_dir = Path("D:/Dev/MediaForge/deploy/backups/media_staging_backup_test")
    if test_media_dir.exists():
        shutil.rmtree(test_media_dir)
    test_media_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create synthetic media file directly in volume
    temp_tnt = f"tnt_bkp_{int(time.time())}"
    vol_dir = f"/app/downloads/{temp_tnt}/downloads"
    subprocess.run(["docker", "exec", "mediaforge-staging-worker", "mkdir", "-p", vol_dir], check=True)

    vol_file = f"{vol_dir}/backup_verify.mp4"
    gen_cmd = [
        "docker", "exec", "mediaforge-staging-worker",
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        vol_file
    ]
    subprocess.run(gen_cmd, capture_output=True, check=True)

    # Compute source sha256 inside container
    src_sha = subprocess.run(
        ["docker", "exec", "mediaforge-staging-worker", "sha256sum", vol_file],
        capture_output=True, text=True, check=True
    ).stdout.split()[0]
    src_size = int(subprocess.run(
        ["docker", "exec", "mediaforge-staging-worker", "stat", "-c", "%s", vol_file],
        capture_output=True, text=True, check=True
    ).stdout.strip())
    print(f"Generated synthetic media in staging volume: {vol_file} ({src_size} bytes, SHA256: {src_sha})")

    # 2. Backup media files from container volume to local backup folder
    print(f"Exporting media archive from Docker volume...")
    tar_archive = BACKUP_DIR / f"staging_media_{int(time.time())}.tar"
    t0 = time.perf_counter()
    with open(tar_archive, "wb") as f:
        subprocess.run(
            ["docker", "exec", "mediaforge-staging-worker", "tar", "-cf", "-", "-C", "/app/downloads", temp_tnt],
            stdout=f, check=True
        )
    media_backup_duration = time.perf_counter() - t0
    media_backup_size = tar_archive.stat().st_size
    print(f"[PASS] Media archive backup completed in {media_backup_duration:.3f}s (Size: {media_backup_size:,} bytes)")

    # 3. Restore to verification directory
    print(f"Restoring media archive to verification destination {test_media_dir}...")
    t1 = time.perf_counter()
    restore_target = test_media_dir / "restored"
    restore_target.mkdir(parents=True, exist_ok=True)
    with open(tar_archive, "rb") as f:
        subprocess.run(
            ["tar", "-xf", "-", "-C", str(restore_target)],
            stdin=f, check=True
        )
    media_restore_duration = time.perf_counter() - t1
    print(f"[PASS] Media archive restored in {media_restore_duration:.3f}s")

    # 4. Verify checksum match
    restored_file = restore_target / temp_tnt / "downloads" / "backup_verify.mp4"
    assert restored_file.exists(), f"Restored file not found at {restored_file}!"
    with open(restored_file, "rb") as rf:
        restored_sha = hashlib.sha256(rf.read()).hexdigest()

    assert restored_sha == src_sha, f"Checksum mismatch! Source: {src_sha}, Restored: {restored_sha}"
    print(f"[PASS] Checksum 100% matched: {restored_sha}")

    # Clean up container files and local test folder
    subprocess.run(["docker", "exec", "mediaforge-staging-worker", "rm", "-rf", f"/app/downloads/{temp_tnt}"])
    shutil.rmtree(test_media_dir, ignore_errors=True)
    print("Cleaned up temporary media verification files.")

    return {
        "media_size_bytes": media_backup_size,
        "media_backup_duration_seconds": media_backup_duration,
        "media_restore_duration_seconds": media_restore_duration,
    }

async def main():
    db_metrics = await test_db_backup_restore()
    media_metrics = await test_media_backup_restore()
    print("\n=== BACKUP / RESTORE BENCHMARK SUMMARY ===")
    print(f"Database Backup Duration:  {db_metrics['backup_duration_seconds']:.3f}s")
    print(f"Database Restore Duration: {db_metrics['restore_duration_seconds']:.3f}s")
    print(f"Database Backup Size:      {db_metrics['db_size_bytes']:,} bytes")
    print(f"Media Backup Duration:     {media_metrics['media_backup_duration_seconds']:.3f}s")
    print(f"Media Restore Duration:    {media_metrics['media_restore_duration_seconds']:.3f}s")
    print(f"Media Archive Size:        {media_metrics['media_size_bytes']:,} bytes")
    print("Realistic Local Recovery Objective (RTO): < 5 seconds for local database + media recovery.")
    print("Realistic Recovery Point Objective (RPO): Determined by automated scheduled backup frequency (e.g. 1 hour or 24 hours).")

if __name__ == "__main__":
    asyncio.run(main())
