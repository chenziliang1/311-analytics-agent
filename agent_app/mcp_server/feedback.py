# -*- coding: utf-8 -*-

"""
Thumbs up or down on an answer, and the way back from a thumbs down to a
golden case:

    UI thumbs -> record_feedback -> FEEDBACK table -> triage
              -> `agent-app feedback --draft <id>` -> a golden case draft
              -> reviewed into data/eval/golden_cases.yml -> next grade

Recorded by the MCP server, like the query audit, because that is where the
warehouse credentials live: the web app and the agent never hold them.

The draft is a draft. A thumbs down means the answer was wrong, so the SQL
that produced it is the one thing the draft must not trust: it is copied in
as the starting point for a human to correct, never as the reference.
"""

import dataclasses
import datetime
import json
import sys
import typing as T
import uuid
from pathlib import Path

import yaml

RATINGS = ("up", "down")

#: The MCP tool name. Served for the web app and hidden from the agent's
#: model (one_04_agent), which has no business rating its own answers.
FEEDBACK_TOOL = "record_feedback"

#: Longest text kept per field. An answer can be a long Markdown table; the
#: first part is enough to see what went wrong.
MAX_TEXT = 4000

_INSERT = """
INSERT INTO CITYPULSE_AUDIT.FEEDBACK
    (feedback_id, session_id, message_id, user_id, rated_at, rating, comment, question, answer)
VALUES
    (:feedback_id, :session_id, :message_id, :user_id, :rated_at, :rating, :comment, :question, :answer)
"""


@dataclasses.dataclass(frozen=True)
class Feedback:
    feedback_id: str
    session_id: str
    message_id: str
    user_id: str
    rated_at: datetime.datetime
    rating: str
    comment: str | None
    question: str
    answer: str

    @classmethod
    def new(
        cls,
        rating: str,
        session_id: str = "",
        message_id: str = "",
        user_id: str = "",
        comment: str | None = None,
        question: str = "",
        answer: str = "",
    ) -> "Feedback":
        """A validated, clipped record, stamped now. Raises ValueError on a bad rating."""
        if rating not in RATINGS:
            raise ValueError(f"rating must be one of {RATINGS}, got {rating!r}")
        return cls(
            feedback_id=str(uuid.uuid4()),
            session_id=session_id[:200],
            message_id=message_id[:200],
            user_id=(user_id or "anonymous")[:200],
            rated_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
            rating=rating,
            comment=(comment or "").strip()[:MAX_TEXT] or None,
            question=question[:MAX_TEXT],
            answer=answer[:MAX_TEXT],
        )

    def to_row(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_row(cls, row: T.Mapping) -> "Feedback":
        values = {k.lower(): v for k, v in dict(row).items()}
        rated_at = values["rated_at"]
        if isinstance(rated_at, str):
            rated_at = datetime.datetime.fromisoformat(rated_at)
        return cls(**{**{f.name: values.get(f.name) for f in dataclasses.fields(cls)}, "rated_at": rated_at})


# ------------------------------------------------------------------------------
# Writing
# ------------------------------------------------------------------------------
FeedbackSink = T.Callable[[Feedback], None]


def jsonl_feedback_log(path: Path) -> FeedbackSink:
    def write(fb: Feedback) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fb.to_row(), default=str) + "\n")

    return write


def warehouse_feedback_log(engine) -> FeedbackSink:
    """
    Inserts into CITYPULSE_AUDIT.FEEDBACK, synchronously: unlike the query
    audit nobody is waiting on an answer here, and the user should only see
    "thanks" once the vote is stored.
    """
    import sqlalchemy as sa

    statement = sa.text(_INSERT)

    def write(fb: Feedback) -> None:
        with engine.begin() as conn:
            conn.execute(statement, fb.to_row())

    return write


def fan_out_feedback(*sinks: FeedbackSink) -> FeedbackSink:
    """Every sink gets the vote; the first failure is raised after all ran."""

    def write(fb: Feedback) -> None:
        errors = []
        for sink in sinks:
            try:
                sink(fb)
            except Exception as e:
                print(f"[Feedback] sink failed: {type(e).__name__}: {e}", file=sys.stderr)
                errors.append(e)
        if errors:
            raise errors[0]

    return write


