# -*- coding: utf-8 -*-

"""
The eval harness against a fictional widget shop: no model, no database.
"""

import pytest

from agent_app.eval.grounding import unsupported_numbers
from agent_app.eval.judge import build_judge_request, parse_verdict
from agent_app.eval.live import tool_activity
from agent_app.eval.matching import golden_rows, match_result, numbers_close, unpivoted
from agent_app.eval.model import GoldenCase, MatchSpec, load_golden_cases
from agent_app.eval.report import render, summarize
from agent_app.eval.runner import Turn, run_all, run_case

COLS = ["plant_code", "is_new", "widgets", "defect_rate"]
ROWS = [
    ["oslo", 0, 1200, 0.346],
    ["lima", 1, 800, 0.306],
    ["kyiv", 0, 450, 0.289],
]


class TestNumbersClose:
    @pytest.mark.parametrize(
        "actual, expected",
        [(0.3461, 0.346), (34.6, 0.346), (0.346, 0.3462), (1200, 1200), (1200.4, 1200), (24.53, 0.2453)],
    )
    def test_close(self, actual, expected):
        assert numbers_close(actual, expected)

    @pytest.mark.parametrize(
        "actual, expected",
        # A rate cut to two decimals blurs rankings, so it does not pass.
        [(0.33, 0.346), (0.35, 0.3462), (1198, 1200), (3106, 3100)],
    )
    def test_not_close(self, actual, expected):
        assert not numbers_close(actual, expected)


class TestMatchResult:
    def test_ordered_top_with_labels(self):
        spec = MatchSpec(labels=["plant_code"], values=["defect_rate"], top=2, ordered=True)
        agent = [["Oslo", 34.6, "x"], ["lima", 0.306], ["kyiv", 0.289]]
        assert match_result(COLS, ROWS, agent, spec).ok

    def test_order_matters_when_ordered(self):
        spec = MatchSpec(labels=["plant_code"], values=["defect_rate"], ordered=True)
        agent = [["lima", 0.306], ["oslo", 0.346], ["kyiv", 0.289]]
        assert not match_result(COLS, ROWS, agent, spec).ok
        assert match_result(COLS, ROWS, agent, spec.model_copy(update={"ordered": False})).ok

    def test_ties_may_come_in_either_order(self):
        cols, rows = ["k", "v"], [["a", 0.5], ["b", 0.4], ["c", 0.4], ["d", 0.3]]
        spec = MatchSpec(labels=["k"], values=["v"], ordered=True)
        assert match_result(cols, rows, [["a", 0.5], ["c", 0.4], ["b", 0.4], ["d", 0.3]], spec).ok
        assert not match_result(cols, rows, [["a", 0.5], ["d", 0.3], ["b", 0.4], ["c", 0.4]], spec).ok

    def test_where_selects_rows_and_flags_need_no_label(self):
        spec = MatchSpec(values=["widgets", "defect_rate"], where=[{"is_new": 1}])
        assert [r["plant_code"] for r in golden_rows(COLS, ROWS, spec)] == ["lima"]
        assert match_result(COLS, ROWS, [["New plants", 800, 0.306]], spec).ok

    def test_pivoted_result_matches(self):
        """Months as columns: the label is one cell, the value another."""
        cols, rows = ["month", "channel", "n"], [["2026-05", "web", 10], ["2026-05", "ivr", 7]]
        spec = MatchSpec(labels=["month"], values=["n"])
        assert match_result(cols, rows, [["2026-05", 10, 7]], spec).ok

    def test_missing_row_is_reported(self):
        spec = MatchSpec(labels=["plant_code"], values=["defect_rate"])
        outcome = match_result(COLS, ROWS, [["oslo", 0.346], ["lima", 0.306]], spec)
        assert not outcome.ok
        assert outcome.missing == [dict(zip(COLS, ROWS[2]))]

    def test_one_agent_row_cannot_match_two_golden_rows(self):
        cols, rows = ["k", "v"], [["a", 1.5], ["a", 1.5]]
        spec = MatchSpec(labels=["k"], values=["v"])
        assert not match_result(cols, rows, [["a", 1.5]], spec).ok

    def test_empty_selection_never_passes(self):
        spec = MatchSpec(values=["widgets"], where=[{"is_new": 9}])
        assert not match_result(COLS, ROWS, [[1]], spec).ok


