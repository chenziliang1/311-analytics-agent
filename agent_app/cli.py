# -*- coding: utf-8 -*-

"""
The application's command line: ``agent-app <command>``.

Installed as a console script by ``pyproject.toml``, so ``mise.toml`` names a
command rather than a file path. That indirection is the point: where a check
happens to live can change without every task, doc, and habit changing with it.

Argument parsing only; the work lives in the module each command calls. The
parsing itself is ``fire``, which derives the interface from :class:`Cli` --
one public method is one subcommand, and its signature is that subcommand's
options. Adding a command is adding a method, with no parser wiring to keep in
step with it.

``fire`` accepts either spelling of a method name, so ``check-content`` and
``check_content`` both work; the hyphenated one is what the docs and tasks use.

    agent-app check-content
    agent-app check-content --retired "patient,bed,ward"
    agent-app test-db-conn
    agent-app print-db-schema
"""

import typing as T

import fire


def _as_word_list(value: T.Any) -> list[str]:
    """
    Normalize whatever ``fire`` handed us into a list of non-empty words.

    ``--retired "a,b"`` arrives as a tuple because fire parses comma-separated
    values for us, but ``--retired a`` arrives as a plain string and
    ``--retired ""`` as an empty one. Accepting all three keeps the flag's
    behaviour independent of how many words happen to be in it.
    """
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(word).strip() for word in value if str(word).strip()]


