# -*- coding: utf-8 -*-

"""
Ask a model for one SQL query, with the semantic layer's definitions in front
of it.

The writer sees three things the agent does not: the domain and SQL rules in
``data/sql-writer-prompt.md``, the schema, and for each metric the question
mentions, its exact formula plus a reference query compiled from it. That last
part is the point of the semantic layer -- the writer adapts a query that is
known to be right instead of reinventing the metric.
"""

import dataclasses
import re
import typing as T

from ..semantic_layer.api import CompileError, Match, SemanticRegistry, compile_metric

#: Takes the full request text, returns the model's raw reply.
Writer = T.Callable[[str], str]

_SQL_BLOCK = re.compile(r"```sql\s*\n(.*?)```", re.S | re.I)
_CLARIFY = re.compile(r"^\s*CLARIFY:\s*(.+)$", re.M)
_CANNOT = re.compile(r"^\s*CANNOT:\s*(.+)$", re.M)


@dataclasses.dataclass(frozen=True)
class ParsedReply:
    sql: str | None = None
    clarification: str | None = None
    #: Why the question cannot be answered from this database at all.
    refusal: str | None = None


def parse_reply(reply: str) -> ParsedReply:
    """
    A CANNOT or CLARIFY line, else the first ```sql block, else nothing.

    The two keyword lines are checked first, and inside a code block too:
    models wrap them in ```sql often enough that the block cannot be trusted
    to hold a query.
    """
    refused = _CANNOT.search(reply)
    if refused:
        return ParsedReply(refusal=refused.group(1).strip())
    asked = _CLARIFY.search(reply)
    if asked:
        return ParsedReply(clarification=asked.group(1).strip())
    found = _SQL_BLOCK.search(reply)
    if found:
        return ParsedReply(sql=found.group(1).strip())
    return ParsedReply()


def _describe_match(registry: SemanticRegistry, match: Match) -> list[str]:
    lines = []
    window = match.time_windows[0].name if match.time_windows else None
    dims = [d.name for d in match.dimensions if not d.sensitive]

    # Terms first: a term can overrule every reference query below it (a
    # health snapshot is read from its table, not recomputed from the metrics).
    for g in match.glossary:
        lines.append(f"### Term: {g.term}\n{' '.join(g.definition.split())}")
    if match.glossary:
        lines.append("")

    for m in match.metrics:
        lines.append(f"### Metric `{m.name}` ({m.display_name})")
        lines.append(m.description)
        lines.append(f"- formula: `{' '.join(m.formula.split())}`")
        lines.append(f"- from: `{m.base_table}`")
        for j in m.joins:
            lines.append(f"- join: `{j}`")
        for f in m.filters:
            lines.append(f"- filter: `{f}`")
        if m.time_column:
            lines.append(f"- time column: `{m.time_column}`")
        if m.window_end_cap:
            lines.append(f"- windows must end by {m.window_end_cap.isoformat()}")
        if m.notes:
            lines.append(f"- notes: {' '.join(m.notes.split())}")
        # A reference query with the dimensions the question named, falling
        # back to none when one of them cannot be joined to this metric.
        for attempt in (dims, []):
            try:
                ref = compile_metric(registry, m.name, window, attempt)
            except CompileError:
                continue
            label = ", ".join(attempt) or "no dimension"
            lines.append(f"Reference query ({window or 'no window'}, by {label}):")
            lines.append("```sql\n" + ref.sql + "\n```")
            break
        lines.append("")

    for d in match.dimensions:
        if d.sensitive:
            lines.append(
                f"### `{d.name}` is residents' personal data and must not be selected."
            )
            continue
        lines.append(f"### Dimension `{d.name}` ({d.display_name}): `{d.expression}`")
        for via, clause in d.joins.items():
            lines.append(f"- from `{via}`: `{clause}`")
        if d.allowed_values:
            lines.append(f"- values: {', '.join(d.allowed_values)}")

    for w in match.time_windows:
        anchor = "closure" if w.anchor == "closed" else "creation"
        lines.append(
            f"### Time window `{w.name}` ({w.display_name}): from {w.start.isoformat()} "
            f"(inclusive) to {w.end.isoformat()} (exclusive), on {anchor} date"
        )
    return lines


def build_request(
    intent: str,
    registry: SemanticRegistry,
    match: Match,
    schema: str,
    previous_sql: str | None = None,
    previous_error: str | None = None,
) -> str:
    parts = [f"## Question\n\n{intent}\n"]
    if match.is_empty:
        parts.append(
            "## Semantic layer\n\nNothing in this question matched a defined metric, "
            "dimension or time window. If the question still names something to "
            "count or compare, answer it from the domain knowledge in your "
            "instructions. If it does not say what to measure, reply CLARIFY.\n"
        )
    else:
        parts.append(
            f"## Semantic layer (version {registry.version})\n\n"
            + "\n".join(_describe_match(registry, match))
        )
    if schema:
        parts.append(f"## Database schema\n\n{schema}\n")
    if previous_sql:
        parts.append(
            "## Your previous query failed\n\n"
            f"```sql\n{previous_sql}\n```\n\nError: {previous_error}\n\n"
            "Write a corrected query."
        )
    return "\n".join(parts)


def writer_system_prompt(prompt: str, schema: str) -> str:
    """
    The writer's instructions with the schema appended.

    The schema is the same for every question, so it belongs in the system
    prompt, which is cached, rather than in each request, which is not. Pass
    ``schema=""`` to :func:`build_request` when it is here.
    """
    return f"{prompt.rstrip()}\n\n## Database schema\n\n{schema}\n"


def model_writer(
    model,
    system_prompt: str,
    on_usage: T.Callable[[int, int], None] = lambda input_tokens, output_tokens: None,
) -> Writer:  # pragma: no cover - calls a model
    """
    A writer backed by a strands model, with a fresh conversation per call.

    :param on_usage: Called with (input, output) tokens after each call. The
        writer is half of what a question costs, so the cost dashboard needs it.
    """
    from strands import Agent

    def write(request: str) -> str:
        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            tools=[],
            # The default handler prints the stream to stdout, which is the
            # protocol channel when the MCP server runs over stdio.
            callback_handler=None,
        )
        result = agent(request)
        invocations = getattr(result.metrics, "agent_invocations", None) or []
        usage = invocations[-1].usage if invocations else {}
        on_usage(int(usage.get("inputTokens", 0) or 0), int(usage.get("outputTokens", 0) or 0))
        return str(result)

    return write
