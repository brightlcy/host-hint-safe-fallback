# Independent Red Team review — 2026-10-20

**Current status after independent follow-up: all three code findings RESOLVED.** Ten analysis regression tests and fourteen reproduction-safety tests passed. Source/archive preservation checks passed; no frozen simulator bytes changed. One release-time provenance sentence still needed a wording refresh at this follow-up; remote CI execution remains unverified. Original findings below are retained as an audit trail.

Scope: newly added `reproduce.py`, configs, analysis/plots, README and technical docs in both projects under `PORTFOLIO_REVIEW_20261019/01_GITHUB_PRIVATE_REVIEW/experiments`. The reviewer did not author these files and made no source, configuration, graph, archive, historical result, or threshold changes. This report records findings, impact, and recommendations for the author to implement.

`RESULTS.md` and CI were explicitly in progress when this review began; their temporary absence is not counted as a completed-delivery defect. Historical final replay was also in progress, so time-sensitive reproduction statements must be refreshed at release.

## Finding 1 — P1: analysis validation disappears under Python optimization — RESOLVED

**Discovery.** Both `analysis/plot_results.py` use `assert` for required validation, including the entire body of `close(a, b)`. Hot/Cold contained 18 assert statements and Host Hint 33 at review. `python -O` and `PYTHONOPTIMIZE=1` remove them. This affects WAF identities, paired policy settings, source/selection checksums, seed uniqueness, symmetry claims, copied CSV comparisons, false-fallback counts, and wear-delta checks. The scripts still print `PASS` and write a manifest whose status says validated.

**Executed proof, without changing a source file:** loaded each script with `runpy.run_path` in a separate `python3 -B -O` process and called `close(1, 2)`. Both exited 0 and printed the review marker `MISMATCH_ACCEPTED`. The identical call in a normal interpreter raises. Therefore validation depends on interpreter optimization flags, not only source/data correctness.

**Impact.** A modified or inconsistent input table can be accepted and plotted as validated in a legitimate optimized environment. Current archived values passed normal-mode checks, so this is a validator defect rather than evidence that current numbers are wrong.

**Recommendation.** Replace required `assert` statements with explicit checks that raise a defined validation exception. Do not change frozen simulator code. Add one meaningful regression test that invokes validation under optimization and confirms a controlled mismatch is rejected. Re-run both normal and optimized numeric validation, regenerate figure manifests if script hashes change, and refresh validation reports.

## Finding 2 — P2: historical replay config can claim values never applied — RESOLVED

**Discovery.** `execute_historical_final` records the full `configs/historical_final.json` as `experiment_config`, but the command passes only stage, selection file, and output directory to the frozen production CLI. The JSON's `total_writes`, `logical_pages`, `workload_seeds`, `block_size`, and `persistence_k` are neither passed nor compared with the actual produced manifest. They happen to equal the production runner's fixed defaults in the checked-in configuration.

**Impact.** Editing a supposedly operative config field can yield a successful original-default replay while the run record says the edited value was the experiment configuration. Archive output comparison will still pass, because it compares original defaults with original defaults rather than with the edited declaration. This weakens the promised config-to-execution traceability.

**Recommendation.** Keep the frozen CLI unchanged. Treat these fields as required declarations and reject a mismatch against the frozen config/selection before work and against the actual final manifest after execution. Alternatively rename them explicitly as descriptive expected fields and validate them. Add a config-mismatch test that fails before launching an expensive replay. The Hot/Cold operative numerical settings are passed through to its CLI correctly; device selection is explicitly recorded separately.

## Finding 3 — P3: smoke seed separation is tested only for the checked-in example — RESOLVED

**Discovery.** Host Hint `execute_smoke` rejects seeds in the historical range 1–305, but does not reject overlap between calibration and measurement sets and does not enforce its own `use 901+` error-message guidance. `test_smoke_is_new_evidence_with_distinct_seed_split` checks only the current static JSON (901/902).

