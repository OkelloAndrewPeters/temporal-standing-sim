"""
Step 5: A distributed receiver, judged by the examination scope's rules.

Five SEPARATE PROCESSES talk only by messages, like real services:

    registry  - the authority registry (approvals, revocations, leases, a log)
    gateway   - gets the governance determination (a STAND-IN for Harmonic)
    issuance  - commits the permit record
    dispatch  - sends the permit out
    observer  - the independent "applicant inbox"; its receipt is the ONLY
                irreversible event

Three receiver designs are compared:

    naive    - trusts the determination; never re-checks
    recheck  - issuance and dispatch re-read the registry before acting
    leased   - dispatch takes a LEASE from the registry before sending.
               While a lease is held, a revocation is logged but only becomes
               effective after the lease is released. The registry then
               knows whether the permit was delivered, so it can tell the
               official the truth: "WITHDRAWN" or "TOO LATE".

A test harness injects a change at one of four points:

    after_determination    - Harmonic stand-in decided, nothing committed yet
    before_dispatch_check  - permit committed, dispatch hasn't checked yet
    after_dispatch_check   - dispatch checked, about to send
    after_receipt          - the observer already has the permit

Fault injection only PAUSES one service until the change is *requested*
(logged). It never waits for the change to become effective, and nothing
in the measurement sits on the commit path, so it can't serialize the
services it measures.

Judging (from the scope):
  Case A (revocation):
    - No permit at the observer + official told "WITHDRAWN"         -> PASS
    - Permit at the observer, and the records PROVE it arrived before
      the revocation became effective, and official told "TOO LATE"  -> PASS
    - Anything else (including "can't prove the order")               -> FAIL
  Case B (harmless change): permit must reach the observer.

Not yet modelled: retries, duplicates, crashes, lost acknowledgements.
Those come next.
"""

import json
import multiprocessing as mp
import os
from datetime import datetime, timezone

TIMEOUT = 15
POINTS = ["after_determination", "before_dispatch_check",
          "after_dispatch_check", "after_receipt"]
RECEIVERS = ["naive", "recheck", "leased"]


# ---------------------------------------------------------------
# Shared helper: a pause point the harness can use to inject a change
# ---------------------------------------------------------------
def checkpoint(harness_q, ctrl_q, service, name, pid):
    harness_q.put({"type": "checkpoint", "service": service, "name": name, "pid": pid})
    ctrl_q.get(timeout=TIMEOUT)  # wait for "continue"


# ---------------------------------------------------------------
# 1. Authority registry
# ---------------------------------------------------------------
def registry_service(inbox, replies):
    permits, log, leases, pending = {}, [], set(), {}

    def record(event, pid, **detail):
        log.append({"pos": len(log) + 1, "event": event, "pid": pid, **detail})
        return len(log)

    def reply(msg, **body):
        replies[msg["from"]].put(body)

    def make_revocation_effective(pid, report):
        permits[pid]["status"] = "revoked"
        permits[pid]["version"] += 1
        return record("revocation_effective", pid, reported_to_official=report)

    while True:
        msg = inbox.get()
        kind, pid = msg["type"], msg.get("pid")

        if kind == "stop":
            break

        elif kind == "approve":
            permits[pid] = {"status": "approved", "version": 1}
            record("approved", pid)
            reply(msg, ok=True)

        elif kind == "read":
            p = permits[pid]
            pos = record("read", pid, by=msg["from"], status=p["status"], version=p["version"])
            reply(msg, status=p["status"], version=p["version"], pos=pos)

        elif kind == "change":
            req = record("change_requested", pid, change=msg["change"])
            if msg["change"] == "harmless":
                permits[pid]["version"] += 1
                record("change_effective", pid, change="harmless")
            elif pid in leases:
                pending.setdefault(pid, []).append(req)
                record("revocation_deferred", pid, reason="dispatch lease held")
            else:
                # Did a lease release already tell us this permit was delivered?
                delivered = permits[pid].get("delivered", False)
                make_revocation_effective(pid, "TOO LATE - already issued" if delivered else "WITHDRAWN")
            reply(msg, logged=req)

        elif kind == "acquire_lease":
            if permits[pid]["status"] != "approved":
                record("lease_refused", pid)
                reply(msg, granted=False)
            else:
                leases.add(pid)
                pos = record("lease_granted", pid, version=permits[pid]["version"])
                reply(msg, granted=True, pos=pos)

        elif kind == "release_lease":
            leases.discard(pid)
            if msg["delivered"]:
                permits[pid]["delivered"] = True  # remember it: the permit is out for good
            record("lease_released", pid, delivered=msg["delivered"])
            report = "TOO LATE - already issued" if msg["delivered"] else "WITHDRAWN"
            for _ in pending.pop(pid, []):
                make_revocation_effective(pid, report)
            reply(msg, ok=True)

        elif kind == "dump":
            reply(msg, log=log)


