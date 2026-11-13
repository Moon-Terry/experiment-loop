#!/usr/bin/env python3
"""Self-test for driver.py. Uses scripted stand-in agents, so it needs no model, no network
and no GPU, and finishes in under a minute. Run it once on every new machine:

    python3 scripts/selftest.py

It builds throwaway projects in a temp directory and checks that the loop keeps wins, undoes
losses, protects frozen files, survives crashes and restarts, and stops when it should, and that
matrix mode runs a fixed table with retries, holds, escalation and one results table.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DRIVER = Path(__file__).resolve().parent / "driver.py"
PY = sys.executable

TRAIN = r'''
import json, math, os, sys, time
cfg = json.load(open("config.json"))
if cfg.get("crash"):
    sys.exit(1)
time.sleep(cfg.get("sleep", 0))
score = 1.0 - 0.1 * (math.log10(cfg["lr"]) + 2) ** 2 + 0.001 * int(os.environ.get("AR_SEED", 0))
if cfg.get("nan"):
    print("epoch 0 loss=nan", flush=True)
    time.sleep(30)
for epoch, v in enumerate([score - 0.05, score, score - 0.02]):   # best epoch is not the last one
    print("epoch %d val_acc=%.4f train_acc=%.4f" % (epoch, v, v + 0.01), flush=True)
json.dump({"final": {"val_loss": 1.0 - score}}, open("out.json", "w"))
'''

STUB = r'''
import json, re, sys
from pathlib import Path
role, prompt = sys.argv[1], sys.argv[2]
here = Path(__file__).parent
sc = json.loads((here / "scenario.json").read_text())
calls = here / "calls.txt"
count = int(calls.read_text()) if calls.exists() else 0
calls.write_text(str(count + 1))
if count < sc.get("fail_first", 0):
    sys.exit(1)

def field(name):
    return re.search(r"^- %s: (.*)$" % re.escape(name), prompt, re.M).group(1).strip()

task, n = field("Task"), int(field("Iteration"))
exp, root = Path(field("Experiment directory")), Path(field("Project root (your working directory)"))
run = exp / "runs" / ("%03d" % n)
step = sc.get("steps", {}).get(str(n), {})

def write_plan(k):
    d = exp / "runs" / ("%03d" % k)
    d.mkdir(parents=True, exist_ok=True)
    url = "" if sc.get("bad_plan") else "https://openreview.net/forum?id=stub%d" % k
    (d / "plan.md").write_text("# Plan\n**IDEA:** idea-%d\n\n## Grounding\n- Source: stub %s\n\n"
                               "## Change\nset the values listed in the scenario\n" % (k, url))

if role == "director":
    if task == "plan":
        write_plan(n)
    elif task == "judge":
        mt = json.loads((run / "metrics.json").read_text())
        keep = mt.get("improved") or step.get("force_keep")
        (run / "verdict.md").write_text("**DECISION:** %s\n\n## Reason\nstub\n" % ("KEEP" if keep else "DISCARD"))
        write_plan(n + 1)
    elif task == "final":
        (exp / "REPORT.md").write_text("# Report\nstub\n")
else:
    cfg_path = root / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg.update(step.get("set", {}))
    cfg_path.write_text(json.dumps(cfg, indent=1) + "\n")
    if step.get("touch_frozen"):
        (root / "evalcfg.py").write_text("SPLIT = 'test'  # tampered\n")
    if not step.get("no_report"):
        (run / "report.md").write_text("STATUS: %s\n" % step.get("status", "IMPLEMENTED"))
'''


class Project:
    def __init__(self, base, name, scenario, **spec):
        self.root = base / name
        self.exp = self.root / ".autoresearch" / "t"
        self.exp.mkdir(parents=True)
        (self.root / "train.py").write_text(TRAIN)
        (self.root / "config.json").write_text(json.dumps({"lr": 0.1}, indent=1) + "\n")
        (self.root / "evalcfg.py").write_text("SPLIT = 'val'\n")
        stub = base / (name + "_stub")
        stub.mkdir()
        (stub / "agent.py").write_text(STUB)
        (stub / "scenario.json").write_text(json.dumps(scenario))
        self.stub = stub / "agent.py"
        full = {
            "name": "t",
            "metric": {"name": "val_acc", "direction": "maximize", "regex": r"val_acc=([0-9.]+)", "pick": "best"},
            "extra_metrics": {"train_acc": {"regex": r"train_acc=([0-9.]+)"}},
            "artifacts": ["out.json"],
            "measurement_cmd": "%s train.py" % PY,
            "baseline_repeats": 2,
            "min_delta": 0.01,
            "scope": ["config.json"],
            "frozen": ["evalcfg.py", "train.py"],
            "budget": {"max_iterations": 3},
            "agents": {r: {"cmd": "%s %s %s" % (PY, self.stub, r), "timeout_s": 60}
                       for r in ("director", "experimenter")},
        }
        full.update(spec)
        (self.exp / "experiment.json").write_text(json.dumps(full, indent=2))

    def drv(self, *args, check=True, env=None):
        e = dict(os.environ, AR_BACKOFF_S="1")
        e.update(env or {})
        r = subprocess.run([PY, str(DRIVER)] + list(args) + [str(self.exp)], capture_output=True, text=True, env=e)
        if check and r.returncode != 0:
            raise AssertionError("driver %s failed (%d):\n%s\n%s" % (args, r.returncode, r.stdout, r.stderr))
        return r

    def setup(self):
        self.drv("init")
        self.drv("baseline")
        return self

    def state(self):
        return json.loads((self.exp / "state.json").read_text())

    def statuses(self):
        rows = (self.exp / "results.tsv").read_text().splitlines()[1:]
        return [r.split("\t")[2] for r in rows]

    def config(self):
        return json.loads((self.root / "config.json").read_text())


def near(a, b):
    return abs(a - b) < 1e-6


def test_full_loop(base):
    """Every outcome the loop can record, in one run."""
    steps = {
        "1": {"set": {"lr": 0.01}},                           # better            -> keep
        "2": {"set": {"lr": 0.5}},                            # worse             -> discard
        "3": {"set": {"crash": True}},                        # run dies          -> crash
        "4": {"set": {"lr": 0.02}, "touch_frozen": True},     # edits frozen file -> violation
        "5": {"set": {"lr": 0.5}},                            # same as #2        -> duplicate
        "6": {"status": "REJECTED_UNGROUNDED"},               # source not found  -> rejected
        "7": {"set": {"lr": 0.02}, "force_keep": True},       # worse, "KEEP"     -> discard
        "8": {},                                              # no edit made      -> failed
    }
    p = Project(base, "full", {"steps": steps}, budget={"max_iterations": 8}).setup()
    assert near(p.state()["baseline"], 0.9005), p.state()["baseline"]      # mean of seeds 0 and 1, best epoch
    p.drv("run")
    st = p.state()
    want = ["baseline", "keep", "discard", "crash", "violation", "duplicate", "rejected", "discard", "failed"]
    assert p.statuses() == want, p.statuses()
    assert st["status"] == "done" and st["stop_reason"] == "max_iterations reached", st
    assert near(st["best"], 1.0) and st["best_iter"] == 1, st
    assert p.config() == {"lr": 0.01}, p.config()                          # project left in its best state
    assert (p.root / "evalcfg.py").read_text() == "SPLIT = 'val'\n"        # frozen file put back
    assert '"lr": 0.01' in (p.exp / "runs/001/change.patch").read_text()
    assert json.loads((p.exp / "runs/001/metrics.json").read_text())["extra"]["train_acc"] > 0.9
    assert not (p.exp / "runs/005/run.0.log").exists()                     # a duplicate costs no run
    out = json.loads((p.root / "out.json").read_text())                    # iteration 7 ran last, but the
    assert near(out["final"]["val_loss"], 0.0), out                        # best run's artifact is in place
    assert (p.exp / "REPORT.md").exists() and not (p.exp / "driver.pid").exists()


def test_stop_conditions(base):
    p = Project(base, "target", {"steps": {"1": {"set": {"lr": 0.01}}}},
                metric={"name": "val_acc", "direction": "maximize", "regex": r"val_acc=([0-9.]+)",
                        "pick": "best", "target": 0.99}, budget={"max_iterations": 9}).setup()
    p.drv("run")
    assert p.state()["stop_reason"] == "target reached" and p.state()["iter"] == 1, p.state()

    p = Project(base, "patience", {"steps": {str(i): {"set": {"lr": 0.5 + i}} for i in range(1, 9)}},
                budget={"patience": 2}).setup()
    p.drv("run")
    assert p.state()["iter"] == 2 and "plateau" in p.state()["stop_reason"], p.state()

    p = Project(base, "stopfile", {"steps": {"1": {"set": {"lr": 0.01}}}}, budget={"unbounded": True}).setup()
    (p.exp / "STOP").write_text("")
    p.drv("run")
    assert p.state()["iter"] == 0 and "STOP" in p.state()["stop_reason"], p.state()
    (p.exp / "STOP").unlink()                                              # removing STOP resumes the loop
    (p.exp / "experiment.json").write_text(
        (p.exp / "experiment.json").read_text().replace('"unbounded": true', '"max_iterations": 1'))
    p.drv("run")
    assert p.state()["iter"] == 1 and p.statuses() == ["baseline", "keep"], p.state()


def test_minimize_json_metric(base):
    p = Project(base, "minimize", {"steps": {"1": {"set": {"lr": 0.01}}, "2": {"set": {"lr": 1.0}}}},
                metric={"name": "val_loss", "direction": "minimize", "json_file": "out.json",
                        "json_key": "final.val_loss"}, extra_metrics={}, scope=["config.json"],
                budget={"max_iterations": 2}, repeats=2, confirm_repeats=1).setup()
    p.drv("run")
    assert p.statuses() == ["baseline", "keep", "discard"], p.statuses()
    m1 = json.loads((p.exp / "runs/001/metrics.json").read_text())
    m2 = json.loads((p.exp / "runs/002/metrics.json").read_text())
    assert len(m1["values"]) == 3 and len(m2["values"]) == 2, (m1, m2)     # only an apparent win is re-measured
    assert p.state()["best"] < p.state()["baseline"]


def test_timeout_and_abort(base):
    p = Project(base, "kill", {"steps": {"1": {"set": {"sleep": 30}}, "2": {"set": {"nan": True}}}},
                run_timeout_s=2, abort_patterns=[r"loss=nan"], budget={"max_iterations": 2}).setup()
    t0 = time.time()
    p.drv("run")
    assert p.statuses() == ["baseline", "timeout", "aborted"], p.statuses()
    assert time.time() - t0 < 25, "a hung or diverged run must be killed early"
    assert p.config() == {"lr": 0.1}


def test_agent_failures(base):
    # the agent command itself fails twice (think: rate limit) -> wait and retry, nothing is lost
    p = Project(base, "flaky", {"fail_first": 2, "steps": {"1": {"set": {"lr": 0.01}}}},
                budget={"max_iterations": 1}).setup()
    p.drv("run")
    assert p.statuses() == ["baseline", "keep"], p.statuses()

    # the director keeps writing plans without a source -> the loop refuses to run them and halts
    p = Project(base, "ungrounded", {"bad_plan": True}).setup()
    r = p.drv("run", check=False)
    assert r.returncode == 2 and p.state()["status"] == "halted", (r.returncode, p.state())
    assert "grounded plan" in p.state()["stop_reason"] and p.statuses() == ["baseline"]

    # the experimenter never reports -> counted as failed, the loop goes on
    p = Project(base, "silent", {"steps": {"1": {"set": {"lr": 0.01}, "no_report": True}}},
                budget={"max_iterations": 1}).setup()
    p.drv("run")
    assert p.statuses() == ["baseline", "failed"] and p.config() == {"lr": 0.1}, p.statuses()


def test_resume_after_kill(base):
    p = Project(base, "resume", {"steps": {"1": {"set": {"lr": 0.01, "sleep": 4}}}},
                budget={"max_iterations": 1}).setup()
    proc = subprocess.Popen([PY, str(DRIVER), "run", str(p.exp)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=dict(os.environ, AR_BACKOFF_S="1"))
    deadline = time.time() + 30
    while time.time() < deadline and not (p.exp / "runs/001/run.0.log").exists():
        time.sleep(0.1)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30)
    st = p.state()
    assert st["iter"] == 0 and st["phase"] == "measure" and not (p.exp / "driver.pid").exists(), st
    p.drv("run")
    assert p.statuses() == ["baseline", "keep"] and p.state()["status"] == "done", p.statuses()


def test_in_session_mode(base):
    """Drive the same state machine with `next` and `measure`, as a chat session would."""
    p = Project(base, "session", {"steps": {"1": {"set": {"lr": 0.01}}, "2": {"set": {"lr": 0.5}}}},
                budget={"max_iterations": 2}).setup()
    seen = []
    for _ in range(40):
        act = json.loads(p.drv("next").stdout)
        seen.append(act["action"] if act["action"] != "agent" else act["role"] + ":" + act["task"])
        if act["action"] == "done":
            break
        if act["action"] == "measure":
            p.drv("measure")
        else:
            subprocess.run([PY, str(p.stub), act["role"], Path(act["prompt_file"]).read_text()], check=True)
    want = ["director:plan", "experimenter:perform", "measure", "director:judge", "experimenter:perform",
            "measure", "director:judge", "director:final", "done"]
    assert seen == want, seen
    assert p.statuses() == ["baseline", "keep", "discard"] and p.config() == {"lr": 0.01}


def test_spec_errors(base):
    p = Project(base, "badspec", {}, budget={})
    r = p.drv("init", check=False)
    assert r.returncode == 1 and "budget" in r.stderr, r.stderr
    p = Project(base, "nodelta", {}, min_delta=None).setup()                # baseline works, the loop does not
    r = p.drv("run", check=False)
    assert r.returncode == 1 and "min_delta" in r.stderr, r.stderr
    p = Project(base, "badscope", {}, scope=["../outside.py"])
    r = p.drv("init", check=False)
    assert r.returncode == 1 and "inside" in r.stderr, r.stderr


MATRIX_TRAIN = r"""
import os, sys
from pathlib import Path
row, seed = os.environ["AR_ROW"], int(os.environ["AR_SEED"])
if row == "flaky":                       # fails once, then works: an infrastructure fault
    mark = Path("flaky_%d.done" % seed)
    if not mark.exists():
        mark.write_text("x")
        sys.exit(1)
