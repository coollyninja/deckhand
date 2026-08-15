from prometheus_client import Counter, Gauge, Histogram

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
PLUGIN_CALLS = Counter(
    "deckhand_plugin_calls_total",
    "Plugin lifecycle calls by normalized outcome",
    ("plugin", "operation", "outcome"),
)
PLUGIN_CALL_SECONDS = Histogram(
    "deckhand_plugin_call_seconds",
    "End-to-end plugin lifecycle call latency, including local admission control",
    ("plugin", "operation"),
)
PLUGIN_QUEUE_SECONDS = Histogram(
    "deckhand_plugin_queue_seconds",
    "Time spent waiting for a plugin rate or concurrency slot",
    ("plugin",),
)
PLUGIN_IN_FLIGHT = Gauge(
    "deckhand_plugin_in_flight",
    "Plugin calls currently executing",
    ("plugin",),
)
PLUGIN_CIRCUIT_STATE = Gauge(
    "deckhand_plugin_circuit_state",
    "Plugin circuit state: 0 closed, 0.5 half-open, 1 open",
    ("plugin",),
)