**Executed proof, without launching or modifying research code:** imported the wrapper, supplied an in-memory config with both calibration and measurement set to `[901]`, and replaced `run_command` with a capture stub. The wrapper accepted and constructed a command with both sets 901. Current committed smoke config is valid and no historical seeds were reused in this probe.

**Impact.** A future config edit can silently leak a calibration workload into measurement while retaining a successful, apparently separated smoke record. Smoke is explicitly not performance evidence, so the immediate scientific impact is limited.

**Recommendation.** Add runtime validation for nonempty integer seeds, required lower bound, and disjoint calibration/measurement sets. Test rejection of overlap using an in-memory input rather than only asserting the example file is valid.

## Release-time checks — P3 / in-progress items

- Local Markdown target checks found only both READMEs' `docs/RESULTS.md` targets missing at the time of review. These are known in-progress files, not an unexpected broken link. Recheck after they are written.
- Some provenance text says production final was not rerun while the author was concurrently completing the historical replay. Update those present-tense statements to distinguish the source export, the separate original-history checkout, and newly executed historical replay. Do not call replay a second untouched validation.
- Intended cross-project GitHub URLs require an authenticated remote existence/visibility check before delivery. The local docs themselves correctly say an intended URL is not evidence of publication.

## Numerical and scientific checks that passed

Executed each project's `python3 -B analysis/plot_results.py --verify-only` in the normal interpreter. Both returned exit code 0. Hot/Cold correctly disclosed missing MSR trace and both dev4 historical latency sanity failures rather than suppressing them. Host Hint reported 220 run rows, 70 conditions, five final seeds and the correct seed-equal versus pooled false-fallback values.

Inspected the following without finding an additional material error:

- WAF denominator is host page writes, with NAND=host+migrations. Hot/Cold figures use after-warm-up phase counters; Host Hint uses the full 50k-write run. No cross-project magnitude comparison is made.
- False-fallback denominators are the beneficial conditions, not all 70 conditions. Primary means weight the five seed rates equally: 34.6667% to 8%; pooled 17/49 to 4/49 is explicitly separate.
- Error-rate observations within each seed are treated as repeated conditions. No 70/49/21-independent-sample confidence interval or significance claim is made.
- Static endpoint WAF equality is checked for every final seed. The explanation restricts full-inversion symmetry to symmetric binary placement resources. Offline oracle `|phi|` is explicitly separated from the causal online detector input.
- Harmful-case plot selects the frozen Static-minus-Device sign rule and shows every one of the 21 retained cases, clustered within five seeds. WAF improvement is shown alongside increased erase-count CV. CV is not labeled actual endurance.
- The independent wrapper strengthens historical replay provenance by checking expected HEAD, exact tag commit identities, selection tracking/hash, simulator bytes and clean status before invoking the unmodified final production runner. No tag fabrication, guard patch, test mode, or fixture is used in this path. Missing original history still correctly blocks standalone production replay.
- `verify_reference` uses explicit conditional errors and is not itself subject to the analysis `assert` defect. It checks simulator bytes, archive byte/member identity and trace or frozen selection consistency.
- Original result archives are read only; selective extraction is allowlisted into a fresh temporary location; new run directories use exclusive creation. Historical output paths are not reused. Existing extraction tests address traversal, symlink payloads and overwrite protection.
- Local project links resolve independently of the sibling project except the known pending Results docs. Runtime package names remain equal, but wrapper subprocess working directories isolate projects; no cross-project import dependency was found.
- Reproduction helpers record expected versus executed simulator hashes, wrapper and config hashes, commands, logs and statuses. A byte match is accurately described as preservation evidence rather than independent scientific validity.

## Visual inspection

