"""
Step 1: A tiny pretend permit office.

The story:
  T0  - An official approves three permits.
        An AI agent sees they're approved and puts them in the queue.
  dN  - Things change while they wait in the queue:
          * Permit 101: approval WITHDRAWN        (Case A - must be HELD)
          * Permit 102: harmless detail changed   (Case B - must be ISSUED)
          * Permit 103: nothing happens           (unrelated - must be ISSUED)
  Tn  - The issuing system processes the queue.

We test two issuers:
  NaiveIssuer    - trusts the queue (only checked approval at queue time)
  CheckingIssuer - re-checks approval at the moment of issuing
"""


# ---------------------------------------------------------------
# 1. The approval registry: the official "source of truth"
# ---------------------------------------------------------------
class ApprovalRegistry:
    def __init__(self):
        self.records = {}  # permit_id -> {"status": ..., "notes": [...]}

    def approve(self, permit_id):
        self.records[permit_id] = {"status": "approved", "notes": []}

    def withdraw(self, permit_id):
        # A material change: removes the basis for the permit
        self.records[permit_id]["status"] = "withdrawn"

    def update_detail(self, permit_id, note):
        # A non-material change: something changed, but approval still stands
        self.records[permit_id]["notes"].append(note)

    def is_approved(self, permit_id):
        record = self.records.get(permit_id)
        return record is not None and record["status"] == "approved"


# ---------------------------------------------------------------
# 2. The AI agent: checks approval, then queues the action
# ---------------------------------------------------------------
class AIAgent:
    def __init__(self, registry, queue):
        self.registry = registry
        self.queue = queue

    def request_permit(self, permit_id):
        if self.registry.is_approved(permit_id):  # check at T0
            self.queue.append(permit_id)


# ---------------------------------------------------------------
# 3. Two issuing systems (the thing we are testing)
# ---------------------------------------------------------------
class NaiveIssuer:
    """Issues whatever is in the queue. Assumes the T0 check is still valid."""

    def __init__(self, registry):
        self.registry = registry

    def process(self, queue):
        results = {}
        for permit_id in queue:
            results[permit_id] = "ISSUED"
        return results


class CheckingIssuer:
    """Re-checks the registry at Tn, right before issuing."""

    def __init__(self, registry):
        self.registry = registry

    def process(self, queue):
        results = {}
        for permit_id in queue:
            if self.registry.is_approved(permit_id):  # check at Tn
                results[permit_id] = "ISSUED"
            else:
                results[permit_id] = "HELD"
        return results


# ---------------------------------------------------------------
# 4. The test: run the scenario and judge the result
# ---------------------------------------------------------------
EXPECTED = {
    101: "HELD",    # Case A: approval withdrawn
    102: "ISSUED",  # Case B: harmless change
    103: "ISSUED",  # unrelated work must continue
}


def run_scenario(issuer_class):
    registry = ApprovalRegistry()
    queue = []
    agent = AIAgent(registry, queue)

    # T0: approve and queue
    for pid in (101, 102, 103):
        registry.approve(pid)
        agent.request_permit(pid)

    # dN: changes while waiting in the queue
    registry.withdraw(101)
    registry.update_detail(102, "applicant phone number updated")

    # Tn: issue
    issuer = issuer_class(registry)
    return issuer.process(queue)


def judge(name, results):
    print(f"\n=== {name} ===")
    all_ok = True
    for pid, expected in EXPECTED.items():
        got = results.get(pid)
        ok = got == expected
        all_ok = all_ok and ok
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] permit {pid}: expected {expected:<6} got {got}")
    print(f"  VERDICT: {'PASS' if all_ok else 'FAIL'}")


if __name__ == "__main__":
    judge("NaiveIssuer", run_scenario(NaiveIssuer))
    judge("CheckingIssuer", run_scenario(CheckingIssuer))
