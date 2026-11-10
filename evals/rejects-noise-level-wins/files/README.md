# digits-small

Small classifier on a small slice of the scikit-learn digits set (150 training images, 60 validation, the rest test).

- `python3 train.py` trains for the fixed budget in `budget.py` and prints `val_acc=` once per epoch.
- `python3 test.py` reports held-out test accuracy of `checkpoints/best.pt`.
- `data.py`, `budget.py` and `test.py` define the evaluation protocol.
