# -*- coding: utf-8 -*-

"""
Which numbers in a live answer did no query this turn return?

The eval asks this of every golden answer (``eval/grounding.py``); this asks it
of every real one. The failure it is for is the one the golden set caught with
Nova as the agent: a follow-up answered from the previous turn's text instead
of a new query, with confident numbers no query ever produced.

It flags, it does not block. The check over-reports on purpose -- a ratio the
agent derived correctly ("3.2 times as many") matches no returned value -- so
refusing those answers would refuse good ones. The UI shows the flagged
numbers in a grey line under the answer, and the turn's metric counts them, so
a rise shows on the dashboard before a user reports it.
"""

import json
import typing as T

from .eval.grounding import unsupported_numbers

#: The tools whose output is query rows. Anything else a tool returns (a chart
#: spec, a feedback receipt) is numbers the agent was handed, not data.
_TOOLS_WITH_ROWS = frozenset({"warehouse_query"})


def unverified_numbers(
    answer: str,
    results: T.Sequence[tuple[str, dict]],
    conversation: T.Sequence[str] = (),
) -> list[str]:
    """
    Numbers in ``answer`` that match no value this turn's queries returned.

    :param results: (tool name, output) for each tool call this turn, as
        :class:`~agent_app.turn_observer.TurnObserver` collects them.
    :param conversation: The text of every message so far, the question being
        answered included. A number already in it -- one the user asked about,
        or an earlier answer showed -- is not new, so a follow-up repeating it
        is not flagged. Earlier turns' rows are not sent back with the history;
        their numbers live on in the answers that showed them.
    """
    if not answer:
        return []
    # A ```chart block stays in the answer on purpose: a chart drawn with a
    # number no query returned is the same claim as a sentence with it.
    values: list = []
    seen = list(conversation)
    for name, output in results:
        if name in _TOOLS_WITH_ROWS:
            for row in output.get("rows") or []:
                values.extend(row)
            if output.get("row_count") is not None:
                values.append(output["row_count"])
            # The query and its warning are evidence too: "capped at 5000
            # rows", "since 2025-06-01", a threshold the SQL filtered on.
            seen += [output.get("sql") or "", output.get("warning") or ""]
        else:
            seen.append(json.dumps(output, default=str))
    flagged = unsupported_numbers(answer, values, "\n".join(seen))
    return list(dict.fromkeys(flagged))
