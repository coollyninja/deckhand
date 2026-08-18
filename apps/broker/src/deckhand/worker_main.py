import asyncio
import logging

from .config import Settings
from .extensions import load_catalog, load_extensions
from .store import Store
from .supervision import backoff_delay
from .worker import Worker

logger = logging.getLogger("deckhand.worker")


async def worker_loop(settings: Settings) -> None:
    store = Store(settings.database_path)
    store.initialize()
    extensions = load_extensions(settings)
    catalog = load_catalog(settings, extensions)
    worker = Worker(settings.worker_id, store, catalog, extensions.adapters)
    consecutive_failures = 0
    while True:
        try:
            completed = await worker.run_once()
            consecutive_failures = 0
            await asyncio.sleep(0.25 if completed is not None else 1.0)
        except Exception:
            # One poison job (catalog drift, an unexpected store/adapter error)
            # must degrade this worker to a backoff-retry, never crash-loop the
            # whole execution engine. The offending job is left in its current
            # state; claim_next_job selects only QUEUED, so the loop continues to
            # drain other work.
            consecutive_failures += 1
            logger.exception("worker iteration failed (attempt %d)", consecutive_failures)
            await asyncio.sleep(backoff_delay(consecutive_failures))


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(worker_loop(Settings()))


if __name__ == "__main__":
    run()
