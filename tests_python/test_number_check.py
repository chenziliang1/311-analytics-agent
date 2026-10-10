# -*- coding: utf-8 -*-

"""
The live answer check, against a fictional widget shop.
"""

from agent_app.chat import ChatResult
from agent_app.number_check import unverified_numbers


def query(rows, sql="SELECT plant, COUNT(*) FROM widget GROUP BY plant", warning=None):
    return ("warehouse_query", {"status": "ok", "rows": rows, "row_count": len(rows), "sql": sql, "warning": warning})


def test_numbers_the_query_returned_pass():
    results = [query([["north", 1204], ["south", 0.346]])]
    assert unverified_numbers("North made 1,204 widgets; south's rate is 34.6%.", results) == []


def test_a_number_no_query_returned_is_flagged():
    results = [query([["north", 1204]])]
    assert unverified_numbers("North made 1,204 widgets and south made 987.", results) == ["987"]


def test_no_query_at_all_flags_every_data_number():
    """The Nova failure: a follow-up answered from memory, with fresh numbers."""
    assert unverified_numbers("That rose to 1,530 last month.", []) == ["1,530"]


def test_numbers_already_in_the_conversation_are_not_new():
    conversation = ["How many widgets?", "North made 1,204 widgets.", "And as a share of 5,000?"]
    assert unverified_numbers("1,204 of 5,000.", [], conversation) == []


def test_numbers_in_the_query_or_its_warning_are_evidence():
    results = [query([["north", 12]], sql="SELECT ... WHERE weight > 250", warning="Only the first 5000 rows were returned.")]
    assert unverified_numbers("Widgets over 250 g, capped at 5000 rows.", results) == []


def test_chart_spec_numbers_count_as_handed_over():
    results = [query([["north", 1204]]), ("visualize", {"status": "ok", "spec": {"height": 320}})]
    assert unverified_numbers("1,204 widgets, chart at 320 px.", results) == []


def test_one_step_arithmetic_on_two_results_is_not_flagged():
    """A ratio, a difference, a share: the agent doing its job, not inventing."""
    results = [query([["north", 1200], ["south", 300], ["rate_a", 0.784], ["rate_b", 0.58]])]
    answer = "North made 1,200, 4.0 times south's 300, so 900 more; south is 20% of north. The gap is 20.4 points."
    assert unverified_numbers(answer, results) == []


def test_two_steps_of_arithmetic_are_still_flagged():
    """Over-reports past one step on purpose: the UI says "check if derived", never "wrong"."""
    results = [query([["north", 1200], ["south", 300]])]
    # 1200 / (1200 + 300) needs a sum and then a ratio.
    assert unverified_numbers("North is 80% of the total.", results) == ["80%"]


def test_a_bare_year_is_a_date_not_a_result():
    """"May 2026" and "2025-06-01" are the data's own calendar, never a figure to verify."""
    assert unverified_numbers("Volume peaked in May 2026, up from 2025-06-01; 1,204 that month.", [query([["m", 1204]])]) == []


def test_empty_answer():
    assert unverified_numbers("", [query([["n", 1]])]) == []


def test_each_number_listed_once():
    assert unverified_numbers("987, then 987 again.", []) == ["987"]


def test_chat_result_round_trips_the_flagged_numbers():
    result = ChatResult("a", "", unverified_numbers=["987"])
    assert ChatResult.from_json(result.to_json()).unverified_numbers == ["987"]
    assert ChatResult.from_json({"answer": "a"}).unverified_numbers == []
