# Provenance and repository decision

Decision: **two independent research repositories**, with versioned snapshots of the inherited FTL core. The original portfolio repository and read-only August archives remain preserved. No force push, tag fabrication, original deletion or historical-result replacement is part of this rebuild.

Online Hot/Cold has 50 Python files also present byte-for-byte in Host Hint; Host Hint adds 19. The duplicated Python snapshot is about 261 kB. The active common core is `address`, `baseline_ftl`, `config`, `gc_policy`, `metadata`, `trace_io`. Host Hint's production path uses lifetime extensions instead of `HotColdFTL`. Therefore these are a research lineage, not one mixed input experiment.

| Option | Assessment |
|---|---|
| A: independent repositories | Selected: each study can be read, tested and copied independently; snapshots are explicit and small. |
| B: shared published core | Deferred: extracting imports changes frozen bytes and requires version/revalidation work; no benefit demonstrated for this closed study. |
| C: independent folders in monorepo | Useful preserved source index; duplicate `simulator` package names require separate working directories and make per-project discovery weaker. |

Local parent export commit: `73664fb0f7fea91260ba0d17fd8a36978c402a0a`. This is **not** the remote portfolio commit. Remote portfolio observed through browser: `213e74847edcb292d2c8a4cb8af11c25e5379606`, main, Private. GitHub connector returned 404; CLI has no credentials. See `ORIGIN_MANIFEST.json` for every imported byte hash and original paths. Existing exported test fixtures differ from historical git-based provenance tests; they establish unit behavior, not original history.

Host Hint historical final commit: `c27b72ae76766a05b6c83de89907ffb00bdf1e71`; protocol `72eb2b472ac9a7b385bca47b106d58ba6d909d53`; selection `f4fc53f951abf0ad9f9451171a0ac4fb06fc76ea`. The original local Git repository has these tags and untracked final results; it was not modified. A historical final replay was executed in a separately restored, clean original-history checkout; all core final CSV/JSON outputs match the archived bytes. This does not turn previously observed seeds into a new untouched holdout. A fresh portfolio clone still requires the separately preserved original history for that production path. Guard success alone does not prove tag identity: match the expected SHA and frozen selection checksum as well. Do not create substitute historical tags on a new repository.

New project destination: `https://github.com/brightlcy/host-hint-safe-fallback`. Remote publication and verification status must be checked in the rebuild delivery report; this intended URL alone is not evidence of upload.

The October historical replay was independently hash-checked against the archived scientific outputs. See results/manifests/rebuild_historical_final_validation.json in the Host Hint project. This historical reuse adds reproduction evidence and does not expand the original claim scope.
