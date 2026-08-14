from prometheus_client import Counter, Histogram

POLICY_DECISIONS = Counter(
    "deckhand_policy_decisions_total",
    "Policy decisions made by the broker",
    ("phase", "outcome"),
)
JOBS_SUBMITTED = Counter(
    "deckhand_jobs_submitted_total",
    "Durable jobs submitted",
    ("action_id",),
)
STATUS_OBSERVATION_SECONDS = Histogram(
    "deckhand_status_observation_seconds",
    "Time spent gathering normalized status",
    ("scope",),
)
