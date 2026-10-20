# Methodology

WAF = NAND page programs / host page writes over the stated evaluation interval. NAND programs = host writes + valid-page migration in this model. `avg_migrated_pages_per_gc` is a different metric, not WAF. Read requests are not in the WAF denominator. GC count and valid-page migration are event counters. Erase-count CV is standard deviation / mean across per-block erase counts, not measured endurance.

Five final workload seeds 301–305, each 50k synthetic host writes, same logical-page namespace of 2048. Paired policies use the same workload and nested corruption. Fourteen rates: 0,.05,.1,.15,.2,.25,.3,.35,.4,.45,.5,.6,.8,1. Baseline and Device-only are run once/seed, three hint-aware policies once/condition (220 runs total). Final WAF is cumulative, with no warm-up subtraction.

Ground truth uses the frozen exact sign of Static WAF minus Device-only WAF: positive harmful, negative beneficial, zero neutral. Do not reinterpret near-zero as a significance test. Timely uses the frozen protocol definition; see `persistence_protocol.py`. The 21 harmful conditions are clustered within five seeds. Report seed distributions and equal-weight seed averages; do not bootstrap all 70 rows as independent trials. False fallback denominators are beneficial conditions (49 total), not all conditions. Policy comparisons hold geometry and placement-resource budget constant.

Calibration 1–3 froze boundary451 and |phi| threshold; v1 held-out101–105 exposed transients; development201–205 selected B512/K2; final301–305 was read once under frozen rules. A present-day replay cannot become a second untouched holdout. Historical tag identity and the frozen selection checksum are checked separately from runner acceptance.
