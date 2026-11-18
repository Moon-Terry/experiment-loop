# 03. Tune your own method, then freeze it

- **When:** your method has a few ranges that no published setting covers, and the final comparison must be run with those ranges fixed.
- **Mode:** `search` on validation data, then `matrix` for the table.

## Prompt

Two prompts: one to start the search, one after you are back.

```
Use experiment-loop to tune the augmentation ranges of my method on the validation
sweep only. These candidate values are my instruction: shift in {0.1, 0.2, 0.3},
scale_max in {1.2, 1.5}, rotation in {10, 20}. Try no other values. Measure the
baseline with 3 seeds.
```

```
Read REPORT.md of .autoresearch/<name> and write a matrix experiment with the best
ranges as the headline row. Do not start it until I confirm.
```

## Key fields

```json
"mode": "search",
"scope": ["configs/method_ranges.json"],
"frozen": ["eval_val_sweep.py", "splits"],
"baseline_repeats": 3,
"budget": {"max_iterations": 7}
```

Only the candidate values you list are recorded as your instruction in `ideas.md`; any other value would need a published source, and the loop halts after three plans without one. The director still looks up the closest prior work for each and tells you during setup if it finds a contradiction.

## What you get

- Ranges selected on validation only, with the noise threshold they had to beat.
- After the second prompt, a matrix definition with those ranges as the headline row, waiting for your yes.

## Mistakes worth avoiding

- **Tuning on the evaluation you will report.** Use a validation sweep that shares no images with the test sets.
- **Re-opening the ranges after the table runs.** Once the matrix starts, a change to the headline row is a post-result change; see case 02.
