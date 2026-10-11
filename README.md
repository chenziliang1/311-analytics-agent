# 311 Analytics Agent (CityPulse 311 Lens)

## What it is

311 Analytics Agent is a BI agent that answers natural-language questions about 311 service requests, backed by a Snowflake warehouse. It is built for the people who run 311 operations and the analysts who support them. Instead of writing SQL, they ask in plain English, such as "Which cities breached SLA most often last month?", and get an answer in seconds: a table or chart where it helps, plus a short note on exactly how each number was computed. The warehouse holds 23 tables and about 11 million rows of synthetic data covering 120 North American cities and counties.

Live demo: https://citypulse-311-lens.vercel.app (no sign-in needed)

## Run it

You need an Anthropic API key and a warehouse of your own: Snowflake, or PostgreSQL with `WAREHOUSE=postgres`. The dataset itself is not in this repository. AWS and Vercel accounts are only needed to deploy.

```bash
git clone https://github.com/chenziliang1/311-analytics-agent && cd 311-analytics-agent
mise run inst                # Python (uv) + Node (pnpm) dependencies
cp .env.example .env         # then fill in ANTHROPIC_API_KEY
mise run dev                 # http://localhost:3000
```

`mise run test` runs the whole test suite with no accounts at all. `mise run snowflake-load` loads the data into Snowflake, and `mise run infra-deploy` ships the agent to AWS Bedrock AgentCore. `mise run cli-grade` scores the agent on the golden set; it calls the model, so it costs money.

Once deployed, two things watch it. A **canary** ([`data/eval/canary.yml`](data/eval/canary.yml)) asks the live agent six plain questions every morning and checks each figure against the one the semantic layer computes by itself; a wrong answer opens a GitHub issue and trips a CloudWatch alarm (`agent-app canary --target local` asks the same of a local agent). Two **switches** in the warehouse (`agent-app switch set --name kill --on --reason "..."`, or `--name suspend:<metric>`) stop every question or questions about one metric within 30 seconds, with the reason shown to users and no deployment.

## How it works

```
Browser (Next.js on Vercel) -> agent runtime (AgentCore) -> MCP tools runtime (AgentCore) -> Snowflake
```

- **Chat UI:** Next.js on Vercel. Open to anyone, with optional GitHub sign-in; live progress while a turn runs, CSV download for long results, and thumbs up or down on every answer. Because the chat needs no sign-in, the bill is bounded twice, in DynamoDB counters: a monthly cap for the whole demo and a daily cap per network address.
- **Agent:** Strands Agents with Claude Sonnet on AWS Bedrock AgentCore. It interprets the question and words the answer.
- **Tools:** a separate MCP server, also on AgentCore. `warehouse_query` goes from semantic layer to SQL writer to guard to Snowflake to audit log.
- **Infrastructure:** AWS CDK, Secrets Manager, and a CloudWatch dashboard with alarms.
- **Quality loop:** a golden-set eval (35 cases, 70 runs: 97.1% SQL accuracy, 0% hallucination, 94.8% of answers citing the definition they rest on, p95 13.4 s; every run is a row in [`data/eval/results.md`](data/eval/results.md)), tests on every push and the eval on demand, a morning canary against the live agent, and thumbs-downs that become new test cases.

The decision I would make again: **the agent never writes SQL.** A dedicated writer model does, and it sees the semantic layer's definition and a reference query for every metric the question names. Each number therefore has exactly one definition, and the agent can stay focused on understanding the user.

The agent never retypes a result either. Every query result gets an id (`r1`, `r2`); the model sees a preview of the rows and writes `[RESULT:r1]` where the table belongs, and the browser draws the whole result there from the rows the query returned. A chart names the result and its columns (`visualize(result_id, x, y)`), and the numbers are bound from the kept rows before the call leaves the agent. A transposed digit or an invented row in a table is no longer a thing the model can do; what is left for the number check is the prose.

The same definitions are the agent's knowledge base, in the shape of an `llms.txt`: an index with one line per document ([`data/llms.txt`](data/llms.txt)), and a document behind each id that the agent reads on demand with `read_source` and cites as `[KB:metric/sla_breach_rate]`. `warehouse_query` returns the definitions it built the query from, whole, so the agent writes "how this was computed" from the document the SQL came from and cites it without a second call, and the eval checks that the citation names that document, not merely that the answer explains itself. (The first version made the agent fetch the definition itself; p95 latency went from 13 s to 18 s and a fifth of answers skipped the fetch, so the query result now carries its own.) Tenant profiles are documents too, read from the warehouse as the signed-in user, so a restricted role sees only its own. There is no vector index on purpose: a few dozen definitions with names the question either uses or does not want exact lookup and a checkable pointer, which nearest-neighbour search cannot promise.

Read-only is enforced three times. sqlglot parses every query and rejects writes, extra statements, unknown tables and residents' personal data. The Snowflake role can only SELECT. Every transaction is rolled back.

Tenant scope is the warehouse's job, not the prompt's. A row access policy on every table with a `tenant_id` decides what a query sees by the session's role: the agent's own role sees all 120 tenants, and a role mapped to one tenant sees that tenant, in the per-request tables (descriptions, events, work orders) as well as in the requests. With `TENANT_ROLE_MAP` set, or `TENANT_ROLE_TABLE` naming the warehouse's login-to-role table, the query tool runs each signed-in user's SQL as their mapped role and puts the connection back afterwards; a user the map does not name gets the default role or is refused. The audit log is always written as the agent's own role. Residents' request text, crew notes and complaint narratives are searched and counted but never returned.

The eval's questions were written by the same person as the prompt. [`data/eval/holdout/`](data/eval/holdout/README.md) holds questions written by someone else, graded with `agent-app grade --golden <file>` and never used to tune the prompt. The first file, seven questions from a colleague, scored 71.4% on SQL: the two misses both read "the same problem at the same address" as address-only, which is now the known gap.

## What I learned

**The model matters more than I expected.** With Amazon Nova Lite as the agent, SQL accuracy on the golden set was 61%. With Claude Sonnet it was 97%, using the same prompts, tools and data. Most of the gap came from follow-up questions: Nova answered them from the previous turn's text instead of running a new query, which produced confident numbers that no query had produced.

**The hardest part was making the eval trustworthy.** My first grader failed correct answers when tied rows came back in a different order, when columns were renamed, when results were pivoted wide, or when a follow-up needed the earlier turn's result. I fixed each of these without letting real errors through. Only then did a score mean something, and only then could I compare models and warehouses with it.

## Next

Latency. p95 is about 13 seconds, because two models run in series: the agent, then the SQL writer. A question that maps cleanly onto one metric can skip the writer and run the semantic layer's compiled query directly: that is `FAST_PATH`, which compiles a question only when it names exactly one metric and nothing the layer has no word for (`agent_app/semantic_layer/fast_path.py`). On by default since the eval of 2026-10-10 answered six of six plain questions right through it at a 4.2 s median against the writer's 8.5 s, with every other question unchanged (`data/eval/results.md`); the eval's `fast_path` input turns it off for a comparison run. Streaming the answer token by token would also make the wait feel shorter even where the total time stays the same.
