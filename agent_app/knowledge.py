# -*- coding: utf-8 -*-

"""
The knowledge base: the approved definitions, as documents an agent reads on
demand and cites.

The shape is llms.txt: one index, a line per document with its id and one
sentence, and a document behind each id. The agent reads the index when it
needs to find something and a document when it is about to say what a number
means. No embeddings: there are a few dozen definitions, each with a name the
question either uses or does not, and a citation has to point at one exact
document, which a nearest neighbour cannot promise.

Everything here is derived from the semantic layer, so a definition exists in
one place and the document the agent cites is the one the SQL writer built the
query from. Entity sources (a customer's profile) are read from the warehouse
by ``mcp_server.knowledge``; this module only knows their kind.

A citation is ``[KB:<id>]`` in the answer text. ``citations`` finds them, so
the eval can check an answer cited the definition its query used rather than
merely that it said something about how the number was computed.
"""

import dataclasses
import re
import typing as T

from .semantic_layer.api import (
    Dimension,
    EntitySource,
    GlossaryTerm,
    Match,
    Metric,
    SemanticRegistry,
    TimeWindow,
)

#: ``[KB:metric/sla_breach_rate]``. The id is ``kind/key``.
CITATION = re.compile(r"\[KB:([a-z_]+/[A-Za-z0-9_\-]+)\]")

KINDS = ("metric", "term", "window", "dimension")


