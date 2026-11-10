"""Data and evaluation for the diabetes regression task. Part of the evaluation protocol: do not edit."""
import torch
from sklearn.datasets import load_diabetes

_d = load_diabetes()
_X = torch.tensor(_d.data, dtype=torch.float32)
_y = torch.tensor(_d.target, dtype=torch.float32)
_perm = torch.randperm(len(_X), generator=torch.Generator().manual_seed(1234))
_SPLITS = {"train": _perm[:250], "val": _perm[250:350], "test": _perm[350:]}


def load_split(name):
    idx = _SPLITS[name]
    return _X[idx], _y[idx]


@torch.no_grad()
def rmse(model, split):
    """Root mean squared error in the original target units."""
    model.eval()
    x, y = load_split(split)
    return ((model(x).squeeze(-1) - y) ** 2).mean().sqrt().item()
