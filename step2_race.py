"""
Step 2: The last-second cancel (a "race condition").

Issuing a permit takes time:  CHECK approval -> PRINT (slow) -> RECORD as issued.
What if the official withdraws approval DURING the printing?

  CheckingIssuer: checks, then prints. The withdrawal sneaks into the gap.
                  The permit goes out even though the withdrawal was
                  recorded BEFORE issuance finished.  -> violation

  LockedIssuer:   holds a per-permit lock from CHECK to RECORD.
                  The withdrawal has to wait its turn. Either:
                    - it arrives first  -> permit is HELD, or
                    - it arrives during -> it waits, permit is issued, and the
                      official is TOLD "too late, already issued".
                  No silent failure either way.

The rule we judge by:
  A permit must never be recorded as issued AFTER a withdrawal for it
  was accepted.
"""

import threading
import time


def now():
    return time.perf_counter()


# ---------------------------------------------------------------
# 1. Registry, now with timestamps and a lock per permit
# ---------------------------------------------------------------
class ApprovalRegistry:
    def __init__(self):
        self.records = {}

    def approve(self, pid):
        self.records[pid] = {
            "approved": True,
            "withdrawn_at": None,
            "issued_at": None,
            "lock": threading.Lock(),  # one lock PER permit, so unrelated work isn't blocked
        }

    def withdraw(self, pid):
        rec = self.records[pid]
        with rec["lock"]:
            if rec["issued_at"] is not None:
                return "TOO LATE - already issued (official is told)"
            rec["approved"] = False
            rec["withdrawn_at"] = now()
            return "WITHDRAWN"

    def is_approved(self, pid):
        return self.records[pid]["approved"]

    def mark_issued(self, pid):
        self.records[pid]["issued_at"] = now()


# ---------------------------------------------------------------
# 2. Issuers
# ---------------------------------------------------------------
def slow_printing(pid):
    time.sleep(0)


class CheckingIssuer:
    """Re-checks at issuance (Step 1's winner), but check and issue are separate steps."""

    def __init__(self, registry, during_print=slow_printing):
        self.registry = registry
        self.during_print = during_print

    def issue_one(self, pid):
        if not self.registry.is_approved(pid):   # CHECK
            return "HELD"
        self.during_print(pid)                    # PRINT (the dangerous gap)
        self.registry.mark_issued(pid)            # RECORD
        return "ISSUED"

    def process(self, queue):
        return {pid: self.issue_one(pid) for pid in queue}


class LockedIssuer(CheckingIssuer):
    """Same steps, but CHECK -> PRINT -> RECORD happen as one locked unit."""

    def issue_one(self, pid):
        with self.registry.records[pid]["lock"]:
            return super().issue_one(pid)


# ---------------------------------------------------------------
# 3. The scenario
# ---------------------------------------------------------------
def run_scenario(issuer_class):
    registry = ApprovalRegistry()
    queue = []

    # T0: approve and queue three permits
    for pid in (101, 102, 103):
        registry.approve(pid)
        queue.append(pid)

    official_messages = {}
    threads = []

    def printing_with_cancel(pid):
        if pid == 101:
            # dN: the official clicks "withdraw" while 101 is printing
            def official():
                official_messages[pid] = registry.withdraw(pid)
            t = threading.Thread(target=official)
            threads.append(t)
            t.start()
            time.sleep(0)  # printing takes a while; the official's click lands meanwhile
        else:
            slow_printing(pid)

    # Tn: issue
    issuer = issuer_class(registry, during_print=printing_with_cancel)
    results = issuer.process(queue)

    for t in threads:
        t.join()
    return registry, results, official_messages


# ---------------------------------------------------------------
# 4. Judge
# ---------------------------------------------------------------
def judge(name, registry, results, official_messages):
    print(f"\n=== {name} ===")
    all_ok = True

    for pid, outcome in results.items():
        rec = registry.records[pid]
        w, i = rec["withdrawn_at"], rec["issued_at"]
        violation = w is not None and i is not None and w < i

        if violation:
            all_ok = False
            note = f"VIOLATION: withdrawal accepted {1000*(i-w):.0f} ms BEFORE issuance"
        elif pid in official_messages:
            note = f"official was told: {official_messages[pid]}"
        else:
            note = "no change, issued normally"

        mark = "FAIL" if violation else "ok  "
        print(f"  [{mark}] permit {pid}: {outcome:<6} - {note}")

    unrelated_ok = results.get(102) == "ISSUED" and results.get(103) == "ISSUED"
    print(f"  Unrelated permits kept flowing: {'yes' if unrelated_ok else 'NO'}")
    all_ok = all_ok and unrelated_ok
    print(f"  VERDICT: {'PASS' if all_ok else 'FAIL'}")



def count_violations(issuer_class, runs=1000):
    bad = 0
    for _ in range(runs):
        registry, results, msgs = run_scenario(issuer_class)
        r = registry.records[101]
        if r["withdrawn_at"] and r["issued_at"] and r["withdrawn_at"] < r["issued_at"]:
            bad += 1
    return bad


if __name__ == "__main__":
    print("CheckingIssuer violations:", count_violations(CheckingIssuer), "/ 1000")
    print("LockedIssuer violations:  ", count_violations(LockedIssuer), "/ 1000")