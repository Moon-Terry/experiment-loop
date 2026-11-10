---
name: experiment-loop
description: Run machine-learning experiments as an autonomous, unattended loop (design, plan, run, evaluate, validate, decide, repeat) so compute keeps working while the researcher is away. Use this whenever the user wants experiments, hyperparameter or training-recipe searches, ablations, or metric-improvement work to run by itself, for example overnight, on a remote GPU server, in tmux, "while I'm away", "keep going until val accuracy reaches X", "experiment loop", "autoresearch", 실험 자동화, 자동 실험 루프, 하이퍼파라미터 탐색. Also use it to set up, check on, resume, extend or stop such a loop, and to summarise its results. Every idea and setting it tries is grounded in top-tier main-conference papers, their official code, or industry-lab settings, never an arbitrary choice. It keeps the best-validation state and runs locally with no git commits or pushes.
---

# Experiment loop

Runs an experiment as a loop that keeps going without the researcher:

```
design -> plan -> perform -> evaluate -> validate -> decide -> (repeat)
```

The researcher's goal is that proposed experiments continue on sound judgment while they are away, and that no compute is wasted: not on an idle GPU, not on guessed settings, not on a "win" that turns out to be noise or a bug. Everything below serves that.

Paths in this file are relative to the directory this file is in. The folder is self-contained: copy it to wherever a machine keeps its skills.

## How it works

Three parts, kept apart on purpose.

| Part | What it is | What it does |
|---|---|---|
| **Driver** | a program, `scripts/driver.py` | Runs the measurement, reads the metric, keeps the best-validation state, undoes failed ideas, protects the evaluation files, decides when to stop. |
| **Director** | a model, ideally the strongest available | Diagnoses results, finds the next idea in the literature, writes the plan; later validates the result and decides keep or discard. Has no shell and edits no project code. |
| **Experimenter** | a second model | Checks the plan's source, implements exactly the plan, smoke-tests it. |

The driver does everything that must be identical in every iteration, because that should not depend on a model's attention at 4 a.m. The models do the judgment. The director judges work it did not write, which is what makes its validation worth something. The parts talk only through files in `<project>/.autoresearch/<name>/`, so the loop survives crashes, restarts, rate limits and context limits: each agent turn starts fresh and reads the state from disk.

| Phase | Who | Written to |
|---|---|---|
| 1. Design: diagnose, choose one sourced idea | Director | `ideas.md` |
| 2. Plan: one change, its source, a prediction made before the result exists | Director | `runs/NNN/plan.md` |
| 3. Perform: confirm the source, apply the change, smoke test | Experimenter | `runs/NNN/report.md`, `change.patch` |
| 4. Evaluate: run with timeout and seeds, compare with the best against measured noise | Driver | `runs/NNN/metrics.json`, logs |
| 5. Validate: is the result real? patch matches plan, evaluation untouched, fair budget, not too good to be true | Director | `runs/NNN/verdict.md` |
| 6. Decide: keep (new best state) or discard (restore), record the lesson, plan the next | Director, enforced by driver | `results.tsv`, `journal.md` |

## Rules that hold everywhere

1. **No arbitrary choices.** Every new idea and every number set must trace to a source opened in that turn: a main-conference paper at a top-tier venue, its official code, or an industry lab's published setting. Workshop papers, blogs and recalled "typical values" do not count. An idea without a source does not run. `references/grounding.md` has the full rules; read it before proposing anything.
2. **Never stall.** After setup, nobody is there to answer. No agent asks a question, waits for approval, or ends its work early to check in. A decision is made from the evidence on disk and its reason is written down.
3. **Select on validation only.** The metric is a validation metric. The test set is evaluated once, at the end, on the best state, and decides nothing.
4. **One change per iteration**, so a moved metric has one cause.
5. **Working rules in `AGENTS.md`.** How to change code (karpathy-guidelines: think first, simplest change, surgical edits, verifiable goals) and how to write for the researcher (attention-kind: bottom line first, exact numbers, warnings never cut). Follow them yourself during setup and when reporting; the driver gives them to every agent it starts.
6. **No git.** No commits, no pushes, no remote. The best state is a file snapshot inside `.autoresearch/`. Code and logs the agents read are sent to the model API as part of their prompts, and literature lookup uses the web; nothing else leaves the machine.

## Setting up an experiment

Setup is the only interactive part, and the last moment the researcher can correct course cheaply. Do it carefully; ask one question at a time, and only what the code cannot tell you.

**0. Once per machine**, check the driver works there: `python3 scripts/selftest.py` (under a minute; needs no model, network or GPU).

**1. Read the project.** Find the training entry point, how and where the validation metric is printed, where settings live, where checkpoints are saved, how long one run takes, and what defines the evaluation (splits, evaluation code, test set, training budget). If the project lacks something the loop depends on, fix that with the researcher now, as ordinary coding work under `AGENTS.md` part 1: a validation metric printed per epoch or written to a file, a seed taken from the `AR_SEED` environment variable, a fixed training budget, model selection that does not look at the test set.

**2. Write the definition** at `<project>/.autoresearch/<name>/experiment.json`, starting from `assets/experiment.example.json`. `references/spec.md` explains every field. Settle with the researcher:

- the goal and the metric, and a target if they have one
- what may change (`scope`: keep it small) and what must not (`frozen`: everything that defines the evaluation)
- when to stop (iterations, hours, patience, target, or until stopped by hand)
- which experiments they already want run (step 4), and venues specific to their field (`extra_venues`)
- the language for reports

