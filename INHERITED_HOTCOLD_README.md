# 공개 블록 I/O 트레이스 기반 Online Hot/Cold FTL 성능 분석

## 1. 프로젝트 한 줄 요약

MSR Cambridge와 Alibaba Cloud EBS의 실제 block I/O trace를 입력으로 사용하는 page-mapped FTL 시뮬레이터를 직접 구현하고, 온라인 Hot/Cold 데이터 배치가 GC, WAF, valid-page migration, tail latency에 미치는 영향을 비교 분석했습니다.

## 2. 문제 정의

SSD에서는 같은 블록에 수명이 다른 데이터가 섞이면, 일부 페이지만 무효화된 블록을 GC할 때 아직 유효한 페이지를 다른 위치로 복사해야 합니다. 이 valid-page migration은 NAND write를 증가시키고 WAF와 지연시간을 악화시킵니다.

본 프로젝트의 가설은 다음과 같습니다.

> 최근 쓰기 이력으로 반복 갱신되는 LPN을 온라인으로 식별하고 Hot/Cold 블록에 분리 배치하면, 덮어쓰기가 많은 workload에서 GC 시 복사해야 할 valid page가 감소한다.

## 3. 구현 범위

- 표준 trace contract: `timestamp,flow_id,op,lba,size`
- Page-mapped FTL
- Greedy GC
- write-working-set 기반 SSD 자동 크기 산정
- warm-up / measurement phase 분리
- WAF, GC, migration, latency 통계
- FCFS 단일 자원 analytical latency model
- GC latency attribution
  - `direct_gc`
  - `gc_spillover`
  - `workload_queue`
  - `unqueued`
- MSR Cambridge trace profiler
- Alibaba 2020 대용량 S3 stream profiler와 다중 device 추출기
- Online Hot/Cold Placement
- 165개 단위 테스트

## 4. 제안 정책

정책은 미래 정보를 사용하지 않습니다.

```text
최근 4,096 host page write를 sliding window로 유지
→ 동일 LPN이 threshold 이상 등장하면 Hot 승격
→ window에서 오래된 write가 빠져 threshold 미만이 되면 Cold 강등
→ Hot/Cold별 write frontier에 분리 배치
→ 두 frontier는 하나의 free-block pool을 공유
```

최종 비교에 사용한 설정:

```text
hot_threshold = 8
hot_window_writes = 4096
```

Baseline과 Proposed에서 다음 조건은 동일합니다.

- SSD logical/physical capacity
- over-provisioning
- GC threshold
- Greedy victim selection
- warm-up/measurement 횟수
- latency cost model

## 5. 데이터셋과 실험 workload

| Workload | 요청 수 | Write page ratio | Overwrite page ratio | 역할 |
|---|---:|---:|---:|---|
| MSR `prxy_1` | 100,000 | 35.21% | 83.27% | Enterprise proxy workload |
| Alibaba dev4 | 100,000 | 97.03% | 87.65% | High-overwrite small random write |
| Alibaba dev49 | 100,000 | 31.78% | 46.94% | Mixed workload |
| Alibaba dev740 | 100,000 | 100.00% | 14.45% | Low-overwrite burst workload |

Alibaba 원본 140.1 GiB는 로컬에 저장하지 않고 S3 → zstd → Python pipeline으로 streaming 분석했습니다. 첫 24시간 525,559,379개 요청에서 후보 device를 선정한 뒤, 각 device 100,000개 요청만 추출했습니다.

## 6. 핵심 결과

### Baseline 대비 Online Hot/Cold 변화

| Workload | WAF | Valid migration | GC count | Avg latency | p99 latency | GC spillover |
|---|---:|---:|---:|---:|---:|---:|
| MSR `prxy_1` | -15.32% | -26.31% | -15.32% | -20.11% | -21.38% | -21.46% |
| Alibaba dev4 | -34.03% | -47.51% | -34.04% | -45.42% | -8.63% | -35.37% |
| Alibaba dev49 | -8.99% | -17.17% | -8.99% | -16.18% | -0.78% | -1.44% |
| Alibaba dev740 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% |

### 해석

- 높은 overwrite와 지속적인 hot set이 존재하면 Hot/Cold 분리 효과가 큽니다.
- Alibaba dev4에서는 valid-page migration이 47.51% 감소하며 WAF가 3.5242에서 2.3249로 감소했습니다.
- 중간 overwrite의 dev49에서는 저장 효율과 평균/p95 지연은 개선됐지만 p99 개선은 제한적이었습니다.
- 낮은 overwrite의 dev740에서는 Hot 승격이 한 번도 발생하지 않았고 Baseline과 동일하게 동작했습니다.
- 따라서 정책 효과는 단순 write 비율보다 `overwrite 정도`와 `hot set 지속성`에 더 강하게 좌우됩니다.

## 7. Threshold 민감도

