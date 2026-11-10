---
tags: [experiment-loop]
max_turns: 300
timeout_seconds: 5400
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash, WebSearch, WebFetch, Agent]
---

이 폴더의 diabetes 회귀 프로젝트에서 validation RMSE를 낮추는 실험 루프를 돌려줘. 이 서버에는 tmux가 없고 claude CLI를 따로 띄울 수도 없으니까, 지금 세션 안에서 agent 기능으로 진행해. metric은 train.py가 쓰는 outputs/metrics.json의 val.rmse야(낮을수록 좋음). data.py, budget.py, test.py는 건드리지 말고 train.py와 config.json만 수정해. 3회 반복 후 종료하고 test RMSE까지 보고해줘. 중간에 나한테 묻지 말고 끝까지 진행해.

Input files: budget.py, config.json, data.py, README.md, test.py, train.py
