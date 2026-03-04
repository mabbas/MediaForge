import tempfile
from datetime import datetime

from src.resume.part_file_manager import (
    PartFileManager,
    PartFileMetadata,
    PartFileProgress,
)
from src.resume.integrity_checker import IntegrityChecker
from src.resume.recovery_manager import RecoveryManager


with tempfile.TemporaryDirectory() as tmp:
    pfm = PartFileManager(in_progress_dir=tmp)

    meta = PartFileMetadata(
        job_id="demo-123",
        url="https://youtube.com/watch?v=test",
        provider="youtube",
        total_bytes=1_000_000,
        filename="Demo Video.mp4",
        media_type="video",
        output_directory=tmp,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    part_path = pfm.create_part_file("demo-123", meta)
    print(f"Part file: {part_path}")
    print(f"Exists: {part_path.exists()}")

    part_path.write_bytes(b"x" * 635_000)
    print(f"Size: {pfm.get_part_file_size('demo-123')} bytes")

    pfm.update_progress(
        "demo-123",
        PartFileProgress(
            job_id="demo-123",
            bytes_downloaded=635_000,
            total_bytes=1_000_000,
            percent=63.5,
            last_byte_position=634_999,
            last_updated=datetime.now().isoformat(),
        ),
    )

    checker = IntegrityChecker()
    valid, reason, pos = checker.check_part_file(part_path, 635_000)
    print(f"Integrity: valid={valid}, position={pos}")
    print(f"Reason: {reason}")

    hash_val = checker.hash_last_chunk(part_path)
    print(f"Last chunk hash: {hash_val[:16]}...")

    mgr = RecoveryManager(part_file_manager=pfm)
    incomplete = mgr.scan_incomplete()
    print(f"Incomplete downloads: {len(incomplete)}")

    for dl in incomplete:
        print(
            f"  {dl.job_id}: {dl.percent_complete:.1f}% "
            f"can_resume={dl.can_resume}"
        )

    print("RESUME SYSTEM OK")

