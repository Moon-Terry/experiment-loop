# 04. Check, extend or stop a running loop

- **When:** you come back and want the state on one screen, or more time.
- **Mode:** either.

## Prompt

```
Use experiment-loop to report on .autoresearch/<name>: the results table, anything
waiting in the inbox, and what is still running. Then raise the budget by 24 hours.
```

## Commands behind it

| Need | Command |
| --- | --- |
| Progress | `python3 ~/.claude/skills/experiment-loop/scripts/driver.py status .autoresearch/<name>` |
| Answer an escalation | read `inbox.jsonl`, fix the cause, then resume; for a failed matrix cell, delete its entry from `cells` in `state.json` first. Writing `ack/<id>.txt` records the answer |
| Pause rows (matrix) | list row names in `.autoresearch/<name>/HOLD` |
| Stop cleanly | `touch .autoresearch/<name>/STOP` |
| Run longer | raise `budget` in `experiment.json` (`max_hours` counts active run time, so set it to the time used plus the extra), remove `STOP`, run `tmux kill-session -t ar-<name>`, then run `launch.sh` again |

## What you get

- The current table and the outcome so far, numbers first.
- Open escalations with their options.
- A resumed loop with the new budget.
