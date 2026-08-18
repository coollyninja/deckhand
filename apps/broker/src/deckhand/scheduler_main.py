import asyncio
import logging

from .config import Settings
from .extensions import load_catalog, load_extensions
from .reconciler import Reconciler
from .store import Store, load_audit_key
from .supervision import backoff_delay

logger = logging.getLogger("deckhand.scheduler")

# Re-verify the audit chain roughly every this-many scheduler ticks (~5s each).
_AUDIT_VERIFY_EVERY_TICKS = 60


async def scheduler_loop(settings: Settings) -> None:
    store = Store(settings.database_path, audit_hmac_key=load_audit_key(settings))
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
    tick = 0
    while True:
        try:
            store.expire_stale_queued(settings.queue_ttl_seconds)
            await reconciler.run_once()
            tick += 1
            if tick % _AUDIT_VERIFY_EVERY_TICKS == 0 and not store.verify_audit_chain():
                logger.error("audit chain verification FAILED during scheduler tick")
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
