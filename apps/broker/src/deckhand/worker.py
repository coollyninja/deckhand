from .adapters import AdapterError, AdapterErrorKind, AdapterRegistry
from .catalog import Catalog
from .models import JobState, JobView, RetryDisposition
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
            execution = await adapter.execute(action, request)
            partial_result = {"execution": execution.model_dump(mode="json")}
            self.store.transition_job(job.id, JobState.VERIFYING, result=partial_result)
            observation = await adapter.observe(action, request)
            verification = await adapter.verify(action, request, execution, observation)
            if not verification.satisfied:
                raise AdapterError(
                    "adapter postcondition is not yet satisfied",
                    kind=AdapterErrorKind.CONFLICT,
                    retry=RetryDisposition.RECONCILE_FIRST,
                    reconciliation_required=True,
                    details=verification.details,
                )
            return self.store.transition_job(
                job.id,
                JobState.SUCCEEDED,
                result={
                    **partial_result,
                    "observation": observation.model_dump(mode="json"),
                    "verification": verification.model_dump(mode="json"),
                },
            )
        except AdapterError as error:
            target = JobState.UNKNOWN_OUTCOME if error.reconciliation_required else JobState.FAILED
            return self.store.transition_job(job.id, target, error=error.as_job_error())