def slug(text: str) -> str:
    """``"Other bucket"`` -> ``other-bucket``: an id that survives being typed."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def source_ids_for(match: Match) -> list[str]:
    """The documents behind a matched question: what an answer to it should cite."""
    ids = [f"metric/{m.name}" for m in match.metrics]
    ids += [f"term/{slug(g.term)}" for g in match.glossary]
    ids += [f"window/{w.name}" for w in match.time_windows]
    return ids


def citations(answer: str) -> list[str]:
    """Every source id cited in ``answer``, in order, once each."""
    return list(dict.fromkeys(CITATION.findall(answer or "")))


@dataclasses.dataclass(frozen=True)
class Source:
    id: str
    title: str
    #: Markdown. Short: a definition, not an essay.
    text: str
    #: What an answer that rests on this document writes.
    summary: str = ""

    @property
    def citation(self) -> str:
        return f"[KB:{self.id}]"


def _bullets(pairs: T.Iterable[tuple[str, T.Any]]) -> str:
    lines = []
    for label, value in pairs:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value)
        lines.append(f"- {label}: {str(value).strip()}")
    return "\n".join(lines)


def _metric_source(m: Metric) -> Source:
    time_axis = m.time_column
    if m.closed_time_column:
        time_axis = f"{m.time_column} (windows anchored on closure: {m.closed_time_column})"
    replay = None
    if m.replay:
        replay = f"{m.replay.expected} over {m.replay.time_window} (tolerance {m.replay.tolerance})"
    body = _bullets([
        ("Formula", m.formula),
        ("Counted from", m.base_table),
        ("Joins", m.joins),
        ("Filters", m.filters),
        ("Time column", time_axis),
        ("Latest window end", m.window_end_cap),
        ("Also called", m.synonyms),
        ("Notes", m.notes),
        ("Reference value", replay),
    ])
    return Source(
        id=f"metric/{m.name}",
        title=m.display_name,
        summary=m.description.strip(),
        text=f"# {m.display_name}\n\n{m.description.strip()}\n\n{body}\n",
    )


def _term_source(g: GlossaryTerm) -> Source:
    body = _bullets([("Also called", g.synonyms), ("Answered from", g.read_from)])
    definition = g.definition.strip()
    return Source(
        id=f"term/{slug(g.term)}",
        title=g.term,
        summary=definition,
        text=f"# {g.term}\n\n{definition}\n\n{body}\n".rstrip() + "\n",
    )


def _window_source(w: TimeWindow) -> Source:
    summary = f"{w.start.isoformat()} up to {w.end.isoformat()}, anchored on {w.anchor}."
    body = _bullets([
        ("Start (inclusive)", w.start.isoformat()),
        ("End (exclusive)", w.end.isoformat()),
        ("Anchored on", "the request's closure" if w.anchor == "closed" else "the metric's own time column"),
        ("Also called", w.synonyms),
    ])
    return Source(
        id=f"window/{w.name}",
        title=w.display_name,
        summary=summary,
        text=f"# {w.display_name}\n\n{summary}\n\n{body}\n",
    )


def _dimension_source(d: Dimension) -> Source:
    summary = f"Group by {d.expression}."
    if d.allowed_values:
        summary += " Values: " + ", ".join(d.allowed_values) + "."
    body = _bullets([
        ("Expression", d.expression),
        ("Values", d.allowed_values),
        ("Also called", d.synonyms),
    ])
    return Source(
        id=f"dimension/{d.name}",
        title=d.display_name,
        summary=summary,
        text=f"# {d.display_name}\n\n{summary}\n\n{body}\n",
    )


@dataclasses.dataclass(frozen=True)
class KnowledgeBase:
    registry: SemanticRegistry

    @property
    def static_sources(self) -> dict[str, Source]:
        """Every document that exists without a warehouse, by id."""
        sources = [_metric_source(m) for m in self.registry.metrics.values()]
        sources += [_term_source(g) for g in self.registry.glossary]
        sources += [_window_source(w) for w in self.registry.time_windows.values()]
        # Personal-data dimensions are never shown to a model, so they have no
        # document either.
        sources += [_dimension_source(d) for d in self.registry.dimensions.values() if not d.sensitive]
        return {s.id: s for s in sources}

    def entity_source(self, source_id: str) -> tuple[EntitySource, str] | None:
        """(the kind, the key) when ``source_id`` names a warehouse-read document."""
        kind, _, key = source_id.partition("/")
        entity = self.registry.sources.get(kind)
        if entity is None or not key or not re.fullmatch(entity.key_pattern, key):
            return None
        return entity, key

    def knows(self, source_id: str) -> bool:
        """``source_id`` could be a real document: a citation to check, not an invention."""
        return source_id in self.static_sources or self.entity_source(source_id) is not None

    def index(self, entities: T.Mapping[str, T.Sequence[tuple[str, str]]] | None = None) -> str:
        """
        The llms.txt: a line per document.

        :param entities: Source name -> (key, title) rows the asker may read,
            from the warehouse. None lists each entity kind once, as a pattern,
            which is the form that is checked into the repository.
        """
        lines = [
            "# Knowledge base",
            "",
            "> The approved definitions every answer rests on, one line each. Read one with",
            "> read_source(id) and cite it in the answer as [KB:id]. Semantic layer version "
            f"{self.registry.version}.",
        ]
        sections = [
            ("Metrics", "metric/"),
            ("Terms", "term/"),
            ("Time windows", "window/"),
            ("Dimensions", "dimension/"),
        ]
        statics = self.static_sources
        for title, prefix in sections:
            rows = [s for s in statics.values() if s.id.startswith(prefix)]
            if not rows:
                continue
            lines += ["", f"## {title}", ""]
            lines += [f"- [{s.id}]({s.id}): {s.title}. {s.summary}" for s in rows]
        for entity in self.registry.sources.values():
            lines += ["", f"## {entity.display_name}s", "", f"> {entity.description.strip()}"]
            if entities is None or entity.name not in entities:
                lines += ["", f"- [{entity.name}/<key>]({entity.name}/<key>): one per key; list_sources shows the keys you may read."]
                continue
            rows = entities[entity.name]
            lines.append("")
            lines += [f"- [{entity.name}/{key}]({entity.name}/{key}): {title}" for key, title in rows]
            if not rows:
                lines.append("- (none you may read)")
        return "\n".join(lines) + "\n"


def entity_document(entity: EntitySource, key: str, columns: list[str], row: list) -> Source:
    """One warehouse row as a document: every column, ``name: value``."""
    title = f"{entity.display_name}: {key}"
    body = _bullets(zip(columns, row))
    return Source(
        id=f"{entity.name}/{key}",
        title=title,
        summary=entity.description.strip(),
        text=f"# {title}\n\n{body}\n",
    )
