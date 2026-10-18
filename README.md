# agentsignal check

![agentsignal check report card: real run against the naive example graph, 1/5 crash tests survived](docs/report-card.png)

*We kill your LangGraph agent mid-approval, restart it, and count how many times the tool ran.* (A real run against the intentionally broken example graph.)

Crash-test your AI agent for **double execution** and **lost approvals** before your users find them.
It runs on your machine or in CI. It's free and MIT-licensed. Built by [Agent Signal](https://tryagentsignal.com).

```bash
pip install "agentsignal-check[langgraph] @ git+https://github.com/MounirMeziani/agentsignal-check"

agentsignal check --graph myapp.graph:build --effect myapp.payments:charge \
  --input '{"amount":1999}' --report agentsignal-report.md
```

- `--graph mod:factory`: `factory(checkpointer)` returns your compiled LangGraph graph. The harness injects a SQLite checkpointer so state survives process death.
- `--effect mod:func` (can be repeated): the side effects that must happen at most once, such as a charge, an email or a delete.
- `--resume JSON`: the value sent to every `interrupt()`. Default `true` (approve).
- `--multi-interrupt each|map`: when several interrupts are pending (parallel approvals, subgraphs), answer them one at a time by interrupt id (`each`, default, like an approval UI) or all at once with an `{interrupt_id: value}` map (`map`).
- `--report FILE`: writes a markdown report with fixes. `--json` prints machine-readable output.
- Exit code 1 on any FAIL, so you can use it as a CI gate. Exit 2 when nothing FAILed but part of the run errored (a detector ERROR/WARN, a child process that raised, crashed, timed out or never reached its crash point, or a driver error), so a broken run never passes. 0 only when every detector is PASS or SKIP. Child timeout: `AGENTSIGNAL_CHILD_TIMEOUT_S` (default 120).

## Static scan (`--scan`)
Find the pattern in your own code in seconds, before you wire up a crash test. `--scan` reads your files with Python's `ast` module. It never imports or runs your code and makes no network calls, so it's cheap enough to run on every PR.

```bash
agentsignal check --scan ./my_agent                # terminal output
agentsignal check --scan ./my_agent --format json  # or --json
```

```
agentsignal scan  ./my_agent  detected: langgraph 1.2.16 (requirements.txt), openai-agents

These 2 tools will re-run on resume:
  1. pay            my_agent/graph.py:9    payments.charge() runs before interrupt() at :10 AS101 high
  2. charge         my_agent/tools.py:22   approved tool has no idempotency key             AS201 high

Prove it on your code (60s): agentsignal check --graph my_agent.graph:build --effect my_agent.payments:charge
2 tools will re-run on resume. Fix (free guide): https://tryagentsignal.com/guides/duplicate-tool-calls-after-resume/
```

Each finding gives `file:line`, the function, a one-line message, the rule id and its confidence (a path too long for its column goes on its own line, with the message below it). A result with findings ends with the count of tools that will re-run on resume ("may re-run" when every such finding is medium confidence) and the fix link. A clean scan ends with `No re-run risks found by static scan.` and no link. The "Prove it" line names `<module>:build` only when that file defines `build()`.

The langgraph version in the header is your project's, from the same lookup the version-aware rules use (nearest `uv.lock`, `requirements*.txt` or `pyproject.toml`), with the file it came from. With no pin or lockfile it says `langgraph version unknown (no pin found)`; it never shows the version installed next to agentsignal. openai-agents is shown without a version (no project lookup yet). In `--json`, `scan.frameworks.<name>` has `version`, `spec` (a range, when there's no exact pin) and `source`; all `null` when unknown.

