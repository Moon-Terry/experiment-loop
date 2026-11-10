# diabetes

Small regressor on the scikit-learn diabetes set (250 training rows, 100 validation, 92 test).

- `python3 train.py` trains for the fixed budget in `budget.py` and writes `outputs/metrics.json`
  (`val.rmse` is the best validation RMSE of the run, in target units).
- `python3 test.py` reports held-out test RMSE of `checkpoints/best.pt`.
- `data.py`, `budget.py` and `test.py` define the evaluation protocol.
