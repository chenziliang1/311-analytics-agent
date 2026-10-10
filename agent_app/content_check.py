# -*- coding: utf-8 -*-

"""
Does the repo still agree with the content in ``data/``?

Every check here compares two things a dataset swap must keep in agreement, and
fails the build when they disagree. Hand-rewritten prose cannot be type-checked,
so the failures this catches are the silent kind: a prompt that queries a table
the previous dataset left behind, a kickstart button whose icon promises a chart
over a paragraph of prose. Nothing crashes. The app simply lies.

The tables that exist are read from ``infra/snowflake/ddl.sql``, the DDL the
warehouse was built from, so every check runs without a database connection --
on a laptop and in CI alike.

Icon names and required ``site.toml`` keys are deliberately **not** checked here.
``lib/site/load.ts`` checks them against ``lib/icons.ts`` when Next.js reads the
TOML, which is a real check at build time rather than a second list to keep in
step. That is what ``mise run build-web`` is for.
"""

import re
import tomllib
import typing as T
from pathlib import Path

from agent_app.paths import path_enum


#: Directories worth scanning for retired vocabulary: everything that is code or
#: config. Prose is deliberately excluded: documentation may legitimately discuss
#: a previous domain, and failing on it would train the reader to ignore this
#: script.
SCAN_DIRS = [
    "app",
    "components",
    "lib",
    "data",
    "types",
    "api",
    "agent_app",
    "tests_python",
    "config",
]

#: File extensions worth scanning inside :data:`SCAN_DIRS`.
#:
#: ``.toml`` earns its place twice over: ``data/site.toml`` holds every sentence
#: the UI says, which makes it the densest concentration of the outgoing domain's
#: vocabulary in the repo, and therefore the one file this scan most needs to
#: read.
SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".sql", ".toml"}

#: Files exempt from the retired-vocabulary scan, because listing those words is
#: precisely their job.
SCAN_EXEMPT = {
    "agent_app/content_check.py",
    # Its --retired help text spells out an example word list.
    "agent_app/cli.py",
}

#: Identifiers that can follow FROM/JOIN in the system prompt without naming a
#: real table: SQL keywords, and the generic stand-ins a prompt uses when it is
#: illustrating a shape rather than a query ("... FROM table").
PROMPT_NON_TABLES = {
    "select",
    "lateral",
    "unnest",
    "values",
    "table",
    "table_name",
    "your_table",
}

#: How many kickstart questions of each kind ``data/site.toml`` must carry.
#:
#: Twelve buttons that all return prose leave the diagram renderer and the chart
#: renderer with nothing to draw, which is the same as not having built them.
RENDER_QUOTAS = {"table": 3, "flowchart": 3, "chart": 3, "free": 3}

#: The phrase that actually makes each block appear.
#:
#: The other half of the contract in the prompt's ``## Rendering Triggers``
#: table. The model follows the wording, not the intent, so these are literal.
RENDER_PHRASES = {
    "table": (("as a markdown table",), ("show the sql",)),
    "flowchart": (("as a flowchart", "as a diagram"),),
    "chart": (("as a bar chart", "as a line chart"),),
}

class Report:
    """
    Accumulates findings so that one run reports *every* problem, not just the
    first.

    A checker that stops at the first failure turns a swap into a slow
    fix-one-rerun loop. Collecting everything means one run gives you the whole
    list of what still needs doing.
    """

    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, check: str, message: str):
        self.errors.append(f"[{check}] {message}")

    def warn(self, check: str, message: str):
        self.warnings.append(f"[{check}] {message}")

    def section(self, title: str, ok: bool | None):
        """``ok=None`` prints SKIP, for a check that could not run at all."""
        mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print(f"  {mark}  {title}")


def iter_scan_files() -> T.Iterator[Path]:
    """Yield every code/config file that the retired-vocabulary scan covers."""
    root = path_enum.dir_project_root
    for dir_name in SCAN_DIRS:
        directory = root / dir_name
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SCAN_SUFFIXES:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            if str(path.relative_to(root)) in SCAN_EXEMPT:
                continue
            yield path


