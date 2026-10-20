# Reproducibility

The simulator and reproduction runner use the Python standard library. The rebuild was validated with **Python 3.14.6** on macOS arm64. The project directory can run tests and smoke experiments independently after copying it out of the portfolio monorepo.

```sh
python3 reproduce.py tests
python3 reproduce.py verify
python3 reproduce.py smoke
```

`tests` runs the original tests plus seven archive/comparison/configuration safety tests and checks Baseline, Hot/Cold, lifetime, development, and final CLI help. The production final experiment is not invoked by that action. `verify` checks 36 simulator file hashes, ZIP CRC, all 18 archived member hashes, the frozen selection hash referenced by the final manifest, and 8 file/tag references in [FROZEN_SOURCE_SHA256.json](../FROZEN_SOURCE_SHA256.json). A byte match is evidence of preservation, not an independent proof that the original experiment was correct.

`smoke` runs the original v1 calibration/measurement CLI with a small **NEW_SMOKE** configuration: calibration seed 901, measurement seed 902, 3,000 writes, 256 logical pages, hint seed 42, and the explicit grids in [configs/smoke.json](../configs/smoke.json). This is a new experiment; its calibration is not the frozen 451-write boundary and its results are not substituted for the historical v2 study. It does not execute final seeds 301–305 or bypass a provenance guard.

## Historical final replay

The archived final study used seeds 301–305, 50,000 writes/seed, 2,048 logical pages, 14 hint-error rates, and the development-selected B512/K2 detector. The calibration boundary and association threshold remain frozen. Its original Git identity is:

- Final runner HEAD: `c27b72ae76766a05b6c83de89907ffb00bdf1e71`
- Protocol tag commit: `72eb2b472ac9a7b385bca47b106d58ba6d909d53`
- Selection tag commit: `f4fc53f951abf0ad9f9451171a0ac4fb06fc76ea`
- Selection SHA-256: `174bff65e305f1b78956bfa4b829a4e8f8876a55673a82229e9110653306d27d`

The exported portfolio snapshot does not ship the original full Git history. The original production runner deliberately requires a clean worktree and frozen Git tags. A fresh clone of the portfolio alone cannot meet that guard. An authorized, separately restored original repository or preservation bundle is required for a production final replay. After restoring that history and checking out the final runner HEAD, run from this project:

```sh
python3 reproduce.py replay --history-checkout /path/to/restored-original-checkout
```

That path is an input supplied by the researcher; the runner never downloads history. It verifies HEAD, tag commits, clean status, a tracked selection file, selection checksum, and all simulator bytes before calling the **unmodified production CLI** in the historical checkout. Output is written outside that checkout to this project's new `artifacts/runs/.../output/` directory, then compared with all archived final run, block, condition, seed-summary CSVs and summary JSON. No guard, test mode, or monkeypatch is used. Only the branch label may differ when using a detached historical checkout.

This operation reuses the original held-out seeds and is labeled **HISTORICAL_REPLAY**. It is not a new holdout evaluation or new evidence of generalization. New detector tuning must use a separately documented development/evaluation split.

## Evidence and validation levels

See [rebuild_validation.json](../results/manifests/rebuild_validation.json) for executed actions and [rebuild_standalone_validation.json](../results/manifests/rebuild_standalone_validation.json) for tests in an independent temporary copy without the parent repository. Large fresh outputs stay under ignored `artifacts/`; compact reports retain their hashes and comparisons.

| Evidence | Verification level |
|---|---|
| Original tests | 206 passed before reproduction safety checks |
| Tests with safety checks | 213 passed; algorithm source unchanged |
| Frozen source and archived results | Hash/CRC and selection consistency checked |
| Final v2 study | Historical production replay PASS: 220 run rows, 6,790 detector block rows, 70 condition rows, 5 seed summaries and final summary JSON match archived bytes |
| New 901/902-seed smoke | Executed separately; not a portfolio performance result |
| Original-history-free final execution | **BLOCKED** by the original production provenance requirements |

The rebuild restored a separate clean copy of the original Git history and completed the production final replay. All four final CSVs and `final_summary.json` matched archived SHA-256 byte for byte. The first verification wrapper rejected the absolute spelling of the same selection path plus a detached branch label; follow-up validation confirmed canonical path/selection SHA and every scientific/protocol field. [rebuild_historical_final_validation.json](../results/manifests/rebuild_historical_final_validation.json) retains the initial failure and correction. The portable runner uses the original relative selection path.

Run manifests record UTC, Python/platform, source and configuration hashes, current portfolio Git identity when available, commands, return codes, logs, and historical source identity for final replay. Outputs use a unique directory; historical results and source files are never overwritten. CSV comparison preserves the row order and checks all fields with numeric tolerance `rel_tol=abs_tol=1e-12`. The final production manifest is also compared, allowing only an execution branch-label difference.

## Metrics and limits

WAF is NAND page programs divided by host page writes over the full synthetic run. Wear imbalance is the coefficient of variation of simulated block erase counts, not actual NAND lifetime. Error-rate conditions sharing a seed are correlated; the five workload seeds are the independent clusters. The 70 seed/error conditions and 220 cached-policy run rows are not 70 or 220 independent samples. The oracle policy belongs to earlier synthetic analysis and is not a deployable firmware baseline. This model does not implement a real NVMe/FDP stack or measure hardware latency.

The 36 snapshot source hashes and all 18 archived member hashes were checked against preservation upload records; see [rebuild_source_preservation.json](../results/manifests/rebuild_source_preservation.json). Frozen Git provenance supports tracing the original experiment; it does not turn the synthetic result into hardware evidence. Retain known false-fallback and wear trade-offs in figures and portfolio claims.

## Direct entry points

```sh
python3 -m simulator.run_lifetime_experiment --help
python3 -m simulator.run_persistence_development --help
python3 -m simulator.run_persistence_final --help
```

Treat edited settings as a new experiment and preserve the old evidence. Figure regeneration uses archived research outputs, not new smoke calibration/measurement files; follow the README and figure provenance records.
