# Changelog

## Unreleased
- **Static scan:** `agentsignal check --scan PATH` finds side effects that re-run on resume, using Python's `ast` only (never imports or runs your code, no network). LangGraph rules AS101–AS105 (effect before `interrupt()`, effect in a loop with `interrupt()`, post-approval effect without an idempotency key, resume path without a ledger lookup, approval handler deliverable twice) and OpenAI Agents rules AS201–AS204 (approved tool without a key, tool without a key, RunState resumed twice in a handler, approval never persisted). A `key=` kwarg counts as idempotency evidence when it's built from a run, thread, call or tool-call id or a stable hash of one; random keys are flagged "key is random per call".
- Output is `file:line`, function, one-line message, rule id and confidence, and every result ends with "N tools will re-run on resume" and the fix link https://tryagentsignal.com/templates/side-effect-ledger/. `--format json` (or `--json`) gives `{"scan": {...}}` with stable ordering.
- Exit 1 on any high finding, 0 otherwise (`--strict` fails on medium too), 2 on usage error. Unparseable files are listed as skipped. `--effect-names`, `--exclude`, `--include-tests`, `--verbose`, inline `# agentsignal: ignore[...]` and `[tool.agentsignal.scan]` in pyproject.toml (suppressions are counted).
- `--effect` is now required only for the crash test; `--scan` needs neither `--effect` nor `--graph`. `--scan` with `--graph/--agent` scans first, then runs the crash test.
- Tests: `tests/test_scan.py` covers every rule, the board 53 acceptance fixtures, a precision corpus of the public templates (`tests/fixtures/scan/corpus/`), false-positive guards, suppression/config, skipped files, a no-import guard, golden terminal/JSON output, and a 2,000-file speed check.
- **Fix:** when the baseline run errors (setup, import or graph/agent failure), `agentsignal check` now exits 2 with a clear "baseline run failed" error on stderr. It used to exit 0, so a broken setup passed CI. Tests: `tests/test_baseline_error.py`.
- **Ledger leases:** if a watched effect raises an exception named `EffectInFlight` while `double_execution.kill_resume` or `double_execution.kill_before_checkpoint` resumes, it counts as a clean refusal: no duplicate was observed, so the detector passes with the note "in flight; retried after lease with the same provider key" (board 54, decision G7). Matched by class name, so there's no import dependency. Any other exception on resume is still a WARN. Tests: `tests/test_effect_in_flight.py`, fixture `tests/fixtures/inflight_graph.py`.

## 0.3.2 (2026-10-16)
- **Fix:** with `--framework openai-agents`, fix links in the terminal card, plain output, Markdown report and HTML report pointed at LangGraph templates. They now point at `/templates/openai-agents-idempotent-approval/` (double execution, replay) and `/templates/openai-agents-approval-resume/` (lost approval on restart), with OpenAI Agents wording in the Markdown report. LangGraph links are unchanged. Links live in one per-framework table (`agentsignal_check/links.py`).
- Tests: `tests/test_fix_links.py` (per-framework mapping across all renderers; `AGENTSIGNAL_NET_TESTS=1` checks every URL returns 200).

## 0.3.1 (2026-10-16)
- **`--upload` (Signal Pro):** posts the `--json` report to `https://tryagentsignal.com/api/check/runs/` with `Authorization: Bearer $AGENTSIGNAL_API_KEY` (override the URL with `AGENTSIGNAL_API_URL`) and prints the returned `run_id` and fix links (detector, status, url). The body follows the server contract: `{report:{framework, results:[{detector, status, detail}], env}, repo, commit_sha, framework, version}`; statuses map to PASS/FAIL/ERROR/SKIP (WARN counts as FAIL, anything unknown as ERROR), and `repo`/`commit_sha` come from `GITHUB_REPOSITORY`/`GITHUB_SHA` or the local git checkout when available. 401, 403, 400, 413, 429 and network errors print a clear message and never change the exit code; add `--upload-strict` to exit 3 when the upload fails on an otherwise passing run. With no key, `--upload` prints a one-line hint and the check runs exactly as before. With `--json`, upload messages go to stderr so stdout stays pure JSON.

## 0.3.0 (2026-10-16)
- **OpenAI Agents SDK:** `agentsignal check --framework openai-agents` with kill/resume, lost-approval, and replay detectors over `RunState.to_json()` / `RunState.from_json()`.
- New `[openai-agents]` extra (`openai-agents>=0.24`); `test` extra now pulls it in.
- Offline fixtures and pytest coverage for naive vs idempotent agents (`tests/fixtures/oai_*.py`, `tests/test_oai_crash.py`).
- README: install and run notes for the openai-agents path.

## 0.2.1 (2026-10-15)
- The card footer and HTML/OG tagline now read: "We kill your LangGraph agent mid-approval, restart it, and count how many times the tool ran."
- The badge reads "Crash-tested by Agent Signal | N/M" by default, and "Agent Signal crash test | passing" only when all detectors pass on the run.
- README: tagline under the screenshot, and badge docs.

## 0.2.0 (2026-10-15)
- **Report card:** a rich 80-column terminal card with the grade ("Approval flow: N/M crash tests survived"), double-charge and lost-approval risk, and one row per detector with why and a fix link. With `NO_COLOR` set or output piped, it prints plain text.
- **New outputs:** `--html` (a self-contained dark report page with OG/Twitter meta), `--svg` (terminal card export), `--png` (a 1200×630 OG card, needs the new `[png]` extra), and `--badge` with `--badge-link` (a static shields-style SVG and README snippet that says "passing" only when every detector passed, otherwise `N/M` in amber).
- **Env block:** `--json`, HTML and the card footer now record framework versions (langgraph, langgraph-checkpoint, …), the agentsignal-check version, the Python version and the run timestamp.
- **Clean-env testing:** a new `test` extra (`uv run --extra test pytest`). Without the LangGraph extra, the LangGraph tests are skipped with a clear message, and the CLI tells you what to install.
- `agentsignal check --version`.
- `rich` is now a runtime dependency.
- Honesty notes: under LangGraph's default async durability, some crash points are timing-dependent, so the safe example can show different results on different runs. The grades always reflect the actual run.

## 0.1.0 (2026-10-15)
- First release: `agentsignal check` with 5 LangGraph detectors (baseline, kill_resume, kill_before_checkpoint, lost_on_restart, replay), `--report` markdown and `--json`.