def check_retired_terms(report: Report, terms: list[str]) -> bool:
    """
    Fail if vocabulary from a retired dataset still appears in the code.

    Terms come from ``--retired``, not from a committed config file. This check
    is a *migration* tool: its value peaks the day you swap datasets and drops to
    zero once the swap is clean. Storing the previous domain's nouns in the repo
    forever puts words from a dead dataset in the current one's config, and the
    list rots silently whenever someone forgets to update it.

    Run it by hand right after a swap::

        mise run cli-check-content -- --retired "patient,bed,ward,nurse"

    Matching is whole-word and case-insensitive, so ``bed`` flags a stray
    hospital reference without flagging ``bedrock``.
    """
    if not terms:
        return True

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b",
        re.IGNORECASE,
    )
    root = path_enum.dir_project_root
    hits = 0
    for path in iter_scan_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = pattern.search(line)
            if match:
                hits += 1
                report.error(
                    "retired-terms",
                    f"{path.relative_to(root)}:{lineno} still says "
                    f"{match.group(0)!r}, left over from a previous dataset.",
                )

    ok = hits == 0
    report.section(f"retired vocabulary ({len(terms)} terms)", ok)
    return ok


def warehouse_tables() -> set[str]:
    """
    The analytics tables the warehouse has, lowercase, from its DDL.

    The DDL, not a live connection: it is the file the warehouse was built from,
    it is in the repo, and reading it needs no credentials, so the checks that
    use it run in CI. Only ``ANALYTICS`` tables count -- the audit schema is not
    something a prompt should ever query.
    """
    path = path_enum.path_warehouse_ddl_sql
    if not path.exists():
        return set()
    return {
        name.lower()
        for name in re.findall(r"CREATE TABLE ANALYTICS\.(\w+)", path.read_text(encoding="utf-8"))
    }


def check_prompt_tables(report: Report, db_tables: set[str]) -> bool:
    """
    Fail if the system prompt queries a table the database does not have.

    Scans for identifiers following ``FROM`` and ``JOIN``, **inside ```sql
    fenced blocks only**. A prompt that teaches the agent to query a table left
    behind by the previous dataset produces confident, entirely wrong SQL at
    runtime.

    Scoping to the fenced blocks is what keeps this honest. Scanning the whole
    file cannot tell prose from SQL, and the prompt legitimately discusses SQL
    in prose: the dialect section says to write ``EXTRACT(QUARTER FROM d)``, and
    a whole-file scan reads that as a query against a table called ``d``.

    A dotted name is skipped too. ``EXTRACT(QUARTER FROM l.disbursement_date)``
    is a column reference that happens to follow ``FROM``.

    Names the prompt defines itself as CTEs (``WITH early_behavior AS (...)``)
    are resolved and excluded -- they read exactly like table references but are
    local to the query, so flagging them would be pure noise.
    """
    path = path_enum.path_bi_agent_system_prompt_md
    if not path.exists():
        report.error("prompt-tables", f"system prompt not found at {path}")
        report.section("prompt table references", False)
        return False

    # The SQL writer's prompt teaches queries too, and its are the ones that
    # actually run. A missing one is reported by check_content_files.
    texts = [path.read_text(encoding="utf-8")]
    if path_enum.path_sql_writer_prompt_md.exists():
        texts.append(path_enum.path_sql_writer_prompt_md.read_text(encoding="utf-8"))
    sql_blocks = "\n".join(
        block for text in texts for block in re.findall(r"```sql\n(.*?)```", text, re.S)
    )
    referenced = set(
        name.lower()
        for name in re.findall(
            # The trailing `(?!\.)` drops `FROM l.disbursement_date`, which is a
            # column inside EXTRACT rather than a table.
            r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)(?!\s*\.)",
            sql_blocks,
            re.IGNORECASE,
        )
    )

    # CTEs the prompt defines itself: `WITH name AS (` and `, name AS (`.
    cte_names = set(
        name.lower()
        for name in re.findall(
            r"(?:\bWITH\s+|,\s*)([a-z_][a-z0-9_]*)\s+AS\s*\(",
            sql_blocks,
            re.IGNORECASE,
        )
    )

    referenced -= PROMPT_NON_TABLES
    referenced -= cte_names

    unknown = sorted(t for t in referenced if t not in db_tables)
    for name in unknown:
        report.error(
            "prompt-tables",
            f"a prompt queries table {name!r}, which does not exist in "
            f"the database. The agent will write SQL that always errors.",
        )

    ok = not unknown
    report.section(f"prompt table references ({len(referenced)} found)", ok)
    return ok


