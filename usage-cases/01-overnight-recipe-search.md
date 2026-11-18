# 01. Overnight recipe search

- **When:** one training script, one validation metric, and hours of GPU time you will not be watching.
- **Mode:** `search`.

## Prompt

```
Use experiment-loop to raise the validation Dice of train.py. Only train.py and
configs/exp.yaml may change; dataset.py, splits/ and eval.py are frozen. Measure the
baseline with 3 seeds first. Run unattended; stop after 12 hours or 5
iterations without improvement.
```

## Key fields

```json
"mode": "search",
"metric": {"name": "val_dice", "direction": "maximize", "regex": "val_dice=([0-9.]+)", "pick": "best"},
"scope": ["train.py", "configs/exp.yaml"],
"frozen": ["dataset.py", "splits", "eval.py"],
"baseline_repeats": 3,
"artifacts": ["checkpoints/best.pt"],
"final_test_cmd": "python test.py --ckpt checkpoints/best.pt",
"budget": {"max_hours": 12, "patience": 5}
```

## What you get

- `REPORT.md` with the outcome in the first sentence.
- `results.tsv`, one row per iteration with the metric, the decision and the source of the idea.
- The project left in its best-validation state, with the checkpoint saved in `best/artifacts/` and restored in place when the loop ends.
- One test evaluation of that state, because `final_test_cmd` is set.

## Mistakes worth avoiding

- **A threshold from one run.** `min_delta` comes from the spread of repeated baseline runs. With one repeat there is no spread, and every later "win" may be noise.
- **A test set in the loop.** The metric must be a validation metric; the test set is evaluated once at the end, through `final_test_cmd`, and decides nothing.
- **A loose scope.** Every file in `scope` is a file the experimenter may rewrite at 4 a.m. Keep it to the files the search is about.
