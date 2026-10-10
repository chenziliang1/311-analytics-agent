# -*- coding: utf-8 -*-

"""
Ask the deployed agent a few plain questions every morning and check the
numbers it answers with against figures computed another way.

The alarms on the dashboard say when the agent is slow or failing. They say
nothing when it is confidently wrong, which is the failure a user notices
last. The canary asks questions whose answers the semantic layer can compute
without a model, compares, and fails loudly: a CloudWatch metric the alarm
watches, and a GitHub issue with the report.

Two comparisons per question:

- the compiled query's result against the frozen value the metric was signed
  off with (``replay`` in its definition): the warehouse changed;
- the agent's answer against the compiled query's result: the agent changed.

Everything here is testable without an agent or a warehouse: ``run`` takes
the two functions that ask and that compute as arguments.
"""

import dataclasses
import datetime
import json
import time
import typing as T
from pathlib import Path

import yaml

from .eval.grounding import answer_states
from .semantic_layer.api import SemanticRegistry, compile_metric


@dataclasses.dataclass(frozen=True)
class CanaryCase:
    id: str
    question: str
    metric: str
    time_window: str | None = None
    dimension: str | None = None
    #: The frozen value a plain (undimensioned) metric must still compute to,
    #: with its tolerance; from the metric's replay block unless given.
    expected: float | None = None
    tolerance: float | None = None


def load_canary_cases(path: Path, registry: SemanticRegistry) -> list[CanaryCase]:
    """Raises on the first case that names an unknown metric, window or dimension."""
    cases = []
    for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        metric = registry.metrics.get(raw["metric"])
        if metric is None:
            raise ValueError(f"canary {raw.get('id')!r}: unknown metric {raw['metric']!r}")
        window = raw.get("time_window")
        if window and window not in registry.time_windows:
            raise ValueError(f"canary {raw['id']!r}: unknown time window {window!r}")
        dimension = raw.get("dimension")
        if dimension and dimension not in registry.dimensions:
            raise ValueError(f"canary {raw['id']!r}: unknown dimension {dimension!r}")
        expected, tolerance = raw.get("expected"), raw.get("tolerance")
        replay = metric.replay
        if expected is None and not dimension and replay and replay.time_window == window:
            expected, tolerance = replay.expected, replay.tolerance
        cases.append(CanaryCase(
            id=str(raw["id"]), question=str(raw["question"]), metric=metric.name,
            time_window=window, dimension=dimension,
            expected=None if expected is None else float(expected),
            tolerance=None if tolerance is None else float(tolerance),
        ))
    return cases


@dataclasses.dataclass
class CanaryResult:
    case_id: str
    question: str
    #: The metric's value per group, computed by the semantic layer.
    reference: list[float]
    #: The reference's drift from the frozen value, or None.
    drift: str | None
    #: Reference values the answer does not state.
    missing: list[float]
    seconds: float
    answer: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.drift is None and not self.missing


#: Asks the agent one question; returns (answer text, seconds).
Ask = T.Callable[[str], tuple[str, float]]
#: Computes a case's reference values on the warehouse.
Reference = T.Callable[[CanaryCase], list[float]]


def compiled_reference(engine, registry: SemanticRegistry, dialect: str) -> Reference:  # pragma: no cover - needs a database
    from .sql_guard import transpile_sql
    from .sql_utils import fetch_rows

    def reference(case: CanaryCase) -> list[float]:
        compiled = compile_metric(registry, case.metric, case.time_window, [case.dimension] if case.dimension else [])
        result = fetch_rows(engine, transpile_sql(compiled.sql, dialect), 500, timeout_seconds=120)
        if result.error:
            raise RuntimeError(f"reference query failed: {result.error}")
        index = result.columns.index(case.metric)
        return [float(row[index]) for row in result.rows if row[index] is not None]

    return reference


def run(cases: T.Sequence[CanaryCase], ask: Ask, reference: Reference) -> list[CanaryResult]:
    """One result per case; never raises, an exception becomes the result's error."""
    results = []
    for case in cases:
        try:
            values = reference(case)
            drift = None
            if case.expected is not None and not case.dimension:
                actual = values[0] if values else None
                tolerance = case.tolerance if case.tolerance is not None else 0.0
                if actual is None or abs(actual - case.expected) > tolerance:
                    drift = f"the warehouse computes {actual}, the signed-off value is {case.expected} (tolerance {tolerance})"
            answer, seconds = ask(case.question)
            missing = [v for v in values if not answer_states(answer, v)]
            results.append(CanaryResult(case.id, case.question, values, drift, missing, seconds, answer))
        except Exception as e:
            results.append(CanaryResult(case.id, case.question, [], None, [], 0.0, "", error=f"{type(e).__name__}: {e}"))
    return results


def render(results: T.Sequence[CanaryResult]) -> str:
    failed = [r for r in results if not r.ok]
    lines = [f"{len(results)} canary questions, {len(failed)} failed", ""]
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        lines.append(f"  {mark}  {r.case_id:28s} {r.seconds:5.1f}s")
        if r.error:
            lines.append(f"        error: {r.error}")
        if r.drift:
            lines.append(f"        drift: {r.drift}")
        if r.missing:
            lines.append(f"        the answer does not state: {', '.join(str(v) for v in r.missing)}")
            lines.append(f"        answer: {' '.join(r.answer.split())[:240]}")
    return "\n".join(lines)


def summary(results: T.Sequence[CanaryResult], target: str) -> dict:
    return {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "target": target,
        "questions": len(results),
        "failed": sum(1 for r in results if not r.ok),
        "results": [dataclasses.asdict(r) for r in results],
    }


def remote_ask(client, runtime_arn: str) -> Ask:  # pragma: no cover - calls the deployed agent
    """Each question in its own chat, as the production agent, as the user "canary"."""
    from .chat import invoke_remote_turn

    def ask(question: str) -> tuple[str, float]:
        body = {
            "id": f"canary-{int(time.time())}",
            "messages": [{"id": "u1", "role": "user", "parts": [{"type": "text", "text": question}]}],
        }
        started = time.monotonic()
        result = invoke_remote_turn(client, runtime_arn, body, user="canary")
        return result.answer, time.monotonic() - started

    return ask


def local_ask() -> Ask:  # pragma: no cover - drives the local agent
    """The same agent code in this process, for a canary without a deployment."""
    from .eval.live import app_chat

    chat = app_chat()

    def ask(question: str) -> tuple[str, float]:
        turn = chat([("user", question)])
        return turn.answer, turn.seconds

    return ask


def publish(metrics, results: T.Sequence[CanaryResult]) -> None:
    """The two numbers the dashboard and the alarm read."""
    metrics.put("CanaryQuestions", len(results))
    metrics.put("CanaryFailures", sum(1 for r in results if not r.ok))


def write_report(directory: Path, report: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}-canary.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
