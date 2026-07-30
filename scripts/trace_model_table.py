#!/usr/bin/env python3
"""Trace the ConfuserEx control-flow-flattened model table in TriXX's GUI assembly.

The method `_3qfzvUC7NGxAR18wC5ynWjLS0Bp._yCt6mPXTyn1erZUByQmXbGwFiKF(...)` is a
switch-flattened factory keyed on PCI device id (`num`) and subsystem device id
(`num10`). Given a (device, subsystem) pair this walks the state machine and reports
which `return new _3qfzvUC7NGxAR18wC5ynWjLS0Bp { ... }` block is reached, i.e. which
Nitro Glow version the card is assigned.

Dispatch header (line 126 of the decompiled file):
    switch ((num4 = (uint)(num3 ^ 0x7EE4DC75)) % 414)
"""
import re, sys

SRC = ("decompiled/132/_cGNaWFZsAAolCJM4GumjkFdUt73/"
       "_3qfzvUC7NGxAR18wC5ynWjLS0Bp.cs")
XOR_K = 0x7EE4DC75
MOD = 414

def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v

def load_cases(path):
    lines = open(path).read().split("\n")
    # locate the dispatch switch, then collect "case N:" blocks until the switch closes
    start = next(i for i, l in enumerate(lines) if "% 414)" in l)
    cases, cur, curn = {}, None, None
    for i in range(start + 1, len(lines)):
        m = re.match(r"\s*case (\d+)u:", lines[i])
        if m:
            if curn is not None:
                cases[curn] = cur
            curn, cur = int(m.group(1)), []
            continue
        if re.match(r"\s*\}\s*$", lines[i]) and lines[i].startswith("\t\t\t}"):
            pass
        if curn is not None:
            cur.append((i + 1, lines[i]))
    if curn is not None:
        cases[curn] = cur
    return cases, lines

NUMS = {}

def eval_expr(e, num4):
    """Evaluate the small integer expressions ConfuserEx emits."""
    e = e.strip().rstrip(";").strip()
    e = re.sub(r"\(u?int\)", "", e)          # drop C# casts; arithmetic is mod 2^32 either way
    e = re.sub(r"\bnum4\b", str(s32(num4)), e)
    for k, v in sorted(NUMS.items(), key=lambda kv: -len(kv[0])):
        e = re.sub(r"\b%s\b" % re.escape(k), str(v), e)
    if not re.fullmatch(r"[-+*^()\s0-9]+", e):
        return None
    try:
        return s32(eval(e))
    except Exception:
        return None

def run(device, subven, subsys, rev, verbose=False):
    cases, lines = load_cases(SRC)
    # initial num3: the statement just before the while(true) at the top of the method
    init = None
    for i, l in enumerate(lines[:126]):
        m = re.search(r"\bnum3 = (-?\d+);", l)
        if m:
            init = int(m.group(1))
    if init is None:
        print("could not find initial num3"); return
    num3, seen = init, set()
    env = {"num": device, "num2": subven, "num10": subsys, "num5": rev}
    for step in range(4000):
        num4 = (num3 ^ XOR_K) & 0xFFFFFFFF
        idx = num4 % MOD
        if (num3, idx) in seen:
            print("loop detected at case %d" % idx); return
        seen.add((num3, idx))
        body = cases.get(idx)
        if body is None:
            print("no case %d (num3=%d)" % (idx, num3)); return
        text = "\n".join(l for _, l in body)
        if verbose:
            print("-> case %du (num3=%d)" % (idx, num3))
        if "return new _3qfzvUC7NGxAR18wC5ynWjLS0Bp" in text:
            print("\n*** REACHED DESCRIPTOR at case %du, source line %d ***" % (idx, body[0][0]))
            print(text[:2600])
            return
        if re.search(r"^\s*return;", text, re.M):
            print("plain return at case %d" % idx); return
        # conditional on num / num10
        mm = re.search(r"if \((num10|num5|num2|num)\b (==|!=) (\d+)\)", text)
        newnum3 = None
        if mm:
            var, op, val = mm.group(1), mm.group(2), int(mm.group(3))
            cond = (env[var] == val) if op == "==" else (env[var] != val)
            # then-branch assignment, else-branch assignment
            br = re.findall(r"num3 = ([^;]+);", text)
            if len(br) >= 2:
                newnum3 = eval_expr(br[0] if cond else br[1], num4)
            else:
                # form: numX = C1/C2 then num3 = numX ^ (...)
                b2 = re.findall(r"num\d+ = (-?\d+);", text)
                fin = re.search(r"num3 = (num\d+ \^ [^;]+);", text)
                if len(b2) >= 2 and fin:
                    pick = b2[0] if cond else b2[1]
                    expr = re.sub(r"num\d+", pick, fin.group(1), count=1)
                    newnum3 = eval_expr(expr, num4)
        if newnum3 is None:
            asg = re.findall(r"num3 = ([^;]+);", text)
            if asg:
                newnum3 = eval_expr(asg[-1], num4)
        if newnum3 is None:
            print("\nSTUCK at case %du (source line %d) — unhandled body:" % (idx, body[0][0]))
            print(text[:900]); return
        num3 = newnum3
    print("step limit")

if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("-")]
    dev = int(a[0], 0) if len(a) > 0 else 0x731F
    sub = int(a[1], 0) if len(a) > 1 else 0xE409
    rev = int(a[2], 0) if len(a) > 2 else 0xC1
    subven = int(a[3], 0) if len(a) > 3 else 0x1DA2
    print("tracing device=0x%04X subven=0x%04X subsys=0x%04X rev=0x%02X" % (dev, subven, sub, rev))
    run(dev, subven, sub, rev, verbose="-v" in sys.argv)
