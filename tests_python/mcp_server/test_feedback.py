# -*- coding: utf-8 -*-

"""Thumbs up or down: the record, its sinks, and the draft a thumbs-down becomes."""

import datetime

import pytest
import sqlalchemy as sa
import yaml

from agent_app.mcp_server.feedback import (
    Feedback,
    draft_golden_case,
    fan_out_feedback,
    jsonl_feedback_log,
    read_jsonl_feedback,
    read_session_queries,
    read_warehouse_feedback,
    warehouse_feedback_log,
)


def test_new_validates_and_clips():
    fb = Feedback.new("down", "chat-1", "m2", "", "  ", "How many widgets?", "x" * 9000)
    assert fb.user_id == "anonymous"
    assert fb.comment is None
    assert len(fb.answer) == 4000
    with pytest.raises(ValueError):
        Feedback.new("sideways")


def test_jsonl_round_trip(tmp_path):
    path = tmp_path / "fb.jsonl"
    fb = Feedback.new("up", "chat-1", "m2", "octocat", None, "q", "a")
    jsonl_feedback_log(path)(fb)
    assert read_jsonl_feedback(path) == [fb]


@pytest.fixture
def audit_engine():
    engine = sa.create_engine("sqlite://", poolclass=sa.pool.StaticPool)
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS CITYPULSE_AUDIT")
        conn.exec_driver_sql(
            "CREATE TABLE CITYPULSE_AUDIT.FEEDBACK (feedback_id, session_id, message_id, user_id, "
            "rated_at TIMESTAMP, rating, comment, question, answer)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE CITYPULSE_AUDIT.QUERY_LOG (session_id, asked_at TIMESTAMP, nl_query, "
            "generated_sql, status, row_count)"
        )
    return engine


def test_warehouse_round_trip_and_session_queries(audit_engine):
    fb = Feedback.new("down", "chat-1", "m2", "octocat", "wrong plant", "Widgets per plant?", "North 41")
    warehouse_feedback_log(audit_engine)(fb)
    with audit_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO CITYPULSE_AUDIT.QUERY_LOG VALUES ('chat-1', :at, 'widgets per plant', "
                    "'SELECT plant, COUNT(*) FROM widget GROUP BY plant', 'ok', 2)"),
            {"at": fb.rated_at - datetime.timedelta(seconds=30)},
        )
    got = read_warehouse_feedback(audit_engine, since=fb.rated_at - datetime.timedelta(days=1))
    assert [(g.feedback_id, g.rating, g.comment) for g in got] == [(fb.feedback_id, "down", "wrong plant")]
    queries = read_session_queries(audit_engine, "chat-1", fb.rated_at)
    assert queries[0]["nl_query"] == "widgets per plant"


def test_fan_out_runs_every_sink_then_raises():
    seen = []

    def broken(fb):
        raise RuntimeError("table missing")

    with pytest.raises(RuntimeError):
        fan_out_feedback(broken, seen.append)(Feedback.new("up"))
    assert len(seen) == 1


def test_draft_is_a_golden_case_marked_for_review():
    fb = Feedback.new("down", "chat-1", "m2", "octocat", "wrong plant", "Widgets per plant?", "North 41\nSouth 12")
    queries = [{"nl_query": "widgets per plant", "generated_sql": "SELECT plant,\n  COUNT(*) FROM widget GROUP BY plant"}]
    text = draft_golden_case(fb, queries)
    assert "# User comment: wrong plant" in text
    assert "reference_sql: |" in text
    [case] = yaml.safe_load(text)
    assert case["id"] == f"F-{fb.feedback_id[:8]}"
    assert case["questions"] == ["Widgets per plant?"]
    assert case["reference_sql"].startswith("SELECT plant,")
    assert case["match"] == {"labels": ["TODO"], "values": ["TODO"]}
    # Nothing ran: the reference is left for the reviewer to write.
    assert "TODO" in yaml.safe_load(draft_golden_case(fb, []))[0]["reference_sql"]
