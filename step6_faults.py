"""
Step 6: The hard cases - duplicates, lost acknowledgements, crashes, freezes.

Step 5 showed that a dispatch LEASE works when nothing goes wrong.
Real systems go wrong. This step scripts four faults exactly, step by step,
so every run is identical and every outcome can be explained.
(This is called "deterministic simulation testing".)

A new rule from the scope discussion:
  MAX_LEASE - a lease expires after this many ticks. A revocation can be
  delayed by a lease, but never for longer than MAX_LEASE.

Three dispatch designs:
  leased      - Step 5's winner. Takes a lease, sends, releases.
                No duplicate protection; trusts its lease after a crash.
  idempotent  - Every send carries an idempotency key, so the inbox ignores
                repeats. Re-checks the lease is still valid before sending.
  fenced      - Like idempotent, but the DELIVERY CHANNEL itself asks the
                registry "is this lease still valid?" at the moment of
                delivery, and the registry records the delivery in that same
                step. Enforcement sits at the irreversible point itself.

Four scripted faults:
  S1 duplicate   - the same dispatch instruction arrives twice
  S2 lost_ack    - the permit is delivered, but the confirmation is lost,
                   so dispatch retries
  S3 crash       - dispatch crashes after taking the lease, the official
                   revokes during the outage, dispatch recovers after expiry
  S4 freeze      - dispatch checks its lease, then freezes (e.g. a long
                   garbage-collection pause) past expiry, then sends

Pass rules (from the scope):
  - Never more than one permit at the inbox
  - Case A: no permit may arrive after the revocation became effective
  - The official is told the truth: "WITHDRAWN" only if nothing arrived,
    "TOO LATE" only if the permit really arrived first
  - A revocation is never delayed longer than MAX_LEASE
"""

MAX_LEASE = 5


# ---------------------------------------------------------------
# The world: a clock, a registry, and the applicant's inbox
# ---------------------------------------------------------------
class World:
    def __init__(self):
        self.now = 0
        self.events = []  # (time, what) - ground truth, in order

    def tick(self, n=1):
        for _ in range(n):
            self.now += 1
            self.registry.expire_leases()

    def note(self, what):
        self.events.append((self.now, what))


class Registry:
    def __init__(self, world):
        self.w = world
        self.status = "approved"
        self.delivered = False
        self.lease = None          # (token, expires_at)
        self.next_token = 1
        self.pending_since = None  # when a deferred revocation was requested
        self.report = None         # what the official was told
        self.effective_at = None
        self.max_deferral = 0

    def acquire_lease(self):
        if self.status != "approved" or self.lease:
            return None
        token = self.next_token
        self.next_token += 1
        self.lease = (token, self.w.now + MAX_LEASE)
        self.w.note(f"lease {token} granted")
        return token

    def lease_valid(self, token):
        return self.lease is not None and self.lease[0] == token

    def release(self, token, delivered):
        if self.lease_valid(token):
            if delivered:
                self.delivered = True
            self.lease = None
            self.w.note(f"lease {token} released (delivered={delivered})")
            self._apply_pending()

    def request_revocation(self):
        self.w.note("revocation REQUESTED")
        if self.lease:
            self.pending_since = self.w.now
            self.w.note("revocation deferred (lease held)")
        else:
            self._make_effective()

    def expire_leases(self):
        if self.lease and self.w.now >= self.lease[1]:
            self.w.note(f"lease {self.lease[0]} EXPIRED")
            self.lease = None
            self._apply_pending()

    def record_delivery(self, token):
        """Used only by the fenced channel: check and record in ONE step."""
        if self.status == "approved" and self.lease_valid(token):
            self.delivered = True
            return True
        return False

    def _apply_pending(self):
        if self.pending_since is not None:
            self.max_deferral = max(self.max_deferral, self.w.now - self.pending_since)
            self.pending_since = None
            self._make_effective()

    def _make_effective(self):
        self.status = "revoked"
        self.effective_at = self.w.now
        self.report = "TOO LATE - already issued" if self.delivered else "WITHDRAWN"
        self.w.note(f"revocation EFFECTIVE; official told {self.report!r}")


class Inbox:
    """The independent observer: the applicant's inbox."""

    def __init__(self, world, dedupe):
        self.w = world
        self.dedupe = dedupe
        self.received = []  # (time, key)

    def deliver(self, key):
        if self.dedupe and any(k == key for _, k in self.received):
            self.w.note(f"inbox ignored duplicate {key}")
            return True
        self.received.append((self.w.now, key))
        self.w.note(f"PERMIT ARRIVED at inbox ({key})")
        return True


