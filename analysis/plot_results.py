#!/usr/bin/env python3
"""Validate and replot the untouched final holdout; never run or tune a simulator.

Run from any directory: python analysis/plot_results.py [--verify-only].
The final archive is read in place. Outputs are prefixed rebuild_; old figures stay intact.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import platform
import statistics
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "results/safe_fallback_v2_final_20260820/"
SEEDS = tuple(range(301, 306))
BLUE, ORANGE, GREEN, GRAY, INK = "#0072B2", "#D55E00", "#009E73", "#697887", "#243347"
POLICIES = {
    "static_host_guided": ("Static Host-guided", GREEN, "o", "-"),
    "device_only_lifetime": ("Device-only", GRAY, "", "--"),
    "safe_fallback_v1": ("Fallback v1", ORANGE, "s", ":"),
    "persistence_fallback_v2": ("Persistence v2", BLUE, "D", "-"),
}


def require(condition, message):
    """Reject invalid research evidence even when Python optimization is enabled."""
    if not condition:
        raise ValueError(message)


def close(a, b):
    require(math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12), f"Numeric mismatch: {a!r} != {b!r}")


def boolean(value):
    require(value in ("True", "False"), 'Validation failed: value in ("True", "False")')
    return value == "True"


def csv_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def validate():
    sources = {}
    archive = ROOT / "results_archive.zip"
    sources["results_archive.zip"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as z:
        def member(name):
            data = z.read(name)
            sources["results_archive.zip::" + name] = hashlib.sha256(data).hexdigest()
            return data

        runs = csv_rows(member(PREFIX + "final_run_results.csv"))
        outcomes = csv_rows(member(PREFIX + "final_condition_outcomes.csv"))
        seed_summaries = csv_rows(member(PREFIX + "final_seed_summaries.csv"))
        final_summary = json.loads(member(PREFIX + "final_summary.json"))
        manifest = json.loads(member(PREFIX + "final_manifest.json"))
        selection = member("results/safe_fallback_v2_development_20260820/frozen_v2_selection.json")
        require(hashlib.sha256(selection).hexdigest() == manifest["frozen_selection_sha256"], 'Validation failed: hashlib.sha256(selection).hexdigest() == manifest["frozen_selection_sha256"]')
    require(manifest["workload_seeds"] == list(SEEDS), 'Validation failed: manifest["workload_seeds"] == list(SEEDS)')
    require(manifest["total_writes"] == 50000, 'Validation failed: manifest["total_writes"] == 50000')
    require(manifest["logical_pages"] == 2048, 'Validation failed: manifest["logical_pages"] == 2048')
    require(manifest["selected_block_size"] == 512, 'Validation failed: manifest["selected_block_size"] == 512')
    require(manifest["selected_persistence_k"] == 2, 'Validation failed: manifest["selected_persistence_k"] == 2')
    require(final_summary["selected_block_size"] == manifest["selected_block_size"], 'Validation failed: final_summary["selected_block_size"] == manifest["selected_block_size"]')
    require(final_summary["selected_persistence_k"] == manifest["selected_persistence_k"], 'Validation failed: final_summary["selected_persistence_k"] == manifest["selected_persistence_k"]')
    require(manifest["stage"] == "final" and manifest["git_worktree_clean"] is True, 'Validation failed: manifest["stage"] == "final" and manifest["git_worktree_clean"] is True')
    rates = manifest["error_rates"]
    require(len(rates) == 14 and len(outcomes) == 70 and len(runs) == 220, 'Validation failed: len(rates) == 14 and len(outcomes) == 70 and len(runs) == 220')
    run_map = {}
    for row in runs:
        seed = int(row["workload_seed"])
        require(seed in SEEDS, 'Validation failed: seed in SEEDS')
        key = (seed, float(row["error_rate"]) if row["error_rate"] else None, row["policy"])
        require(key not in run_map, 'Validation failed: key not in run_map')
        run_map[key] = row
        host, nand, migrated = (int(row[k]) for k in ("host_write_pages", "nand_write_pages", "valid_page_migrations"))
        require(host == 50000 and nand == host + migrated, 'Validation failed: host == 50000 and nand == host + migrated')
        close(nand / host, row["waf"])
        require(float(row["wear_imbalance_cv"]) >= 0, 'Validation failed: float(row["wear_imbalance_cv"]) >= 0')
    for seed in SEEDS:
        require((seed, None, "device_only_lifetime") in run_map, 'Validation failed: (seed, None, "device_only_lifetime") in run_map')
        require((seed, None, "baseline_zero_information") in run_map, 'Validation failed: (seed, None, "baseline_zero_information") in run_map')
        for rate in rates:
            rows = [run_map[(seed, rate, p)] for p in ("static_host_guided", "safe_fallback_v1", "persistence_fallback_v2")]
            require(len({r["hint_realization_id"] for r in rows}) == 1, 'Validation failed: len({r["hint_realization_id"] for r in rows}) == 1')
        close(run_map[(seed, 0.0, "static_host_guided")]["waf"], run_map[(seed, 1.0, "static_host_guided")]["waf"])
    seen = set()
    for row in outcomes:
        seed, rate = int(row["workload_seed"]), float(row["error_rate"])
        require((seed, rate) not in seen, 'Validation failed: (seed, rate) not in seen')
        seen.add((seed, rate))
        static = run_map[(seed, rate, "static_host_guided")]
        device = run_map[(seed, None, "device_only_lifetime")]
        delta = float(static["waf"]) - float(device["waf"])
        close(delta, row["delta_static_device"])
        truth = "harmful" if delta > 0 else "beneficial" if delta < 0 else "neutral"
        require(truth == row["ground_truth"], 'Validation failed: truth == row["ground_truth"]')
        for name, policy in (("static", "static_host_guided"), ("v1", "safe_fallback_v1"), ("v2", "persistence_fallback_v2")):
            close(row[name + "_waf"], run_map[(seed, rate, policy)]["waf"])
        close(row["device_waf"], device["waf"])
        for version, policy in (("v1", "safe_fallback_v1"), ("v2", "persistence_fallback_v2")):
            triggered = boolean(run_map[(seed, rate, policy)]["fallback_triggered"])
            require(boolean(row[version + "_fallback_triggered"]) == triggered, 'Validation failed: boolean(row[version + "_fallback_triggered"]) == triggered')
            require(boolean(row[version + "_false_fallback"]) == (truth == "beneficial" and triggered), 'Validation failed: boolean(row[version + "_false_fallback"]) == (truth == "beneficial" and triggered)')
            close(row[version + "_recovered_harm"], float(static["waf"]) - float(row[version + "_waf"]))
    seed_derived = []
    for seed in SEEDS:
        paired = [r for r in outcomes if int(r["workload_seed"]) == seed]
        beneficial = [r for r in paired if r["ground_truth"] == "beneficial"]
        harmful = [r for r in paired if r["ground_truth"] == "harmful"]
        summary = next(r for r in seed_summaries if int(r["workload_seed"]) == seed)
        require(len(beneficial) == int(summary["beneficial_condition_count"]), 'Validation failed: len(beneficial) == int(summary["beneficial_condition_count"])')
        require(len(harmful) == int(summary["harmful_condition_count"]), 'Validation failed: len(harmful) == int(summary["harmful_condition_count"])')
        derived = {"seed": seed, "beneficial_n": len(beneficial), "harmful_n": len(harmful)}
        for version in ("v1", "v2"):
            count = sum(boolean(r[version + "_false_fallback"]) for r in beneficial)
            rate = count / len(beneficial)
            close(rate, summary[version + "_beneficial_false_fallback_rate"])
            derived[version + "_false_n"] = count
            derived[version + "_false_rate"] = rate
        seed_derived.append(derived)
    for version in ("v1", "v2"):
        close(statistics.mean(r[version + "_false_rate"] for r in seed_derived), final_summary["safety_improvement"][version + "_mean_seed_level_beneficial_false_fallback_rate"])
    for filename, archived in (("final_condition_outcomes.csv", outcomes), ("final_seed_summaries.csv", seed_summaries)):
        data = (ROOT / "results/final_summary" / filename).read_bytes()
        sources["results/final_summary/" + filename] = hashlib.sha256(data).hexdigest()
        require(csv_rows(data) == archived, filename + " copy differs from the final archive")
    waf_data = (ROOT / "results/final_summary/final_waf_summary.csv").read_bytes()
    sources["results/final_summary/final_waf_summary.csv"] = hashlib.sha256(waf_data).hexdigest()
    labels = {"Static Host-guided": "static_host_guided", "Fallback v1": "safe_fallback_v1", "Persistence Fallback v2": "persistence_fallback_v2"}
    for row in csv_rows(waf_data):
        vals = [float(run_map[(s, float(row["error_rate"]), labels[row["label"]])]["waf"]) for s in SEEDS]
        for field, value in (("median", statistics.median(vals)), ("min", min(vals)), ("max", max(vals))):
            close(value, row[field])
    tradeoff_data = (ROOT / "results/final_summary/final_tradeoff_summary.csv").read_bytes()
    sources["results/final_summary/final_tradeoff_summary.csv"] = hashlib.sha256(tradeoff_data).hexdigest()
    tradeoffs = csv_rows(tradeoff_data)
    harmful_keys = {(int(r["workload_seed"]), float(r["error_rate"])) for r in outcomes if r["ground_truth"] == "harmful"}
    require(len(tradeoffs) == len(harmful_keys) == 21, 'Validation failed: len(tradeoffs) == len(harmful_keys) == 21')
    require({(int(r["workload_seed"]), float(r["error_rate"])) for r in tradeoffs} == harmful_keys, 'Validation failed: {(int(r["workload_seed"]), float(r["error_rate"])) for r in tradeoffs} == harmful_keys')
    metric_fields = {"waf": "waf", "nand_write": "nand_write_pages", "migration": "valid_page_migrations", "gc": "gc_count", "wear_imbalance_cv": "wear_imbalance_cv"}
    for row in tradeoffs:
        seed, rate = int(row["workload_seed"]), float(row["error_rate"])
        static, v2 = (run_map[(seed, rate, p)] for p in ("static_host_guided", "persistence_fallback_v2"))
        for field, key in metric_fields.items():
            close(float(v2[key]) - float(static[key]), row[field + "_delta_v2_minus_static"])
    require(sum(r["v1_false_n"] for r in seed_derived) == 17, 'Validation failed: sum(r["v1_false_n"] for r in seed_derived) == 17')
    require(sum(r["v2_false_n"] for r in seed_derived) == 4, 'Validation failed: sum(r["v2_false_n"] for r in seed_derived) == 4')
    require(sum(r["beneficial_n"] for r in seed_derived) == 49, 'Validation failed: sum(r["beneficial_n"] for r in seed_derived) == 49')
    require({float(r["error_rate"]) for r in outcomes if boolean(r["v2_false_fallback"])} == {.35}, 'Validation failed: {float(r["error_rate"]) for r in outcomes if boolean(r["v2_false_fallback"])} == {.35}')
    require(all(float(r["waf_delta_v2_minus_static"]) < 0 and float(r["wear_imbalance_cv_delta_v2_minus_static"]) > 0 for r in tradeoffs), 'Validation failed: all(float(r["waf_delta_v2_minus_static"]) < 0 and float(r["wear_imbalance_cv_delta_v2_minus_static"]) > 0 for r in tradeoffs)')
    return run_map, outcomes, seed_derived, tradeoffs, manifest, final_summary, sources


def setup_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11,
        "axes.titlesize": 13, "axes.labelsize": 11,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": INK, "ytick.color": INK,
        "svg.fonttype": "none", "svg.hashsalt": "host-hint-final-20261020",
        "savefig.facecolor": "white",
    })
    return plt, matplotlib.__version__


def save(plt, fig, name):
    out = ROOT / "figures"
    out.mkdir(exist_ok=True)
    for suffix in ("png", "svg"):
        metadata = {"Software": "Matplotlib"} if suffix == "png" else {"Date": None, "Creator": "Matplotlib"}
        fig.savefig(out / f"{name}.{suffix}", dpi=180, bbox_inches="tight", metadata=metadata)
    plt.close(fig)


def plot_error_sweep(plt, run_map, manifest):
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.5), gridspec_kw={"width_ratios": [1.45, 1]})
    fig.suptitle("Hint error is not a monotonic measure of placement quality", fontsize=16, fontweight="bold", y=1.01)
    rates = manifest["error_rates"]
    x = [v * 100 for v in rates]
    ax = axes[0]
    for policy, (label, color, marker, style) in POLICIES.items():
        values = [[float(run_map[(s, None if policy == "device_only_lifetime" else e, policy)]["waf"]) for s in SEEDS] for e in rates]
        medians = [statistics.median(v) for v in values]
        ax.fill_between(x, [min(v) for v in values], [max(v) for v in values], color=color, alpha=.10, linewidth=0)
        ax.plot(x, medians, label=label, color=color, marker=marker, markersize=4.5, linestyle=style, linewidth=2)
    ax.set_ylim(2.20, 2.87)
    ax.set_ylabel("WAF = NAND page writes / host page writes")
    ax.set_title("(a) Median WAF; shaded min–max across 5 seeds", loc="left", pad=13, fontsize=12)
    ax.legend(frameon=False, ncol=2, loc="upper left", fontsize=9)
    ax.annotate("0% and 100% error:\nStatic WAF identical in all 5 seeds", xy=(100, 2.2757), xytext=(43, 2.36), fontsize=9.5, arrowprops={"arrowstyle": "->", "color": INK}, ha="center")
    ax = axes[1]
    values = [[float(run_map[(s, e, "static_host_guided")]["offline_hint_absolute_phi"]) for s in SEEDS] for e in rates]
    ax.fill_between(x, [min(v) for v in values], [max(v) for v in values], color=GREEN, alpha=.15)
    ax.plot(x, [statistics.median(v) for v in values], color=GREEN, marker="o", markersize=4, linewidth=2)
    ax.set_ylim(-.035, 1.10)
    ax.set_ylabel("Offline |phi|: hint / true lifetime-class association")
    ax.set_title("(b) Inversion preserves two-group separation", loc="left", pad=13, fontsize=12)
    ax.annotate("Random-like hints\nlose separation", xy=(50, .01), xytext=(62, .29), fontsize=10, arrowprops={"arrowstyle": "->", "color": INK}, ha="center")
    for ax in axes:
        ax.set_xlabel("Injected binary hint error (%)")
        ax.set_xlim(-2, 103)
        ax.set_xticks([0, 20, 40, 60, 80, 100])
        ax.grid(color="#DDE3E8", linewidth=.7)
        ax.set_axisbelow(True)
    fig.text(.5, -.02, "Untouched final holdout: seeds 301–305; 14 error rates; 50,000 host writes/run; 2,048 logical pages.\nB=512, K=2 frozen before final evaluation. Curves connect tested rates; ranges are descriptive, not confidence intervals.\nOffline |phi| is explanatory evidence, not the causal online detector input. Synthetic symmetric model; no real FDP/hardware claim.", ha="center", fontsize=9.2, color="#4B5E70")
    fig.tight_layout(rect=(0, .09, 1, .94), w_pad=2)
    save(plt, fig, "rebuild_hint_error_symmetry")


def plot_false_fallback(plt, seeds):
    from matplotlib.ticker import PercentFormatter
    fig, ax = plt.subplots(figsize=(10.7, 5.1))
    fig.suptitle("Persistence reduces false fallback, but does not eliminate it", fontsize=16, fontweight="bold", y=1.01)
    for version, offset, color, hatch, label in (("v1", -.19, ORANGE, "", "Rolling fallback v1"), ("v2", .19, BLUE, "///", "Persistence fallback v2")):
        rates = [r[version + "_false_rate"] for r in seeds]
        xs = [i + offset for i in range(5)]
        ax.bar(xs, rates, width=.36, color=color, hatch=hatch, edgecolor=INK, linewidth=.5, label=label)
        for x, rate, row in zip(xs, rates, seeds):
            ax.text(x, rate + .023, f"{row[version + '_false_n']}/{row['beneficial_n']}", ha="center", fontsize=10, fontweight="bold")
    ax.text(.98, .97, "Seed-equal mean: 34.67% → 8.00%\nPooled counts: 17/49 → 4/49", transform=ax.transAxes, ha="right", va="top", fontsize=11.5, bbox={"facecolor": "#F0F5F8", "edgecolor": "none", "pad": 9})
    ax.set_ylim(0, .86)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xticks(range(5), [str(r["seed"]) for r in seeds])
    ax.set_xlabel("Final workload seed (each seed has 9 or 10 beneficial error-rate conditions)")
    ax.set_ylabel("False fallbacks / beneficial conditions")
    ax.legend(frameon=False, loc="upper left")
    ax.grid(axis="y", color="#DDE3E8", linewidth=.7)
    ax.set_axisbelow(True)
    fig.text(.5, -.02, "False fallback: policy switches away from Host-guided placement where Static WAF < paired Device-only WAF.\nCount labels show every denominator. Error-rate conditions within a seed are repeated conditions, not independent samples.\nFinal seeds 301–305; frozen B=512/K=2; four remaining v2 false fallbacks occur at error=0.35.", ha="center", fontsize=9.5, color="#4B5E70")
    fig.tight_layout(rect=(0, .10, 1, .95))
    save(plt, fig, "rebuild_false_fallback")


def plot_wear_tradeoff(plt, rows):
    fig, ax = plt.subplots(figsize=(11.5, 5.4))
    fig.suptitle("Lower WAF comes with higher erase-count imbalance", fontsize=16, fontweight="bold", y=1.01)
    colors, markers = [BLUE, ORANGE, GREEN, "#AA4499", GRAY], ["o", "s", "D", "^", "v"]
    for seed, color, marker in zip(SEEDS, colors, markers):
        paired = [r for r in rows if int(r["workload_seed"]) == seed]
        xs = [float(r["waf_delta_v2_minus_static"]) for r in paired]
        ys = [float(r["wear_imbalance_cv_delta_v2_minus_static"]) for r in paired]
        ax.scatter(xs, ys, s=64, color=color, marker=marker, edgecolors="white", linewidth=.6, label=f"Seed {seed} ({len(paired)} conditions)")
    ax.axhline(0, color=INK, linewidth=1)
    ax.axvline(0, color=INK, linewidth=1)
    xvals = [float(r["waf_delta_v2_minus_static"]) for r in rows]
    yvals = [float(r["wear_imbalance_cv_delta_v2_minus_static"]) for r in rows]
    ax.set_xlim(min(xvals) - .008, .005)
    ax.set_ylim(-.004, max(yvals) + .015)
    ax.text(.98, .97, f"All 21 harmful paired conditions:\nΔWAF < 0 and Δwear CV > 0\nMedian Δwear CV = +{statistics.median(yvals):.5f}", transform=ax.transAxes, ha="right", va="top", fontsize=10.5, bbox={"facecolor": "#F0F5F8", "edgecolor": "none", "pad": 8})
    ax.set_xlabel("WAF(v2) − WAF(Static)  ← lower write amplification")
    ax.set_ylabel("Wear CV(v2) − Wear CV(Static)  ↑ more imbalance")
    ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.01, .5), fontsize=9)
    ax.set_axisbelow(True)
    ax.grid(color="#DDE3E8", linewidth=.7)
    fig.text(.5, -.02, "Harmful = Static WAF > paired Device-only WAF; 4–5 error-rate conditions per final seed, not 21 independent samples.\nWear CV = population SD of block erase counts / their mean, over all 40 simulated blocks at run end.\nEach point is a paired (seed, error-rate) delta. Erase-count balance is not measured NAND endurance or SSD lifetime.", ha="center", fontsize=9.4, color="#4B5E70")
    fig.tight_layout(rect=(0, .10, 1, .95))
    save(plt, fig, "rebuild_waf_wear_tradeoff")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Validate with the standard library; do not plot or write files.")
    args = parser.parse_args()
    runs, outcomes, seeds, tradeoffs, historical_manifest, summary, sources = validate()
    print("PASS: 220 archived run rows; 70 paired conditions; 5 final seeds; frozen selection checksum; WAF/migration identities; copied summaries; 21 WAF/wear tradeoffs.")
    print("False fallback: seed-equal means 34.6667% -> 8.0000%; pooled counts 17/49 -> 4/49.")
    if args.verify_only:
        return
    plt, version = setup_plot()
    plot_error_sweep(plt, runs, historical_manifest)
    plot_false_fallback(plt, seeds)
    plot_wear_tradeoff(plt, tradeoffs)
    manifest = {
        "status": "historical-final-holdout-validated-and-replotted; simulator-not-rerun; no-final-retuning",
        "source_sha256": sources,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "generator": {"python": platform.python_version(), "matplotlib": version},
        "historical_git_commit": historical_manifest["git_commit_hash"],
        "historical_protocol_commit": historical_manifest["frozen_protocol_commit"],
        "historical_selection_commit": historical_manifest["frozen_selection_commit"],
        "historical_config": {k: historical_manifest[k] for k in ("stage", "total_writes", "logical_pages", "selected_block_size", "selected_persistence_k", "workload_seeds", "error_rates", "ssd_config")},
        "metric_definition": "WAF = total NAND page writes / total host page writes over the complete 50k-write run (no warm-up excluded); wear CV = population SD(block erase counts)/mean(block erase counts)",
        "uncertainty": "5 workload seeds; descriptive min/median/max at each error rate. Within-seed error rates are repeated conditions, not independent samples. No confidence intervals or significance claims.",
        "seed_summaries": seeds,
        "historical_final_verdict": summary["verdict"],
        "notes": ["Binary label-inversion symmetry holds for every final seed at 0% versus 100% error.", "Offline hint/lifetime phi uses oracle true-class labels for interpretation only; the online policy observes resolved lifetimes.", "Original final outputs remain untouched. No new experiment or threshold selection was performed."],
    }
    (ROOT / "analysis/figure_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("Wrote 3 PNG/SVG figures and analysis/figure_manifest.json.")


if __name__ == "__main__":
    main()
