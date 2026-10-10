# -*- coding: utf-8 -*-

"""
Load the semantic layer from disk and look things up in it.

The whole layer is read into memory once. It is a few dozen small files, and a
registry that is either fully loaded or failed to load is easier to reason about
than one that discovers a broken file halfway through answering a question.
"""

import dataclasses
import re
from pathlib import Path

import yaml

from .model import Dimension, EntitySource, GlossaryTerm, Metric, TimeWindow


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from e


def _load_each(directory: Path, model):
    items = []
    for path in sorted(directory.glob("*.yml")):
        try:
            items.append(model.model_validate(_read_yaml(path)))
        except ValueError as e:
            raise ValueError(f"{path}: {e}") from e
    return items


def _index(items, kind: str) -> dict:
    by_name = {}
    for item in items:
        if item.name in by_name:
            raise ValueError(f"duplicate {kind} name {item.name!r}")
        by_name[item.name] = item
    return by_name


def _phrases(name: str, display_name: str, synonyms: list[str]) -> list[str]:
    return [name.replace("_", " "), display_name, *synonyms]


def _mentions(text: str, phrases: list[str]) -> bool:
    """Whole-word, case-insensitive. ``rate`` must not match ``separate``."""
    for phrase in phrases:
        pattern = r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)"
        if re.search(pattern, text):
            return True
    return False


@dataclasses.dataclass(frozen=True)
class Match:
    """What a question mentions, by name or synonym."""

    metrics: list[Metric]
    dimensions: list[Dimension]
    time_windows: list[TimeWindow]
    glossary: list[GlossaryTerm]

    @property
    def is_empty(self) -> bool:
        return not (self.metrics or self.dimensions or self.time_windows or self.glossary)


@dataclasses.dataclass(frozen=True)
class SemanticRegistry:
    version: str
    metrics: dict[str, Metric]
    dimensions: dict[str, Dimension]
    time_windows: dict[str, TimeWindow]
    glossary: list[GlossaryTerm]
    #: Knowledge-base documents read from the warehouse (see EntitySource).
    sources: dict[str, EntitySource] = dataclasses.field(default_factory=dict)

    @property
    def sensitive_columns(self) -> set[str]:
        """``table.column`` of every personal-data column, lowercase."""
        return {
            d.source_column.lower()
            for d in self.dimensions.values()
            if d.sensitive and d.source_column
        }

    def match(self, text: str) -> Match:
        text = text.lower()
        return Match(
            metrics=[
                m
                for m in self.metrics.values()
                if _mentions(text, _phrases(m.name, m.display_name, m.synonyms))
            ],
            dimensions=[
                d
                for d in self.dimensions.values()
                if _mentions(text, _phrases(d.name, d.display_name, d.synonyms))
            ],
            time_windows=[
                w
                for w in self.time_windows.values()
                if _mentions(text, _phrases(w.name, w.display_name, w.synonyms))
            ],
            glossary=[
                g for g in self.glossary if _mentions(text, [g.term, *g.synonyms])
            ],
        )


def load_registry(directory: Path) -> SemanticRegistry:
    """
    Read and validate every file under ``directory``.

    Raises on the first problem, naming the file: a half-loaded layer would
    answer some questions with a definition and others by guessing.
    """
    metrics = _index(_load_each(directory / "metrics", Metric), "metric")
    dimensions = _index(_load_each(directory / "dimensions", Dimension), "dimension")
    time_windows = _index(
        _load_each(directory / "time_windows", TimeWindow), "time window"
    )
    glossary = []
    for path in sorted((directory / "glossary").glob("*.yml")):
        for entry in _read_yaml(path) or []:
            glossary.append(GlossaryTerm.model_validate(entry))
    # Optional: a layer with nothing worth reading per row has no sources/.
    sources = _index(_load_each(directory / "sources", EntitySource), "source")

    for metric in metrics.values():
        if metric.replay and metric.replay.time_window not in time_windows:
            raise ValueError(
                f"metric {metric.name!r} replays unknown time window "
                f"{metric.replay.time_window!r}"
            )
        # Fails here, at load, rather than when the first question compiles.
        metric.aliases

    version_path = directory / "VERSION"
    version = (
        version_path.read_text(encoding="utf-8").strip()
        if version_path.exists()
        else "unversioned"
    )
    return SemanticRegistry(
        version=version,
        metrics=metrics,
        dimensions=dimensions,
        time_windows=time_windows,
        glossary=glossary,
        sources=sources,
    )
