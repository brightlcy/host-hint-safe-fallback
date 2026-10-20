# Architecture and data flow

The simulator remains in its original package to preserve algorithm bytes. No unnecessary source split or new shared dependency was introduced. `reproduce.py` orchestrates a project-local interpreter, immutable archive reads and output isolation. Analysis scripts consume frozen evidence, not simulator outputs silently substituted into old results.

`synthetic workload + seed → true lifetime classes → nested hint corruption → baseline/device/static/v1/v2 FTL → resolved past lifetime tracker → rolling or persistence detector → device-side placement switch → counters/outcomes → frozen final tables → figures`.

`lifetime_workload.py` generates events and corruption, `lifetime_tracker.py` observes only resolved lifetimes, `lifetime_ftl.py` implements policy subclasses, `persistence_detector.py` non-overlapping evidence states, `persistence_protocol.py` frozen splits/selection/gates. `run_lifetime_experiment.py` handles calibration/v1; `run_persistence_development.py` selection; `run_persistence_final.py` one frozen candidate. Common active modules: address/baseline_ftl/config/gc_policy/metadata/trace_io. Inherited HotColdFTL, Alibaba adapters and analytical latency are not called by this final research path. They are retained as historical lineage and tests, not presented as Host Hint contributions.

[File-by-file symbols/imports](FILE_MAP.md) · [Machine-readable graph](DEPENDENCIES.json)
