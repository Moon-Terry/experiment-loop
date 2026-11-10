"""Data and evaluation for the digits task. Part of the evaluation protocol: do not edit."""
import torch
from sklearn.datasets import load_digits

_d = load_digits()
_X = torch.tensor(_d.data, dtype=torch.float32).reshape(-1, 1, 8, 8) / 16.0
_y = torch.tensor(_d.target)
_perm = torch.randperm(len(_X), generator=torch.Generator().manual_seed(1234))
_SPLITS = {"train": _perm[:150], "val": _perm[150:210], "test": _perm[210:]}


def load_split(name):
    idx = _SPLITS[name]
    return _X[idx], _y[idx]


@torch.no_grad()
def evaluate(model, split):
    model.eval()
    x, y = load_split(split)
    return (model(x).argmax(1) == y).float().mean().item()
