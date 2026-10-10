# -*- coding: utf-8 -*-

import json

import pytest

from agent_app import canary
from agent_app.canary import CanaryCase, CanaryResult, load_canary_cases, run
from agent_app.eval.grounding import answer_states
from agent_app.paths import path_enum
from agent_app.semantic_layer.api import load_registry


@pytest.fixture(scope="module")
def registry():
    return load_registry(path_enum.dir_semantic_layer)


class TestAnswerStates:
    def test_a_rate_shown_as_a_percent(self):
        assert answer_states("The SLA breach rate was 21.4% over the period.", 0.2139)
        assert answer_states("about 21%", 0.2139)
        assert not answer_states("The SLA breach rate was 24.1%.", 0.2139)

    def test_a_count_with_separators(self):
        assert answer_states("There were 1,234 requests.", 1234.0)
        assert not answer_states("There were 1,235 requests.", 1234.0)

    def test_sql_blocks_and_empty_answers_do_not_count(self):
        assert not answer_states("```sql\nSELECT 0.2139\n```", 0.2139)
        assert not answer_states("", 0.2139)
        assert not answer_states(None, 0.2139)


class TestLoadCases:
    def test_the_committed_cases_name_real_metrics_windows_and_dimensions(self, registry):
        cases = load_canary_cases(path_enum.path_canary_yml, registry)
        assert len(cases) >= 4
        assert len({c.id for c in cases}) == len(cases)
        plain = [c for c in cases if not c.dimension]
        # Every undimensioned case checks the warehouse against the signed-off value.
        assert plain and all(c.expected is not None and c.tolerance is not None for c in plain)
        dimensioned = [c for c in cases if c.dimension]
        assert dimensioned and all(c.expected is None for c in dimensioned)

    def test_an_unknown_metric_or_window_fails_loudly(self, registry, tmp_path):
        bad = tmp_path / "c.yml"
        bad.write_text("- {id: X, question: q, metric: nope}\n")
        with pytest.raises(ValueError, match="unknown metric"):
            load_canary_cases(bad, registry)
        bad.write_text("- {id: X, question: q, metric: sla_breach_rate, time_window: next_week}\n")
        with pytest.raises(ValueError, match="unknown time window"):
            load_canary_cases(bad, registry)
        bad.write_text("- {id: X, question: q, metric: sla_breach_rate, dimension: colour}\n")
        with pytest.raises(ValueError, match="unknown dimension"):
            load_canary_cases(bad, registry)

    def test_an_explicit_expected_value_wins_over_the_replay(self, registry, tmp_path):
        f = tmp_path / "c.yml"
        f.write_text("- {id: X, question: q, metric: sla_breach_rate, time_window: trailing_12m, expected: 0.5, tolerance: 0.1}\n")
        [case] = load_canary_cases(f, registry)
        assert (case.expected, case.tolerance) == (0.5, 0.1)


PLAIN = CanaryCase("C01", "breach rate?", "sla_breach_rate", "trailing_12m", None, expected=0.2139, tolerance=0.0005)
BY_TIER = CanaryCase("C02", "by tier?", "sla_breach_rate", "trailing_12m", "size_tier")


def asks(*answers):
    replies = iter(answers)
    return lambda question: (next(replies), 1.5)


class TestRun:
    def test_pass_when_the_answer_states_every_reference_value(self):
        results = run([PLAIN, BY_TIER], asks("It was 21.4%.", "Small 10.0%, medium 20.0%, large 30.5%."),
                      lambda case: [0.214] if case is PLAIN else [0.10, 0.20, 0.305])
        assert [r.ok for r in results] == [True, True]
        assert results[1].missing == []

    def test_a_missing_figure_fails_and_is_named(self):
        [r] = run([BY_TIER], asks("Small 10.0%, medium 20.0%."), lambda case: [0.10, 0.20, 0.305])
        assert not r.ok
        assert r.missing == [0.305]
        assert r.drift is None

    def test_warehouse_drift_fails_even_when_the_agent_repeats_it(self):
        [r] = run([PLAIN], asks("It was 25.0%."), lambda case: [0.25])
        assert not r.ok
        assert r.missing == [] and "signed-off value is 0.2139" in r.drift

    def test_an_exception_becomes_the_result_not_a_crash(self):
        def reference(case):
            raise RuntimeError("warehouse down")

        [r] = run([PLAIN], asks("x"), reference)
        assert not r.ok and r.error == "RuntimeError: warehouse down"


def test_render_summary_publish_and_report(tmp_path):
    results = [
        CanaryResult("C01", "q", [0.214], None, [], 1.0, "21.4%"),
        CanaryResult("C02", "q", [0.1, 0.2], None, [0.2], 2.0, "10%"),
        CanaryResult("C03", "q", [], None, [], 0.0, "", error="RuntimeError: x"),
    ]
    text = canary.render(results)
    assert text.startswith("3 canary questions, 2 failed")
    assert "PASS  C01" in text and "FAIL  C02" in text and "does not state: 0.2" in text and "error: RuntimeError: x" in text

    report = canary.summary(results, "remote")
    assert (report["questions"], report["failed"], report["target"]) == (3, 2, "remote")
    path = canary.write_report(tmp_path / "canary", report)
    assert json.loads(path.read_text())["failed"] == 2

    puts = []

    class Metrics:
        def put(self, name, value):
            puts.append((name, value))

    canary.publish(Metrics(), results)
    assert puts == [("CanaryQuestions", 3), ("CanaryFailures", 2)]


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(__file__, "agent_app.canary", preview=False)
