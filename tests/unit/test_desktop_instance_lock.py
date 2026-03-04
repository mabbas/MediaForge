"""Tests for instance lock."""

from __future__ import annotations

import os
from pathlib import Path

from desktop.instance_lock import InstanceLock


def test_lock_acquire(tmp_path: object) -> None:
    lock = InstanceLock(str(tmp_path))
    assert lock.acquire() is True
    assert lock._locked is True


def test_lock_release(tmp_path: object) -> None:
    lock = InstanceLock(str(tmp_path))
    lock.acquire()
    lock.release()
    lock_file = Path(str(tmp_path)) / ".instance.lock"
    assert not lock_file.exists()


def test_lock_stale_pid(tmp_path: object) -> None:
    lock_file = Path(str(tmp_path)) / ".instance.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("999999999")
    lock = InstanceLock(str(tmp_path))
    assert lock.acquire() is True


def test_lock_own_pid(tmp_path: object) -> None:
    lock_file = Path(str(tmp_path)) / ".instance.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(str(os.getpid()))
    lock = InstanceLock(str(tmp_path))
    assert lock.acquire() is False
