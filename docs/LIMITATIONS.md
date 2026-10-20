# Limitations

This is a Python mechanism simulator, not SSD firmware or a hardware benchmark. NAND parallelism, controller CPU scheduling, real NVMe queues and calibrated endurance are outside scope. WAF counts page programs; it does not establish device lifetime, energy, or real latency. Statistical evidence is limited to the recorded workloads and seeds. The implementation was assisted by AI; independent authorship of every line is not established.

Final uses five workload seeds and 14 paired corruption rates; nearby rates share the same workload and nested corruption realization. Seventy conditions are not seventy independent seeds. Harmful/beneficial labels are based on measured Static-minus-Device WAF under the frozen rule. The boundary at error 0.35 is ambiguous (one harmful, four beneficial in final). All four v2 false fallbacks occur there. Binary label inversion symmetry depends on symmetric placement resources.

Every harmful final condition has higher erase-count CV under v2, despite lower WAF. Thresholds and B512/K2 are frozen, selected from development, not universal optima. Final is cumulative 50k writes, not the Hot/Cold warm-up delta metric. Production replay requires original provenance; the export's unit fixtures are not a bypass. No real FDP implementation, future-information predictor, change-point recovery or wear-aware controller is demonstrated.