class Channel:
    """How dispatch reaches the inbox. The fenced version checks authority."""

    def __init__(self, world, inbox, registry, fenced):
        self.w, self.inbox, self.registry, self.fenced = world, inbox, registry, fenced

    def send(self, key, token):
        if self.fenced:
            already = any(k == key for _, k in self.inbox.received)
            if not already and not self.registry.record_delivery(token):
                self.w.note(f"channel REJECTED send (lease {token} no longer valid)")
                return "rejected"
        self.inbox.deliver(key)
        return "delivered"


# ---------------------------------------------------------------
# Dispatch: the three designs share one script with fault hooks
# ---------------------------------------------------------------
def dispatch(world, design, fault, revoke_at=None):
    reg, ch = world.registry, world.channel
    key = "permit-501"  # same key on every retry

    def maybe_revoke(stage):
        if revoke_at == stage:
            reg.request_revocation()

    token = reg.acquire_lease()
    if token is None:
        world.note("dispatch HELD (no lease)")
        return
    maybe_revoke("after_lease")

    if fault == "crash":
        world.note("dispatch CRASHED")
        maybe_revoke("during_outage")
        world.tick(MAX_LEASE + 1)
        world.note("dispatch RECOVERED")

    if design in ("idempotent", "fenced") and not reg.lease_valid(token):
        world.note("dispatch sees lease is gone -> HELD")
        return

    if fault == "freeze":
        world.note("dispatch FROZEN after its check")
        maybe_revoke("during_freeze")
        world.tick(MAX_LEASE + 1)
        world.note("dispatch UNFROZEN, sending")

    result = ch.send(key, token)
    if result == "rejected":
        world.note("dispatch HELD (channel rejected)")
        reg.release(token, delivered=False)
        return

    if fault == "lost_ack":
        world.note("acknowledgement LOST; dispatch retries")
        ch.send(key if design != "leased" else key + "-retry", token)

    reg.release(token, delivered=True)


# ---------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------
SCENARIOS = {
    "S1 duplicate": dict(fault=None, revoke_at=None, runs=2),
    "S2 lost_ack": dict(fault="lost_ack", revoke_at=None, runs=1),
    "S3 crash": dict(fault="crash", revoke_at="during_outage", runs=1),
    "S4 freeze": dict(fault="freeze", revoke_at="during_freeze", runs=1),
}
DESIGNS = ["leased", "idempotent", "fenced"]


def run(design, spec):
    w = World()
    w.registry = Registry(w)
    w.inbox = Inbox(w, dedupe=(design != "leased"))
    w.channel = Channel(w, w.inbox, w.registry, fenced=(design == "fenced"))

    for i in range(spec["runs"]):
        if i > 0:
            w.note("SAME dispatch instruction delivered again")
            # a duplicate that arrives while no lease is held must not re-send
            if design != "leased" and w.registry.delivered:
                w.note("dispatch sees permit already delivered -> skip")
                continue
        dispatch(w, design, spec["fault"], spec["revoke_at"])
    return w


def judge(w):
    reg, received = w.registry, w.inbox.received
    problems = []
    if len(received) > 1:
        problems.append(f"{len(received)} permits arrived (duplicate)")
    if reg.effective_at is not None:
        late = [t for t, _ in received if t >= reg.effective_at]
        if late:
            problems.append("permit arrived AFTER revocation took effect")
        if reg.report == "WITHDRAWN" and received:
            problems.append("official told WITHDRAWN, but a permit arrived")
        if reg.report.startswith("TOO LATE") and not received:
            problems.append("official told TOO LATE, but nothing arrived")
    if reg.max_deferral > MAX_LEASE:
        problems.append(f"revocation delayed {reg.max_deferral} ticks (max {MAX_LEASE})")
    return ("PASS", "") if not problems else ("FAIL", "; ".join(problems))


def main(show_timeline_for=None):
    results = {}
    print(f"{'Scenario':<15}" + "".join(f"{d:<13}" for d in DESIGNS))
    print("-" * 54)
    for name, spec in SCENARIOS.items():
        row = f"{name:<15}"
        for d in DESIGNS:
            w = run(d, spec)
            verdict, why = judge(w)
            results[(name, d)] = (verdict, why, w.events)
            row += f"{verdict:<13}"
        print(row)

    print("\nWhy each FAIL happened:")
    for (name, d), (verdict, why, _) in results.items():
        if verdict == "FAIL":
            print(f"  {d:<11} {name:<14} {why}")

    if show_timeline_for:
        name, d = show_timeline_for
        print(f"\nTimeline: {d} / {name}")
        for t, what in results[(name, d)][2]:
            print(f"  t={t:<3} {what}")


if __name__ == "__main__":
    main(show_timeline_for=("S4 freeze", "idempotent"))