class Cli:
    """
    Application commands. One public method is one subcommand.

    Every command exits through :class:`SystemExit` rather than returning a
    code, because fire prints a returned value and then exits 0 -- which would
    turn a failed check into a green build with the number 1 on stdout.
    """

    def check_content(self, retired: T.Any = "") -> None:
        """
        Verify data/ still agrees with the prompt, the seed, and itself.

        :param retired: Vocabulary from the dataset this repo used to serve,
            comma-separated. Pass it right after a swap to prove none of it
            survives, e.g. ``--retired "patient,bed,ward"``. Empty by default.
        """
        from agent_app.content_check import run

        raise SystemExit(run(retired=_as_word_list(retired)))

    def kb_index(self) -> None:
        """
        Write data/llms.txt, the knowledge base index, from the semantic layer.

        The agent builds the same index at run time; the file is for readers
        of the repository, and `check-content` fails when it has drifted from
        the definitions.
        """
        from agent_app.knowledge import KnowledgeBase
        from agent_app.paths import path_enum
        from agent_app.semantic_layer.api import load_registry

        index = KnowledgeBase(load_registry(path_enum.dir_semantic_layer)).index()
        path_enum.path_llms_txt.write_text(index, encoding="utf-8")
        print(f"Wrote {path_enum.path_llms_txt} ({index.count(chr(10))} lines)")

    def canary(self, target: str = "remote", publish: bool = False, cases: T.Any = "") -> None:
        """
        Ask the agent the canary questions (data/eval/canary.yml) and check
        each answer against the figure the semantic layer computes itself.

        Exits 1 when any question fails. The report goes to tmp/canary/.

        :param target: ``remote`` asks the deployed agent (AGENTCORE_RUNTIME_ARN
            and AWS credentials that may invoke it); ``local`` runs the agent in
            this process, which spends model calls here.
        :param publish: Also publish CanaryQuestions and CanaryFailures to
            CloudWatch (METRICS_NAMESPACE), which the dashboard's alarm watches.
        :param cases: Comma-separated case ids, or prefixes, to run a subset.
        """
        from agent_app import canary as c
        from agent_app.api import one
        from agent_app.paths import path_enum
        from agent_app.semantic_layer.api import load_registry

        registry = load_registry(path_enum.dir_semantic_layer)
        wanted = _as_word_list(cases)
        selected = [
            case for case in c.load_canary_cases(path_enum.path_canary_yml, registry)
            if not wanted or any(case.id == w or case.id.startswith(w) for w in wanted)
        ]
        if target == "remote":
            arn = one.config.agentcore_runtime_arn
            if not arn:
                raise SystemExit("AGENTCORE_RUNTIME_ARN is not set; nothing to ask. Use --target local to run the agent here.")
            ask = c.remote_ask(one.bedrock_agentcore_client(), arn)
        elif target == "local":
            ask = c.local_ask()
        else:
            raise SystemExit(f"--target must be remote or local, not {target!r}")
        print(f"Canary: {len(selected)} questions to the {target} agent\n")
        results = c.run(selected, ask, c.compiled_reference(one.engine, registry, one.sql_dialect))
        print(c.render(results))
        report = c.summary(results, target)
        print(f"\nReport: {c.write_report(path_enum.dir_canary_reports, report)}")
        if publish:
            c.publish(one.metrics("canary"), results)
            print("Published CanaryQuestions and CanaryFailures.")
        raise SystemExit(1 if report["failed"] else 0)

    def switch(self, action: str = "show", name: str = "", on: bool = False, reason: str = "") -> None:
        """
        Read or flip the operator's switches (agent_app/switches.py), which the
        warehouse tool re-reads every SWITCH_TTL seconds: no deployment.

            agent-app switch                          # what is thrown now
            agent-app switch set --name kill --on --reason "load failed at 03:00"
            agent-app switch set --name suspend:sla_breach_rate --on --reason "definition under review"
            agent-app switch set --name kill            # off

        Writes as the loader (its key under ~/.snowflake/citypulse/), because
        the agent's own role may only read the table.

        :param action: ``show`` or ``set``.
        :param name: ``kill`` or ``suspend:<metric>``.
        :param on: Throw the switch; without it, the switch is cleared.
        :param reason: Shown to users in the refusal. Required with --on.
        """
        import getpass
        import os
        from pathlib import Path

        import snowflake.connector

        from agent_app.api import one
        from agent_app.switches import KILL, SUSPEND, Switches, upsert_sql

        config = one.config
        table = config.switch_table
        if not table:
            raise SystemExit("SWITCH_TABLE is not set.")
        key_path = Path(os.environ.get("SNOWFLAKE_LOADER_KEY_PATH", "~/.snowflake/citypulse/loader_rsa_key.p8")).expanduser()
        conn = snowflake.connector.connect(
            account=config.snowflake_account,
            user=os.environ.get("SNOWFLAKE_LOADER_USER", "CITYPULSE_LOADER_SVC"),
            private_key_file=str(key_path),
            role=os.environ.get("SNOWFLAKE_LOADER_ROLE", "CITYPULSE_LOADER"),
            warehouse=config.snowflake_warehouse,
            database=config.snowflake_database,
        )
        cur = conn.cursor()
        qualified = table if table.count(".") == 2 else f"{config.snowflake_database}.{table}"
        if action == "set":
            if not name or not (name == KILL or (name.startswith(SUSPEND) and len(name) > len(SUSPEND))):
                raise SystemExit("--name must be kill or suspend:<metric>")
            if on and not reason:
                raise SystemExit("--reason is required when throwing a switch: users will read it.")
            cur.execute(upsert_sql(qualified), {
                "name": name, "value": "on" if on else "off", "reason": reason or None,
                "changed_by": getpass.getuser(),
            })
            conn.commit()
            print(f"{name} = {'on' if on else 'off'}; the tool sees it within {config.switch_ttl_seconds:.0f}s")
        elif action != "show":
            raise SystemExit("action must be show or set")
        rows = cur.execute(f"SELECT name, value, reason, changed_at, changed_by FROM {qualified} ORDER BY name").fetchall()
        conn.close()
        switches = Switches.from_rows(rows)
        print(f"kill: {'ON, ' + switches.killed if switches.killed else 'off'}")
        print(f"suspended metrics: {dict(switches.suspended) or 'none'}")
        for row in rows:
            print(f"  {row[0]:28s} {row[1]:4s} {row[3]}  {row[4] or ''}  {row[2] or ''}")
        raise SystemExit(0)

    def test_db_conn(self) -> None:
        """
        Prove this machine can reach the cloud PostgreSQL, and nothing else.

        The smallest possible round trip: connect, ``SELECT 1``, disconnect. It
        touches no schema and no table on purpose -- when the agent is not
        working, this separates a credential or network problem from an unsynced
        or empty schema, which are the two failures that otherwise look alike.
        """
        import sqlalchemy as sa

        from agent_app.api import one

        host = one.config.db_host
        print(f"Connecting to {host} ...")
        try:
            with one.remote_postgres_engine.connect() as conn:
                value = conn.execute(sa.text("SELECT 1;")).scalar_one()
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")
            print(
                "\nCheck the DB_ variables in .env. They are secrets, so they "
                "are not in .env.defaults -- see .env.example for the full list."
            )
            raise SystemExit(1)

        print(f"OK: the database answered {value}.")
        print(
            "This proves credentials and network only. Whether this project's "
            "schema holds any rows is a separate question -- print-db-schema "
            "answers it."
        )
        raise SystemExit(0)

    def grade(
        self,
        cases: T.Any = "",
        judge: bool = True,
        agent_provider: str = "",
        agent_model: str = "",
        writer_provider: str = "",
        writer_model: str = "",
        golden: str = "",
    ) -> None:
        """
        Grade the agent on the golden conversations in data/eval/.

        Spends real money: every case is a full agent turn plus a judge call.
        Writes tmp/eval/<time>-<agent provider>.json and exits 1 when any of the
        five scores misses its threshold.

        :param cases: Comma-separated case ids, or id prefixes (``G0``), to run
            a subset. Empty runs all of them.
        :param golden: Another golden file to grade instead of the default,
            e.g. ``data/eval/holdout/<name>.yml``: questions written by someone
            who did not write the prompt (see data/eval/holdout/README.md).
        :param judge: ``--nojudge`` skips the LLM judge: only result matching,
            the explained check and latency are scored.
        :param agent_provider: Overrides LLM_PROVIDER for this run, which is how
            one golden set is run across providers.
        :param agent_model: Overrides LLM_MODEL_ID.
        :param writer_provider: Overrides SQL_WRITER_PROVIDER.
        :param writer_model: Overrides SQL_WRITER_MODEL_ID.
        """
        import datetime
        import json
        import os

        # Before anything reads the config: .env files never override a
        # variable that is already set, so these win.
        for name, value in (
            ("LLM_PROVIDER", agent_provider),
            ("LLM_MODEL_ID", agent_model),
            ("SQL_WRITER_PROVIDER", writer_provider),
            ("SQL_WRITER_MODEL_ID", writer_model),
        ):
            if value:
                os.environ[name] = value

        from agent_app.api import one
        from agent_app.eval import live
        from agent_app.eval.model import load_golden_cases
        from agent_app.eval.report import render, summarize
        from agent_app.eval.runner import run_all
        from agent_app.paths import path_enum

        from pathlib import Path

        golden_path = Path(golden) if golden else path_enum.path_golden_cases_yml
        wanted = _as_word_list(cases)
        selected = [
            c for c in load_golden_cases(golden_path)
            if not wanted or any(c.id == w or c.id.startswith(w) for w in wanted)
        ]
        config = one.config
        print(
            f"Agent {config.llm_provider}/{config.model_id}, writer "
            f"{config.sql_writer_provider}/{config.sql_writer_model_id}, judge "
            f"{config.eval_judge_provider + '/' + config.eval_judge_model_id if judge else 'off'}"
        )
        self_graded = bool(judge) and config.judge_is_self_graded
        if self_graded:
            print(
                "Warning: the judge shares a model family with the agent or the writer, "
                "and may favor their answers. Set EVAL_JUDGE_PROVIDER, or add another "
                "provider's API key."
            )
        print(f"{len(selected)} cases, {sum(len(c.questions) for c in selected)} runs\n")

        def progress(score):
            mark = "S" if score.sql_ok else "s"
            mark += {True: "A", False: "a", None: "?"}[score.answer_ok]
            print(f"  {mark}  {score.case_id:28s} {score.seconds:5.1f}s", flush=True)

        from agent_app.knowledge import KnowledgeBase
        from agent_app.semantic_layer.api import load_registry

        scores = run_all(
            selected,
            chat=live.app_chat(),
            reference=live.db_reference(),
            judge=live.model_judge() if judge else None,
            on_score=progress,
            # A citation of a document that does not exist scores as none.
            knows=KnowledgeBase(load_registry(path_enum.dir_semantic_layer)).knows,
        )
        summary = summarize(scores)
        summary["config"] = {
            "agent": f"{config.llm_provider}/{config.model_id}",
            "writer": f"{config.sql_writer_provider}/{config.sql_writer_model_id}",
            "judge": f"{config.eval_judge_provider}/{config.eval_judge_model_id}" if judge else None,
            "judge_self_graded": self_graded,
            "cases": (f"{golden_path.stem}:" if golden else "") + (",".join(wanted) if wanted else "all"),
            "golden": str(golden_path),
            "fast_path": config.fast_path,
        }
        path_enum.dir_eval_reports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = path_enum.dir_eval_reports / f"{stamp}-{config.llm_provider}.json"
        out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print("\n" + render(summary))
        print(f"\nReport: {out}")
        # The committed record: every number quoted anywhere points back here.
        from agent_app.eval.report import append_results

        notes = (["self-graded judge"] if self_graded else []) + (["fast path on"] if config.fast_path else [])
        append_results(path_enum.path_eval_results_md, summary, note=", ".join(notes))
        print(f"Recorded in: {path_enum.path_eval_results_md}")
        failed = [k for k, v in summary["passed"].items() if v is False]
        raise SystemExit(1 if failed else 0)

    def feedback(self, rating: str = "down", days: int = 7, draft: str = "") -> None:
        """
        Triage thumbs up and down, and turn a thumbs-down into a golden case.

        Lists the votes of the last ``days`` days, newest first. With
        ``--draft <id>`` (an id or its first characters) it writes a golden
        case draft for that vote to tmp/feedback/, built from the question,
        the comment and the query that ran -- the suspect query, for a
        reviewer to correct before the case joins data/eval/golden_cases.yml.

        :param rating: down, up, or all.
        :param days: How far back to list.
        :param draft: A vote id (prefix) to draft a golden case from.
        """
        import datetime

        from agent_app.api import one
        from agent_app.mcp_server.feedback import (
            draft_golden_case,
            read_jsonl_feedback,
            read_jsonl_session_queries,
            read_session_queries,
            read_warehouse_feedback,
        )
        from agent_app.paths import path_enum

        on_snowflake = one.config.warehouse == "snowflake"
        since = datetime.datetime.utcnow() - datetime.timedelta(days=int(days))
        if on_snowflake:
            votes = read_warehouse_feedback(one.engine, since)
        else:
            votes = [v for v in read_jsonl_feedback(path_enum.path_feedback_jsonl) if v.rated_at >= since]
            votes.sort(key=lambda v: v.rated_at, reverse=True)

        if draft:
            matches = [v for v in votes if v.feedback_id.startswith(str(draft))]
            if len(matches) != 1:
                print(f"{len(matches)} votes in the last {days} days match {draft!r}; need exactly one.")
                raise SystemExit(1)
            vote = matches[0]
            if on_snowflake:
                queries = read_session_queries(one.engine, vote.session_id, vote.rated_at)
            else:
                queries = read_jsonl_session_queries(path_enum.path_query_log_jsonl, vote.session_id, vote.rated_at)
            out = path_enum.dir_feedback_drafts / f"F-{vote.feedback_id[:8]}.yml"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(draft_golden_case(vote, queries), encoding="utf-8")
            print(f"Draft written: {out}")
            print("Fix reference_sql and match, append it to data/eval/golden_cases.yml, then")
            print(f"run: agent-app grade --cases F-{vote.feedback_id[:8]}")
            raise SystemExit(0)

        shown = [v for v in votes if rating == "all" or v.rating == rating]
        ups = sum(v.rating == "up" for v in votes)
        print(f"Last {days} days: {ups} up, {len(votes) - ups} down. Showing {rating}:\n")
        for v in shown:
            mark = "+" if v.rating == "up" else "-"
            print(f"  {mark} {v.feedback_id[:8]}  {v.rated_at:%Y-%m-%d %H:%M}  {v.user_id}")
            print(f"      Q: {v.question[:110]}")
            if v.comment:
                print(f"      comment: {v.comment[:110]}")
        if any(v.rating == "down" for v in shown):
            print("\nDraft a golden case from one: agent-app feedback --draft <id>")
        raise SystemExit(0)

    def push_secrets(self, llm_secret: str = "citypulse/llm", db_secret: str = "citypulse/db") -> None:
        """
        Copy this machine's model key and database settings into the Secrets
        Manager entries the deployed runtimes read (created empty by CDK).

        Prints key names, never values. Run it after the first deploy and
        whenever a credential rotates; the runtimes pick the new value up on
        their next cold start.
        """
        import json

        from agent_app.api import one
        from agent_app.config import load_env_files

        from pathlib import Path

        load_env_files()
        c = one.config
        db = {
            "DB_HOST": c.db_host,
            "DB_PORT": None if c.db_port is None else str(c.db_port),
            "DB_USER": c.db_user,
            "DB_PASS": c.db_pass,
            "DB_NAME": c.db_name,
        }
        if c.warehouse == "snowflake":
            # The runtimes have no ~/.snowflake: they get the key's PEM text.
            key = c.snowflake_private_key or (
                Path(c.snowflake_private_key_path).expanduser().read_text()
                if c.snowflake_private_key_path else None
            )
            db["SNOWFLAKE_PRIVATE_KEY"] = key
        payloads = {
            llm_secret: {"ANTHROPIC_API_KEY": c.anthropic_api_key},
            db_secret: db,
        }
        missing = [k for p in payloads.values() for k, v in p.items() if not v]
        if missing:
            print(f"Not set on this machine: {', '.join(missing)}. Nothing was pushed.")
            raise SystemExit(1)
        client = one.boto_ses.client("secretsmanager")
        for secret_id, values in payloads.items():
            client.put_secret_value(SecretId=secret_id, SecretString=json.dumps(values))
            print(f"{secret_id}: {', '.join(values)}")
        raise SystemExit(0)

    def agentcore_app(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """
        Serve the agent the way AgentCore Runtime runs it: GET /ping and
        POST /invocations on port 8080. The container's entrypoint for the
        agent runtime; also runnable locally to test that contract.
        """
        import uvicorn

        from agent_app.agentcore_app import build_app

        uvicorn.run(build_app(), host=host, port=int(port), log_level="info")
        raise SystemExit(0)

    def mcp_server(
        self,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        """
        Serve the warehouse tools over MCP.

        :param transport: ``stdio`` when the agent starts this as a child
            process (the local default), ``streamable-http`` when it runs as
            its own service.
        :param host: Bind address for ``streamable-http``. A container needs
            ``0.0.0.0``.
        :param port: Port for ``streamable-http``.
        """
        from agent_app.mcp_server.server import build_server, warm_up

        if transport == "stdio":
            # Started on the first question, so a user is waiting: get the
            # warehouse ready while the agent is still on its first model
            # call. Writes only to stderr, so it is safe under stdio.
            warm_up()
            server = build_server()
        else:
            # No warm-up here: a session can start with nobody asking (an
            # agent container opening its connection), and the warm-up's
            # SELECT 1 resumed the warehouse for its 60-second minimum each
            # time. The first tool call connects instead.
            #
            # Stateless: each request stands alone, so any instance behind a
            # load balancer can answer it. Plain JSON responses, not an SSE
            # stream per request: behind AgentCore's proxy the stream was
            # closed once the result arrived, and the server logged a
            # ClosedResourceError on every call. Every tool here returns one
            # result, so the stream bought nothing.
            server = build_server(
                host=host, port=int(port), stateless_http=True, json_response=True
            )
        server.run(transport=transport)
        raise SystemExit(0)

    def replay_semantic_layer(self) -> None:
        """
        Run every metric in data/semantic_layer/ against the database and
        compare it with the value it was signed off with.

        A metric definition is a promise that the number means one thing. This
        is the check that the promise still holds after someone edits a
        formula: a changed number fails here instead of shipping silently.
        """
        from agent_app.api import one
        from agent_app.paths import path_enum
        from agent_app.semantic_layer.api import load_registry, replay

        registry = load_registry(path_enum.dir_semantic_layer)
        print(
            f"Semantic layer {registry.version}: replaying on {one.config.warehouse} "
            f"({one.warehouse_schema})"
        )
        results = replay(one.engine, registry, dialect=one.sql_dialect)
        for r in results:
            mark = "PASS" if r.ok else "FAIL"
            actual = "error" if r.actual is None else f"{r.actual:.6g}"
            print(
                f"  {mark}  {r.metric:28s} expected {r.expected:.6g} "
                f"(+/- {r.tolerance:g})  got {actual}  [{r.seconds:.1f}s]"
            )
            if r.error:
                print(f"        {r.error}")
        failed = [r for r in results if not r.ok]
        print(f"\n{len(results) - len(failed)}/{len(results)} metrics reproduce.")
        raise SystemExit(1 if failed else 0)

    def print_db_schema(self) -> None:
        """
        Print the database schema exactly as the agent is shown it.

        Not a dump of the database: it is the compact encoding the agent reads
        as its entire picture of the tables, so this is the one way to see what
        the model actually knows before it writes a line of SQL. Reads this
        project's schema only, and needs a synced one -- an empty schema raises
        rather than printing nothing, because nothing is what an unsynced
        database and a broken encoder both look like.
        """
        from agent_app.api import one

        print(one.database_schema_str)
        raise SystemExit(0)


def main() -> None:
    fire.Fire(Cli, name="agent-app")


if __name__ == "__main__":
    main()
