import asyncio

from .config import Settings
from .extensions import load_catalog, load_extensions
from .store import Store
from .worker import Worker


async def worker_loop(settings: Settings) -> None:
    store = Store(settings.database_path)
    store.initialize()
    extensions = load_extensions(settings)
    catalog = load_catalog(settings, extensions)
    worker = Worker(settings.worker_id, store, catalog, extensions.adapters)
    while True:
        completed = await worker.run_once()
        await asyncio.sleep(0.25 if completed is not None else 1.0)


def run() -> None:
    asyncio.run(worker_loop(Settings()))


if __name__ == "__main__":
    run()
