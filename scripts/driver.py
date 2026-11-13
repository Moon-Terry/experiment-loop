#!/usr/bin/env python3
"""experiment-loop driver: the deterministic half of an unattended experiment loop.

The driver owns everything that must be identical every iteration: running the
measurement, reading the metric, keeping the best-validation state, restoring
after a failed idea, enforcing the frozen files and deciding when to stop. The
agents (director, experimenter) own judgment and only ever communicate through
files in the experiment directory.

Layout:  <project>/.autoresearch/<name>/experiment.json   (see references/spec.md)

Commands:
  init      validate the spec, snapshot scope and frozen files
  baseline  measure the untouched project and record noise
  doctor    check that the agent commands work unattended
  run       run the loop until a stop condition (use inside tmux)
  next      print the next required action as JSON (in-session mode)
  measure   run the measurement for the current iteration (in-session mode)
  status    print a summary

Two modes (experiment.json "mode"):
  search    (default) design -> plan -> perform -> evaluate -> validate -> decide, keeping the best state
  matrix    a fixed, pre-registered table of rows x seeds, run once each in seed-major order; no agent
            chooses what runs next, so results cannot steer the experiment

Standard library only, Python 3.8+.
"""

import argparse
import datetime as dt
import difflib
import filecmp
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 3                      # valid-output attempts per agent task
# waits after an agent command fails (rate limit, outage); the last value repeats
BACKOFF_S = [int(x) for x in os.environ.get("AR_BACKOFF_S", "60,120,300,600,900").split(",")]
HEADER = ["iter", "time", "status", "metric", "best", "delta", "duration_s", "idea", "source"]

DEFAULTS = {
    "language": "en",
    "repeats": 1,
    "baseline_repeats": 3,
    "confirm_repeats": 0,
    "min_delta": None,  # required before the loop starts; set it from the baseline noise
    "run_timeout_s": 3600,
    "abort_patterns": [],
    "frozen": [],
    "exclude": ["__pycache__", "*.pyc", ".DS_Store", ".ipynb_checkpoints"],
    "artifacts": [],
    "extra_metrics": {},
    "validation_rules": [],
    "extra_venues": [],
    "budget": {},
    "pivot_after": 3,
    "min_ready_ideas": 3,
    "max_snapshot_mb": 500,
    "agents": {},
    "mode": "search",
    "matrix": {},
    "escalation": {},
    "report": {},
}
MATRIX_TSV = ["time", "row", "seed", "status", "metric", "extra", "duration_s", "detail"]

LIVE = []  # running child processes, killed if the driver is interrupted


class SpecError(Exception):
    """The experiment definition or the way the driver was called is wrong."""


class Halt(Exception):
    """The loop cannot continue safely without a human."""


# ----------------------------------------------------------------- utilities