Viewed current PNG outputs for the Hot/Cold policy comparison, Host Hint symmetry sweep and per-seed false fallback. Axes, numeric labels, min–max ranges and denominator annotations are readable; no cropping or material visual distortion was seen. The WAF bar chart starts at zero. The Host Hint line chart uses an explicitly described bounded WAF range. Hatches, line styles and markers supplement color. The neutral zero-migration case is labeled unchanged instead of dividing by zero.

## Disposition

The scientific presentation is materially stronger and does not currently overstate the verified hardware scope. Finding 1 should be fixed before calling the analysis pipeline robust. Finding 2 should be fixed to keep config provenance truthful under edits. Finding 3 is a low-cost runtime hardening improvement. Revalidate changed wrappers/analysis and source hashes; do not modify the frozen algorithm or historical final thresholds to address these findings.


## Independent follow-up verification

The implementation author fixed all three findings; this reviewer then inspected and exercised the changed code without editing it. The preceding initial disposition is historical; the current disposition is the following.

| Finding | Follow-up evidence | Status |
|---|---|---|
| P1 optimized validation bypass | Both analysis scripts now use explicit `require(...)/ValueError`; AST inspection found zero `assert` nodes. Each analysis regression suite ran five tests covering intact inputs under `-O` and `PYTHONOPTIMIZE=1`, numeric corruption, phase/frozen-config corruption and trace/selection checksum corruption. All 10 tests passed. | RESOLVED |
| P2 historical config declaration mismatch | `validate_historical_final_config` compares the requested dimensions and provenance against the preserved final manifest before launching and the actual final manifest after execution. The frozen production CLI remains unchanged. Negative dimension tests passed in each project's reproduction suite. | RESOLVED |
| P3 smoke split overlap | Runtime checks now require nonempty, disjoint calibration/measurement seed sets with values >=901 before command construction. Negative overlap/empty/historical-seed tests passed. | RESOLVED |

Executed independently from each project directory:

```sh
python3 -B -m unittest discover -s analysis -p 'test_plot_validation.py' -v
python3 -B -m unittest discover -s tests -p 'test_reproduce.py' -v
```

Results: analysis 5+5 tests PASS; reproduction safety 7+7 tests PASS. Also called each `verify_reference` against its recorded snapshot: both PASS. Independently recomputed all simulator hashes against `configs/evidence_snapshot.json`: exact equality in both projects. No algorithm source change was required.

Rechecked all local README and top-level technical Markdown links after Results docs were created: zero missing local targets in both projects. Inspected project-local CI definitions: they call byte verification, core tests, smoke, numeric plot validation, optimized validator regression tests, and separate pinned-environment figure generation. Their existence is local configuration evidence; this reviewer did not observe a remote GitHub Actions run.

The historical final replay compact report records PASS after preserving an initial wrapper FAIL caused by absolute-versus-relative selection-path metadata and detached branch spelling. This reviewer independently located the fresh output directory and recomputed hashes of every scientific output against both the archive and compact report: all five files were byte-identical (`final_run_results.csv`: 220 rows; `final_block_results.csv`: 6,790 rows; `final_condition_outcomes.csv`: 70 rows; `final_seed_summaries.csv`: 5 rows; final summary JSON). This verifies output identity of the completed run; the reviewer did not rerun the expensive final study. The report correctly labels historical holdout reuse and denies new generalization evidence. Initial failure information remains visible rather than being replaced with an invented uninterrupted PASS.

Remaining editorial follow-up at review time: both `docs/PROVENANCE.md` files still said “Production final is not rerun in this export.” This should explicitly describe the separate original-history checkout's completed historical replay while retaining the fact that a standalone source export cannot satisfy historical Git guards on its own. The author was notified. No unresolved P1 or P2 code defect remains from this review.

## Author disposition after review

The stale provenance sentence identified above has been corrected in PROVENANCE.md to distinguish the completed separate original-history replay from the history-free export. No unresolved P1/P2 findings remain. The full Alibaba dev740 Hot/Cold replay is still running and is not represented as passed. Remote CI status is reported separately from local checks.
