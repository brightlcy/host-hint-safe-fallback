# Focused independent finalizer review — 2026-10-20

Reviewed canonical commits `online-hotcold-ftl@a38c475cbcfdc7f0dd3a58cb6f97d358b5e3b1dc` and `host-hint-safe-fallback@f24ad1a842e45e4b7947ac84ebcc00582ee160ff`. Their identical `reproduce.py` SHA-256 is `89394459823b9554f56def8db90dc695f4968f213b86fb91ecff134931a400ec`. Scope is the new existing-run finalizer, its CLI dispatch, comparison helper and added safety test. No simulator or long-running process was launched, interrupted, or changed. No source was edited.

**Current status: RESOLVED. No material guard-bypass or overwrite defect found; the minor provenance hardening recommendation below was implemented and independently rechecked.** The finalizer is Hot/Cold-only at CLI dispatch, requires an existing replay with PASS status, rejects RUNNING/failed runs, validates device identities, compares archived policy metrics and phase configuration/input hashes, and scans existing latency logs. Its call graph contains no simulator entrypoint, `run_command`, or subprocess invocation. The new evidence class explicitly says `HISTORICAL_REPLAY_VALIDATION` and `simulation_executed=false`.

New validation output is created under the CLI's unique, exclusively created artifacts directory. The optional checked-in report is restricted to `results/manifests/rebuild_*.json` and opened with exclusive `x` mode. It cannot replace an existing report. The input run manifest, raw outputs, archive, frozen source, and historical Host Hint production guards are read only in this path. Running replay refuses finalization before touching its output.

Independently executed each project's reproduction safety suite: **8 + 8 tests PASS**. The added test confirms a RUNNING manifest is rejected and no output directory is created. These are temporary safety checks; they do not execute the simulator.

Original minor provenance hardening recommendation (P3), now **RESOLVED**: the finalizer currently compares the prior manifest's `source_sha256` (documented as expected reference bytes) to current actual bytes, without separately comparing the prior `actual_source_sha256` (executed-byte record). A normal PASS run from this wrapper already passed source verification, so this does not invalidate the existing completed replay. Requiring both maps to equal the current preserved source would align the finalizer's explicit check with the manifest's stated source semantics and reject an inconsistent supplied manifest. The implementation author was notified; no change was made by this reviewer.

Remote CI success and GitHub file equality were reported by the author and were outside this focused read-only review. No remote run or expensive research simulation was repeated.


## Follow-up — P3 RESOLVED

Reviewed the author's final source-evidence change independently. If the historical run records `actual_source_sha256`, the finalizer now requires that map to match current preserved source. It also requires a historical preservation `verification.status=PASS` with no recorded errors. Legacy runs without a separate executed digest are explicitly identified in `historical_source_evidence`; the report no longer implies that such a digest existed. Both the finalizer's working record and optional new report preserve this interpretation. Original run manifests and worker processes remain untouched.

Independently reran only the safety suites: **9 + 9 tests PASS**, including mismatch of recorded executed source and missing historical verification. The refusal happens before any validation output is written. No simulator execution or long-run process operation occurred. No unresolved finding remains in this focused review.

Rechecked source identities:

- `online-hotcold-ftl@2d5d86236bba69f5ecd20ea4e89b9041816574f3`; `reproduce.py` SHA-256 `722374d5fc07620a8a8449ef38c3ba250956624c5c5eb0e2ff15985c42de378d`.
- `host-hint-safe-fallback@2fc876c622017b77b80f040aeefd933f8d9c0d77`; `reproduce.py` SHA-256 `722374d5fc07620a8a8449ef38c3ba250956624c5c5eb0e2ff15985c42de378d`.
