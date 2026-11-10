"""Held-out test accuracy of the saved best-validation checkpoint. Do not edit."""
import json

import torch

from data import evaluate
from train import build_model

with open("config.json") as f:
    model = build_model(json.load(f))
model.load_state_dict(torch.load("checkpoints/best.pt"))
print(f"test_acc={evaluate(model, 'test'):.4f}")
