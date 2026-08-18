import asyncio
import logging

from .config import Settings
from .extensions import load_catalog, load_extensions
from .reconciler import Reconciler
from .store import Store
from .supervision import backoff_delay

logger = logging.getLogger("deckhand.scheduler")


async def scheduler_loop(settings: Settings) -> None:
    store = Store(settings.database_path)
    store.initialize()
    extensions = load_extensions(settings)
    catalog = load_catalog(settings, extensions)
    reconciler = Reconciler(
        store,
        catalog,
        extensions.adapters,
        max_attempts=settings.reconcile_max_attempts,
    )
    consecutive_failures = 0
    while True:
        try:
            store.expire_stale_queued(settings.queue_ttl_seconds)
            await reconciler.run_once()
            consecutive_failures = 0
            await asyncio.sleep(5)
        except Exception:
            consecutive_failures += 1
            logger.exception("scheduler iteration failed (attempt %d)", consecutive_failures)
            await asyncio.sleep(backoff_delay(consecutive_failures))


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(scheduler_loop(Settings()))


if __name__ == "__main__":
    run()