if row == "broken":
    sys.exit(3)
base = float(os.environ.get("BASE", "0.5"))
print("val_acc=%.4f" % (base + 0.001 * (seed - 42)))
"""


def test_matrix_mode(base):
    """A fixed rows x seeds table: no agents, retries, escalation to the inbox, holds, one results table."""
    p = Project(base, "matrix", {}, mode="matrix", min_delta=None, scope=[], budget={},
                measurement_cmd="%s mtrain.py" % PY,
                metric={"name": "val_acc", "direction": "maximize", "regex": r"val_acc=([0-9.]+)"},
                extra_metrics={},
                matrix={"rows": [{"name": "off", "env": {"BASE": "0.5"}}, {"name": "mine", "env": {"BASE": "0.6"}},
                                 {"name": "flaky"}, {"name": "broken"}, {"name": "held"}],
                        "seeds": [42, 43], "retries": 1},
                escalation={"notify_cmd": "printf '%s|%s\\n' \"$AR_MESSAGE\" \"$AR_ATTACHMENT\" >> notes.txt"})
    (p.root / "mtrain.py").write_text(MATRIX_TRAIN)
    p.drv("init")
    (p.exp / "HOLD").write_text("held\n")
    seen = []
    for _ in range(40):
        act = json.loads(p.drv("next").stdout)
        seen.append(act["action"])
        if act["action"] != "measure":
            break
        p.drv("measure")
    assert seen[-1] == "wait" and "held" in json.loads(p.drv("next").stdout)["reason"], seen
    cells = p.state()["cells"]
    assert cells["flaky|42"]["status"] == "ok" and cells["flaky|42"]["attempts"] == 2, cells["flaky|42"]
    assert cells["broken|43"]["status"] == "crash" and cells["broken|43"]["attempts"] == 2, cells["broken|43"]
    assert near(cells["mine|43"]["value"], 0.601), cells["mine|43"]
    inbox = [json.loads(x) for x in (p.exp / "inbox.jsonl").read_text().splitlines()]
    assert len(inbox) == 2 and all("broken" in i["what"] for i in inbox), inbox   # one per broken seed
    (p.exp / "HOLD").write_text("")                                         # release the held row
    p.drv("run")
    st = p.state()
    assert st["status"] == "done" and st["stop_reason"] == "every cell has run", st
    md = (p.exp / "results_table.md").read_text()
    assert "**0.6005**" in md and "| held |" in md, md                     # best row bold, every row listed
    notes = (p.root / "notes.txt").read_text()
    assert "seed 42 finished" in notes and "seed 43 finished" in notes and "finished: every cell" in notes, notes
    assert (p.exp / "REPORT.md").exists() and "broken" in (p.exp / "REPORT.md").read_text()
    assert not list(p.exp.glob("runs/0*")), "matrix mode must not create search-mode iterations"


def main():
    keep = "--keep" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="ar-selftest-"))
    tests = [test_full_loop, test_stop_conditions, test_minimize_json_metric, test_timeout_and_abort,
             test_agent_failures, test_resume_after_kill, test_in_session_mode, test_spec_errors,
             test_matrix_mode]
    failed = 0
    for t in tests:
        t0 = time.time()
        try:
            t(base)
            print("PASS  %-28s %.1fs" % (t.__name__, time.time() - t0))
        except Exception as e:  # report every failure, not just the first
            failed += 1
            print("FAIL  %-28s %s: %s" % (t.__name__, type(e).__name__, e))
    if keep or failed:
        print("test projects kept in %s" % base)
    else:
        shutil.rmtree(base, ignore_errors=True)
    print("%d of %d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
