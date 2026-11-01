"""Dedicated test media & subscription worker pointing to grabitdown_test (STEP 04-B2-A)."""

import asyncio
import os
import signal
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ["GID_API_DATABASE_URL"] = (
    "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test"
)

from api.config import get_api_settings
from api.database.connection import init_database
from api.workers.media_worker import MediaWorker


async def run():
    worker = MediaWorker(worker_id="worker-browser-acceptance-01")
    settings = get_api_settings()

    await init_database(
        database_url="postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test",
        schema=settings.database_schema,
        pool_size=5,
        max_overflow=10,
    )
    worker.running = True
    print("[TEST WORKER] Test MediaWorker started on grabitdown_test", flush=True)

    # Launch subscription loop with 1.0s initial delay
    loop_task = asyncio.create_task(worker._subscription_transitions_loop(initial_delay_seconds=1.0))
    worker.active_tasks.add(loop_task)

    try:
        while worker.running:
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        worker.stop()
        loop_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
