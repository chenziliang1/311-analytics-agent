# Golden-set eval results

One row per `agent-app grade` run, appended by the command itself; the eval
workflow commits the row it produced. Rates are over the runs that had a
verdict: the judge columns are blank on a `--nojudge` run, and citation only
counts runs whose case names a metric. Thresholds: SQL >= 90%, answer >= 85%,
hallucination <= 5%, citation >= 70%, p95 <= 15s.

| Date (UTC) | Agent | SQL writer | Judge | Cases | Runs | Errors | SQL accuracy | Answer accuracy | Hallucination | Citation | p95 (s) | Note |
| :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 2026-10-10 00:04 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | off | G07-monthly-channel-volume | 1 | 0 | 100.0% |  |  | 100.0% | 9.1 | a one-case smoke run, before citation was redefined (see PR #2) |
| 2026-10-10 02:31 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | all | 35 | 0 | 97.1% | 97.1% | 5.7% | 79.3% | 18.1 | first full run after the knowledge base (run 38016675397); hallucination and p95 gates failed, judge self-graded; recorded by hand because the workflow skipped the record step on failure |
| 2026-10-10 03:55 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | all | 62 | 0 | 95.2% | 95.2% | 1.6% | 94.0% | 12.7 | self-graded judge, fast path off; baseline after PRs #4-#6 (run 38021363244); recorded by hand because the workflow's push was rejected when main moved during the run |
| 2026-10-10 04:11 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | all | 62 | 0 | 95.2% | 95.2% | 1.6% | 94.0% | 12.3 | self-graded judge, fast path on |
| 2026-10-10 04:19 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | colleague-2026-10:all | 7 | 0 | 71.4% | 71.4% | 14.3% | 50.0% | 13.4 | self-graded judge |
| 2026-10-10 04:39 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | all | 70 | 0 | 95.7% | 95.7% | 5.7% | 93.1% | 12.6 | self-graded judge, fast path on |
| 2026-10-10 06:02 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | all | 70 | 0 | 95.7% | 95.7% | 0.0% | 94.8% | 12.1 | self-graded judge |
| 2026-10-10 06:28 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | anthropic/claude-sonnet-5-5 | all | 70 | 0 | 97.1% | 97.1% | 0.0% | 94.8% | 13.4 | self-graded judge, fast path on |
