from .adapters import AdapterError, AdapterRegistry
from .catalog import Catalog
from .models import JobState, JobView
from .store import Store


class Reconciler:
    def __init__(self, store: Store, catalog: Catalog, adapters: AdapterRegistry) -> None:
        self.store = store
        self.catalog = catalog
        self.adapters = adapters

    async def run_once(self) -> list[JobView]:
        self.store.expire_leases()
        reconciled: list[JobView] = []
        for job in self.store.list_jobs(JobState.UNKNOWN_OUTCOME):
            request, _ = self.store.get_job_context(job.id)
            action = self.catalog.validate_request(request)
            adapter = self.adapters.get(action.adapter)
            try:
                observed = await adapter.verify(action, request, job.result or {})
                reconciled.append(
                    self.store.transition_job(job.id, JobState.SUCCEEDED, result=observed)
                )
            except AdapterError as error:
                reconciled.append(
                    self.store.transition_job(job.id, JobState.FAILED, error=str(error))
                )
        return reconciled
