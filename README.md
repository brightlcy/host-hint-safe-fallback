# Host Hint / Safe Fallback 실험

불완전한 Host Hint를 계속 신뢰해도 되는지, 과거에 해결된 데이터 수명만으로
위험 구간을 감지하고 fallback할 수 있는지 검증한 실험입니다.

이 실험의 입력은 **controlled synthetic workload**입니다. Alibaba trace를
입력으로 사용한 실험은 옆의 `../online_hotcold_ftl/`에 별도로 있습니다.

## 구현과 근거

- `simulator/lifetime_ftl.py`: Device-only, Static Host-guided, v1 및 v2 정책
- `simulator/lifetime_tracker.py`: 온라인으로 해결된 수명 관측
- `simulator/persistence_detector.py`, `persistence_protocol.py`: persistence-aware v2
- `simulator/run_lifetime_experiment.py`, `run_persistence_development.py`,
  `run_persistence_final.py`: 실험 실행과 development/final 분리
- `tests/`: 동작 및 frozen source 무결성 테스트
- `FINAL_REPORT.md`: 최종 결과와 해석
- `results/final_summary/`: 포트폴리오에 사용한 최종 요약표
- `results_archive.zip`: core frozen, development, final의 원래 CSV/JSON 경로
- `FROZEN_SOURCE_SHA256.json`: 원본 Git tag commit과 frozen source의 SHA-256

```sh
python3 -m unittest discover -s tests -v
python3 -m zipfile -e results_archive.zip .
python3 -m simulator.run_lifetime_experiment --help
python3 -m simulator.run_persistence_final --help
```

이 export에서는 과거 Git tag를 요구하던 세 source-integrity 테스트를
`FROZEN_SOURCE_SHA256.json`의 동일 파일 SHA-256 비교로 이식했습니다.
알고리즘 코드와 production final runner의 Git provenance guard는 변경하지 않았습니다.
development/final runner의 단위 테스트에서는 역사적 Git metadata를 test fixture로
대체했습니다. fixture는 테스트 안에서만 적용되며 실제 연구 실행의 provenance가 아닙니다.
따라서 원래 final holdout을 production 모드로 재실행하려면 별도로 보존한 원본
저장소의 역사적 tags와 clean worktree가 필요합니다. 이 포트폴리오의 새 커밋을
과거 연구 커밋인 것처럼 tag하지 않습니다. 이번 export는 기존 결과를 보존하며
최종 실험을 다시 수행하거나 재튜닝하지 않았습니다.

Python 표준 라이브러리를 사용합니다. `INHERITED_HOTCOLD_README.md`와
`PROJECT_STATUS.md`는 원본 소스에 포함되어 있던 이전 단계 기록이며, 현재 Host Hint
연구의 설명은 이 README와 최상위 `docs/`, `FINAL_REPORT.md`를 기준으로 읽습니다.

결과는 mechanism-level proof-of-concept입니다. 실제 NVMe FDP 구현이나
실제 SSD latency 검증으로 해석하지 않습니다. WAF 이득과 wear imbalance의
trade-off를 함께 봅니다.
