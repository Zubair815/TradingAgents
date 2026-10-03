# Phase 3 - Graph-aware LLM cost estimator

Evidence date: 2026-10-02 (Asia/Karachi)

Status: **COMPLETE**

Requirements: `COST-001`, `COST-002`, `COST-003`, `COST-004`, `GAP-02`

## Implemented contract

The pre-launch estimator now models the Forex graph rather than applying one
fixed token amount per analysis. Its typed result includes:

- analysis points, analyst count, debate settings, and workflow multiplier;
- analyst, bull, bear, research-manager, trader, risk-debate, risk-manager,
  portfolio-manager, memory, and deterministic-risk call fields;
- quick-model and deep-model call totals;
- separate estimated input, output, and total tokens;
- per-model pricing status, rates, source, and provider/model identity;
- baseline, low, and high estimated cost when all required pricing is known;
- an explicit `UNAVAILABLE` cost state when any required model price is unknown;
- backtest, walk-forward, and ablation workflow scaling;
- disclosures for retries, tool loops, caching, prompt-size variation, and
  provider price changes; and
- `estimate_kind=ESTIMATE` with `actual_usage=None`, preserving provider-reported
  usage as a separate runtime record.

## Graph semantics

- Each enabled analyst is one base quick-model call per analysis point.
- Bull and bear calls scale independently with debate rounds.
- Research manager and portfolio manager use the deep model.
- Trader uses the quick model.
- Forex deterministic risk and sizing are not counted as LLM calls.
- Historical memory retrieval is not counted as an LLM call.
- Optional risk-debate fields remain explicit and default to zero for Forex.
- Retries and tool-driven re-entry are disclosed uncertainty, not guaranteed
  calls.

## Pricing behavior

The built-in catalogue contains pinned GPT-4.1 and GPT-4.1 mini input/output
rates with direct OpenAI model-page sources and a verification date. Other
provider/model pairs remain `UNAVAILABLE` until explicitly configured; they
never produce a misleading `$0` estimate.

## Workflow behavior

- Backtests multiply one graph estimate by capped analysis points.
- Walk-forward estimates use the validator's actual multi-period split count.
- Generic ablation estimates apply the requested variant multiplier once.
- Historical ablation launch estimates aggregate each variant's analyst,
  debate, memory, provider, and model configuration independently.
- The dashboard shows estimate-versus-actual wording, quick/deep calls, separate
  input/output tokens, cost range or unavailable state, and uncertainty text.

## Regression coverage

- one versus three debate rounds;
- one analyst versus the complete analyst set;
- debate enabled versus disabled;
- memory enabled versus disabled;
- distinct quick/deep calls, token assumptions, and rates;
- supported versus unsupported providers/models;
- backtest, multi-period walk-forward, and ablation multipliers;
- maximum-analysis-point caps;
- no deterministic-risk or memory call inflation; and
- estimate and actual usage separation.

## Verification evidence

- Focused cost, historical backtest, walk-forward, and ablation suites:
  `64 passed`.
- Full non-browser suite: `2223 passed, 5 skipped, 88 subtests passed`.
- Chromium end-to-end suite: `6 passed`.
- Ruff full-repository check: passed.
- Python byte-compilation (`tradingagents`, `web`, and `cli`): passed.
- JavaScript syntax checks for every file under `web`: passed.
- Git whitespace/error check: passed; Git emitted only line-ending notices.
- Broker-write static search: no runtime broker-write call exists; the only
  match is the read-only service docstring stating that `order_send()` is never
  invoked.

The five skips are the existing Windows-inapplicable POSIX permission tests and
optional/live-provider tests whose dependencies or credentials are unavailable.
