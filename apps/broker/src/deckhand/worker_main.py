import asyncio

from .adapters import AdapterRegistry, DisabledMutationAdapter, FakeAdapter
from .catalog import Catalog
from .config import Settings
from .store import Store
from .worker import Worker


async def worker_loop(settings: Settings) -> None:
    store = Store(settings.database_path)
    store.initialize()
    catalog = Catalog.from_path(settings.catalog_path)
    adapters = AdapterRegistry(
        {
            "fake": FakeAdapter(),
            "proxmox": DisabledMutationAdapter("proxmox"),
        }
    )
    worker = Worker(settings.worker_id, store, catalog, adapters)
    while True:
        completed = await worker.run_once()
        await asyncio.sleep(0.25 if completed is not None else 1.0)


def run() -> None:
    asyncio.run(worker_loop(Settings()))


if __name__ == "__main__":
    run()
