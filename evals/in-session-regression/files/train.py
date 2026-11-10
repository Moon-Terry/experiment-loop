"""Train a small regressor on the scikit-learn diabetes set. Writes outputs/metrics.json."""
import argparse
import json
import os

import torch
import torch.nn as nn

from budget import EPOCHS
from data import load_split, rmse


def build_model(cfg):
    h = cfg["hidden"]
    return nn.Sequential(nn.Linear(10, h), nn.ReLU(), nn.Linear(h, h), nn.ReLU(), nn.Linear(h, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    torch.manual_seed(int(os.environ.get("AR_SEED", 0)))
    x, y = load_split("train")
    model = build_model(cfg)
    opt = torch.optim.SGD(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    loss_fn = nn.MSELoss()
    best = float("inf")
    history = []
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(x))
        for i in range(0, len(x), cfg["batch_size"]):
            idx = perm[i:i + cfg["batch_size"]]
            loss = loss_fn(model(x[idx]).squeeze(-1), y[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
        val = rmse(model, "val")
        history.append(val)
        if val < best:
            best = val
            os.makedirs("checkpoints", exist_ok=True)
            torch.save(model.state_dict(), "checkpoints/best.pt")
    os.makedirs("outputs", exist_ok=True)
    with open("outputs/metrics.json", "w") as f:
        json.dump({"val": {"rmse": best, "last_rmse": history[-1]}, "train": {"rmse": rmse(model, "train")}}, f, indent=1)
    print(f"done: best val rmse {best:.3f}")


if __name__ == "__main__":
    main()
