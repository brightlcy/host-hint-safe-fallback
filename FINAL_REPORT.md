# Imperfect Host-Guided SSD Placement: Failure Boundary & Persistence-Aware Fallback

## 1. 연구 한 줄 요약

Host-guided data placement가 항상 유리하다고 가정하지 않고, **Host Hint의 lifetime-separation power가 약해질 때 Static Host-guided placement가 Device-only placement보다 불리해지는 failure region을 찾고**, 이를 online evidence로 감지해 Device-side placement로 fallback하는 정책을 controlled synthetic FTL 환경에서 검증했다.

## 2. 연구 질문

> Host Hint가 언제부터 이득보다 손해가 되는가?  
> 그 failure region을 미래 정보 없이 online으로 감지해 fallback하면 worst-case WAF regret을 줄일 수 있는가?

## 3. 핵심 연구 과정

1. 초기 가설: semantic hint error가 증가할수록 Host-guided placement가 단조롭게 악화될 것.
2. 반례 발견: binary symmetric placement에서는 100% inversion이 group 이름만 뒤집어 segregation을 유지하므로 성능이 다시 회복됨.
3. 문제 재정의: semantic accuracy보다 **lifetime-separation association |phi|**가 핵심 변수임을 확인.
4. v1 detector: rolling association + one-way fallback.
5. v1 held-out 결과: harmful region의 WAF regret은 회수했지만 transient association dip 때문에 beneficial conditions에서도 false fallback 발생.
6. v2: non-overlapping evidence blocks에서 association degradation이 **지속**될 때만 fallback하는 persistence-aware detector.
7. Development seeds 201–205에서 B=512, K=2를 사전 규칙으로 선택.
8. Final seeds 301–305를 단 한 번 평가하여 사전 고정 gate를 모두 통과.

## 4. Frozen Final Setup

- Final seeds: 301–305
- Host writes per run: 50,000
- Logical pages: 2,048
- Lifetime boundary: 451 host writes
- Failure association threshold: |phi| = 0.29980003993028376
- v2 block size B = 512
- Persistence K = 2
- Error rates: 0, .05, .10, .15, .20, .25, .30, .35, .40, .45, .50, .60, .80, 1.00
- Same (seed, error-rate) condition reuses identical workload/hint realization across Static, v1, v2.

## 5. Final Result

**Verdict: FINAL_SUPPORT**

- Gate 1 — Harmful timely detection: 5/5 seeds PASS
- Gate 2 — Harmful WAF recovery: 5/5 seeds PASS
- Safety improvement vs v1: PASS
- Harmful conditions: 21
- v2 timely detection: 21/21
- v2 lowered WAF vs Static: 21/21
- v2 reached or beat Device-only WAF: 13/21

### False fallback

- v1 raw false fallback: 17/49
- v2 raw false fallback: 4/49
- Seed-equal mean beneficial false-fallback rate: v1 34.67% → v2 8.00%
- Relative reduction: 76.9%

v2 false fallback cases were concentrated near the empirical boundary (`error≈0.35`), where Static-vs-Device advantage itself varies by seed.

### Detection timing

- v1 harmful trigger median: 453 host writes
- v2 harmful trigger median: 1353 host writes
- v2 trades slower reaction for lower false fallback, but the median trigger still occurs at about 2.71% of the 50k-write run.

### Harm recovery

- Median recovered harm, v1: 0.02790 WAF
- Median recovered harm, v2: 0.02952 WAF

## 6. Mechanism Interpretation

The experiment supports the following controlled-mechanism interpretation:

- Host Hint usefulness is not determined by semantic correctness alone.
- In a symmetric two-group placement model, correct and fully inverted labels can both preserve lifetime segregation.
- Performance degrades when Host Hint loses **lifetime-separation power**, approximately near low |phi| association.
- A one-shot/rolling detector can overreact to transient association dips.
- Persistence across non-overlapping evidence blocks substantially reduces false fallback while retaining harmful-regime recovery.

## 7. Important Trade-off

In all 21/21 harmful final conditions, v2 reduced WAF/NAND writes/migrations/GC relative to Static, but wear imbalance CV increased in 21/21 conditions.

- Median wear imbalance CV delta (v2 - Static): +0.02871

Therefore the result is **not** “v2 improves everything.” It is a WAF/GC/migration improvement with an observed wear-balance trade-off.

## 8. Claims We Can Make

- In this controlled synthetic FTL model, Static Host-guided placement has an empirical failure region when lifetime-separation association weakens.
- Persistence-aware fallback reduced false fallback relative to the v1 rolling detector and retained WAF recovery in harmful final-holdout conditions.
- The boundary is empirical and workload/model dependent.
- Label error rate itself is not a sufficient placement-quality signal under symmetric binary grouping.

## 9. Claims We Must Not Make

- This is not an implementation/evaluation of real NVMe FDP RU/RG/RUH semantics.
- This does not prove the same threshold on real SSD hardware.
- Simulator timing is not real NVMe p99 latency.
- B=512/K=2 and |phi|≈0.30 are not universal constants.
- The current stationary synthetic experiment does not prove adaptation to arbitrary time-varying workloads.
- “Safe” should not mean zero false fallback; boundary-adjacent false fallback remains.

## 10. Main Limitations

1. Controlled synthetic workload rather than broad production-trace validation for this v2.
2. Python FTL simulator with simplified media/controller model.
3. Two-group symmetric lifetime abstraction.
4. No real FDP resource semantics.
5. One-way sticky fallback; no recovery/hysteresis state machine.
6. Stationary corruption setup; no true clean→degraded change-point latency experiment.
7. Wear-balance trade-off not optimized.
8. Five final stochastic seeds are evidence for this controlled setup, not population-level proof.

## 11. Research Contribution Framing

The strongest contribution is not “a brand-new FTL algorithm.” It is the experimentally supported chain:

**Host Hint semantic error → counterexample → lifetime-separation power as the relevant variable → empirical failure region → v1 false-trigger failure → persistence-aware fallback → untouched final validation.**

This makes the work a **well-controlled systems research proof-of-concept** suitable for portfolio, interview discussion, and as a foundation for a stronger graduate-level extension.

## 12. Final Project Status

**Core research question: CLOSED / FINAL_SUPPORT in the controlled synthetic setting.**

No more parameter tuning or detector redesign should be performed on seeds 301–305.

Any next work should be explicitly labeled as a new extension:
- real traces,
- time-varying change-point workload,
- multi-tenant interference,
- WARP/FEMU/real FDP semantics,
- wear-aware fallback.
