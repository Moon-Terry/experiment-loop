# The experiment definition: `experiment.json`

One file defines an experiment. It lives at `<project>/.autoresearch/<name>/experiment.json`; every path inside it is relative to the project root. Start from `assets/experiment.example.json`.

Once the loop has started, treat it as fixed. Changing the metric, the measurement or the frozen files mid-run makes earlier and later results incomparable. Raising the budget is the one edit that is always safe.

## Contents

- [What is being optimised](#what-is-being-optimised)
- [How a result is measured](#how-a-result-is-measured)
- [What may and may not change](#what-may-and-may-not-change)
- [When the loop stops](#when-the-loop-stops)
- [Agents](#agents)
- [Other fields](#other-fields)
- [Worked examples](#worked-examples)

## What is being optimised

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Short identifier; also the directory name. |
| `goal` | recommended | One or two sentences for the agents: what the researcher wants and under which constraints. |
| `metric.name` | yes | For example `val_acc`. Always a validation metric: the loop selects on it. |
| `metric.direction` | yes | `maximize` or `minimize`. |
| `metric.regex` | one of | Regular expression with exactly one capture group around the number, applied to the run's output: `val_acc=([0-9.]+)`. |
| `metric.json_file`, `metric.json_key` | one of | Alternative to `regex`: a JSON file the run writes, and a dotted key into it (`final.val_loss`). |
| `metric.pick` | no (`last`) | When the run prints the metric many times (once per epoch): `best` takes the best value in the run, `last` the final one. Use `best` when the training script saves its best-validation checkpoint, so the number and the saved model agree. |
| `metric.target` | no | The loop stops when the best value reaches this. |
| `extra_metrics` | no | Other numbers to record for the director's validation, read the same way as `metric` (the last value a run prints): `{"train_acc": {"regex": "train_acc=([0-9.]+)"}}`. They never decide keep or discard by themselves. |

## How a result is measured

| Field | Default | Meaning |
|---|---|---|
| `measurement_cmd` | required | Shell command that trains and evaluates, run from the project root. Write it so it works in a fresh shell: name the interpreter or environment explicitly (`conda run -n env python train.py`). It must use a **fixed training budget** (epochs, steps or time), or runs are not comparable. |
| `run_timeout_s` | 3600 | A run that exceeds this is killed and counted as failed. Set it to about twice a normal run. |
| `abort_patterns` | `[]` | Regular expressions (case-insensitive) that end a run early when they appear in its output: `"loss[=: ]+nan"`, `"CUDA out of memory"`. Keep them precise; a bare `nan` also matches "nanoseconds". |
| `repeats` | 1 | Runs per measurement; their mean is the result. Each run gets its index (0, 1, 2, ...) in the `AR_SEED` environment variable; the training script must use it as the random seed. |
| `baseline_repeats` | 3 | Runs used for the baseline, to measure run-to-run noise. |
| `confirm_repeats` | 0 | Extra runs made only when a result looks like an improvement, before it is believed. Cheap insurance against a lucky seed. |
| `min_delta` | required | The smallest difference from the best that counts as an improvement. The loop refuses to start until it is set, so that a gain inside the noise is never kept. Set it from the measured noise; see below. |
| `smoke_cmd` | none | A short version of the run (one epoch, a few batches) that the experimenter uses to catch crashes before the full measurement. |
| `final_test_cmd` | none | Run once when the loop ends, on the best state, to report the held-out test result. Its output goes to `final_test.log` and never influences a decision. |

### Setting `min_delta`

Whether a difference is real depends on how much the metric moves between identical runs, so the threshold is measured, not chosen. `driver.py baseline` runs the unchanged project `baseline_repeats` times with different seeds and prints the standard deviation and a suggested `min_delta`: two standard errors of the difference between the baseline mean and a later measurement, `2 * std * sqrt(1/baseline_repeats + 1/repeats)`. Write the value into `experiment.json` and note in `journal.md` how it was derived.

When one run is too expensive to repeat, the noise estimate has to come from somewhere else: the seed variance a source reports for this model and dataset (cite it), or a value the researcher states. Record which.

## What may and may not change

| Field | Default | Meaning |
|---|---|---|
| `scope` | required | Files and directories the experimenter may edit. Keep it small: the training script and its config. The driver snapshots these paths as the best state and restores them after a failed idea. |
| `frozen` | `[]` | Files that define the evaluation and must never change: data loading and split code, split lists, the evaluation and metric code, the test script, the fixed-budget setting. The driver keeps a copy, checks them before every measurement, puts changed files back, and discards the iteration. This is what stops a loop from "improving" the metric by changing how it is computed. List code and small files, not the dataset itself. |
| `exclude` | caches | Name patterns skipped inside `scope` and `frozen` (`__pycache__`, `*.pyc`, ...). Add data and checkpoint folders that sit inside a scoped directory. |
| `artifacts` | `[]` | Files or directories the run produces that belong to a result, typically the best-validation checkpoint. On every keep they are copied to `best/artifacts/`; when the loop ends they are put back in place, so the checkpoint on disk matches the best code. |
| `max_snapshot_mb` | 500 | Upper bound on the size of the scope snapshot and of the frozen copy. Hitting it means `scope` or `frozen` contains data that belongs in `exclude`. |

## When the loop stops

At least one of these is required, so that "forever" is always a deliberate choice.

| Field | Meaning |
|---|---|
| `budget.max_iterations` | Stop after this many iterations. |
| `budget.max_hours` | Stop after this much wall-clock time. Checked between iterations. |
| `budget.patience` | Stop after this many iterations in a row without improvement. |
| `metric.target` | Stop when the best value reaches the target. |
| `budget.unbounded` | `true`: run until a person creates the `STOP` file. |

`pivot_after` (default 3) is not a stop condition: after that many iterations in a row without improvement, the director must re-diagnose and switch to a different family of ideas.

## Agents

`agents.director` and `agents.experimenter` each take:

| Field | Meaning |
|---|---|
| `cmd` | The command that runs one unattended turn. The driver appends the prompt as the last argument. Exit code 0 means the turn ran. |
| `timeout_s` | Default 3600. |
| `model`, `effort` | Model alias and effort level used for this role's subagents in in-session mode (`opus` / `medium`, `fable` / `high`, ...). |

For Claude Code, put `-p` last so the prompt is not read as one more tool name:

```json
"agents": {
  "director": {
    "cmd": "claude --model claude-fable-5-2 --effort high --tools Read,Write,Edit,Glob,Grep,WebSearch,WebFetch --allowedTools Read,Write,Edit,Glob,Grep,WebSearch,WebFetch -p",
    "model": "fable",
    "effort": "high"
  },
  "experimenter": {
    "cmd": "claude --model claude-opus-5-5 --effort medium --allowedTools Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch -p",
    "model": "opus",
    "effort": "medium"
  }
}
```

Set the effort level explicitly: a headless call without `--effort` runs at whatever the machine's Claude Code default is, which may be far more than the role needs. The values above are Anthropic's recommended starting points for these models (Claude Opus 5.5: start at `medium`; Claude Fable 5.2: `high` for long unattended work, with most of the gain arriving by `high`); raise one only after measuring that it helps.

The director gets no shell: it reads, searches the web and writes files. The experimenter needs a shell for the smoke test, which means it can run any command, so only use this in a project and on a machine where that is acceptable. Using a stronger model to direct and judge and a second model to implement gives the judgment independence from the implementation.

Another agent CLI can be used the same way if one command runs one turn, takes the prompt as its last argument, and has the tools that role needs. Only Claude Code has been tested.

## Other fields

| Field | Default | Meaning |
|---|---|---|
| `language` | `en` | Language for the journal, verdicts and final report (`ko`, `en`, ...). |
| `validation_rules` | `[]` | Extra pass/fail rules the director checks before keeping a result, in plain sentences: "peak GPU memory stays under 24 GB", "training time per run does not exceed the baseline by more than 20%". |
| `extra_venues` | `[]` | Venues accepted as tier A in addition to the default list, for the experiment's field: `["MICCAI", "Medical Image Analysis"]`. |
| `min_ready_ideas` | 3 | The director keeps at least this many verified, untested ideas in the bank, restocking it from the literature when it runs low. |

## Worked examples

**Image classifier, maximise best validation accuracy**

```json
{
  "name": "resnet-recipe",
  "goal": "Raise best validation top-1 of ResNet-18 on our dataset within the fixed 60-epoch budget. Architecture stays ResNet-18.",
  "metric": {"name": "val_top1", "direction": "maximize", "regex": "val_top1=([0-9.]+)", "pick": "best", "target": 0.80},
  "extra_metrics": {"train_top1": {"regex": "train_top1=([0-9.]+)"}},
  "measurement_cmd": "conda run -n vision python train.py --config configs/exp.yaml",
  "smoke_cmd": "conda run -n vision python train.py --config configs/exp.yaml --epochs 1 --limit-batches 20",
  "final_test_cmd": "conda run -n vision python test.py --ckpt checkpoints/best.pt",
  "run_timeout_s": 14400,
  "abort_patterns": ["loss[=: ]+nan", "CUDA out of memory"],
  "baseline_repeats": 3, "confirm_repeats": 1, "min_delta": 0.004,
  "scope": ["train.py", "configs/exp.yaml"],
  "frozen": ["dataset.py", "splits/", "eval.py", "test.py", "configs/budget.yaml"],
  "artifacts": ["checkpoints/best.pt"],
  "budget": {"max_hours": 72, "patience": 8},
  "validation_rules": ["peak GPU memory stays under 24 GB"]
}
```

**Regression, minimise validation error, metric read from a JSON file**

```json
{
  "name": "tabular-rmse",
  "metric": {"name": "val_rmse", "direction": "minimize", "json_file": "outputs/metrics.json", "json_key": "val.rmse"},
  "measurement_cmd": "python train.py",
  "scope": ["train.py", "config.json"],
  "frozen": ["data.py", "splits.json"],
  "budget": {"max_iterations": 30}
}
```

**Fine-tuning a language model, expensive runs**

```json
{
  "name": "lora-finetune",
  "metric": {"name": "eval_loss", "direction": "minimize", "regex": "'eval_loss': ([0-9.]+)", "pick": "best"},
  "measurement_cmd": "accelerate launch finetune.py --config lora.yaml --max_steps 2000",
  "run_timeout_s": 28800,
  "baseline_repeats": 1,
  "min_delta": 0.01,
  "scope": ["lora.yaml"],
  "frozen": ["finetune.py", "eval_set.jsonl"],
  "budget": {"max_hours": 96, "patience": 5}
}
```

With `baseline_repeats` at 1 there is no measured noise, so the `min_delta` here must be justified in the journal from a cited source or the researcher's own statement.
