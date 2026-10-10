# -*- coding: utf-8 -*-

"""
The knowledge-base tools' logic: the index and a document, for one asker.

The static documents come from the semantic layer and need no warehouse. An
entity document (a customer's profile) is one warehouse row, read as the
asking user: the same role switch the query tool makes, so the index a
restricted user sees lists only the keys their role may read, and a document
their role may not read does not exist for them.
"""

from ..knowledge import KnowledgeBase, Source, entity_document
from ..sql_guard import transpile_sql
from ..sql_utils import fetch_rows
from ..semantic_layer.api import EntitySource
from .models import SourceIndexOutput, SourceOutput
from .warehouse import WarehouseDeps, run_as_for

#: An entity listing is index material, not an answer: enough for every
#: customer and still bounded.
LIST_ROWS = 1000


def _run(deps: WarehouseDeps, sql: str, run_as: dict, params: dict | None = None, max_rows: int = LIST_ROWS):
    target = deps.target_dialect or deps.dialect
    sql = transpile_sql(sql, target, source=deps.dialect) if target != deps.dialect else sql
    return fetch_rows(deps.engine, sql, max_rows, deps.timeout_seconds, params=params, **run_as)


def source_index(deps: WarehouseDeps, user_id: str = "") -> SourceIndexOutput:
    version = deps.registry.version
    kb = KnowledgeBase(deps.registry)
    run_as, refused = run_as_for(deps, user_id)
    if refused:
        return SourceIndexOutput(status="blocked", warning=f"Refused: {refused}.", semantic_layer_version=version)
    entities: dict[str, list[tuple[str, str]]] = {}
    warnings = []
    for entity in deps.registry.sources.values():
        result = _run(deps, entity.list_sql, run_as)
        if result.error:
            # The static index is still worth having; say what is missing.
            warnings.append(f"{entity.display_name}s could not be listed: {result.error}")
            continue
        entities[entity.name] = [(str(row[0]), str(row[1])) for row in result.rows]
    return SourceIndexOutput(
        status="ok",
        index=kb.index(entities),
        warning=" ".join(warnings) or None,
        semantic_layer_version=version,
    )


def _read_entity(deps: WarehouseDeps, entity: EntitySource, key: str, run_as: dict) -> SourceOutput:
    version = deps.registry.version
    source_id = f"{entity.name}/{key}"
    result = _run(deps, entity.read_sql, run_as, params={"key": key}, max_rows=2)
    if result.error:
        return SourceOutput(status="error", id=source_id, warning=result.error, semantic_layer_version=version)
    if not result.rows:
        # Either no such key, or one the asker's role may not read; the two
        # are the same answer on purpose.
        return SourceOutput(status="not_found", id=source_id, semantic_layer_version=version)
    doc = entity_document(entity, key, result.columns, result.rows[0])
    return _found(doc, version)


def _found(doc: Source, version: str) -> SourceOutput:
    return SourceOutput(
        status="ok", id=doc.id, title=doc.title, text=doc.text, citation=doc.citation,
        semantic_layer_version=version,
    )


def read_source(deps: WarehouseDeps, source_id: str, user_id: str = "") -> SourceOutput:
    version = deps.registry.version
    kb = KnowledgeBase(deps.registry)
    source_id = (source_id or "").strip()
    static = kb.static_sources.get(source_id)
    if static is not None:
        return _found(static, version)
    entity = kb.entity_source(source_id)
    if entity is None:
        return SourceOutput(
            status="not_found", id=source_id,
            warning="No such source. Use the ids warehouse_query returned, or list_sources.",
            semantic_layer_version=version,
        )
    run_as, refused = run_as_for(deps, user_id)
    if refused:
        return SourceOutput(status="blocked", id=source_id, warning=f"Refused: {refused}.", semantic_layer_version=version)
    return _read_entity(deps, *entity, run_as)
