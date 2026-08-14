import asyncio

from .adapters import AdapterRegistry, DisabledMutationAdapter, FakeAdapter
from .catalog import Catalog
from .config import Settings
from .reconciler import Reconciler
from .store import Store


async def scheduler_loop(settings: Settings) -> None:
    store = Store(settings.database_path)
    store.initialize()
    catalog = Catalog.from_path(settings.catalog_path)
    adapters = AdapterRegistry(
        {
            "fake": FakeAdapter(),
            "proxmox": DisabledMutationAdapter("proxmox"),
        }
    )
    reconciler = Reconciler(store, catalog, adapters)
    while True:
        await reconciler.run_once()
        await asyncio.sleep(5)


def run() -> None:
    asyncio.run(scheduler_loop(Settings()))


if __name__ == "__main__":
    run()
