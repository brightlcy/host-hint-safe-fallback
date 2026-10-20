# Executed validation snapshot

Verified on 2026-10-20. [GitHub CI run](https://github.com/brightlcy/host-hint-safe-fallback/actions/runs/39377644703) **PASS**: core and figure jobs. Tested source commit: `cfd55a16a5d91af5bced16464020e8b015455bf5`. Subsequent documentation-only changes do not replace that recorded run identity.

## Execution evidence

- 215 local core/reproduction tests PASS, and the same source passes the Ubuntu CI test command.
- Five optimized-mode analysis regression tests PASS; damaged numeric/config/checksum fixtures are rejected.
- Source/archive preservation and plot-source validation PASS.
- The Quick Start verify/tests/smoke commands passed in an independent project copy and in GitHub CI.
- Pinned Python 3.12.14 figure generation passed in CI and uploaded regenerated figures. Rendering bytes can differ across platforms; numeric validation is independent of renderer packages.
- At the initial publication, all 130 tracked files downloaded from GitHub matched local bytes. The final delivery audit records any subsequent documentation-file count.
- Both research repositories remain private. The original project-wide code license was not changed to a permissive license; Alibaba data attribution/CC BY 4.0 are recorded separately.

## Historical reproduction status

Original-history production replay completed with unchanged frozen guards. All 220 run rows, 6790 block rows, 70 condition rows, 5 seed summaries and final summary JSON are byte-identical to the archived scientific outputs. This is known-seed historical reproduction, not a new untouched holdout. The separate history-free project requires the original Git history to run the production final path.

See [reproducibility](REPRODUCIBILITY.md), [current compact reports](../results/manifests/), [figure data and captions](FIGURES.md), and [Red Team review](RED_TEAM.md). No algorithm or original research result was altered during the rebuild.

The finalizer explicitly validates recorded executed-source hashes when available and labels legacy source metadata. The underlying research simulator and results are unchanged.
