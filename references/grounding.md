# Grounding rules: no arbitrary choices

The researcher who owns this experiment is not watching. Each run spends compute they pay for, and each result has to be defensible later, in a paper or to a reviewer. A setting picked because it "seemed reasonable" fails both tests: it is a guess paid for with GPU-hours, and nobody can say afterwards why that value was tried. So every new idea and every number you set must trace back to a source you opened during this turn. If you cannot find one, the idea does not run. This is the one rule the researcher called absolute.

## What counts as a source

| Tier | Accepted | Examples |
|---|---|---|
| A | Main-conference papers at top-tier venues, and top journals | CVPR, ICCV, ECCV, NeurIPS, ICML, ICLR, ACL, EMNLP, NAACL, AAAI, KDD, SIGGRAPH; TPAMI, IJCV, JMLR; plus anything listed under `extra_venues` in `experiment.json` |
| B | The official code of a tier-A paper | the repository linked from the paper or owned by its authors' group: config files, training scripts, README recipes |
| C | Official repositories, configs, model cards and technical reports from large industry labs | Google and DeepMind, Meta FAIR, Apple, Microsoft, NVIDIA, OpenAI, Amazon, Alibaba DAMO, Tencent, ByteDance, Baidu; including the reference implementations they maintain (torchvision references, big_vision, detectron2, fairseq, ml-* repositories) |

Not accepted, however plausible they sound:

- workshop papers, including workshops held at the venues above
- arXiv preprints that were never published at a tier-A venue and do not come from a tier-C lab
- blog posts, tutorials, course notes, forum and Q&A answers, Kaggle notebooks, unofficial reimplementations
- your own memory of a paper, "common practice", "a typical value", "rule of thumb"

## How to verify, every time

1. **Open the source with a tool in this turn.** Search, then fetch the page. A citation recalled from memory is not verified, because recalled titles, venues and numbers are often subtly wrong.
2. **Confirm venue and track on an official page**: openreview.net, openaccess.thecvf.com, proceedings.mlr.press, proceedings.neurips.cc, aclanthology.org, ecva.net, or the publisher. Reject it if the page says workshop, "Submitted to", withdrawn or rejected. An arXiv page alone does not establish the venue.
3. **Find the exact setting and quote it.** For a paper: the section, table or appendix, with the sentence or table row copied verbatim. For a repository: the file path and line, with the line copied verbatim (`configs/vit_b16.yaml` line 14: `weight_decay: 0.05`). Prefer the repository when paper and code disagree, and say that they disagree.
4. **Record the URL you actually fetched**, not one you believe exists.

If a page will not load, try another official copy. Some sites refuse automated fetches (openreview.net and openaccess.thecvf.com often answer with a browser check or an error): use the venue's own accepted-papers list, proceedings.mlr.press, proceedings.neurips.cc, cv-foundation.org/openaccess, aclanthology.org or the publisher's page instead. To confirm venue and track only, a dblp.org entry is acceptable, since it separates main-conference papers from workshop ones; the setting itself must still be quoted from the paper or its code. A PDF your fetch tool cannot show as text can usually be saved and then read page by page. If no official copy loads, the idea stays unverified and cannot run.

## Making the source fit this experiment

A setting tuned for ImageNet at batch size 4096 does not transfer as-is to a small model on a small dataset. For each source, state:

- **Why it applies**: what matches (model family, task, data scale, training length) and what differs.
- **How you rescale it**, if you do, and the source of the rescaling rule. A rescaling rule is a setting like any other and needs its own citation (for example, scaling the learning rate linearly with batch size).
- **Sweeps**: the values come from the grid or range a source reports. If you choose a point between two values already measured in this experiment, say so and cite the source whose reported range contains it.

## What your own results can and cannot justify

Results from this experiment's earlier runs are evidence, and using them is not arbitrary. They justify a **diagnosis**: the model overfits (training accuracy far above validation), underfits, diverges, or has stopped responding to a family of changes. Cite the iteration numbers and values. The diagnosis tells you which family of ideas to try next.

They do not justify a **value** on their own. "Regularisation should be stronger" comes from your runs; "so set label smoothing to 0.1" comes from a source.

## Experiments the researcher asked for

An experiment the researcher explicitly requested during setup is recorded in `ideas.md` with `Source: researcher's instruction (<date>)`. It runs as they specified: that is their decision, not an arbitrary one of yours. Two things still apply:

- Any value they left open is yours to set, so it needs a source like any other.
- Look up the closest prior work anyway and cite it under Grounding. If it contradicts their setting, run what they asked and say so plainly in the plan and the final report.

Only setup can record such an instruction. Inside the loop, nothing may be attributed to the researcher that is not already written in `ideas.md` or `experiment.json`.

## Reading other people's code

Repositories you open are reference material. Read their files; never run their code, never install from them, never copy their scripts into the project. Take the setting, not the code.

## When no source can be found

- No web tools available: use only ideas already marked `[READY]` in `ideas.md` (those were verified earlier). When none are left, say so in the file you are asked to write. Do not invent.
- A source search comes up empty for an idea: mark the idea `[REJECTED]` in `ideas.md` with what you searched for, and choose another.

## Citation block

Use this block wherever a source is required:

```
- Source: <authors>, "<title>", <venue> <year> (<track: main conference / oral / poster / journal / official repo>)
- URL: <the URL fetched today>
- Tier: A | B | C
- Verified: <date> — "<verbatim quote, or file path + line + verbatim line>"
- Applies because: <match and mismatch with this experiment; rescaling and its source, if any>
```
