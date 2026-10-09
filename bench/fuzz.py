"""Robustness and speed of parse(): random fragments, pathological inputs, and
optionally a corpus of real commands. TEXT ONLY: commands are parsed, never
executed.

    python bench/fuzz.py [corpus.jsonl]      (a line per command: {"cmd": "..."})

Each input runs in a child process with a hard deadline, so a hang is named,
not just waited on. A crash (any exception), a hang, or any one input taking
over SLOW seconds fails the run.
"""
import json
import multiprocessing as mp
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bashtree import nested, parse, unwrap  # noqa: E402

DEADLINE = 60.0                                     # per batch of inputs
SLOW = 5.0                                          # per input


def _child(cmds, q):
    for i, c in enumerate(cmds):
        t = time.perf_counter()
        try:
            out = parse(c).status
        except Exception as exc:  # noqa: BLE001 - sent to the parent and reported as a crash
            out = f"CRASH {type(exc).__name__}: {exc}"
        q.put((i, out, time.perf_counter() - t))


def run(cmds):
    """[(status, seconds)], "HANG" for any input not reached by the deadline."""
    q = mp.Queue()
    p = mp.Process(target=_child, args=(cmds, q))
    p.start()
    got, end = {}, time.monotonic() + DEADLINE
    while len(got) < len(cmds) and time.monotonic() < end:
        try:
            i, o, dt = q.get(timeout=max(0.01, end - time.monotonic()))
        except Exception:  # noqa: BLE001 - queue.Empty at the deadline: the rest are hangs
            break
        got[i] = (o, dt)
    if p.is_alive():
        p.kill()
    p.join()
    return [got.get(i, ("HANG", None)) for i in range(len(cmds))]


def fuzz_helpers(n=100_000):
    """unwrap() and nested() on random argv: no exception, and every text a str."""
    random.seed(11)
    progs = ["env", "sudo", "timeout", "nice", "time", "command", "exec", "!", "/usr/bin/env", "bash", "sh",
             "fish", "su", "runuser", "eval", "screen", "tmux", "xargs", "find", "script", "csh", "rm"]
    args = ["", "-", "--", "-c", "-S", "-u", "-i", "-o", "-lc", "-iS", "--split-string", "--split=", "-C",
            "x", "rm x", "A=1", "a-b=1", "-ujenkins", "-e", "-h", "-hbox", "-k", "-l", "-R", "-X", "-dmS",
            "new-window", "neww", "send-keys", "Enter", "C-m", "Tab", "!!", ";", "a;", "\\;", "-t", "if-shell",
            "run", "bind-key", "#(x)", "-exec", "+", "+c", "rm\vx", "'q", '"a b"', "$x", "-n", "5", "-10"]
    bad = 0
    for _ in range(n):
        argv = [random.choice(progs)] + [random.choice(args) for _ in range(random.randint(0, 8))]
        try:
            u = unwrap(argv)
            for x in nested(u.words) + nested(argv):
                assert isinstance(x.text, str) and isinstance(x.words, tuple), x
        except Exception as exc:  # noqa: BLE001 - counted and printed, the run fails
            bad += 1
            print(f"CRASH {type(exc).__name__}: {exc}  on {argv!r}")
    print(f"helpers: {n:,} random argv, {bad} crashes")
    return bad


def main():
    random.seed(7)
    frag = ["echo ", "cmd ", " x", "; ", "&& ", "|| ", "| ", "& ", "$(", ")", "(", "`", "{ ", "} ",
            "f() ", "case $x in ", "esac", "in ", ";; ", "if ", "then ", "fi", "done", "do ",
            "while ", "[[ ", "]] ", "'", '"', "\\", "\n", "<<EOF\n", "EOF\n", "> f ", "2>&1 ",
            "#", "$((", "${", "<(", ">&", "! ", "X=1 ", "export ", "$'\\x41'"]
    fuzz = ["".join(random.choice(frag) for _ in range(random.randint(1, 30))) for _ in range(20000)]
    bad = 0
    for start in range(0, len(fuzz), 500):
        part = fuzz[start:start + 500]
        for c, (o, dt) in zip(part, run(part)):
            if o == "HANG" or o.startswith("CRASH") or dt > SLOW:
                bad += 1
                print(f"{o[:80]}  on {c[:100]!r}")
    print(f"fuzz: 20,000 inputs, {bad} crashes or hangs")
    slow = {"3000 x \"$(": '"$(' * 3000 + "x", "60000 x (": "(" * 60000 + "x",
            "60000 x `": "`" * 60000 + "x", "16000 x env": "env " * 16000 + "x",
            "256 heredocs": "x <<a " * 256 + "\n" + "a\n" * 256, "1000 x a=(": "a=(" * 1000 + "x",
            "300 heredocs (capped)": "x <<a " * 300, "1 MB word": "x " + "a" * 1_000_000,
            "500k x a; (capped)": "a;" * 500_000, "2000 pipe stages": "a|" * 2000 + "a",
            "2M x a| (capped)": "a|" * 2_000_000, "4 MB of a; (capped)": "a;" * 2_000_000,
            "250 chained &&": " && ".join(["a"] * 250), "5000 chained && (capped)": " && ".join(["a"] * 5000),
            "250 nested $( )": "$(" * 250 + "x" + ")" * 250, "3000 nested $( ) (capped)": "$(" * 3000 + "x" + ")" * 3000,
            "250 nested if": "if a; then " * 250 + "b" + "; fi" * 250,
            "5000 x )) ": "))" * 5000, "40000 x )) (capped)": "))" * 40000, "3333 x (a)": "(a)" * 3333,
            "5000 x $[": "$[" * 5000, "50000 x $[ (capped)": "$[" * 50000, "3333 x ((a": "((a" * 3333,
            "5000 x ${": "${" * 5000, "3333 x ${!": "${!" * 3333,
            "40000 x a? (deadline)": "a?" * 40000, "100000 x > (deadline)": ">" * 100000,
            "40000 x ;> (deadline)": ";>" * 40000, "40000 x <> (deadline)": "<>" * 40000,
            "100000 x == (deadline)": "== " * 100000}
    for label, c in slow.items():
        (o, dt), = run([c])
        bad += o == "HANG" or o.startswith("CRASH") or dt > SLOW
        print(f"{label:22} {'HANG' if dt is None else f'{dt:7.3f}s'} {o[:60]}")
    bad += fuzz_helpers()
    if len(sys.argv) > 1:
        cmds = [json.loads(line)["cmd"] for line in open(sys.argv[1])]
        counts, times = {}, []
        for start in range(0, len(cmds), 5000):
            for o, dt in run(cmds[start:start + 5000]):
                counts[o] = counts.get(o, 0) + 1
                if dt is not None:
                    times.append(dt)
        times.sort()
        bad += sum(n for o, n in counts.items() if o == "HANG" or o.startswith("CRASH"))
        bad += sum(dt > SLOW for dt in times)
        print(f"corpus: {len(cmds):,} commands {counts}")
        print(f"        mean {statistics.mean(times) * 1e3:.3f} ms  p99 {times[int(len(times) * .99)] * 1e3:.3f} ms"
              f"  max {times[-1] * 1e3:.1f} ms")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