MSR `prxy_1`에서 threshold 2, 4, 8을 비교했습니다.

| Threshold | WAF 개선 | Migration 개선 | p99 개선 | Max latency 개선 |
|---:|---:|---:|---:|---:|
| 2 | 7.22% | 12.40% | 8.84% | -17.64% (악화) |
| 4 | 14.58% | 25.05% | 10.65% | 9.62% |
| 8 | 15.32% | 26.31% | 21.38% | 14.74% |

Threshold 2는 조기 승격과 분류 churn이 커서 최악 지연이 악화됐고, threshold 8이 실험 범위에서 가장 균형이 좋았습니다. 이 값은 모든 workload의 보편적 최적값이 아니라 MSR에서 고정한 뒤 Alibaba에 그대로 적용한 검증값입니다.

## 8. 무결성 검증

모든 최종 workload에서:

```text
placement_fallback_pages = 0
mixed_labeled_blocks = 0
```

즉 Hot/Cold 공간 부족으로 분리를 포기하거나 한 블록에 온도가 섞인 결과가 아닙니다.

```bash
python3 -m unittest discover -s tests -v
# Ran 165 tests
# OK
```

## 9. Latency 결과 해석 시 주의사항

현재 latency는 실제 SSD 측정값이 아닙니다.

```text
FCFS single-resource queue
command overhead       20 us
NAND read/page         80 us
NAND program/page     200 us
block erase          2000 us
```

따라서 절대 latency를 실제 장치 성능으로 주장하지 않고, 동일 모델 안에서 정책 간 상대 변화와 GC 전파 원인을 분석하는 데 사용합니다. NAND channel/die 병렬성, NVMe queue, firmware CPU scheduling은 포함하지 않습니다.

## 10. 실행 예시

### 테스트

```bash
python3 -m unittest discover -s tests -v
```

### Workload profile

```bash
python3 -m simulator.run_profile_workload \
  --input results/alibaba_candidate_100k/device_4.standard.csv \
  --output-dir results/alibaba_profile_device_4 \
  --window-seconds 1.0 \
  --burst-percentile 95
```

### Baseline vs Online Hot/Cold

```bash
python3 -m simulator.run_compare_hot_cold \
  --input results/alibaba_candidate_100k/device_4.standard.csv \
  --output-dir results/alibaba_compare_device_4_t8 \
  --warmup-rounds 1 \
  --measurement-rounds 1 \
  --hot-threshold 8 \
  --hot-window-writes 4096
```

## 11. 프로젝트 구조

```text
simulator/
  baseline_ftl.py             Baseline page-mapped FTL
  hot_cold_ftl.py             Online classifier + temperature-aware placement
  metadata.py                 Page/block/mapping metadata
  gc_policy.py                Greedy victim policy
  trace_io.py                 Standard trace reader and auto sizing
  workload_profile.py         Overwrite/hotness/burst profiling
  latency_model.py            Analytical latency model
  latency_attribution.py      GC spillover attribution
  alibaba_trace.py            Alibaba schema adapter
  alibaba_streaming.py        Multi-device streaming extraction
  alibaba_workload.py         O(number_of_devices) pilot profiler
  run_compare_hot_cold.py     Equal-condition comparison CLI

tests/                        165 unit tests
results/                      Reproducibility artifacts
docs/                         Final report and interview materials
figures/                      Final result figures
```

## 12. 결론

Online Hot/Cold Placement는 모든 workload에서 무조건 성능을 높이는 정책이 아닙니다. 높은 overwrite와 안정적인 hot set이 있을 때 valid-page migration과 GC 비용을 줄이며, 해당 조건이 없으면 Baseline과 동일하게 퇴화했습니다.

본 프로젝트의 핵심 기여는 단순히 성능 향상 수치를 제시한 것이 아니라 다음 인과관계를 실제 production trace에서 검증한 것입니다.

```text
workload overwrite/hotness
→ online temperature classification
→ block-level separation
→ valid-page migration 감소
→ WAF/GC 감소
→ GC spillover 및 latency 감소
```

## 13. 한계와 다음 단계

- 실제 SSD 하드웨어 또는 MQSim과 절대 latency 교정
- NAND channel/die parallelism 모델 추가
- 시간상 분리된 warm-up/measurement 구간 사용
- 더 많은 Alibaba device와 장기 구간 교차 검증
- workload 변화에 따른 threshold 자동 조정
- 공간적 비용을 줄이는 Hot/Cold Placement와 시간적 피해를 줄이는 burst-aware GC scheduling 결합

## Safe-Fallback Artifact Semantics

Synthetic Safe-Fallback 결과에서 `detector_sample_count`는 measurement 종료 시
rolling detector window의 sample count다. `fallback_trigger_write_index`와
`fallback_trigger_sample_count`는 최초 fallback trigger 시점에 고정된 값이며,
fallback이 발생하지 않은 run에서는 비어 있다.
