# Results and verification status

The source tables, complete captions, sample sizes, frozen conditions and regeneration commands are in [FIGURES.md](FIGURES.md). Figures are regenerated from preserved research evidence, not the new smoke runs.

## Host Hint

![Error sweep and symmetry](../figures/rebuild_hint_error_symmetry.png)

Static endpoint equality at 0% and 100% corruption holds in every final seed. The explanatory offline association curve is not the causal online detector signal. WAF peaks near loss of separation, not necessarily maximum semantic error.

![False fallback](../figures/rebuild_false_fallback.png)

Seed-equal false fallback falls from 34.6667% to 8.0000%; pooled counts are 17/49 to 4/49. Five seed rates are the primary aggregation units. The remaining four errors are all at the 0.35 boundary, and five seeds provide limited generalization evidence.

![WAF and wear trade-off](../figures/rebuild_waf_wear_tradeoff.png)

All 21 harmful conditions lower WAF under v2 versus Static, while erase-count CV rises in all 21. Median relative WAF change −1.08%, median absolute WAF recovery 0.02952 and median CV difference +0.02871 describe the retained conditions. These are different units and cannot be substituted for SSD endurance improvement.

Data: [final conditions](../results/final_summary/final_condition_outcomes.csv), [seed summaries](../results/final_summary/final_seed_summaries.csv), [trade-offs](../results/final_summary/final_tradeoff_summary.csv), [archived complete policy runs and manifests](../results_archive.zip). Script: [plot_results.py](../analysis/plot_results.py).

## Reproduction status

Numeric evidence validation checks 220 archived run rows, 70 paired conditions and five seeds. Historical production replay requires restored original history and exact frozen tag/selection checks. See [current validation](../results/manifests/rebuild_validation.json) and [standalone checks](../results/manifests/rebuild_standalone_validation.json). Any replay is a historical reproduction using previously observed final seeds, not a new untouched holdout or retuned result. New smoke calibration uses separate seeds and is not a performance headline.
