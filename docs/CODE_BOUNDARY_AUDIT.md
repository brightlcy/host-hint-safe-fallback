# Code and experiment boundary audit — 2026-10-20

Scope: read-only inspection of `PORTFOLIO_REVIEW_20261019/01_GITHUB_PRIVATE_REVIEW`. No algorithm, detector threshold, raw result, historical manifest, source file, or test was changed. Hash comparison and Python AST import analysis were executed; full experiments/tests were not rerun by this reviewer.

## Decision

**Recommend A: two independent portfolio repositories, preserving the original repository as the provenance/archive record.** Retain a documented frozen copy of the shared code in each repository for this release. Do not extract a new live dependency or rename research modules just to satisfy a conventional folder template.

The projects are separate research questions and data paths, with a common implementation lineage. They are not two independent implementations. Every one of Online Hot/Cold's 50 Python files exists at the same relative path in Host Hint with the same SHA-256 (27 simulator modules and 23 test files); Host Hint adds 9 simulator modules and 10 tests. Shared simulator source is only 260,800 bytes, so explicit frozen vendoring has a modest cost and avoids changing historical execution semantics. A future maintained core package can be a new version with parity tests, rather than a retroactive rewrite of the frozen experiments.

| Option | Evidence-based assessment |
|---|---|
| A — two repositories | Best visitor experience; each can run its own package and tests without sibling dependencies. Small duplicate core is accepted and documented as frozen provenance. Preserve the existing repository and source SHA manifest. |
| B — shared published core | Not yet warranted. Module extraction changes imports/source bytes and frozen-hash paths, needs dependency publication/versioning, complicates offline reproduction, and exposes both projects to shared updates. Reasonable for a separately versioned future development effort. |
| C — independent packages in monorepo | Suitable for the existing archive/index. Current snapshots are not installed packages: both use the name `simulator`; running all tests in one interpreter risks module-cache collisions. Per-project working directories/processes avoid that issue. |

## Actual call boundaries

A's primary comparison entrypoint is `python3 -m simulator.run_compare_hot_cold`. It passes one `common` configuration to `experiment_runner.run_phased_baseline_experiment` and `hot_cold_experiment.run_phased_hot_cold_experiment`. Both consume the same standard trace and capacity basis, warm-up/measurement round counts, SSD geometry, OP, and GC settings. The proposed path adds `hot_threshold`/`hot_window_writes` and uses `HotColdFTL`, which extends `BaselineFTL`. Analytical latency and attribution modules surround both paths. Alibaba adapters/profilers/extractors prepare traces; the FTL does not directly parse raw Alibaba schemas.

B's entrypoints are `run_lifetime_experiment`, `run_persistence_development`, and `run_persistence_final`. Their transitive import closure is exactly:

```text
address, baseline_ftl, config, gc_policy, metadata, trace_io,
lifetime_workload, lifetime_tracker, lifetime_ftl, lifetime_experiment,
persistence_detector, persistence_protocol,
run_lifetime_experiment, run_persistence_development, run_persistence_final
```

Thus **B's research path does not import `hot_cold_ftl`, Alibaba adapters, the trace comparison runner, or the latency model.** Their presence in B is inherited source preservation, not evidence that Alibaba traces were used in the Host Hint experiment. Do not advertise them as B contributions. The six shared active modules are `address`, `baseline_ftl`, `config`, `gc_policy`, `metadata`, and `trace_io` (plus the package initializer).

B generates a classified synthetic page-write prefix per workload seed, injects nested random hint errors with a deterministic seed, and runs Static Host, v1, and v2 against the same event list and `hint_realization_id`. Baseline-zero-information and Device-only do not use hint error and are run once per workload seed. Device-only uses `OnlineLifetimeTracker`; v1/v2 share the device predictor and allocator. Their evidence rule for entering sticky fallback differs. `run_page_write_events` dispatches annotated writes according to FTL type; it does not call the standard trace/analytical-latency runner.

## Metric and comparison semantics

