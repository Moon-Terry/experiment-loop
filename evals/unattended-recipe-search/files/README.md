# digits

Small classifier on the scikit-learn digits set (400 training images, 600 validation, 797 test).

- `python3 train.py` trains for the fixed budget in `budget.py` and prints `val_acc=` once per epoch.
- `python3 test.py` reports held-out test accuracy of `checkpoints/best.pt`.
- `data.py`, `budget.py` and `test.py` define the evaluation protocol.
