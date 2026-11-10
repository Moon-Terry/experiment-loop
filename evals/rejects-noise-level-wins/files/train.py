"""Train a small classifier on the scikit-learn digits set. Prints one line per epoch."""
import argparse
import json
import os

import torch
import torch.nn as nn

from budget import EPOCHS
from data import evaluate, load_split


def build_model(cfg):
    h = cfg["hidden"]
    return nn.Sequential(nn.Flatten(), nn.Linear(64, h), nn.ReLU(), nn.Linear(h, h), nn.ReLU(), nn.Linear(h, 10))


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
    opt = torch.optim.SGD(model.parameters(), lr=cfg["lr"], momentum=cfg["momentum"],
                          weight_decay=cfg["weight_decay"])
    loss_fn = nn.CrossEntropyLoss()
    best = -1.0
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(x))
        for i in range(0, len(x), cfg["batch_size"]):
            idx = perm[i:i + cfg["batch_size"]]
            loss = loss_fn(model(x[idx]), y[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
        train_acc, val_acc = evaluate(model, "train"), evaluate(model, "val")
        print(f"epoch={epoch} loss={loss.item():.4f} train_acc={train_acc:.4f} val_acc={val_acc:.4f}", flush=True)
        if val_acc > best:
            best = val_acc
            os.makedirs("checkpoints", exist_ok=True)
            torch.save(model.state_dict(), "checkpoints/best.pt")


if __name__ == "__main__":
    main()
