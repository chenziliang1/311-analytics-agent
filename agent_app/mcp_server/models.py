# -*- coding: utf-8 -*-

"""
What the MCP tools return. These schemas are the contract with the agent: the
MCP server publishes them, and the agent's model reads every field name.
"""

import typing as T

from pydantic import BaseModel, Field

QueryStatus = T.Literal[
    "ok", "sql_error", "timeout", "empty", "blocked", "needs_clarification"
]


class WarehouseQueryOutput(BaseModel):
    status: QueryStatus
    #: The query that actually ran, after the guard rewrote it. Show this one.
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[T.Any]] = Field(default_factory=list)
    row_count: int = 0
    #: More rows existed than were returned.
    truncated: bool = False
    #: Definition, truncation, empty or personal-data notes, to pass on verbatim.
    warning: str | None = None
    #: The question to ask the user when status is needs_clarification.
    clarification: str | None = None
    #: Which version of the metric definitions produced this answer.
    semantic_layer_version: str
    #: Knowledge-base ids of the definitions this query was built from
    #: (metric/..., term/..., window/...). Read them with read_source and cite
    #: them in the answer.
    sources: list[str] = Field(default_factory=list)


class SourceIndexOutput(BaseModel):
    status: T.Literal["ok", "blocked", "error"]
    #: The llms.txt: one line per document the asker may read, with its id.
    index: str | None = None
    warning: str | None = None
    semantic_layer_version: str


class SourceOutput(BaseModel):
    status: T.Literal["ok", "not_found", "blocked", "error"]
    id: str
    title: str | None = None
    #: The document, Markdown.
    text: str | None = None
    #: Put this marker, exactly, in the answer that rests on the document.
    citation: str | None = None
    warning: str | None = None
    semantic_layer_version: str


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class VisualizeOutput(BaseModel):
    status: T.Literal["ok", "unsupported", "render_error"]
    chart_type: str
    #: A ```chart fenced block to paste into the answer unchanged.
    block: str | None = None
    error: str | None = None


class FeedbackOutput(BaseModel):
    status: T.Literal["ok", "invalid", "error"]
    feedback_id: str | None = None
    error: str | None = None