- Functional WAF is `nand_write_pages / host_write_pages`; the accounting invariant is `nand_write_pages == host_write_pages + valid_page_migrations` (`baseline_ftl.py:404`, `:444`). This is a valid model-level page-write ratio, not GC migrations per collection and not measured hardware WAF.
- For A, WAF and event counters in the reported phase are differences from a snapshot after warm-up (`baseline_ftl.py:330`, `hot_cold_experiment.py:124`). Warm-up and measurement replay the same trace; state persists but measurement queue timing resets. This is a repeated-prefix controlled simulation, not a time-disjoint held-out trace evaluation.
- A's synthetic timing is a single-resource FCFS event-cost model (20 us command, 80 us page read, 200 us page program, 2,000 us erase). No hardware latency, NVMe/FDP command execution, or NAND parallelism claim is supported.
- B's final WAF is cumulative over 50,000 synthetic host page writes, not A's after-warm-up measurement interval. Comparing their WAF magnitudes as if they were the same experiment would be invalid.
- B `wear_imbalance_cv` is population standard deviation of per-block erase counts divided by their mean (`lifetime_experiment.py:109`). It describes erase-count dispersion; it does not measure lifetime extension or wear-leveling effectiveness on a real device.
- B harmful/beneficial labels use exact sign of paired `static_waf - device_waf`, with exact numeric equality neutral (`persistence_protocol.py:130`). Do not silently replace this historical definition with a tolerance or statistical-significance rule.
- Target OP is `(physical-logical)/physical`; rounded block geometry changes actual OP. Record target and actual OP rather than calling target 20% exact for every workload (`config.py:99`, `:145`).

## Frozen provenance and test portability

The eight frozen source hashes in `FROZEN_SOURCE_SHA256.json` were recomputed and all match. The manifest records v1 core commit `bb3f3637fb1042caeb8b575ffda215cec7fc3d05` and frozen v2 protocol commit `72eb2b472ac9a7b385bca47b106d58ba6d909d53`. The historical final manifest records final commit `c27b72ae76766a05b6c83de89907ffb00bdf1e71`, frozen selection commit `f4fc53f951abf0ad9f9451171a0ac4fb06fc76ea`, and selection SHA-256 `174bff65e305f1b78956bfa4b829a4e8f8876a55673a82229e9110653306d27d`.

The audited portfolio checkout is commit `73664fb`, origin `https://github.com/brightlcy/ssd-ftl-research-portfolio.git`, with a clean working tree and **no historical Git tags** at inspection. This is a source/result export, not a recovered original experiment Git history. The old absolute input path `/Users/changyeop/ssd_firstExp/ftl_step01` is absent locally.

Production development/final runners still require HEAD, historical frozen tags, and a clean worktree; final also requires frozen selection B512/K2, exact final seed split, 50k writes, logical pages 2048, and the frozen error grid. Test mode cannot use the final seeds. The production guard must remain intact. Do not create historical-looking tags pointing to new portfolio commits.

Export unit tests patch Git metadata with test fixtures; integrity tests compare the above hashes instead of using historical `git show`. Those tests can validate code behavior and fixture-controlled runner gates, but cannot prove historical commit ancestry or reproduce the frozen production environment. Document this distinction in every standalone B README.

**Additional guard limitation:** current runner code checks that named tags resolve and records their commits, but does not itself compare those commits to the historical expected SHA values or verify that all executed sources match the tagged contents. Therefore passing the runner guard alone is not a source-identity proof. Any stronger archive/source audit should be an external verifier and should not silently change the frozen runner.

## Reproduction availability

A archive contains the actual 100k-request standard traces for Alibaba dev4/dev49/dev740. Each matches an input SHA-256 in the archived experiment manifests. Its MSR `prxy_1_100k.standard.csv` is not in this archive; the original manifest records SHA-256 `030108f84013cb7470a130b45b6f03d600b96ccca0cf963fa168c63b7dbf2d23`, size 3,151,752 bytes, and a historical local path. Treat MSR as archived-output-verified until the exact trace is recovered, not fully rerun from this checkout. Small sample fixtures are parser/testing inputs, not replacements for that missing trace.

B archive contains calibration/core, development, frozen selection, final rows, condition outcomes, block results, seed summaries, and manifests. It has no Alibaba input because final B uses controlled synthetic events. Source-only smoke/unit experiments must be labeled new test executions and must not overwrite these historical paths.

## Implementation boundaries to preserve

1. Keep the small existing `simulator/` modules intact; they already separate mapping metadata, GC, geometry, placement, trace preparation, experimental execution, and analysis semantics. Add packaging, runnable examples/config wrappers, docs, provenance, and analysis scripts around them.
2. Separate processed plotting tables from archived raw results and from new verification output. Keep input checksums and old absolute paths in immutable original manifests; add relocation mappings separately.
3. In B retain inherited modules/tests as provenance or explicitly documented inherited material. Moving/renaming them solely for aesthetics can break frozen source paths and common tests.
4. Run project tests in separate processes/work directories to avoid the same-named `simulator` package resolving to the wrong project.
5. New canonical docs should label original result inspection, newly rerun smoke/trace verification, and blocked historical final reproduction separately.
