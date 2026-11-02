#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Importer fuzz runner.

Multiprocessing over --workers cores. Cycles through every (format, property) task in harness.TASKS.
Each task call runs one Hypothesis engine pass (see harness.run_one). Failures are deduped by
(exception type, top countersign_shadow/ frame); the first example of each unique failure is saved to
fuzz/findings/<n>-<key>/ (input file + traceback.txt + meta.json). Runs until --deadline-seconds, then
writes fuzz/REPORT.md. Also re-writes REPORT.md periodically so a kill/OOM mid-run still leaves a report.

Usage: python3 -m fuzz.runner --deadline-seconds 21600 --workers 22 [--out-dir fuzz]
"""
from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OUTER_BACKSTOP = 90.0  # seconds; generous on purpose -- the real per-case guarantee is the 5s SIGALRM
# inside each example (timeout.py). This is only a defense-in-depth catch for a worker truly wedged
# past Python's reach (e.g. stuck in a C call that never checks for signals); a slow-but-finite
# Hypothesis shrink session (many examples, each well under 5s) must not trip it.


def _ext_for(task: str) -> str:
    if "pdf" in task:
        return ".pdf"
    if "msg" in task:
        return ".msg"
    if "camt053" in task:
        return ".xml"
    if task.startswith("checks"):
        return ".txt"
    return ".txt"


def _save_finding(out_dir: Path, idx: int, result: dict) -> Path:
    key = f"{idx:03d}-{result['exc_type']}"
    folder = out_dir / "findings" / key
    folder.mkdir(parents=True, exist_ok=True)
    content = result.get("content") or b""
    if len(content) > 1_000_000:
        content = content[:1_000_000]
    (folder / ("input" + _ext_for(result["task"]))).write_bytes(content)
    (folder / "traceback.txt").write_text(result.get("traceback", ""))
    (folder / "meta.json").write_text(json.dumps({
        "task": result["task"], "exc_type": result["exc_type"], "exc_msg": result["exc_msg"],
        "top_frame": result["top_frame"], "note": result.get("note", ""), "is_hang": result.get("is_hang", False),
    }, indent=2))
    return folder


def write_report(out_dir: Path, started: float, counts: Counter, unique: dict, total_calls: int) -> None:
    lines = ["# Importer fuzz -- REPORT", "",
              f"Started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started))}",
              f"Elapsed: {time.time() - started:.0f}s",
              f"Hypothesis-engine calls made: {total_calls}", "",
              "## Counts per task (one call = up to max_examples Hypothesis draws)", ""]
    for task in sorted(counts):
        ok, fail = counts[task].get("ok", 0), counts[task].get("fail", 0)
        lines.append(f"- `{task}`: {ok} clean engine passes, {fail} failing passes")
    lines += ["", f"## Unique failures: {len(unique)}", ""]
    if not unique:
        lines.append("None found. (Or the deadline hit before any; check the counts above and logs/.)")
    for i, (key, info) in enumerate(sorted(unique.items(), key=lambda kv: -kv[1]["count"])):
        lines += [f"### {i + 1}. `{key[0]}` at `{key[1]}`  (seen {info['count']}x)", "",
                  f"- First task: `{info['task']}`",
                  f"- Hang (per-case timeout exceeded): {info.get('is_hang', False)}",
                  f"- Message: `{info['exc_msg'][:300]}`",
                  f"- Reproducer: `{info['folder']}`", ""]
    (out_dir / "REPORT.md").write_text("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deadline-seconds", type=float, default=6 * 3600)
    ap.add_argument("--workers", type=int, default=min(22, mp.cpu_count()))
    ap.add_argument("--out-dir", type=Path, default=REPO / "fuzz")
    args = ap.parse_args()

    from fuzz import harness  # imported after sys.path fix; also only needed in the parent for TASK_NAMES

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "findings").mkdir(exist_ok=True)

    started = time.time()
    deadline = time.monotonic() + args.deadline_seconds
    task_cycle = itertools.cycle(harness.TASK_NAMES)
    counts: dict[str, dict[str, int]] = {t: {"ok": 0, "fail": 0} for t in harness.TASK_NAMES}
    unique: dict[tuple, dict] = {}
    total_calls = 0
    finding_idx = 0
    last_report = 0.0

    ctx = mp.get_context("fork")
    pool = ctx.Pool(args.workers)
    pending: list[tuple] = []
    try:
        while time.monotonic() < deadline or pending:
            while len(pending) < args.workers and time.monotonic() < deadline:
                task = next(task_cycle)
                pending.append((pool.apply_async(harness.run_one, (task,)), task, time.monotonic()))

            still_pending = []
            progressed = False
            for ar, task, t0 in pending:
                if ar.ready():
                    progressed = True
                    total_calls += 1
                    try:
                        result = ar.get()
                    except Exception as exc:  # the worker process itself died (OOM, killed, ...)
                        result = {"task": task, "ok": False, "exc_type": type(exc).__name__,
                                  "exc_msg": str(exc), "top_frame": "?", "traceback": repr(exc),
                                  "content": b"", "note": "worker process died", "is_hang": False}
                    if result["ok"]:
                        counts[task]["ok"] += 1
                    else:
                        counts[task]["fail"] += 1
                        key = (result["exc_type"], result["top_frame"])
                        if key in unique:
                            unique[key]["count"] += 1
                        else:
                            finding_idx += 1
                            folder = _save_finding(out_dir, finding_idx, result)
                            unique[key] = {"count": 1, "task": task, "exc_msg": result["exc_msg"],
                                           "is_hang": result.get("is_hang", False), "folder": str(folder)}
                elif time.monotonic() - t0 > OUTER_BACKSTOP:
                    progressed = True
                    print(f"[runner] backstop timeout on {task}, recreating the pool", file=sys.stderr, flush=True)
                    key = ("HangBackstop", task)
                    if key in unique:
                        unique[key]["count"] += 1
                    else:
                        finding_idx += 1
                        folder = _save_finding(out_dir, finding_idx, {
                            "task": task, "exc_type": "HangBackstop", "exc_msg": f">{OUTER_BACKSTOP}s, pool killed",
                            "top_frame": task, "traceback": "", "content": b"", "note": "outer backstop",
                            "is_hang": True})
                        unique[key] = {"count": 1, "task": task, "exc_msg": "outer backstop timeout",
                                       "is_hang": True, "folder": str(folder)}
                    pool.terminate()
                    pool.join()
                    pool = ctx.Pool(args.workers)
                    pending = []
                    still_pending = []
                    break
                else:
                    still_pending.append((ar, task, t0))
            pending = still_pending
            if not progressed:
                time.sleep(0.05)
            if time.time() - last_report > 60:
                write_report(out_dir, started, counts, unique, total_calls)
                last_report = time.time()
    finally:
        pool.terminate()
        pool.join()
        write_report(out_dir, started, counts, unique, total_calls)
        print(f"[runner] done: {total_calls} engine calls, {len(unique)} unique failures", file=sys.stderr)


if __name__ == "__main__":
    main()
