from .adapters import AdapterError, AdapterErrorKind, AdapterExecution, AdapterRegistry
from .catalog import Catalog
from .models import JobError, JobState, JobView, RetryDisposition
from .store import Store


class Reconciler:
    def __init__(
        self,
        store: Store,
        catalog: Catalog,
        adapters: AdapterRegistry,
        max_attempts: int = 10,
    ) -> None:
        self.store = store
        self.catalog = catalog
        self.adapters = adapters
        self.max_attempts = max_attempts

    async def run_once(self) -> list[JobView]:
        self.store.expire_leases()
        reconciled: list[JobView] = []
        for job in self.store.claim_reconcile_candidates(self.max_attempts):
            request, _ = self.store.get_job_context(job.id)
            action = self.catalog.validate_request(request)
            adapter = self.adapters.get(action.adapter)
            partial_result = job.result or {}
            execution_data = partial_result.get("execution")
            execution = (
                AdapterExecution.model_validate(execution_data)
                if isinstance(execution_data, dict)
                else AdapterExecution(details=partial_result)
            )
            self.store.transition_job(
                job.id, JobState.VERIFYING, result=partial_result, error=job.error
            )
            try:
                observation = await adapter.observe(action, request)
                verification = await adapter.verify(action, request, execution, observation)
                if verification.satisfied:
                    reconciled.append(
                        self.store.transition_job(
                            job.id,
                            JobState.SUCCEEDED,
                            result={
                                **partial_result,
                                "observation": observation.model_dump(mode="json"),
                                "verification": verification.model_dump(mode="json"),
                            },
                        )
                    )
                else:
                    reconciled.append(
                        self.store.transition_job(
                            job.id,
                            JobState.UNKNOWN_OUTCOME,
                            result=partial_result,
                            error=JobError(
                                code=AdapterErrorKind.CONFLICT.value,
                                message="adapter postcondition is not yet satisfied",
                                retry=RetryDisposition.RECONCILE_FIRST,
                                reconciliation_required=True,
                                details=verification.details,
                            ),
                        )
                    )
            except AdapterError as error:
                target = (
                    JobState.UNKNOWN_OUTCOME
                    if error.retry != RetryDisposition.NEVER
                    else JobState.FAILED
                )
                reconciled.append(
                    self.store.transition_job(
                        job.id,
                        target,
                        result=partial_result,
                        error=error.as_job_error(),
                    )
                )
        return reconciled