def check_content_files(report: Report) -> bool:
    """Fail if the hand-written content a swap must replace is missing or empty."""
    ok = True

    for label, path, floor in (
        ("system prompt", path_enum.path_bi_agent_system_prompt_md, 500),
        ("SQL writer prompt", path_enum.path_sql_writer_prompt_md, 500),
        ("site copy", path_enum.path_site_toml, 500),
        ("business context", path_enum.path_business_context_md, 500),
    ):
        if not path.exists():
            report.error("content", f"{label} missing: {path}")
            ok = False
        elif len(path.read_text(encoding="utf-8").strip()) < floor:
            report.error("content", f"{label} looks like a stub: {path}")
            ok = False

    report.section("content files", ok)
    return ok


def semantic_layer_tables(registry) -> set[str]:
    """
    Every table a metric, dimension or glossary term reads, including inside
    derived tables.
    """
    fragments = []
    for metric in registry.metrics.values():
        fragments.append("FROM " + metric.base_table)
        fragments.extend(metric.joins)
    for dim in registry.dimensions.values():
        fragments.extend(dim.joins.values())
    for term in registry.glossary:
        if term.read_from:
            fragments.append("FROM " + term.read_from)
    return {
        name.lower()
        for name in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)", "\n".join(fragments))
    }


def check_semantic_layer(report: Report, db_tables: set[str] | None) -> bool:
    """
    Fail if data/semantic_layer/ does not load, or reads a table the warehouse
    lacks.

    Loading validates every file against its model, so a misspelled field is
    caught here rather than when the first question compiles. The table check
    is the same one the prompt gets: a metric left behind by the previous
    dataset compiles fine and fails only at run time.
    """
    from agent_app.semantic_layer.api import load_registry

    try:
        registry = load_registry(path_enum.dir_semantic_layer)
    except Exception as e:
        report.error("semantic-layer", str(e))
        report.section("semantic layer", False)
        return False

    ok = True
    if not registry.metrics:
        # A missing directory loads as an empty layer, and an empty layer
        # leaves the query tool matching nothing for every question.
        report.error("semantic-layer", f"no metrics under {path_enum.dir_semantic_layer}")
        ok = False
    # A date an entity source hard-codes (the "current" snapshot month) must
    # be the one the glossary states, or the two drift apart silently.
    stated = " ".join(g.definition for g in registry.glossary)
    for source in registry.sources.values():
        for date in sorted(set(re.findall(r"'(\d{4}-\d{2}-\d{2})'", source.read_sql + source.list_sql))):
            if date not in stated:
                report.error(
                    "semantic-layer",
                    f"source {source.name!r} hard-codes {date}, which no glossary definition states",
                )
                ok = False
    if db_tables is not None:
        for name in sorted(semantic_layer_tables(registry) - db_tables):
            report.error("semantic-layer", f"reads table {name!r}, not in the warehouse")
            ok = False
    report.section("semantic layer", ok)
    return ok


def check_knowledge_index(report: Report) -> bool:
    """
    Fail if data/llms.txt is not what the semantic layer generates.

    The agent builds the index from the layer at run time, so the file can
    only be wrong, never load-bearing; it is kept so a reader sees what the
    agent can cite, and a stale one would show them definitions that no
    longer exist. ``agent-app kb-index`` rewrites it.
    """
    from agent_app.knowledge import KnowledgeBase
    from agent_app.semantic_layer.api import load_registry

    ok = True
    path = path_enum.path_llms_txt
    try:
        expected = KnowledgeBase(load_registry(path_enum.dir_semantic_layer)).index()
    except Exception as e:
        report.error("knowledge-index", str(e))
        expected = None
        ok = False
    if expected is not None:
        if not path.exists():
            report.error("knowledge-index", f"missing: {path}; run `agent-app kb-index`")
            ok = False
        elif path.read_text(encoding="utf-8") != expected:
            report.error("knowledge-index", f"{path} is stale; run `agent-app kb-index`")
            ok = False
    report.section("knowledge index", ok)
    return ok


