from .adapters import AdapterError, AdapterRegistry, UnknownOutcome
from .catalog import Catalog
from .models import JobState, JobView
from .store import Store


class Worker:
    def __init__(
        self, worker_id: str, store: Store, catalog: Catalog, adapters: AdapterRegistry
    ) -> None:
        self.worker_id = worker_id
        self.store = store
        self.catalog = catalog
        self.adapters = adapters

    async def run_once(self) -> JobView | None:
        job = self.store.claim_next_job(self.worker_id)
        if job is None:
            return None
        request, _ = self.store.get_job_context(job.id)
        action = self.catalog.validate_request(request)
        adapter = self.adapters.get(action.adapter)
        try:
            result = await adapter.execute(action, request)
            self.store.transition_job(job.id, JobState.VERIFYING, result=result)
            verified = await adapter.verify(action, request, result)
            return self.store.transition_job(job.id, JobState.SUCCEEDED, result=verified)
        except UnknownOutcome as error:
            return self.store.transition_job(job.id, JobState.UNKNOWN_OUTCOME, error=str(error))
        except AdapterError as error:
            return self.store.transition_job(job.id, JobState.FAILED, error=str(error))
