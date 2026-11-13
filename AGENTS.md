# Working rules for every agent in this loop

These apply to every role, on top of the grounding rules: the session that sets an experiment up, the director, and the experimenter. The driver puts this file into every agent prompt, so the rules hold whether or not the matching plugins are installed on the machine.

- Part 1 governs how you **change code**. It is the `karpathy-guidelines` skill, adapted to a loop where nobody is present to answer questions.
- Part 2 governs how you **write for the researcher**.

## Part 1. Changing code: karpathy-guidelines

Adapted from the karpathy-guidelines skill by forrestchang (https://github.com/multica-ai/andrej-karpathy-skills), released under the MIT License, which is derived from Andrej Karpathy's observations on common LLM coding mistakes. The guidelines trade speed for caution, which is the right trade here: one careless edit costs a full training run.

### 1. Think before acting

Don't assume. Don't hide confusion. Surface trade-offs.

- State your assumptions explicitly, in the file the next reader will open: `plan.md` for the director, the Deviations section of `report.md` for the experimenter.
- If a plan can be read two ways, do not pick one silently.
- If a simpler change would test the same hypothesis, say so and use it.
- **Unattended adaptation.** The original says "if uncertain, ask". During setup, with the researcher present, do exactly that. Inside the loop nobody can answer, so asking means writing the question down and handing it back: the experimenter stops with `STATUS: FAILED` and names what is unclear; the director records the open question in the journal and chooses the option the evidence supports, with its reason. Guessing quietly is the one thing that is never acceptable.

### 2. Simplicity first

The minimum change that tests the hypothesis. Nothing speculative.

- No features beyond what the plan asks for.
- No abstractions for code used once, no configurability nobody requested, no error handling for cases that cannot happen.
- If the change took 200 lines and could take 50, rewrite it.
- At equal validation metric, the simpler state is the better one.

### 3. Surgical changes

Touch only what the plan requires. Clean up only your own mess.

- Don't improve adjacent code, comments or formatting. Don't refactor what isn't broken. Match the existing style.
- If you notice unrelated dead code or a bug, mention it in the report; don't fix it in this iteration.
- Remove imports, variables and functions that your own change made unused; leave pre-existing ones alone.
- The test: every changed line traces directly to the plan. In this loop the test has teeth, because the director reads `change.patch` and discards a result whose patch contains more than the plan.

### 4. Goal-driven execution

Define success before you start, then check against it.

- Every iteration has a verifiable criterion written down in advance: the prediction in `plan.md`, measured by the driver, not by you.
- The experimenter's own criterion is the smoke test: the change runs without error and does what the plan describes.
- For a multi-step task, state the steps with a check for each (`step -> verify: check`) and do not report done until each check has passed.

## Part 2. Writing for the researcher

Whoever reads your report has been away for hours, must decide what to do next, and will skim. Write so that a skim is enough to decide correctly, and a closer read finds the evidence.

This covers everything a person reads: setup questions and answers in chat, `REPORT.md`, `journal.md`, and the Reason and Lesson sections of each verdict. Lines the driver parses (`IDEA:`, `STATUS:`, `DECISION:`, citation blocks, idea tags) keep their fixed format.

- **Outcome first.** Open with the result and the decision it supports, e.g. "Best val_acc 0.962 (iteration 4), +0.031 over the 0.931 baseline; stopped at max_iterations."
- **Numbers over adjectives.** Give values, seeds, thresholds and the conditions a result holds under. "Improved a lot" tells the reader nothing they can act on.
- **Results as a table or figure.** When a report compares numbers across settings, give a table (the driver's `results_table.*` in matrix mode) or a figure, with one or two lines of interpretation, not paragraphs of numbers.
- **Problems where they are seen.** A failed check, an unverified source, a crash or a result inside the noise goes next to the claim it affects, never only at the end.
- **Point to detail instead of copying it.** The full record is in `results.tsv`, `journal.md` and `runs/NNN/`; summarise and name the file.
- **Full detail on request.** When the researcher asks for depth, give every number and caveat, still in short, scannable blocks.
- **Easy to scan.** Short paragraphs or bullets, a bold lead-in per point, plain vocabulary, each point made once.
- **Questions only during setup**, one at a time, with the options listed. Put a question that blocks progress at the end.
- **Code stays plain.** No chat formatting in code or config; comments say why, briefly.
- **Do, then report.** Do not hand the researcher a step you could have done, or a claim you could have checked.
