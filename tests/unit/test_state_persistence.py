"""Tests for GrabItDown state persistence."""

from pathlib import Path

from src.download.state_persistence import StatePersistence
from src.models.download import DownloadJob, DownloadProgress, DownloadRequest
from src.models.enums import DownloadStatus


def test_save_and_load(tmp_path):
    """Save state and load it back."""
    sp = StatePersistence(state_dir=str(tmp_path))

    jobs = [
        DownloadJob(
            request=DownloadRequest(url=f"https://test.com/v{i}"),
            progress=DownloadProgress(
                job_id=f"job-{i}",
                status=DownloadStatus.QUEUED,
            ),
            priority="normal",
            user_id="alice",
            tenant_id="default",
        )
        for i in range(3)
    ]

    sp.save_state(queued_jobs=jobs, active_jobs=[])
    loaded = sp.load_state()

    assert len(loaded) == 3
    assert loaded[0]["url"] == "https://test.com/v0"
    assert loaded[0]["status"] == "queued"
    assert loaded[0]["user_id"] == "alice"


def test_save_active_as_interrupted(tmp_path):
    """Active jobs saved as interrupted."""
    sp = StatePersistence(state_dir=str(tmp_path))

    active_job = DownloadJob(
        request=DownloadRequest(url="https://test.com/active"),
        progress=DownloadProgress(
            job_id="active-1",
            status=DownloadStatus.DOWNLOADING,
        ),
    )

    sp.save_state(queued_jobs=[], active_jobs=[active_job])
    loaded = sp.load_state()

    assert len(loaded) == 1
    assert loaded[0]["status"] == "interrupted"


def test_load_no_state(tmp_path):
    """Load returns empty list when no state file."""
    sp = StatePersistence(state_dir=str(tmp_path))
    assert sp.load_state() == []


def test_has_saved_state(tmp_path):
    """Detects saved state file."""
    sp = StatePersistence(state_dir=str(tmp_path))
    assert sp.has_saved_state() is False

    sp.save_state(queued_jobs=[], active_jobs=[])
    assert sp.has_saved_state() is True


def test_clear_state(tmp_path):
    """Clear removes state file."""
    sp = StatePersistence(state_dir=str(tmp_path))
    sp.save_state(queued_jobs=[], active_jobs=[])
    sp.clear_state()
    assert sp.has_saved_state() is False


def test_load_corrupted_state(tmp_path):
    """Corrupted state file returns empty list."""
    sp = StatePersistence(state_dir=str(tmp_path))
    (Path(tmp_path) / "engine_state.json").write_text("not valid json{{{")
    assert sp.load_state() == []