# ---------------------------------------------------------------
# 2. Gateway, with a stand-in for Harmonic's determination
# ---------------------------------------------------------------
def gateway_service(inbox, ctrl, reg_in, reg_reply, issuance_in, harness_q):
    while True:
        msg = inbox.get()
        if msg["type"] == "stop":
            break
        pid = msg["pid"]
        reg_in.put({"type": "read", "pid": pid, "from": "gateway"})
        r = reg_reply.get(timeout=TIMEOUT)
        # STAND-IN for Harmonic: permit only if standing currently holds
        determination = "PERMITTED" if r["status"] == "approved" else "REFUSED"
        checkpoint(harness_q, ctrl, "gateway", "after_determination", pid)
        if determination == "REFUSED":
            harness_q.put({"type": "outcome", "pid": pid, "result": "HELD", "by": "gateway"})
        else:
            issuance_in.put({"pid": pid, "determination": determination})


# ---------------------------------------------------------------
# 3. Issuance
# ---------------------------------------------------------------
def issuance_service(variant, inbox, reg_in, reg_reply, dispatch_in, harness_q):
    while True:
        msg = inbox.get()
        if msg.get("type") == "stop":
            break
        pid = msg["pid"]
        if variant in ("recheck", "leased"):
            reg_in.put({"type": "read", "pid": pid, "from": "issuance"})
            if reg_reply.get(timeout=TIMEOUT)["status"] != "approved":
                harness_q.put({"type": "outcome", "pid": pid, "result": "HELD", "by": "issuance"})
                continue
        # commit the permit record (internal; NOT irreversible)
        dispatch_in.put({"pid": pid})


# ---------------------------------------------------------------
# 4. Dispatch
# ---------------------------------------------------------------
def dispatch_service(variant, inbox, ctrl, reg_in, reg_reply, observer_in, ack_q, harness_q):
    def ask(**m):
        reg_in.put({**m, "from": "dispatch"})
        return reg_reply.get(timeout=TIMEOUT)

    while True:
        msg = inbox.get()
        if msg.get("type") == "stop":
            break
        pid = msg["pid"]
        checkpoint(harness_q, ctrl, "dispatch", "before_dispatch_check", pid)

        if variant == "recheck":
            if ask(type="read", pid=pid)["status"] != "approved":
                harness_q.put({"type": "outcome", "pid": pid, "result": "HELD", "by": "dispatch"})
                continue
        elif variant == "leased":
            if not ask(type="acquire_lease", pid=pid)["granted"]:
                harness_q.put({"type": "outcome", "pid": pid, "result": "HELD", "by": "dispatch"})
                continue

        checkpoint(harness_q, ctrl, "dispatch", "after_dispatch_check", pid)

        observer_in.put({"type": "permit", "pid": pid})   # the external effect
        ack_q.get(timeout=TIMEOUT)                         # observer confirms receipt

        if variant == "leased":
            ask(type="release_lease", pid=pid, delivered=True)

        checkpoint(harness_q, ctrl, "dispatch", "after_receipt", pid)
        harness_q.put({"type": "outcome", "pid": pid, "result": "ISSUED", "by": "dispatch"})


# ---------------------------------------------------------------
# 5. Independent observer (the applicant's inbox)
# ---------------------------------------------------------------
def observer_service(inbox, dispatch_ack, harness_reply):
    receipts = []
    while True:
        msg = inbox.get()
        if msg["type"] == "stop":
            break
        if msg["type"] == "dump":
            harness_reply.put({"receipts": receipts})
        else:
            receipts.append({"n": len(receipts) + 1, "pid": msg["pid"]})
            dispatch_ack.put({"ok": True})


# ---------------------------------------------------------------
# 6. Harness: start services, inject changes, collect evidence
# ---------------------------------------------------------------
def build_cases():
    cases, pid = [], 300
    for point in POINTS:
        for case in ("A", "B"):
            cases.append({"pid": pid, "point": point, "case": case,
                          "change": "revoke" if case == "A" else "harmless"})
            pid += 1
    return cases


