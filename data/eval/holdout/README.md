# Held-out questions

The golden set in `../golden_cases.yml` and the agent's prompt were written by
the same person, at the same time, and the prompt's Analysis Index lists the
analyses the golden set asks for. Its scores say how the agent does on
questions its author expected. A held-out file says how it does on questions
somebody else thought of.

## How a file gets here

1. Someone who has **not** read `data/system-prompt.md` or `golden_cases.yml`
   writes ten or so questions a 311 operations analyst would actually ask,
   in their own words. They get the business context (`data/business-context.md`)
   and nothing else.
2. The maintainer writes the reference SQL for each question, runs it on the
   warehouse, and checks the numbers by hand. A question that cannot be
   pinned to one reference query is dropped, not reworded.
3. The file goes in this directory as `<author-or-date>.yml`, in the same
   format as the golden set, with ids that do not collide with it
   (`check-content` enforces that).

Nothing here is graded by default. `agent-app grade --golden data/eval/holdout/<name>.yml`
grades one file locally; in GitHub, Actions -> eval -> Run workflow with the
`golden` input set to that path (or `gh workflow run eval.yml -f golden=data/eval/holdout/<name>.yml`).
Its row in `results.md` names the file in the Cases column.

## What is here

- `colleague-2026-10.yml`: ten questions from one person on 2026-10-09, seven
  with reference SQL verified on the warehouse. The other three asked for a
  crew's first visit time, a report's language, and the cost of repeat
  visits, none of which the warehouse holds; they are listed in the file's
  header as dropped, because "the data cannot answer it" is part of the
  finding. Where a question read differently from the golden set's wording
  ("the same problem at the same address" is the same category, not just the
  same address), the reference follows the question, and the `source` field
  says so.
