# Golden-set eval results

One row per `agent-app grade` run, appended by the command itself; the eval
workflow commits the row it produced. Rates are over the runs that had a
verdict: the judge columns are blank on a `--nojudge` run, and citation only
counts runs whose case names a metric. Thresholds: SQL >= 90%, answer >= 85%,
hallucination <= 5%, citation >= 70%, p95 <= 15s.

| Date (UTC) | Agent | SQL writer | Judge | Cases | Runs | Errors | SQL accuracy | Answer accuracy | Hallucination | Citation | p95 (s) | Note |
| :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 2026-10-10 03:04 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | off | G07-monthly-channel-volume | 1 | 0 | 100.0% |  |  | 100.0% | 9.1 | a one-case smoke run, before citation was redefined (see PR #2) |