# ------------------------------------------------------------------------------
# Reading, and the draft
# ------------------------------------------------------------------------------
def read_jsonl_feedback(path: Path) -> list[Feedback]:
    if not path.exists():
        return []
    return [Feedback.from_row(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_warehouse_feedback(engine, since: datetime.datetime) -> list[Feedback]:
    import sqlalchemy as sa

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT feedback_id, session_id, message_id, user_id, rated_at, rating, comment, "
                "question, answer FROM CITYPULSE_AUDIT.FEEDBACK WHERE rated_at >= :since "
                "ORDER BY rated_at DESC"
            ),
            {"since": since},
        ).mappings().all()
    return [Feedback.from_row(r) for r in rows]


def read_session_queries(engine, session_id: str, until: datetime.datetime) -> list[dict]:
    """The queries run in one chat up to a vote, newest first, from QUERY_LOG."""
    import sqlalchemy as sa

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT asked_at, nl_query, generated_sql, status, row_count "
                "FROM CITYPULSE_AUDIT.QUERY_LOG WHERE session_id = :sid AND asked_at <= :until "
                "ORDER BY asked_at DESC LIMIT 5"
            ),
            {"sid": session_id, "until": until},
        ).mappings().all()
    return [{k.lower(): v for k, v in dict(r).items()} for r in rows]


def read_jsonl_session_queries(path: Path, session_id: str, until: datetime.datetime) -> list[dict]:
    """The same as :func:`read_session_queries`, from the local audit file."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        at = datetime.datetime.fromisoformat(r.get("at", "1970-01-01")).replace(tzinfo=None)
        if r.get("session_id") == session_id and at <= until:
            rows.append({"asked_at": at, "nl_query": r.get("intent"), "generated_sql": r.get("sql"),
                         "status": r.get("status"), "row_count": r.get("row_count")})
    return sorted(rows, key=lambda r: r["asked_at"], reverse=True)[:5]


class _LiteralDumper(yaml.SafeDumper):
    """Multi-line strings (the SQL) as ``|`` blocks, as golden_cases.yml has them."""


def _str(dumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LiteralDumper.add_representer(str, _str)


def draft_golden_case(fb: Feedback, queries: T.Sequence[dict]) -> str:
    """
    A golden case for a human to finish, as YAML text.

    Valid as it stands except for the reference: ``reference_sql`` is the
    query the agent ran -- the one that got the thumbs down -- and ``match``
    has no columns yet. Both are marked TODO, and the case only loads into
    the golden set once a reviewer has replaced them.
    """
    ran = queries[0] if queries else {}
    case = {
        "id": f"F-{fb.feedback_id[:8]}",
        "source": f"feedback {fb.feedback_id} ({fb.rated_at:%Y-%m-%d}, {fb.user_id})",
        "questions": [fb.question],
        "reference_sql": (ran.get("generated_sql") or "-- TODO: write the correct query").rstrip() + "\n",
        "match": {"labels": ["TODO"], "values": ["TODO"]},
    }
    notes = [
        "# Draft from a thumbs-down. Before adding it to data/eval/golden_cases.yml:",
        "#   1. Fix reference_sql. It is the query the agent ran, i.e. the suspect one.",
        "#   2. Set match.labels / match.values to columns of the corrected result.",
        "#   3. Run `agent-app grade --cases <id>` and confirm the agent now fails or passes as expected.",
        f"# User comment: {fb.comment or '(none)'}",
        f"# Agent intent sent to the warehouse: {ran.get('nl_query') or '(no query ran)'}",
        "# Agent answer (start):",
        *[f"#   {line}" for line in fb.answer.splitlines()[:15]],
    ]
    body = yaml.dump([case], Dumper=_LiteralDumper, sort_keys=False, allow_unicode=True, width=100)
    return "\n".join(notes) + "\n" + body
