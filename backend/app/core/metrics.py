"""Prometheus metrics for the remote agent connection layer.

These metrics expose what an operator needs to diagnose a flapping
agent fleet without trawling logs:

- ``agent_connections`` — number of currently registered WebSocket
  connections. A drop here is the first signal that an agent host
  went offline.
- ``agent_pending_requests{agent_id}`` — per-agent in-flight + queued
  request count. Climbing values mean the agent is back-pressured;
  hitting ``MAX_PENDING_PER_AGENT`` produces ``AgentBusyError``.
- ``agent_request_duration_seconds{op}`` — wall-clock duration of
  ``send_request`` calls labelled by ``msg_type`` (exec, read_file,
  grep, …). Use the histogram buckets to spot slow operations.
- ``agent_request_errors_total{reason}`` — counter of failed requests
  bucketed by failure mode. The reason values are bounded:
  ``offline``, ``busy``, ``timeout``, ``agent_error``, ``internal``.

All metrics are registered on the default ``prometheus_client``
registry, so importing this module is the only setup needed. The
``/metrics`` HTTP endpoint serialises the same registry.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Number of agents currently registered with AgentConnectionManager.
agent_connections = Gauge(
    "agent_connections",
    "Number of agent WebSocket connections currently registered.",
)

# Per-agent pending count. Mirrors AgentConnectionManager._pending_count
# so operators can see back-pressure build up before it trips
# AgentBusyError. Cardinality is bounded by the size of the agent
# fleet (~tens), which is well within Prometheus best practices.
agent_pending_requests = Gauge(
    "agent_pending_requests",
    "In-flight + queued remote requests per agent.",
    labelnames=("agent_id",),
)

# Wall-clock duration of send_request, labelled by the msg_type so
# slow operations (e.g. remote_exec running a long shell command)
# can be distinguished from fast ones (e.g. file_exists). Buckets
# cover sub-millisecond config calls all the way to the
# REMOTE_MAX_TIMEOUT_SECONDS=300 hard ceiling.
agent_request_duration_seconds = Histogram(
    "agent_request_duration_seconds",
    "Wall-clock duration of remote agent requests.",
    labelnames=("op",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# Failed-request counter, bucketed by a small fixed set of reasons
# so the cardinality cannot explode no matter how many distinct
# error messages the agent emits.
agent_request_errors_total = Counter(
    "agent_request_errors_total",
    "Remote agent request failures, bucketed by reason.",
    labelnames=("reason",),
)