class TestUnsupportedNumbers:
    def test_rounded_and_percent_forms_are_supported(self):
        answer = "Oslo leads at 34.6% (0.35 rounded), with 1,200 widgets."
        assert unsupported_numbers(answer, [0.346, 1200], "") == []

    def test_invented_number_is_reported(self):
        assert unsupported_numbers("Defects rose to 41.2%.", [0.346], "") == ["41.2%"]

    def test_skips_sql_dates_small_ints_and_question_numbers(self):
        answer = "In the top 5 over 2026-05 (asked for 500):\n```sql\nLIMIT 777\n```\n500 plants"
        assert unsupported_numbers(answer, [], "plants with at least 500 widgets") == []


class TestJudge:
    def test_request_carries_every_part(self):
        text = build_judge_request(
            ["first", "second"], COLS, ROWS, [{"status": "ok", "columns": ["a"], "rows": [[1]]}],
            "the answer", ["41.2%"],
        )
        for piece in ("1. first", "2. second", "oslo", "status ok", "the answer", "41.2%"):
            assert piece in text

    def test_parse(self):
        v = parse_verdict('Sure.\n{"correct": true, "hallucinated": false, "reason": "ok"}')
        assert (v.correct, v.hallucinated, v.reason) == (True, False, "ok")
        assert parse_verdict("no json here").correct is None


def test_tool_activity_pairs_results_to_calls():
    messages = [
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "1", "name": "warehouse_query", "input": {"intent": "x"}}},
            {"toolUse": {"toolUseId": "2", "name": "visualize", "input": {}}},
        ]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "1", "content": [{"text": '{"status": "ok", "rows": [[1]]}'}]}},
            {"toolResult": {"toolUseId": "2", "content": [{"text": '{"status": "ok", "block": "b"}'}]}},
        ]},
    ]
    calls, outputs = tool_activity(messages)
    assert [c[0] for c in calls] == ["warehouse_query", "visualize"]
    assert outputs == [{"status": "ok", "rows": [[1]]}]


CASE = GoldenCase(
    id="W01-defects",
    source="test",
    questions=["Which plants have the highest defect rate?"],
    history=["How many plants are there?"],
    reference_sql="SELECT ...",
    match=MatchSpec(labels=["plant_code"], values=["defect_rate"], top=2, ordered=True),
    metrics=["defect_rate"],
)


def scripted_chat(answer, outputs):
    seen = []

    def chat(conversation):
        seen.append(list(conversation))
        return Turn(answer=answer, seconds=2.0, tool_calls=[], query_outputs=outputs)

    chat.seen = seen
    return chat


def test_run_case_scores_everything():
    chat = scripted_chat(
        "Oslo 34.6%, Lima 30.6%.\nHow this was computed: returned / shipped. [KB:metric/defect_rate]",
        [{"status": "ok", "rows": [["oslo", 0.346], ["lima", 0.306]]}],
    )
    judged = []

    def judge(request):
        judged.append(request)
        return '{"correct": true, "hallucinated": false, "reason": "matches"}'

    score = run_case(CASE, CASE.questions[0], chat, lambda sql: (COLS, ROWS), judge)
    assert score.sql_ok and score.answer_ok and score.hallucinated is False
    assert score.cited is True
    # The history turn ran first and its answer was carried into the graded turn.
    assert chat.seen[1][0] == ("user", "How many plants are there?")
    assert chat.seen[1][1][0] == "assistant"
    assert "1. How many plants are there?" in judged[0]


class TestCitedCorrectly:
    """A citation names the document the number rests on; a sentence does not."""

    @staticmethod
    def score(answer, knows=None, metrics=("defect_rate",)):
        from agent_app.eval.runner import cited_correctly

        return cited_correctly(answer, list(metrics), knows)

    def test_the_metrics_own_document(self):
        assert self.score("34.6%. How this was computed: x. [KB:metric/defect_rate]") is True

    def test_an_explanation_without_a_citation_is_not_enough(self):
        assert self.score("34.6%. How this was computed: returned over shipped.") is False

    def test_citing_only_something_else(self):
        assert self.score("34.6% [KB:term/scrap-bin]") is False

    def test_an_invented_source_fails_even_next_to_a_right_one(self):
        known = {"metric/defect_rate"}
        assert self.score("[KB:metric/defect_rate] [KB:metric/profit]", knows=known.__contains__) is False
        assert self.score("[KB:metric/defect_rate]", knows=known.__contains__) is True

    def test_no_metric_no_verdict(self):
        assert self.score("There are 2 plants.", metrics=()) is None