def run_receiver(variant):
    ctx = mp.get_context("spawn")
    Q = ctx.Queue
    reg_in, gw_in, iss_in, dsp_in, obs_in = Q(), Q(), Q(), Q(), Q()
    harness_q, harness_reply, dispatch_ack = Q(), Q(), Q()
    ctrl = {"gateway": Q(), "dispatch": Q()}
    reg_reply = {"gateway": Q(), "issuance": Q(), "dispatch": Q(), "harness": harness_reply}

    procs = [
        ctx.Process(target=registry_service, args=(reg_in, reg_reply)),
        ctx.Process(target=gateway_service,
                    args=(gw_in, ctrl["gateway"], reg_in, reg_reply["gateway"], iss_in, harness_q)),
        ctx.Process(target=issuance_service,
                    args=(variant, iss_in, reg_in, reg_reply["issuance"], dsp_in, harness_q)),
        ctx.Process(target=dispatch_service,
                    args=(variant, dsp_in, ctrl["dispatch"], reg_in, reg_reply["dispatch"],
                          obs_in, dispatch_ack, harness_q)),
        ctx.Process(target=observer_service, args=(obs_in, dispatch_ack, harness_reply)),
    ]
    for p in procs:
        p.start()

    def ask_registry(**m):
        reg_in.put({**m, "from": "harness"})
        return harness_reply.get(timeout=TIMEOUT)

    cases = build_cases()
    for c in cases:
        ask_registry(type="approve", pid=c["pid"])
        gw_in.put({"type": "request", "pid": c["pid"]})
        c["injected"] = False
        while True:
            ev = harness_q.get(timeout=TIMEOUT)
            if ev["type"] == "checkpoint":
                if ev["pid"] == c["pid"] and ev["name"] == c["point"] and not c["injected"]:
                    ask_registry(type="change", pid=c["pid"], change=c["change"])
                    c["injected"] = True  # requested and logged, NOT necessarily effective
                ctrl[ev["service"]].put("continue")
            elif ev["type"] == "outcome" and ev["pid"] == c["pid"]:
                c["outcome"], c["decided_by"] = ev["result"], ev["by"]
                break

    log = ask_registry(type="dump")["log"]
    obs_in.put({"type": "dump"})
    receipts = harness_reply.get(timeout=TIMEOUT)["receipts"]

    for q in (reg_in, gw_in, iss_in, dsp_in, obs_in):
        q.put({"type": "stop"})
    for p in procs:
        p.join(timeout=TIMEOUT)

    return cases, log, receipts


def judge(c, log, receipts):
    pid = c["pid"]
    received = any(r["pid"] == pid for r in receipts)

    if c["case"] == "B":
        return ("PASS", "issued") if received else ("FAIL", "harmless change blocked the permit")

    if not c["injected"]:
        return "INVALID", "change was never injected"

    effective = [e for e in log if e["pid"] == pid and e["event"] == "revocation_effective"]
    if not effective:
        return "FAIL", "revocation never became effective"
    p, report = effective[0]["pos"], effective[0]["reported_to_official"]

    if not received:
        if report == "WITHDRAWN":
            return "PASS", "held; official told WITHDRAWN"
        return "FAIL", f"held, but official told {report!r}"

    # Permit reached the observer. Can the records PROVE it arrived first?
    released_after_delivery = [e["pos"] for e in log if e["pid"] == pid
                               and e["event"] == "lease_released" and e["delivered"]]
    proven = (released_after_delivery and released_after_delivery[0] < p) \
        or c["point"] == "after_receipt"   # harness only requested it after receipt
    if not proven:
        return "FAIL", f"permit arrived; order unproven; official told {report!r}"
    if report != "TOO LATE - already issued":
        return "FAIL", f"issued before revocation, but official told {report!r}"
    return "PASS", "issued first; official told TOO LATE"


def main():
    all_results = {}
    for variant in RECEIVERS:
        cases, log, receipts = run_receiver(variant)
        for c in cases:
            c["verdict"], c["reason"] = judge(c, log, receipts)
        all_results[variant] = {"cases": cases, "registry_log": log, "observer_receipts": receipts}

    # Print the matrix
    print(f"{'Injection point':<24}{'Case':<6}" + "".join(f"{v:<10}" for v in RECEIVERS))
    print("-" * 60)
    for i, c in enumerate(build_cases()):
        row = f"{c['point']:<24}{c['case']:<6}"
        for v in RECEIVERS:
            row += f"{all_results[v]['cases'][i]['verdict']:<10}"
        print(row)

    print("\nWhy each FAIL happened:")
    for v in RECEIVERS:
        for c in all_results[v]["cases"]:
            if c["verdict"] != "PASS":
                print(f"  {v:<8} {c['point']:<22} Case {c['case']}: {c['reason']}")

    print()
    for v in RECEIVERS:
        fails = sum(c["verdict"] != "PASS" for c in all_results[v]["cases"])
        print(f"  {v:<8} {fails} failing case(s) -> {'PASS' if fails == 0 else 'FAIL'}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "receiver_runs")
    os.makedirs(out_dir, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("receiver_%Y%m%dT%H%M%SZ.json")
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull logs saved to receiver_runs/{name}")


if __name__ == "__main__":
    main()
