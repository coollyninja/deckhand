import asyncio

from .config import Settings
from .extensions import load_catalog, load_extensions
from .reconciler import Reconciler
from .store import Store


async def scheduler_loop(settings: Settings) -> None:
    store = Store(settings.database_path)
    store.initialize()
    extensions = load_extensions(settings)
    catalog = load_catalog(settings, extensions)
    reconciler = Reconciler(store, catalog, extensions.adapters)
    while True:
        await reconciler.run_once()
        await asyncio.sleep(5)


def run() -> None:
    asyncio.run(scheduler_loop(Settings()))


if __name__ == "__main__":
    run()