Show them the finished definition in a few lines and get a yes before continuing. If they have already said they cannot answer (they handed the task over and left), do not block: take what they stated, choose the conservative option for anything open (smaller scope, more files frozen), write each assumption into `journal.md`, and continue.

**3. Initialise and measure the baseline.**

```bash
python3 scripts/driver.py init     <project>/.autoresearch/<name>
python3 scripts/driver.py baseline <project>/.autoresearch/<name>
```

The baseline is measured, never taken from memory or from what the researcher recalls. The command prints the run-to-run standard deviation and a suggested `min_delta`; write that value into `experiment.json` and record how it was derived in `journal.md`. `references/spec.md` covers the case where a run is too expensive to repeat.

**4. Seed the idea bank** in `<project>/.autoresearch/<name>/ideas.md`, acting as the director: read `references/director.md` and `references/grounding.md`, diagnose the baseline run, and add at least `min_ready_ideas` verified ideas. Experiments the researcher proposed go first, recorded as their instruction; look up the closest prior work for each and tell them now if you could not find support or found a contradiction, while they can still respond. Doing the first literature search with the researcher present lets them veto a direction before it costs a run.

**5. Choose how it runs**, with the researcher (next section), and fill in `agents` in `experiment.json`.

**6. Start it**, then tell the researcher in a few lines how to watch it and how to stop it.

## Running it

### Unattended, on a server with tmux (preferred for long runs)

Each agent turn is a separate headless call (for Claude Code, `claude -p`), started by the driver. Different models for the two roles are set in `agents`, for example a stronger model directing and judging and a second one implementing.

```bash
python3 scripts/driver.py doctor <project>/.autoresearch/<name>   # agents answer unattended? web reachable?
bash scripts/launch.sh <project>/.autoresearch/<name>
```

`doctor` must pass before the researcher leaves: it catches a missing login, a wrong model name, a permission prompt nobody will answer, or blocked web access. The experimenter can run shell commands without confirmation, which unattended operation requires; say so once and let the researcher choose the machine and project accordingly.

`launch.sh` starts a detached tmux session named `ar-<name>` with three windows: the driver, a status view, and the live log of the current run. Without tmux it falls back to `screen`, then `nohup`. Run it from the shell that has the experiment's environment (conda env, `CUDA_VISIBLE_DEVICES`, API keys).

The driver keeps the loop alive by itself: a failed agent call (rate limit, outage) is retried with growing waits; a crashed, hung or diverged run is killed and recorded; an agent that returns nothing usable is retried, then the safe default is taken (discard) and the loop moves on.

### In-session, with Claude Code agents (no tmux or no headless CLI)

You are the orchestrator, and the loop lives in this session. Repeat until the driver says `done` or `halt`, without ending your turn in between and without asking the researcher anything:

```bash
python3 scripts/driver.py next <project>/.autoresearch/<name>
```

It prints one JSON action:

- `"action": "agent"`: `prompt_file` contains the complete instructions for that role's turn. Start a subagent with the model and effort named for that role in `experiment.json` (`agents.<role>.model`, `agents.<role>.effort`) and the prompt "Read `<prompt_file>` and carry it out." Use a different subagent for the experimenter than for the director; the independence is the point. Delegating also keeps your own context small over a long loop. If subagents are unavailable, carry out the prompt file yourself.
- `"action": "measure"`: run `python3 scripts/driver.py measure <exp-dir>`, in the background when a run takes more than a couple of minutes.
- `"action": "done"` or `"halt"`: stop and report.

Call `next` only after the previous action has finished: it restores the best state before each experimenter turn.

## While it runs, and afterwards

| Need | How |
|---|---|
| Progress | `python3 scripts/driver.py status <exp-dir>`; attach to the tmux session; read `journal.md` |
| Stop cleanly | `touch <exp-dir>/STOP`: the current iteration finishes, the project is left in its best state, `REPORT.md` is written |
| Stop now | kill the tmux session; state is saved and nothing is lost but the run in progress |
| Resume | run `launch.sh` again (or continue calling `next`) |
| Run longer | raise the budget in `experiment.json` (and remove `STOP`), then resume |

When the loop ends, the project is in its best-validation state with the matching checkpoint in place, and `REPORT.md` holds the outcome. When the researcher asks how it went, read `REPORT.md` and `results.tsv` and answer under `AGENTS.md` part 2: the outcome in the first sentence with exact numbers, anything that needs their attention next, and where the rest is.

A `halt` means the loop stopped because continuing would have been unsafe or pointless: the director could not produce a sourced plan in three attempts (the idea bank is exhausted or web access is gone). The reason is in `status`. Fix the cause and resume.

## Files

```
<project>/.autoresearch/<name>/
  experiment.json   the definition
  state.json        baseline, best, iteration counter (driver)
  results.tsv       one row per iteration (driver)
  journal.md        narrative record (director)
  ideas.md          idea bank with sources (director)
  best/             snapshot of scope at the best validation result, plus saved artifacts
  runs/NNN/         plan.md, report.md, change.patch, run logs, metrics.json, verdict.md
  agent_logs/       prompt and output of every agent turn
  REPORT.md         final report
  STOP              create this file to stop
```

## Reference files

| File | Read it when |
|---|---|
| `references/spec.md` | writing or changing `experiment.json` |
| `references/grounding.md` | before proposing any idea or setting; it defines an acceptable source |
| `references/director.md` | acting as director: seeding ideas at setup, or in-session without subagents |
| `references/experimenter.md` | acting as experimenter in-session without subagents |
| `AGENTS.md` | changing project code, and writing anything the researcher will read |