| Rule | Framework | Confidence | Flags |
|---|---|---|---|
| AS101 | LangGraph | high | a side-effecting call before `interrupt()` in the same node (the node re-runs from the top on resume) |
| AS102 | LangGraph | high | a side effect and `interrupt()` in the same loop |
| AS103 | LangGraph | medium | a node or tool that runs after an approval and calls an effect with no idempotency key |
| AS104 | LangGraph | medium | the same, with no ledger lookup or `if state.get(<id>)` guard before the effect |
| AS105 | LangGraph | medium | `Command(resume=...)` in a web handler or queue consumer with no processed-id, state or lock check |
| AS106 | LangGraph < 1.2.16 | high / medium | a side effect after `interrupt()` in a parallel node inside a compiled subgraph: answering approvals one at a time re-runs answered siblings ([langgraph#9315](https://github.com/langchain-ai/langgraph/pull/9315)) |
| AS107 | LangGraph < 1.2.16 | high / medium | `Command(resume=..., update=...)` on a graph whose node calls `interrupt()` more than once: the update is applied twice ([#9322](https://github.com/langchain-ai/langgraph/pull/9322)) |
| AS108 | LangGraph < 1.2.16 | high / medium | `durability="exit"` with 3+ parallel `interrupt()` nodes: earlier answers applied twice ([#9314](https://github.com/langchain-ai/langgraph/pull/9314)) |
| AS201 | OpenAI Agents | high | `@function_tool(needs_approval=...)` whose effect has no idempotency key |
| AS202 | OpenAI Agents | medium | the same for a tool without `needs_approval` |
| AS203 | OpenAI Agents | medium | `RunState.from_json/from_string` + approve/run in a handler with no processed-id check |
| AS204 | OpenAI Agents | low | approval tools whose RunState is never saved with `to_json()`/`to_string()` (shown with `--verbose`/`--json`) |

AS106–AS108 are version-aware. The scan reads the langgraph version from the nearest `uv.lock` (resolved version), `requirements*.txt` or `pyproject.toml` at or above each file (up to the repo root). They're high when the project pins or resolves langgraph < 1.2.16, medium with the note "fixed in 1.2.16; pin >=1.2.16" when no version (or only an open range) is found, and not reported when it requires >= 1.2.16. AS101 is unconditional.

Idempotency evidence: a kwarg named `idempotency_key`, `idempotencyKey`, `request_id`, `dedupe_key` or `client_token`; a `key=` built from a run, thread, call or tool-call id (or a stable hash of one); an `Idempotency-Key` header; or a `@ledger`, `@once`, `@idempotent` or `@side_effect_ledger` decorator. A key from `uuid4()`, `time.time()` or `random` is flagged as "key is random per call".

- **Exit code:** 1 if there's any high finding, otherwise 0 (`--strict` also fails on medium). 2 on a usage error. Files that don't parse are listed as skipped and never fail the scan.
- **Options:** `--framework auto|langgraph|openai-agents`, `--effect-names payments.charge,notify` (extra effect callees), `--exclude GLOB` (repeatable), `--include-tests` (test files are skipped by default), `--verbose`. `.venv`, `venv`, `node_modules`, `site-packages`, `.git`, `build` and `dist` are always skipped. Google ADK and Vercel AI SDK files are not scanned yet.
- **Suppress:** `# agentsignal: ignore[AS101]` (or a bare `# agentsignal: ignore`) on the call or `def` line, or in `pyproject.toml`:
  ```toml
  [tool.agentsignal.scan]
  ignore = ["AS202"]
  effect_names = ["payments.charge"]
  exclude = ["scripts/*"]
  ```
  Suppressed findings are counted in the output.
- `--scan PATH --graph ...` (or `--agent ...`) scans first and then runs the crash test; with `--json` the scan lands under `"scan"` in the report. `--upload` doesn't send the scan.
- A static scan can't see runtime behavior. A clean scan means "no re-run risks found by static scan", not "safe"; the crash test is what proves it.

## Report card, HTML, OG image, badge
```bash
agentsignal check --graph myapp.graph:build --effect myapp.payments:charge \
  --html report.html --png og.png --badge agentsignal-badge.svg --badge-link https://example.com/report.html
```
- **Terminal:** an 80-column report card with the grade ("Approval flow: 3/5 crash tests survived"), double-charge and lost-approval risk, and one row per detector with why and how to fix. With `NO_COLOR` set or output piped, it prints plain text. `--json` output is unchanged.
- `--html`: a self-contained dark report page with OG/Twitter meta, and `--svg` exports the terminal card.
- `--png`: a 1200×630 OG card. It needs `pip install "agentsignal-check[png]"` (Pillow).
- `--badge`: a static SVG plus a README snippet. The badge reads **Crash-tested by Agent Signal | N/M** (amber) by default. It switches to **Agent Signal crash test | passing** (green) only when all 5 detectors passed on that run. For example:
  ```bash
  agentsignal check --graph myapp.graph:build --effect myapp.payments:charge \
    --badge docs/agentsignal-badge.svg --badge-link https://example.com/report.html
  # prints e.g.: [![Crash-tested by Agent Signal: 3/5 passed](docs/agentsignal-badge.svg)](https://example.com/report.html)
  ```
  Commit the SVG, paste the snippet into your README, and link it to that repo's latest report. Re-run in CI so it reflects the current commit.

The grade, risks and badge are computed only from the detector results of the current run. The screenshot at the top is a real run against `tests/fixtures/naive_graph.py`, the intentionally broken example. Regenerate it with `scripts/shots.sh`.

## Detectors (LangGraph)
| id | fault injected | FAIL means |
|---|---|---|
| `baseline` | none (clean run, auto-approve) | an effect ran twice with no crash (side effect before `interrupt()` in the same node) |
| `double_execution.kill_resume` | hard kill (`os._exit`) right after each effect, then resume in a fresh process | the effect was re-executed |
| `double_execution.kill_before_checkpoint` | hard kill before each checkpoint `put()`, after `put_writes` ([langgraph#8039](https://github.com/langchain-ai/langgraph/issues/8039)) | the effect was re-executed on recovery |
| `approval.lost_on_restart` | restart the process while paused | the pending approval is gone |
| `approval.replay` | deliver the same approval twice, including against a stale checkpoint | the effect ran again |

A duplicate means the same effect args returned **different** results. A retry that returns the same result (for example, a provider idempotency key) passes.

## Detectors (OpenAI Agents SDK, preview on branch oai-runstate)

```bash
pip install "agentsignal-check[openai-agents] @ git+https://github.com/MounirMeziani/agentsignal-check@oai-runstate"
agentsignal check --framework openai-agents --agent myapp.agent:build \
  --effect myapp.payments:charge --context '{"order_id": "order-42"}'
```

`build()` returns an `agents.Agent` whose side-effect tools use `needs_approval=True`. State crosses process
boundaries only as `RunState.to_json()` / `RunState.from_json()`, as a real app would persist a paused run.

| Detector | What it does |
|---|---|
| baseline | Clean run, approve once: does the effect run once? |
| double_execution.kill_resume | Approve + resume, hard-kill (`os._exit(137)`) right after the effect, restart from the persisted RunState, approve again |
| approval.lost_on_restart | Is the pending approval visible from a fresh process, and is an approval persisted via `to_json()` honoured without re-asking? |
| approval.replay | The same persisted RunState resumed twice (double-click, worker retry, queue redelivery) |

Real run, openai-agents 0.24.0, offline scripted model (`tests/fixtures/oai_*.py`): naive agent
baseline PASS, kill_resume FAIL, lost_on_restart PASS, replay FAIL; idempotent agent (key = order_id + tool name) 4/4 PASS.

## Test scratch space
Each scenario sets `AS_SCENARIO` to a unique path. If your effect writes to a local store during the check, key the store on it so scenarios don't leak into each other.

## Upload runs (Signal Pro)

```bash
export AGENTSIGNAL_API_KEY=...   # create one at https://tryagentsignal.com/pro/keys
agentsignal check --graph app:build --effect app.tools:charge --json --upload
```

Prints the `run_id` and a free fix link (detector, status, url) for every failing detector (the CLI's own link table, never a Pro template). `repo` and `commit_sha` are filled from `GITHUB_REPOSITORY`/`GITHUB_SHA` or your git checkout. Upload errors (bad key, too large, rate limit, network) are reported but don't change the exit code unless you pass `--upload-strict` (exit 3). Without a key, `--upload` only prints a one-line hint.

## Fixing failures
Every FAIL in the report links to a free tested pattern on [tryagentsignal.com](https://tryagentsignal.com). Want it done for you? Use the [$79 Fix](https://tryagentsignal.com/fix).

## Develop / test
```bash
uv run --extra test pytest -q          # or: pip install -e ".[test]" && pytest -q
```
`test` installs pytest and the `langgraph` extra. If you run without it, the LangGraph tests are skipped with a note rather than failing with a cryptic error.
MIT © Mounir Meziani
