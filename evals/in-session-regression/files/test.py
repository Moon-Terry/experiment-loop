"""Held-out test RMSE of the saved best-validation checkpoint. Do not edit."""
import json

import torch

from data import rmse
from train import build_model

with open("config.json") as f:
    model = build_model(json.load(f))
model.load_state_dict(torch.load("checkpoints/best.pt"))
print(f"test_rmse={rmse(model, 'test'):.3f}")
