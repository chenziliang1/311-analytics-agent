# -*- coding: utf-8 -*-

"""
Operational metrics for the CloudWatch dashboard and alarms.

Published from a background thread: a metric is never worth a slower answer,
and a CloudWatch outage must never fail a user's question. Off unless
METRICS_NAMESPACE is set, so local runs and tests publish nothing.
"""

import concurrent.futures
import typing as T

from .utils import debug

#: Dimension every metric carries, so the dashboard can tell the two runtimes
#: (and a local run pointed at the namespace) apart.
COMPONENT = "Component"


class Metrics:
    def __init__(self, client_factory: T.Callable[[], T.Any], namespace: str, component: str):
        self._client_factory = client_factory
        self._client = None
        self.namespace = namespace
        self.component = component
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="metrics")

    def put(
        self,
        name: str,
        value: float,
        unit: str = "Count",
        dimensions: dict[str, str] | None = None,
    ) -> concurrent.futures.Future:
        """Queue one data point; returns the future so tests can wait on it."""
        dims = [{"Name": COMPONENT, "Value": self.component}]
        dims += [{"Name": k, "Value": str(v)} for k, v in (dimensions or {}).items()]
        datum = {"MetricName": name, "Value": float(value), "Unit": unit, "Dimensions": dims}
        return self._pool.submit(self._send, datum)

    def _send(self, datum: dict) -> None:
        try:
            if self._client is None:
                self._client = self._client_factory()
            self._client.put_metric_data(Namespace=self.namespace, MetricData=[datum])
        except Exception as e:  # pragma: no cover - logged, never raised
            debug(f"[Metrics] dropped {datum['MetricName']}: {type(e).__name__}: {e}")


class NoMetrics:
    """What a process gets when no namespace is configured."""

    def put(self, *args, **kwargs) -> None:
        return None


def query_metrics_sink(metrics: "Metrics | NoMetrics") -> T.Callable[[dict], None]:
    """
    An audit sink that turns each warehouse query record into metrics:
    a count by status (the error-rate and blocked-query alarms read it) and
    the tool's latency.
    """

    def write(record: dict) -> None:
        metrics.put("WarehouseQueries", 1, dimensions={"Status": record.get("status", "unknown")})
        metrics.put("WarehouseQueryLatency", record.get("seconds", 0.0), unit="Seconds")

    return write