def check_golden_cases(report: Report) -> bool:
    """
    Fail if data/eval/golden_cases.yml does not load.

    Its reference queries are checked by running them, which `agent-app grade`
    does first thing; this catches the cheaper mistake of a misspelled field
    before anyone spends a model call finding it.
    """
    from agent_app.eval.model import load_golden_cases

    path = path_enum.path_golden_cases_yml
    try:
        cases = load_golden_cases(path)
    except FileNotFoundError:
        report.error("golden-cases", f"missing: {path}")
        cases = None
    except Exception as e:
        report.error("golden-cases", f"{path}: {e}")
        cases = None
    ok = bool(cases)
    if cases == []:
        report.error("golden-cases", f"no cases in {path}")
    # Held-out files load by the same rules; an id shared with the golden set
    # would be graded as the same case twice.
    known = {c.id for c in cases or []}
    held_out = 0
    for extra in sorted(path.parent.glob("holdout/*.yml")):
        try:
            more = load_golden_cases(extra)
        except Exception as e:
            report.error("golden-cases", f"{extra}: {e}")
            ok = False
            continue
        for dup in sorted(known & {c.id for c in more}):
            report.error("golden-cases", f"{extra}: case {dup!r} is also in the golden set")
            ok = False
        held_out += len(more)
    label = f"golden cases ({len(cases or [])}" + (f", {held_out} held out)" if held_out else ")")
    report.section(label, ok)
    return ok


def kind_from_phrases(action: str) -> str:
    """Which block this question's wording will actually produce."""
    hits = [
        kind
        for kind, groups in RENDER_PHRASES.items()
        if all(any(p in action for p in group) for group in groups)
    ]
    if len(hits) == 1:
        return hits[0]
    return "ambiguous" if hits else "free"


def check_render_quota(report: Report) -> bool:
    """
    Fail if the kickstart questions do not exercise every renderer.

    Checked in both directions on purpose. ``render`` is what the button
    promises the reader, the phrase is what the model reacts to, and each can be
    wrong without the other noticing: a question marked ``chart`` whose wording
    forgot "as a bar chart" draws a chart icon over a paragraph of prose.
    """
    ok = True
    site_toml = path_enum.path_site_toml
    if not site_toml.exists():
        report.section("render quota", False)
        return False

    actions = tomllib.loads(site_toml.read_text(encoding="utf-8")).get("chat", {}).get(
        "suggestedActions", []
    )
    tally: dict[str, int] = {kind: 0 for kind in RENDER_QUOTAS}

    for i, item in enumerate(actions):
        missing = {"render", "title", "label", "action"} - set(item)
        if missing:
            report.error("quota", f"suggestedActions[{i}] is missing {sorted(missing)}")
            ok = False
            continue

        declared = item["render"]
        if declared not in RENDER_QUOTAS:
            report.error(
                "quota",
                f"suggestedActions[{i}] '{item['title']}': unknown render {declared!r}",
            )
            ok = False
            continue

        tally[declared] += 1
        actual = kind_from_phrases(item["action"].lower())
        if actual != declared:
            report.error(
                "quota",
                f"suggestedActions[{i}] '{item['title']}': render is {declared!r} but "
                f"the wording produces {actual!r}",
            )
            ok = False

    for kind, want in RENDER_QUOTAS.items():
        if tally[kind] != want:
            report.error("quota", f"{tally[kind]} {kind} question(s), expected {want}")
            ok = False

    report.section(f"render quota ({len(actions)} questions)", ok)
    return ok


def run(retired: list[str] | None = None) -> int:
    """Run every check, print one report, and return a shell exit code."""
    print()
    print("Content consistency check")
    print("=" * 60)

    report = Report()
    db_tables = warehouse_tables()

    check_retired_terms(report, retired or [])

    if db_tables:
        check_prompt_tables(report, db_tables)
    else:
        # Once, instead of one error per table the prompt mentions.
        report.error("warehouse", f"no CREATE TABLE ANALYTICS.* found in {path_enum.path_warehouse_ddl_sql}")
        report.section("warehouse tables", False)

    check_content_files(report)
    check_semantic_layer(report, db_tables or None)
    check_knowledge_index(report)
    check_golden_cases(report)
    check_render_quota(report)

    print("=" * 60)

    for warning in report.warnings:
        print(f"WARN   {warning}")
    for error in report.errors:
        print(f"ERROR  {error}")

    if report.errors:
        print()
        print(f"{len(report.errors)} problem(s) found.")
        return 1

    print()
    print("All checks passed.")
    return 0
