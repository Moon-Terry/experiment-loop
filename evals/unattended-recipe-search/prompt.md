---
tags: [experiment-loop]
max_turns: 300
timeout_seconds: 5400
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash, WebSearch, WebFetch, Agent]
---

이 폴더의 digits 프로젝트에서 best validation accuracy를 올리는 실험을, 내가 없는 동안 알아서 계속 돌려줘. 나는 지금 자리를 비워서 질문에 답을 못 해. 조건: 학습 예산(budget.py의 30 epoch), 데이터 split, 평가 코드(data.py, test.py)는 그대로 두고 train.py와 config.json만 바꿀 수 있어. 실험 구현은 Opus 5.5, 검증과 다음 실험 결정은 Fable 5.2로 나눠서 무인으로 돌려. 총 3회 반복까지만 하고, 끝나면 test accuracy까지 포함해서 결과를 요약해줘.

Input files: budget.py, config.json, data.py, README.md, test.py, train.py