def test_earlier_turn_results_count_as_evidence():
    """A follow-up may repeat what the first turn's query returned."""
    turns = iter([
        Turn("There are 2 plants.", 1.0, [], [{"status": "ok", "sql": "SELECT 42.5", "rows": [[42.5]]}]),
        Turn("Oslo 34.6%, Lima 30.6%, and as before 42.5.", 1.0, [],
             [{"status": "ok", "rows": [["oslo", 0.346], ["lima", 0.306]]}]),
    ])
    judged = []

    def judge(request):
        judged.append(request)
        return '{"correct": true, "hallucinated": false, "reason": ""}'

    score = run_case(CASE, CASE.questions[0], lambda conv: next(turns), lambda sql: (COLS, ROWS), judge)
    assert score.unsupported == []
    assert "(an earlier turn)" in judged[0] and "SELECT 42.5" in judged[0]


def test_run_case_never_raises():
    def broken(conversation):
        raise RuntimeError("endpoint down")

    score = run_case(CASE, CASE.questions[0], broken, lambda sql: (COLS, ROWS), None)
    assert not score.sql_ok
    assert "endpoint down" in score.error


def test_summary_and_render():
    good = scripted_chat("Oslo 34.6%. How this was computed: x. [KB:metric/defect_rate]", [{"status": "ok", "rows": [["oslo", 0.346], ["lima", 0.306]]}])
    scores = run_all([CASE], good, lambda sql: (COLS, ROWS), None, knows={"metric/defect_rate"}.__contains__)
    summary = summarize(scores)
    assert summary["metrics"]["sql_accuracy"] == 1.0
    assert summary["metrics"]["citation_rate"] == 1.0
    assert summary["metrics"]["answer_accuracy"] is None  # judge off
    assert summary["passed"]["sql_accuracy"] is True
    assert summary["passed"]["answer_accuracy"] is None
    assert "W01-defects" in render(summary)


def test_results_file_records_each_run(tmp_path):
    import datetime

    from agent_app.eval.report import RESULTS_HEADER, append_results

    good = scripted_chat("Oslo 34.6%. [KB:metric/defect_rate]", [{"status": "ok", "rows": [["oslo", 0.346], ["lima", 0.306]]}])
    summary = summarize(run_all([CASE], good, lambda sql: (COLS, ROWS), None))
    summary["config"] = {"agent": "a/m", "writer": "a/w", "judge": None, "cases": "W01"}
    path = tmp_path / "results.md"
    when = datetime.datetime(2026, 10, 10, 3, 4, tzinfo=datetime.timezone.utc)
    row = append_results(path, summary, when, note="first | run")
    assert row == "| 2026-10-10 03:04 | a/m | a/w | off | W01 | 1 | 0 | 100.0% |  |  | 100.0% | 2.0 | first / run |\n"
    assert path.read_text(encoding="utf-8") == RESULTS_HEADER + row
    append_results(path, summary, when)
    assert path.read_text(encoding="utf-8").count("| 2026-10-10 03:04 |") == 2  # appended, header once


def test_load_golden_cases(tmp_path):
    path = tmp_path / "golden.yml"
    path.write_text(
        "- id: W01\n  source: t\n  questions: [q]\n  reference_sql: SELECT 1\n"
        "  match: {values: [n]}\n"
        "- id: W01\n  source: t\n  questions: [q]\n  reference_sql: SELECT 1\n"
        "  match: {values: [n]}\n"
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_golden_cases(path)


def test_a_result_pivoted_wide_matches_once_unpivoted():
    spec = MatchSpec(labels=["plant_code"], values=["widgets"])
    ref_cols, ref_rows = ["plant_code", "widgets"], [["north", 40], ["south", 12]]
    wide_cols, wide_rows = ["total", "north", "south"], [[52, 40, 12]]
    assert not match_result(ref_cols, ref_rows, wide_rows, spec).ok
    assert match_result(ref_cols, ref_rows, unpivoted(wide_cols, wide_rows), spec).ok
    # A wrong number is still wrong, however the result is laid out.
    assert not match_result(ref_cols, ref_rows, unpivoted(wide_cols, [[52, 41, 12]]), spec).ok