def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg):
    # progress goes to stderr; stdout carries only what a caller parses
    print("[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg), file=sys.stderr, flush=True)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files_under(base):
    base = Path(base)
    out = []
    if base.is_dir():
        for d, _dirs, files in os.walk(base):
            for f in files:
                out.append((Path(d) / f).relative_to(base).as_posix())
    return sorted(out)


def read_text_or_none(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def fmt(x):
    return "" if x is None else ("%.6g" % x if isinstance(x, float) else str(x))


def to_float(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def is_better(value, best, direction, min_delta):
    gain = value - best if direction == "maximize" else best - value
    return gain > 0 and gain >= float(min_delta or 0)


def section(text, name):
    m = re.search(r"^#+\s*%s\b[^\n]*\n(.*?)(?=^#+\s|\Z)" % re.escape(name), text, re.M | re.S | re.I)
    return m.group(1) if m else ""


def keyword(text, key, choices=None):
    """Read 'KEY: value' tolerantly (models like to add markdown emphasis)."""
    value = r"(%s)\b" % "|".join(choices) if choices else r"(.+?)[\s*_`]*$"
    m = re.search(r"^[\s>#*_`-]*%s[\s*_`]*:[\s*_`]*%s" % (key, value), text, re.M)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- experiment

class Exp:
    def __init__(self, exp_dir):
        self.dir = Path(exp_dir).expanduser().resolve()
        spec_path = self.dir / "experiment.json"
        if not spec_path.is_file():
            raise SpecError("no experiment.json in %s" % self.dir)
        try:
            raw = read_json(spec_path)
        except ValueError as e:
            raise SpecError("experiment.json is not valid JSON: %s" % e)
        self.spec = dict(DEFAULTS)
        self.spec.update(raw)
        self.root = self.dir.parent.parent
        self.best_files = self.dir / "best" / "files"
        self.state_path = self.dir / "state.json"
        self._frozen = None
        self.validate()
        self.state = read_json(self.state_path) if self.state_path.is_file() else None

    # -- spec

    def validate(self):
        s = self.spec

        def need(cond, msg):
            if not cond:
                raise SpecError(msg)

        need(isinstance(s.get("name"), str) and s["name"], "name is required")
        need(isinstance(s.get("measurement_cmd"), str) and s["measurement_cmd"].strip(),
             "measurement_cmd is required")
        metrics = [("metric", s.get("metric") or {})]
        metrics += [("extra_metrics." + k, v) for k, v in s["extra_metrics"].items()]
        for label, m in metrics:
            if m.get("regex"):
                try:
                    rx = re.compile(m["regex"])
                except re.error as e:
                    raise SpecError("%s.regex does not compile: %s" % (label, e))
                need(rx.groups == 1, "%s.regex needs exactly one capture group around the number" % label)
            else:
                need(m.get("json_file") and m.get("json_key"),
                     "%s needs either regex or json_file + json_key" % label)
        m = s["metric"]
        need(m.get("name"), "metric.name is required")
        need(m.get("direction") in ("maximize", "minimize"), "metric.direction must be maximize or minimize")
        need(m.get("pick", "last") in ("last", "best"), "metric.pick must be last or best")
        need(s["mode"] in ("search", "matrix"), "mode must be search or matrix")
        if s["mode"] == "search":
            need(isinstance(s.get("scope"), list) and s["scope"], "scope must list at least one file or directory")
        else:
            s.setdefault("scope", [])
            mx = s["matrix"]
            rows = mx.get("rows")
            need(isinstance(rows, list) and rows and all(isinstance(r, dict) and r.get("name") for r in rows),
                 "matrix.rows must list at least one row, each with a name")
            names = [r["name"] for r in rows]
            need(len(set(names)) == len(names), "matrix.rows names must be unique")
            need(all(re.match(r"^[A-Za-z0-9_.+-]+$", n) for n in names),
                 "matrix.rows names may use letters, digits and _ . + - only")
            seeds = mx.get("seeds")
            need(isinstance(seeds, list) and seeds and all(isinstance(x, int) for x in seeds),
                 "matrix.seeds must list at least one integer seed")
        for key in ("scope", "frozen", "artifacts"):
            for rel in s[key]:
                p = (self.root / rel).resolve()
                need(not os.path.isabs(rel) and (p == self.root or self.root in p.parents),
                     "%s entry %r must be a relative path inside %s" % (key, rel, self.root))
                if key != "artifacts":
                    need(p.exists(), "%s entry %r does not exist under %s" % (key, rel, self.root))
        need(not set(s["scope"]) & set(s["frozen"]), "a path cannot be both in scope and frozen")
        b = s["budget"]
        need(isinstance(b, dict), "budget must be an object")
        need(s["mode"] == "matrix" or any(b.get(k) for k in ("max_iterations", "max_hours", "patience"))
             or m.get("target") is not None or b.get("unbounded") is True,
             "set budget.max_iterations, budget.max_hours, budget.patience or metric.target, "
             "or say budget.unbounded=true to run until stopped by hand")

    # -- files

    def run_dir(self, n):
        return self.dir / "runs" / ("%03d" % n)

    def save(self):
        write_json(self.state_path, self.state)

    def set_phase(self, phase):
        self.state["phase"] = phase
        self.save()

    def _excluded(self, rel):
        parts = Path(rel).parts
        if parts and parts[0] in (".autoresearch", ".git"):
            return True
        for pat in self.spec["exclude"]:
            if fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(part, pat) for part in parts):
                return True
        return False

    def _expand(self, rels, skip_frozen):
        out = set()
        for rel in rels:
            p = self.root / rel
            if p.is_file():
                cand = [Path(rel).as_posix()]
            elif p.is_dir():
                cand = []
                for d, dirs, files in os.walk(p):
                    here = Path(d).relative_to(self.root)
                    dirs[:] = [x for x in dirs if not self._excluded((here / x).as_posix())]
                    cand.extend((here / f).as_posix() for f in files)
            else:
                cand = []
            for c in cand:
                if self._excluded(c) or (skip_frozen and c in self.frozen_manifest()):
                    continue
                out.add(c)
        return sorted(out)

    def scope_files(self):
        return self._expand(self.spec["scope"], skip_frozen=True)

    def snapshot_scope(self):
        """Make best/files an exact copy of the scope as it is now."""
        files = self.scope_files()
        total = sum((self.root / f).stat().st_size for f in files)
        limit = self.spec["max_snapshot_mb"] * 1024 * 1024
        if total > limit:
            raise SpecError("scope holds %.0f MB, over max_snapshot_mb=%s. Narrow scope or add data and "
                            "checkpoint folders to exclude." % (total / 1048576, self.spec["max_snapshot_mb"]))
        tmp = self.best_files.with_name("files.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for rel in files:
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.root / rel, tmp / rel)
        shutil.rmtree(self.best_files, ignore_errors=True)
        tmp.rename(self.best_files)

    def restore_scope(self):
        """Make the scope an exact copy of best/files."""
        snap = set(files_under(self.best_files))
        for rel in sorted(set(self.scope_files()) - snap):
            try:
                (self.root / rel).unlink()
            except OSError:
                pass
        for rel in sorted(snap):
            src, dst = self.best_files / rel, self.root / rel
            if not dst.is_file() or not filecmp.cmp(str(src), str(dst), shallow=False):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    def tree_hash(self):
        h = hashlib.sha256()
        for rel in self.scope_files():
            h.update(rel.encode() + b"\0" + sha_file(self.root / rel).encode() + b"\n")
        return h.hexdigest()

    def write_patch(self, n):
        """Diff of the scope against the best state, saved as runs/NNN/change.patch."""
        snap, cur = set(files_under(self.best_files)), set(self.scope_files())
        out = []
        for rel in sorted(snap | cur):
            a, b = self.best_files / rel, self.root / rel
            if rel in snap and rel in cur and filecmp.cmp(str(a), str(b), shallow=False):
                continue
            ta = read_text_or_none(a) if rel in snap else ""
            tb = read_text_or_none(b) if rel in cur else ""
            if ta is None or tb is None:
                out.append("Binary file %s changed\n" % rel)
                continue
            for line in difflib.unified_diff(ta.splitlines(True), tb.splitlines(True),
                                             "a/" + rel if rel in snap else "/dev/null",
                                             "b/" + rel if rel in cur else "/dev/null"):
                out.append(line if line.endswith("\n") else line + "\n")
        text = "".join(out)
        (self.run_dir(n) / "change.patch").write_text(text, encoding="utf-8")
        return text

    def save_artifacts(self):
        dest = self.dir / "best" / "artifacts"
        shutil.rmtree(dest, ignore_errors=True)
        for rel in self.spec["artifacts"]:
            src = self.root / rel
            if src.is_dir():
                shutil.copytree(src, dest / rel)
            elif src.is_file():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / rel)

    def restore_artifacts(self):
        base = self.dir / "best" / "artifacts"
        for rel in files_under(base):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base / rel, self.root / rel)

    def point_current(self, target):
        link = self.dir / "current_run.log"
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(os.path.relpath(str(target), str(self.dir)))
        except OSError:
            pass

    # -- frozen files: the evaluation must not move under the experiment

    def frozen_manifest(self):
        if self._frozen is None:
            p = self.dir / "frozen" / "manifest.json"
            self._frozen = read_json(p) if p.is_file() else {}
        return self._frozen

    def init_frozen(self):
        """Copy the frozen files aside, so a later change can be detected and undone."""
        base = self.dir / "frozen"
        shutil.rmtree(base, ignore_errors=True)
        (base / "files").mkdir(parents=True)
        self._frozen = {}
        files = self._expand(self.spec["frozen"], skip_frozen=False)
        total = sum((self.root / f).stat().st_size for f in files)
        if total > self.spec["max_snapshot_mb"] * 1024 * 1024:
            raise SpecError("frozen holds %.0f MB, over max_snapshot_mb=%s. List the code and split files "
                            "that define the evaluation, not the dataset itself."
                            % (total / 1048576, self.spec["max_snapshot_mb"]))
        manifest = {}
        for rel in files:
            p = self.root / rel
            (base / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, base / "files" / rel)
            manifest[rel] = {"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns, "sha": sha_file(p)}
        write_json(base / "manifest.json", manifest)
        self._frozen = manifest
        return manifest

    def check_frozen(self):
        """Put back every frozen file that changed or disappeared; return their names."""
        changed = []
        for rel, meta in self.frozen_manifest().items():
            p = self.root / rel
            if p.is_file():
                st = p.stat()
                if st.st_size == meta["size"] and (st.st_mtime_ns == meta["mtime_ns"]
                                                   or sha_file(p) == meta["sha"]):
                    continue
            changed.append(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.dir / "frozen" / "files" / rel, p)
        return changed


# --------------------------------------------------------------- measurement

def kill_group(proc):
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run_cmd(exp, cmd, log_path, timeout, env_extra=None, abort_patterns=()):
    """Run a shell command with a timeout, logging to a file. Returns (status, detail, seconds)."""
    env = dict(os.environ)
    env.update(env_extra or {})
    aborts = [re.compile(p, re.I) for p in abort_patterns]
    t0 = time.time()
    status, detail = "ok", ""
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(cmd, shell=True, executable=shutil.which("bash"), cwd=str(exp.root),
                                stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)
        LIVE.append(proc)
        pos, tail = 0, ""
        try:
            while True:
                rc = proc.poll()
                if aborts:
                    with open(log_path, "rb") as rf:
                        rf.seek(pos)
                        chunk = rf.read()
                    pos += len(chunk)
                    window = tail + chunk.decode("utf-8", "replace")
                    tail = window[-300:]
                    hit = next((a.pattern for a in aborts if a.search(window)), None)
                    if hit:
                        if rc is None:
                            kill_group(proc)
                        status, detail = "aborted", "abort pattern matched: %s" % hit
                        break
                if rc is not None:
                    if rc != 0:
                        status, detail = "crash", "exit code %d" % rc
                    break
                if time.time() - t0 > timeout:
                    kill_group(proc)
                    status, detail = "timeout", "killed after %ds" % timeout
                    break
                time.sleep(0.5)
        finally:
            LIVE.remove(proc)
    return status, detail, time.time() - t0


def extract_metric(exp, m, text, pick="last"):
    """Read one number from a run: by regex from its output, or by key from a JSON file it wrote."""
    if m.get("regex"):
        vals = [v for v in (to_float(x) for x in re.findall(m["regex"], text)) if v is not None]
        if not vals:
            return None
        return max(vals) if pick == "max" else min(vals) if pick == "min" else vals[-1]
    try:
        obj = read_json(exp.root / m["json_file"])
    except (OSError, ValueError):
        return None
    for key in str(m["json_key"]).split("."):
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return to_float(obj)


def measure(exp, n, repeats=None, baseline=False):
    """Run the measurement for iteration n and write runs/NNN/metrics.json."""
    s, st = exp.spec, exp.state
    m = s["metric"]
    pick = "last" if m.get("pick", "last") == "last" else "max" if m["direction"] == "maximize" else "min"
    run_dir = exp.run_dir(n)
    run_dir.mkdir(parents=True, exist_ok=True)
    res = {"iter": n, "status": "ok", "detail": "", "values": [], "value": None, "extra": {},
           "improved": False, "best_before": st.get("best"), "delta": None,
           "min_delta": s["min_delta"], "tree": exp.tree_hash(), "started": now()}
    extras = {}
    t0 = time.time()

    def done():
        res["duration_s"] = round(time.time() - t0, 1)
        write_json(run_dir / "metrics.json", res)
        return res

    changed = exp.check_frozen()
    if changed:
        res["status"] = "violation"
        res["detail"] = "frozen files were modified (now restored): %s" % ", ".join(changed)
        return done()

    def one(k):
        logp = run_dir / ("run.%d.log" % k)
        exp.point_current(logp)
        if m.get("json_file"):
            try:
                (exp.root / m["json_file"]).unlink()  # never read a stale result
            except OSError:
                pass
        status, detail, _ = run_cmd(exp, s["measurement_cmd"], logp, s["run_timeout_s"],
                                    {"AR_SEED": str(k)}, s["abort_patterns"])
        if status != "ok":
            return status, detail
        text = logp.read_text(encoding="utf-8", errors="replace")
        v = extract_metric(exp, m, text, pick)
        if v is None:
            return "no_metric", "the run finished but %s could not be read from its output" % m["name"]
        res["values"].append(v)
        for name, em in s["extra_metrics"].items():
            ev = extract_metric(exp, em, text)
            if ev is not None:
                extras.setdefault(name, []).append(ev)
        return "ok", ""

    planned = repeats or s["repeats"]
    total, k = planned, 0
    while k < total:
        status, detail = one(k)
        if status != "ok":
            res["status"], res["detail"] = status, detail
            return done()
        k += 1
        if k == total:
            res["value"] = statistics.fmean(res["values"])
            res["extra"] = {name: statistics.fmean(v) for name, v in extras.items()}
            if not baseline and st.get("best") is not None:
                res["delta"] = res["value"] - st["best"]
                res["improved"] = is_better(res["value"], st["best"], m["direction"], s["min_delta"])
                if res["improved"] and s["confirm_repeats"] and total == planned:
                    total += s["confirm_repeats"]  # re-measure an apparent win before believing it
                    res["confirm_runs"] = s["confirm_repeats"]
    return done()


def append_row(exp, row):
    path = exp.dir / "results.tsv"
    new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write("\t".join(HEADER) + "\n")
        f.write("\t".join(re.sub(r"\s+", " ", fmt(row.get(h))) for h in HEADER) + "\n")


# ------------------------------------------------- escalation and reporting

def notify(exp, message, attachment=None):
    """Hand a message (and optionally an image) to escalation.notify_cmd, if one is set. Never raises."""
    cmd = exp.spec["escalation"].get("notify_cmd")
    if not cmd:
        return False
    logs = exp.dir / "agent_logs"
    logs.mkdir(exist_ok=True)
    msg_file = logs / ("notify.%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    msg_file.write_text(message + "\n", encoding="utf-8")
    env = {"AR_MESSAGE": message, "AR_MESSAGE_FILE": str(msg_file),
           "AR_ATTACHMENT": str(attachment) if attachment and Path(attachment).is_file() else ""}
    try:
        status, detail, _ = run_cmd(exp, cmd, Path(str(msg_file) + ".log"), 120, env)
    except OSError as e:
        status, detail = "crash", str(e)
    if status != "ok":
        log("notify_cmd failed: %s %s" % (status, detail))
    return status == "ok"


def escalate(exp, severity, what, options=None, evidence=None, attachment=None):
    """Put a decision only a person can make into inbox.jsonl and notify. Does not wait for an answer:
    the caller holds only the affected item and keeps the rest of the work going."""
    item = {"id": "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), hashlib.sha1(what.encode()).hexdigest()[:6]),
            "time": now(), "experiment": exp.spec["name"], "severity": severity, "what": what,
            "options": options or [], "evidence": [str(e) for e in (evidence or [])]}
    inbox = exp.dir / exp.spec["escalation"].get("inbox", "inbox.jsonl")
    with open(inbox, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    (exp.dir / "ack").mkdir(exist_ok=True)
    lines = ["[%s] %s: %s" % (exp.spec["name"], severity, what)]
    lines += ["- option: %s" % o for o in item["options"]]
    lines.append("inbox id %s; answer by writing %s" % (item["id"], exp.dir / "ack" / (item["id"] + ".txt")))
    notify(exp, "\n".join(lines), attachment)
    log("escalated %s: %s" % (item["id"], what))
    return item


def write_table(exp):
    """Render the matrix results as one table: markdown always, LaTeX (booktabs) always, PNG when
    report.render_cmd is set. Returns the PNG path or None."""
    s, st = exp.spec, exp.state
    m, extras = s["metric"], list(s["extra_metrics"])
    columns = [(m["name"], m["direction"])] + [(e, s["extra_metrics"][e].get("direction")) for e in extras]
    seeds = s["matrix"]["seeds"]
    cells = st.get("cells", {})

    def stats(row, col):
        vals = []
        for seed in seeds:
            c = cells.get("%s|%s" % (row, seed), {})
            if c.get("status") != "ok":
                continue
            v = c.get("value") if col == m["name"] else (c.get("extra") or {}).get(col)
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        return statistics.fmean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0), len(vals)

    rows = [r["name"] for r in s["matrix"]["rows"]]
    table = {(r, c): stats(r, c) for r in rows for c, _ in columns}
    best = {}
    for c, direction in columns:
        filled = [(table[(r, c)][0], r) for r in rows if table[(r, c)]]
        if filled and direction in ("maximize", "minimize"):
            best[c] = (max if direction == "maximize" else min)(filled)[1]
    arrow = {"maximize": "↑", "minimize": "↓"}
    md = ["| row | " + " | ".join("%s %s" % (c, arrow.get(d, "")) for c, d in columns) + " |",
          "|---|" + "---|" * len(columns)]
    tex_rows = []
    for r in rows:
        md_cells, tex_cells = [], []
        for c, _ in columns:
            t = table[(r, c)]
            if t is None:
                md_cells.append("·")
                tex_cells.append(r"$\cdot$")
                continue
            mean, sd, n = t
            flag = "" if n == len(seeds) else " (%d)" % n
            bold = best.get(c) == r
            md_cells.append(("**%.4g**" if bold else "%.4g") % mean + " ± %.2g%s" % (sd, flag))
            body = r"%.4g_{\scriptscriptstyle %.2g}" % (mean, sd)
            if bold:
                body = r"\mathbf{%.4g}_{\scriptscriptstyle %.2g}" % (mean, sd)
            tex_cells.append("$%s%s$" % (body, "" if n == len(seeds) else r"^{\scriptscriptstyle (%d)}" % n))
        md.append("| %s | %s |" % (r, " | ".join(md_cells)))
        tex_rows.append("%s & %s \\\\" % (r.replace("_", r"\_"), " & ".join(tex_cells)))
    note = ("Mean and SD over seeds %s; (n) marks cells with fewer finished seeds; bold is the best filled "
            "value per column; a dot means no finished seed yet (not run, held or failed; see REPORT.md)." % ", ".join(str(x) for x in seeds))
    (exp.dir / "results_table.md").write_text("\n".join(md) + "\n\n" + note + "\n", encoding="utf-8")
    header = " & ".join(r"%s %s" % (c.replace("_", r"\_"), {"maximize": r"$\uparrow$",
                                                             "minimize": r"$\downarrow$"}.get(d, ""))
                        for c, d in columns)
    tex = "\n".join([
        r"\documentclass[border=8pt]{standalone}",
        r"\usepackage[T1]{fontenc}\usepackage{lmodern,booktabs,amsmath}",
        r"\begin{document}",
        r"\begin{tabular}{@{}l%s@{}}" % ("c" * len(columns)),
        r"\toprule",
        r"Row & %s \\" % header,
        r"\midrule",
        "\n".join(tex_rows),
        r"\bottomrule",
        r"\multicolumn{%d}{@{}l}{\footnotesize %s}" % (len(columns) + 1, note),
        r"\end{tabular}",
        r"\end{document}", ""])
    tex_path = exp.dir / "results_table.tex"
    tex_path.write_text(tex, encoding="utf-8")
    render = s["report"].get("render_cmd")
    if not render:
        return None
    png = exp.dir / "results_table.png"
    cmd = render.format(tex=shlex.quote(str(tex_path)), dir=shlex.quote(str(exp.dir)),
                        pdf=shlex.quote(str(exp.dir / "results_table.pdf")),
                        png_stem=shlex.quote(str(exp.dir / "results_table")))
    status, detail, _ = run_cmd(exp, cmd, exp.dir / "results_table.render.log", 300)
    if status != "ok" or not png.is_file():
        log("report.render_cmd: %s %s (see results_table.render.log)" % (status, detail))
        return None
    return png


# ------------------------------------------------------------ matrix mode

def matrix_cells(exp):
    """All (row, seed) cells in seed-major order, so a cut at any time leaves whole seeds."""
    mx = exp.spec["matrix"]
    return [(r["name"], seed) for seed in mx["seeds"] for r in mx["rows"]]


def held_rows(exp):
    """Rows listed in the HOLD file (one name per line) are skipped until the line is removed."""
    text = read_text_or_none(exp.dir / "HOLD") or ""
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def measure_cell(exp, row, seed):
    """Run one matrix cell, record it in state, matrix.tsv and the results table."""
    s, st = exp.spec, exp.state
    m = s["metric"]
    pick = "last" if m.get("pick", "last") == "last" else "max" if m["direction"] == "maximize" else "min"
    spec_row = next(r for r in s["matrix"]["rows"] if r["name"] == row)
    run_dir = exp.dir / "runs" / row / ("seed_%d" % seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    key = "%s|%s" % (row, seed)
    cell = st["cells"].setdefault(key, {"attempts": 0})
    cell["attempts"] += 1
    logp = run_dir / ("run.%d.log" % cell["attempts"])
    exp.point_current(logp)
    res = {"row": row, "seed": seed, "status": "ok", "detail": "", "value": None, "extra": {},
           "attempt": cell["attempts"], "started": now()}
    changed = exp.check_frozen()
    if changed:
        res["status"], res["detail"] = "violation", "frozen files were modified (now restored): %s" % ", ".join(changed)
        dur = 0.0
    else:
        if m.get("json_file"):
            try:
                (exp.root / m["json_file"]).unlink()
            except OSError:
                pass
        env = {"AR_ROW": row, "AR_SEED": str(seed)}
        env.update({k: str(v) for k, v in (spec_row.get("env") or {}).items()})
        status, detail, dur = run_cmd(exp, s["measurement_cmd"], logp, s["run_timeout_s"], env, s["abort_patterns"])
        res["status"], res["detail"] = status, detail
        if status == "ok":
            text = logp.read_text(encoding="utf-8", errors="replace")
            res["value"] = extract_metric(exp, m, text, pick)
            if res["value"] is None:
                res["status"], res["detail"] = "no_metric", "%s could not be read from the run" % m["name"]
            for name, em in s["extra_metrics"].items():
                ev = extract_metric(exp, em, text)
                if ev is not None:
                    res["extra"][name] = ev
    res["duration_s"] = round(dur, 1)
    write_json(run_dir / "metrics.json", res)
    cell.update({"status": res["status"], "value": res["value"], "extra": res["extra"],
                 "duration_s": res["duration_s"], "detail": res["detail"]})
    retries = int(s["matrix"].get("retries", 1))
    if res["status"] != "ok" and cell["attempts"] <= retries and res["status"] != "violation":
        cell["status"] = "retry"   # an infrastructure fault gets another try before anyone is bothered
    path = exp.dir / "matrix.tsv"
    new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write("\t".join(MATRIX_TSV) + "\n")
        row_out = {"time": now(), "row": row, "seed": seed, "status": cell["status"], "metric": res["value"],
                   "extra": json.dumps(res["extra"]) if res["extra"] else "", "duration_s": res["duration_s"],
                   "detail": res["detail"]}
        f.write("\t".join(re.sub(r"\s+", " ", fmt(row_out[h])) for h in MATRIX_TSV) + "\n")
    st["active_s"] += res["duration_s"]
    exp.save()
    png = write_table(exp)
    if cell["status"] not in ("ok", "retry"):
        escalate(exp, "block", "%s seed %d failed after %d attempt(s): %s %s"
                 % (row, seed, cell["attempts"], res["status"], res["detail"]),
                 ["re-run the cell after fixing the cause (delete its entry from state.json cells)",
                  "accept it as failed and report it"], [run_dir])
    seed_done = all(st["cells"].get("%s|%s" % (r["name"], seed), {}).get("status") in ("ok", "failed",
                    "crash", "timeout", "aborted", "no_metric", "violation") for r in s["matrix"]["rows"])
    if seed_done and seed not in st.setdefault("seeds_reported", []):
        st["seeds_reported"].append(seed)
        exp.save()
        notify(exp, "[%s] seed %d finished for every row; results table attached" % (s["name"], seed), png)
    return res


def matrix_report(exp, reason):
    """Deterministic final report for matrix mode: no model writes the conclusions of a fixed study."""
    st = exp.state
    png = write_table(exp)
    failed = ["%s: %s %s" % (k, c.get("status"), c.get("detail", "")) for k, c in st["cells"].items()
              if c.get("status") not in ("ok", None)]
    todo = ["%s|%s" % c for c in matrix_cells(exp) if "%s|%s" % c not in st["cells"]
            or st["cells"]["%s|%s" % c].get("status") == "retry"]
    text = ["# %s" % exp.spec["name"], "", "Stopped: %s" % reason, "",
            (read_text_or_none(exp.dir / "results_table.md") or ""), "",
            "Failed cells: %s" % ("; ".join(failed) if failed else "none"),
            "Cells not run: %s" % (", ".join(todo) if todo else "none"), "",
            "Per-cell values: matrix.tsv and runs/<row>/seed_<seed>/metrics.json."]
    (exp.dir / "REPORT.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    notify(exp, "[%s] finished: %s. Results table attached; REPORT.md has the details." % (exp.spec["name"], reason), png)


def matrix_pending(exp):
    """Cells that have not run yet or are waiting for their retry."""
    cells = exp.state["cells"]
    return [(r, sd) for r, sd in matrix_cells(exp)
            if cells.get("%s|%s" % (r, sd)) is None or cells["%s|%s" % (r, sd)].get("status") == "retry"]


def next_matrix_action(exp):
    st = exp.state
    if st["status"] in ("done", "halted"):
        return {"action": st["status"] if st["status"] == "done" else "halt", "reason": st.get("stop_reason")}
    reason = None
    if (exp.dir / "STOP").exists():
        reason = "stopped by a human (STOP file)"
    elif exp.spec["budget"].get("max_hours") and st["active_s"] >= exp.spec["budget"]["max_hours"] * 3600:
        reason = "max_hours reached"
    held = held_rows(exp)
    pending = matrix_pending(exp)
    runnable = [(r, sd) for r, sd in pending if r not in held]
    if reason is None and not pending:
        reason = "every cell has run"
    if reason is None and not runnable:
        return {"action": "wait", "reason": "only held rows remain: %s (edit HOLD to release)"
                % ", ".join(sorted({r for r, _ in pending}))}
    if reason:
        st["status"], st["stop_reason"] = "done", reason
        exp.save()
        matrix_report(exp, reason)
        return {"action": "done", "reason": reason}
    row, seed = runnable[0]
    return {"action": "measure", "row": row, "seed": seed}


# ------------------------------------------------------- files agents write

def read_plan(exp, n):
    p = exp.run_dir(n) / "plan.md"
    if not p.is_file():
        return None, ""
    text = p.read_text(encoding="utf-8", errors="replace")
    idea = keyword(text, "IDEA")
    if not idea:
        return None, "plan.md has no 'IDEA:' line"
    url = re.search(r"https?://[^\s)>\]]+", section(text, "Grounding"))
    if not url:
        return None, "the '## Grounding' section of plan.md names no source URL"
    if len(section(text, "Change").strip()) < 10:
        return None, "the '## Change' section of plan.md is empty"
    return {"idea": idea, "source": url.group(0)}, ""


def read_report(exp, n):
    p = exp.run_dir(n) / "report.md"
    if not p.is_file():
        return None
    return keyword(p.read_text(encoding="utf-8", errors="replace"), "STATUS",
                   ["IMPLEMENTED", "REJECTED_UNGROUNDED", "FAILED"])


def read_verdict(exp, n):
    p = exp.run_dir(n) / "verdict.md"
    if not p.is_file():
        return None
    return keyword(p.read_text(encoding="utf-8", errors="replace"), "DECISION", ["KEEP", "DISCARD"])


def count_ready(exp):
    text = read_text_or_none(exp.dir / "ideas.md") or ""
    return len(re.findall(r"^#+\s*\[READY\]", text, re.M))


# ------------------------------------------------------------- state machine

def target_reached(exp):
    m, best = exp.spec["metric"], exp.state.get("best")
    if m.get("target") is None or best is None:
        return False
    return best >= m["target"] if m["direction"] == "maximize" else best <= m["target"]


def stop_reason(exp):
    st, b = exp.state, exp.spec["budget"]
    if (exp.dir / "STOP").exists():
        return "stopped by a human (STOP file)"
    if target_reached(exp):
        return "target reached"
    if b.get("max_iterations") and st["iter"] >= b["max_iterations"]:
        return "max_iterations reached"
    if b.get("max_hours") and st["active_s"] >= b["max_hours"] * 3600:
        return "max_hours reached"
    if b.get("patience") and st["non_improve"] >= b["patience"]:
        return "plateau: %d iterations in a row without improvement" % st["non_improve"]
    return None


def stop_rules(exp):
    st, b, m = exp.state, exp.spec["budget"], exp.spec["metric"]
    out = []
    if b.get("max_iterations"):
        out.append("max_iterations=%s (completed %d)" % (b["max_iterations"], st["iter"]))
    if b.get("max_hours"):
        out.append("max_hours=%s (used %.2f)" % (b["max_hours"], st["active_s"] / 3600))
    if b.get("patience"):
        out.append("patience=%s iterations in a row without improvement (now %d)"
                   % (b["patience"], st["non_improve"]))
    if m.get("target") is not None:
        out.append("target: %s %s %s" % (m["name"], ">=" if m["direction"] == "maximize" else "<=",
                                         m["target"]))
    return out or ["none: the loop runs until a human creates the STOP file"]


def build_prompt(exp, role, task, n, note=""):
    s, st, m = exp.spec, exp.state, exp.spec["metric"]
    ref = SKILL_DIR / "references"
    lines = [
        "# Your turn",
        "",
        "You are one step of an unattended experiment loop. Nobody reads your chat reply and nobody "
        "can answer a question: only the files you write count. Do the task, write the required "
        "files, then end your turn.",
        "",
        "- Role: %s" % role,
        "- Task: %s" % task,
        "- Iteration: %03d" % n,
        "- Project root (your working directory): %s" % exp.root,
        "- Experiment directory: %s" % exp.dir,
        "- This iteration's files: %s" % exp.run_dir(n),
        "- Next iteration's directory: %s" % exp.run_dir(n + 1),
        "- Metric: %s, %s, min_delta %s" % (m["name"], m["direction"], s["min_delta"]),
        "- Baseline: %s | best so far: %s (iteration %s)" % (fmt(st.get("baseline")), fmt(st.get("best")),
                                                          st.get("best_iter")),
        "- Iterations completed: %d | in a row without improvement: %d | pivot_after: %s"
        % (st["iter"], st["non_improve"], s["pivot_after"]),
        "- The loop stops when: %s" % "; ".join(stop_rules(exp)),
        "- READY ideas in ideas.md: %d (keep at least %d)" % (count_ready(exp), s["min_ready_ideas"]),
        "- Write journal, verdict and report prose in language: %s" % s["language"],
    ]
    if note:
        lines.append("- Note from the driver: %s" % note)
    return "\n\n".join([(ref / ("%s.md" % role)).read_text(encoding="utf-8"),
                        (ref / "grounding.md").read_text(encoding="utf-8"),
                        (SKILL_DIR / "AGENTS.md").read_text(encoding="utf-8"),
                        "\n".join(lines)]) + "\n"


def agent_task(exp, role, task, n, note=""):
    logs = exp.dir / "agent_logs"
    logs.mkdir(exist_ok=True)
    pf = logs / ("%03d.%s.%s.%s.prompt.md" % (n, role, task, time.strftime("%H%M%S")))
    pf.write_text(build_prompt(exp, role, task, n, note), encoding="utf-8")
    return {"action": "agent", "role": role, "task": task, "iter": n, "prompt_file": str(pf), "note": note}


def halt(exp, reason):
    exp.state["status"], exp.state["stop_reason"] = "halted", reason
    exp.save()
    escalate(exp, "block", "the loop halted: %s" % reason, ["fix the cause, then resume"])
    return {"action": "halt", "reason": reason}


def finalize(exp, reason):
    s, st = exp.spec, exp.state
    if st["status"] != "finalizing":
        exp.restore_scope()  # leave the project in its best-validation state,
        exp.restore_artifacts()  # with the checkpoint that goes with it
        st["status"], st["stop_reason"] = "finalizing", reason
        exp.save()
        if s.get("final_test_cmd") and not (exp.dir / "final_test.log").exists():
            log("running final_test_cmd once on the best state")
            status, detail, _ = run_cmd(exp, s["final_test_cmd"], exp.dir / "final_test.log",
                                        s["run_timeout_s"])
            if status != "ok":
                log("final_test_cmd: %s %s" % (status, detail))
    if (exp.dir / "REPORT.md").exists():
        st["status"] = "done"
        exp.save()
        return {"action": "done", "reason": reason}
    return agent_task(exp, "director", "final", st["iter"])


def close_iteration(exp, n, decision):
    st = exp.state
    mt = read_json(exp.run_dir(n) / "metrics.json")
    plan = read_plan(exp, n)[0] or {}
    note = ""
    if mt["status"] != "ok":
        outcome = mt["status"]
    elif decision == "KEEP" and not mt.get("improved"):
        outcome, note = "discard", "KEEP overridden: the result did not beat the best by min_delta"
    elif decision == "KEEP" and exp.tree_hash() != mt.get("tree"):
        outcome, note = "discard", "KEEP overridden: files in scope changed after the measurement"
    else:
        outcome = "keep" if decision == "KEEP" else "discard"
    best_before = st.get("best")
    if outcome == "keep":
        exp.snapshot_scope()
        exp.save_artifacts()
        st["best"], st["best_iter"], st["non_improve"] = mt["value"], n, 0
    else:
        exp.restore_scope()
        st["non_improve"] += 1
    if mt.get("tree") and mt["tree"] not in st["tried"]:
        st["tried"].append(mt["tree"])
    delta = None if mt.get("value") is None or best_before is None else mt["value"] - best_before
    append_row(exp, {"iter": n, "time": now(), "status": outcome, "metric": mt.get("value"),
                     "best": st["best"], "delta": delta, "duration_s": mt.get("duration_s"),
                     "idea": plan.get("idea", ""), "source": plan.get("source", "")})
    st["iter"], st["phase"], st["attempts"] = n, "plan", {}
    st["last"] = {"iter": n, "outcome": outcome, "note": note or mt.get("detail", "")}
    exp.save()
    log("iteration %03d closed: %s (metric %s, best %s) %s"
        % (n, outcome, fmt(mt.get("value")), fmt(st["best"]), note))


def next_action(exp):
    """Advance as far as possible without an agent or a measurement; return what is needed next."""
    st = exp.state
    if st is not None and exp.spec["mode"] == "matrix":
        return next_matrix_action(exp)
    if st is None or st["status"] == "initialized":
        raise SpecError("run init and baseline first")
    if exp.spec["min_delta"] is None:
        raise SpecError("min_delta is not set. Write the value suggested by `baseline` (or a cited one) "
                        "into experiment.json; without it any gain, however small, would be kept")
    while True:
        if st["status"] == "done":
            return {"action": "done", "reason": st.get("stop_reason")}
        if st["status"] == "halted":
            return {"action": "halt", "reason": st.get("stop_reason")}
        if st["status"] == "finalizing":
            return finalize(exp, st["stop_reason"])
        n = st["iter"] + 1
        run_dir = exp.run_dir(n)
        run_dir.mkdir(parents=True, exist_ok=True)
        phase = st["phase"]

        if phase == "plan":
            reason = stop_reason(exp)
            if reason:
                return finalize(exp, reason)
            plan, why = read_plan(exp, n)
            if plan is None:
                return agent_task(exp, "director", "plan", n,
                                  "your previous plan.md was rejected: %s" % why if why else "")
            exp.set_phase("perform")

        elif phase == "perform":
            status = read_report(exp, n)
            if status is None:
                exp.restore_scope()  # every attempt starts from the best state
                return agent_task(exp, "experimenter", "perform", n)
            patch = exp.write_patch(n)
            changed = exp.check_frozen()
            outcome = detail = None
            if changed:
                outcome, detail = "violation", "frozen files were modified (now restored): %s" % ", ".join(changed)
            elif status != "IMPLEMENTED":
                outcome = "rejected" if status == "REJECTED_UNGROUNDED" else "failed"
                detail = "the experimenter reported %s; see report.md" % status
            elif not patch.strip():
                outcome, detail = "failed", "the experimenter reported IMPLEMENTED but nothing in scope changed"
            elif exp.tree_hash() in st["tried"]:
                outcome, detail = "duplicate", "this exact state of the scope was measured before"
            if outcome:
                write_json(run_dir / "metrics.json",
                           {"iter": n, "status": outcome, "detail": detail, "values": [], "value": None,
                            "improved": False, "best_before": st.get("best"), "duration_s": 0})
                exp.restore_scope()
                exp.set_phase("judge")
            else:
                exp.set_phase("measure")

        elif phase == "measure":
            if not (run_dir / "metrics.json").is_file():
                return {"action": "measure", "iter": n}
            exp.set_phase("judge")

        elif phase == "judge":
            decision = read_verdict(exp, n)
            if decision is None:
                return agent_task(exp, "director", "judge", n)
            close_iteration(exp, n, decision)

        else:
            raise SpecError("unknown phase %r in state.json" % phase)


# --------------------------------------------------------------- agent calls

def call_agent(exp, act):
    """Run one headless agent turn. Returns the exit code (0 = the command itself worked)."""
    cfg = exp.spec["agents"].get(act["role"]) or {}
    if not cfg.get("cmd"):
        raise Halt("experiment.json has no agents.%s.cmd" % act["role"])
    pf = Path(act["prompt_file"])
    argv = shlex.split(cfg["cmd"]) + [pf.read_text(encoding="utf-8")]
    logp = Path(str(pf).replace(".prompt.md", ".log"))
    with open(logp, "wb") as f:
        try:
            proc = subprocess.Popen(argv, cwd=str(exp.root), stdin=subprocess.DEVNULL, stdout=f,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        except FileNotFoundError:
            raise Halt("agent command not found: %s" % argv[0])
        LIVE.append(proc)
        try:
            rc = proc.wait(timeout=cfg.get("timeout_s", 3600))
        except subprocess.TimeoutExpired:
            kill_group(proc)
            rc = -1
        finally:
            LIVE.remove(proc)
    return rc


def fallback(exp, act):
    """An agent produced no usable file in MAX_ATTEMPTS tries: take the safe default and move on."""
    n, task = act["iter"], act["task"]
    by = "(written by the driver: no usable output after %d attempts)" % MAX_ATTEMPTS
    if task == "plan":
        return halt(exp, "the director produced no valid, grounded plan for iteration %03d in %d attempts"
                    % (n, MAX_ATTEMPTS))
    if task == "perform":
        (exp.run_dir(n) / "report.md").write_text("STATUS: FAILED\n\n%s\n" % by, encoding="utf-8")
    elif task == "judge":
        (exp.run_dir(n) / "verdict.md").write_text("DECISION: DISCARD\n\n%s\n" % by, encoding="utf-8")
    elif task == "final":
        rows = read_text_or_none(exp.dir / "results.tsv") or ""
        (exp.dir / "REPORT.md").write_text(
            "# %s\n\nStopped: %s\n\nBaseline %s, best %s (iteration %s).\n\n%s\n\n```\n%s```\n"
            % (exp.spec["name"], exp.state.get("stop_reason"), fmt(exp.state.get("baseline")),
               fmt(exp.state.get("best")), exp.state.get("best_iter"), by, rows), encoding="utf-8")
    return None


# ------------------------------------------------------------------ commands

def cmd_init(exp, args):
    if exp.state is not None:
        raise SpecError("already initialised. To start over, delete everything in %s except "
                        "experiment.json" % exp.dir)
    for sub in ("runs", "best", "agent_logs"):
        (exp.dir / sub).mkdir(parents=True, exist_ok=True)
    frozen = exp.init_frozen()
    if exp.spec["mode"] == "matrix":
        exp.state = {"status": "ready", "stop_reason": None, "mode": "matrix", "cells": {}, "active_s": 0.0,
                     "seeds_reported": [], "created": now()}
        exp.save()
        print("initialised %s (matrix mode)" % exp.dir)
        print("  cells:  %d (%d rows x %d seeds, seed-major)" % (len(matrix_cells(exp)),
              len(exp.spec["matrix"]["rows"]), len(exp.spec["matrix"]["seeds"])))
        print("  frozen: %d files watched" % len(frozen))
        print("next: driver.py run %s (or next/measure in-session)" % exp.dir)
        return
    exp.snapshot_scope()
    exp.state = {"status": "initialized", "stop_reason": None, "iter": 0, "phase": "plan",
                 "baseline": None, "baseline_values": [], "best": None, "best_iter": 0,
                 "non_improve": 0, "active_s": 0.0, "attempts": {}, "tried": [], "run_s": None,
                 "created": now()}
    exp.save()
    for name, text in (("ideas.md", "# Idea bank\n\n"), ("journal.md", "# Journal\n\n")):
        if not (exp.dir / name).exists():
            (exp.dir / name).write_text(text, encoding="utf-8")
    print("initialised %s" % exp.dir)
    print("  scope:  %d files snapshotted as the starting best state" % len(exp.scope_files()))
    print("  frozen: %d files watched" % len(frozen))
    print("next: driver.py baseline %s" % exp.dir)


def cmd_baseline(exp, args):
    st, s = exp.state, exp.spec
    if st is None:
        raise SpecError("run init first")
    if s["mode"] == "matrix":
        raise SpecError("matrix mode has no baseline step: the control rows are part of the matrix")
    if st["iter"] > 0:
        raise SpecError("the loop has already started; the baseline cannot be re-measured")
    exp.restore_scope()
    res = measure(exp, 0, repeats=s["baseline_repeats"], baseline=True)
    if res["status"] != "ok":
        print("baseline failed: %s %s\nsee %s" % (res["status"], res["detail"], exp.run_dir(0)))
        return 1
    vals = res["values"]
    spread = statistics.stdev(vals) if len(vals) > 1 else None
    st.update({"baseline": res["value"], "baseline_values": vals, "baseline_std": spread,
               "best": res["value"], "best_iter": 0, "tried": [res["tree"]],
               "run_s": res["duration_s"] / len(vals), "status": "ready"})
    exp.save()
    exp.save_artifacts()
    results = exp.dir / "results.tsv"
    if results.exists():
        results.unlink()
    append_row(exp, {"iter": 0, "time": now(), "status": "baseline", "metric": res["value"],
                     "best": res["value"], "duration_s": res["duration_s"], "idea": "baseline"})
    print(json.dumps({"baseline": res["value"], "values": vals, "std": spread,
                      "seconds_per_run": round(st["run_s"], 1), "extra": res["extra"],
                      # two standard errors of the difference between the baseline mean and a
                      # later measurement made of `repeats` runs
                      "suggested_min_delta": None if spread is None
                      else 2 * spread * (1.0 / len(vals) + 1.0 / s["repeats"]) ** 0.5,
                      "current_min_delta": s["min_delta"]}, indent=2))
    return 0


def cmd_doctor(exp, args):
    ok = True
    print("python      : %s" % sys.version.split()[0])
    print("tmux        : %s" % (shutil.which("tmux") or "not found (use nohup/screen or in-session mode)"))
    probes = {
        "director": ("Open https://proceedings.mlr.press/ with your web fetch tool.",
                     "exactly one line: WEB_OK if the page loaded, WEB_FAIL if it did not"),
        "experimenter": ("Run the shell command `echo ar-doctor`. Then open https://proceedings.mlr.press/ "
                         "with your web fetch tool.",
                         "two lines: SHELL_OK or SHELL_FAIL, then WEB_OK or WEB_FAIL"),
    }
    (exp.dir / "agent_logs").mkdir(exist_ok=True)
    for role, (steps, content) in probes.items():
        out = exp.dir / ("doctor.%s.txt" % role)
        if out.exists():
            out.unlink()
        pf = exp.dir / "agent_logs" / ("doctor.%s.prompt.md" % role)
        pf.write_text("Connectivity check for an automated experiment loop. Do exactly this and "
                      "nothing else. %s Then write the file %s containing %s.\n" % (steps, out, content),
                      encoding="utf-8")
        try:
            rc = call_agent(exp, {"role": role, "prompt_file": str(pf)})
        except Halt as e:
            print("%-12s: FAIL (%s)" % (role, e))
            ok = False
            continue
        text = (read_text_or_none(out) or "").strip().replace("\n", " ")
        good = rc == 0 and "FAIL" not in text and "WEB_OK" in text and (role == "director" or "SHELL_OK" in text)
        ok = ok and good
        print("%-12s: %s (exit %s; wrote %r; log %s)" % (role, "ok" if good else "FAIL", rc, text,
                                                        str(pf).replace(".prompt.md", ".log")))
    return 0 if ok else 1


def cmd_next(exp, args):
    if exp.state and exp.state["status"] == "ready":
        exp.state["status"] = "running"
    print(json.dumps(next_action(exp), indent=2))
    return 0


def cmd_measure(exp, args):
    st = exp.state
    act = next_action(exp)
    if act["action"] != "measure":
        raise SpecError("nothing to measure now; next action is: %s" % json.dumps(act))
    if exp.spec["mode"] == "matrix":
        res = measure_cell(exp, act["row"], act["seed"])
        print(json.dumps({k: res[k] for k in ("row", "seed", "status", "detail", "value", "extra", "duration_s")},
                         indent=2))
        return 0
    t0 = time.time()
    res = measure(exp, act["iter"])
    st["active_s"] += time.time() - t0
    exp.save()
    print(json.dumps({k: res[k] for k in ("iter", "status", "detail", "value", "values", "improved",
                                          "best_before", "delta", "extra", "duration_s")}, indent=2))
    return 0


def cmd_status(exp, args):
    st, s = exp.state, exp.spec
    if st is None:
        print("not initialised")
        return 1
    print("%s: %s%s" % (s["name"], st["status"], " (%s)" % st["stop_reason"] if st.get("stop_reason") else ""))
    if s["mode"] == "matrix":
        cells = st["cells"]
        done = sum(1 for c in cells.values() if c.get("status") == "ok")
        bad = sum(1 for c in cells.values() if c.get("status") not in ("ok", "retry"))
        print("  matrix   : %d of %d cells ok, %d failed, held rows: %s, %.2f h of runs"
              % (done, len(matrix_cells(exp)), bad, ", ".join(sorted(held_rows(exp))) or "none",
                 st["active_s"] / 3600))
        write_table(exp)
        print((read_text_or_none(exp.dir / "results_table.md") or "").rstrip())
        return 0
    print("  metric   : %s (%s), min_delta %s" % (s["metric"]["name"], s["metric"]["direction"], s["min_delta"]))
    print("  baseline : %s" % fmt(st.get("baseline")))
    print("  best     : %s at iteration %s" % (fmt(st.get("best")), st.get("best_iter")))
    print("  progress : %d iterations done, working on %03d (%s), %d in a row without improvement, %.2f h used"
          % (st["iter"], st["iter"] + 1, st["phase"], st["non_improve"], st["active_s"] / 3600))
    print("  stops    : %s" % "; ".join(stop_rules(exp)))
    print("  ideas    : %d READY" % count_ready(exp))
    rows = (read_text_or_none(exp.dir / "results.tsv") or "").splitlines()
    for line in rows[:1] + rows[1:][-10:]:
        print("  " + line)
    return 0


def cmd_run(exp, args):
    st = exp.state
    if st is None or st["status"] == "initialized":
        raise SpecError("run init and baseline first")
    lock = exp.dir / "driver.pid"
    if lock.exists():
        try:
            pid = int(lock.read_text())
            os.kill(pid, 0)
            raise SpecError("another driver (pid %d) is already running this experiment" % pid)
        except (ValueError, ProcessLookupError):
            pass
    lock.write_text(str(os.getpid()))

    def on_signal(signum, _frame):
        log("signal %d: stopping children; state is saved and `run` resumes from here" % signum)
        for p in list(LIVE):
            kill_group(p)
        if lock.exists():
            lock.unlink()
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    if st["status"] == "halted":
        log("resuming after halt: %s" % st.get("stop_reason"))
        st.update({"status": "running", "stop_reason": None, "attempts": {}})
    elif exp.spec["mode"] == "matrix":
        if st["status"] == "done" and not (exp.dir / "STOP").exists() and matrix_pending(exp):
            log("cells remain (STOP removed or cells reset): resuming")
            st.update({"status": "running", "stop_reason": None})
    elif st["status"] in ("done", "finalizing") and stop_reason(exp) is None:
        log("the stop condition no longer holds (budget raised or STOP removed): resuming")
        for name in ("REPORT.md", "final_test.log"):
            if (exp.dir / name).exists():
                os.replace(exp.dir / name, exp.dir / (name + ".prev"))
        st.update({"status": "running", "stop_reason": None, "attempts": {}})
    elif st["status"] == "ready":
        st["status"] = "running"
    exp.save()
    log("driver started for %s (%s)" % (exp.spec["name"], "matrix mode" if exp.spec["mode"] == "matrix"
                                         else "best %s" % fmt(st.get("best"))))
    failures = 0
    try:
        while True:
            t0 = time.time()
            try:
                act = next_action(exp)
                if act["action"] == "wait":
                    log(act["reason"])
                    time.sleep(int(os.environ.get("AR_WAIT_S", "300")))
                    continue
                if act["action"] == "measure" and "row" in act:
                    log("cell %s seed %s: measuring" % (act["row"], act["seed"]))
                    res = measure_cell(exp, act["row"], act["seed"])
                    log("cell %s seed %s: %s value=%s %s" % (act["row"], act["seed"], res["status"],
                                                            fmt(res["value"]), res["detail"]))
                elif act["action"] == "measure":
                    n = act["iter"]
                    log("iteration %03d: measuring" % n)
                    res = measure(exp, n)
                    log("iteration %03d: %s value=%s improved=%s %s"
                        % (n, res["status"], fmt(res["value"]), res["improved"], res["detail"]))
                elif act["action"] == "agent":
                    key = "%d:%s:%s" % (act["iter"], act["role"], act["task"])
                    if st["attempts"].get(key, 0) >= MAX_ATTEMPTS:
                        log("iteration %03d: %s/%s gave no usable output; using the safe default"
                            % (act["iter"], act["role"], act["task"]))
                        act = fallback(exp, act) or act
                    else:
                        log("iteration %03d: %s -> %s" % (act["iter"], act["role"], act["task"]))
                        rc = call_agent(exp, act)
                        if rc == 0:
                            failures = 0
                            st["attempts"][key] = st["attempts"].get(key, 0) + 1
                        else:
                            wait = BACKOFF_S[min(failures, len(BACKOFF_S) - 1)]
                            failures += 1
                            log("agent command failed (exit %s); retrying in %ds" % (rc, wait))
                            time.sleep(wait)
            except Halt as e:
                act = halt(exp, str(e))
            st["active_s"] += time.time() - t0
            exp.save()
            if act["action"] in ("done", "halt"):
                log("%s: %s" % ("finished" if act["action"] == "done" else "HALTED", act["reason"]))
                return 0 if act["action"] == "done" else 2
    finally:
        if lock.exists():
            lock.unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name, fn in (("init", cmd_init), ("baseline", cmd_baseline), ("doctor", cmd_doctor),
                     ("run", cmd_run), ("next", cmd_next), ("measure", cmd_measure), ("status", cmd_status)):
        p = sub.add_parser(name)
        p.add_argument("exp_dir", help="<project>/.autoresearch/<name>")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    try:
        return args.fn(Exp(args.exp_dir), args) or 0
    except SpecError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
