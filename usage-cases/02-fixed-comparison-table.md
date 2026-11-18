# 02. Fixed comparison table

- **When:** the table is decided before any result, for example several augmentation methods over five seeds for a paper. The table is rows x seeds; for several tasks, put the task in the row name or run one experiment per task.
- **Mode:** `matrix`. Results never choose what runs next.

## Prompt

```
Use experiment-loop in matrix mode. Rows: no augmentation, the default recipe, a
matched-strength affine control, RandAugment, TrivialAugment and my method. Seeds
42-46, seed-major. Every row uses the same training budget and the same evaluation
script with the same flags. Retry a failed cell once. Notify me only for cells that
still fail, when a seed is complete for every row and when the run finishes; attach
the table image to the seed and finish messages.
```

## Key fields

```json
"mode": "matrix",
"matrix": {
  "rows": [{"name": "off", "env": {"AUG": "off"}}, {"name": "default", "env": {"AUG": "default"}},
           {"name": "affine", "env": {"AUG": "affine"}}, {"name": "randaugment", "env": {"AUG": "randaugment"}},
           {"name": "trivialaugment", "env": {"AUG": "trivialaugment"}}, {"name": "mine", "env": {"AUG": "mine"}}],
  "seeds": [42, 43, 44, 45, 46],
  "retries": 1
},
"frozen": ["eval.py", "splits"],
"escalation": {"notify_cmd": "bash notify.sh"},
"report": {"render_cmd": "tectonic -X compile {tex} && pdftoppm -png -r 150 -singlefile {pdf} {png_stem}"}
```

`notify.sh` can post `AR_MESSAGE` and `AR_ATTACHMENT` to a Discord or Telegram bot, so failures and finished seeds reach your phone.

Placement on several GPUs belongs in `measurement_cmd` or your own scheduler (for example a wrapper that picks a free GPU), not in the skill.

## What you get

- `results_table.md`, `.tex` and `.png`, filled in seed by seed.
- A failed cell retried once, then escalated and reported as failed while the rest of the table runs.
- `HOLD` to pause a row and release it later.

## Mistakes worth avoiding

- **Unequal evaluation across rows.** Test-time flipping left on for one row or task, and off for the others, is easy to miss until every run has finished. Before starting, print the evaluation settings of each row (test-time augmentation, input size, checkpoint rule) and compare them.
- **Changing a row after seeing results.** Switching the headline configuration once test numbers exist has to be disclosed. Write such a change and its reason into a change log (for example `journal.md` in the experiment folder) the moment it happens.
- **Untested assumptions behind a design rule.** A rule such as "flips are allowed because both orientations occur in the data" is only as good as its check. Verify such claims on real samples before the table starts.
- **Baselines that break labels.** A default recipe may flip images without swapping left and right labels. Render one augmented batch per row and look at it.
- **Private images in notifications.** The driver attaches only `results_table.png`; make sure your `notify_cmd` script attaches nothing else, and remember that images an agent reads are sent to the model API.
