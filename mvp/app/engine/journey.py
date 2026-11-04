"""Journey definition + execution as a small, debuggable state machine.

A journey is a YAML file (versioned in git, reviewed like code). The engine has
exactly one job: given a customer's state and their position in a journey, say
what happens next and when.

Step types (deliberately only four):
    wait   -> schedule the next evaluation
    send   -> emit an action for a channel adapter
    tag    -> emit an internal action (e.g. tag customer in Magento)
    end    -> finish the run

There is no visual canvas and no branching in the MVP. Branching is where
journey builders become unmaintainable; add it when a real journey needs it.

State machine invariant
-----------------------
`step_index` always points at the *next step to execute*. Executing a `wait`
sets `next_run_at = now + hours` and advances past itself. Therefore a tick that
arrives early is a no-op, and a tick that arrives late resumes exactly where it
left off. No "did we already schedule this?" flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..config import Policy
from .lifecycle import Profile


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any = None
    reason: str | None = None

    def test(self, p: Profile) -> bool:
        return compare(get_field(p, self.field), self.op, self.value)


@dataclass(frozen=True)
class Step:
    type: str
    hours: int = 0
    channel: str | None = None
    template: str | None = None
    include_offer: bool = False
    tag: str | None = None


@dataclass
class Journey:
    key: str
    name: str
    priority: int
    goal: str
    entry_lifecycles: tuple[str, ...]
    entry_all: tuple[Condition, ...]
    entry_any: tuple[Condition, ...]
    exit_conditions: tuple[Condition, ...]
    cooldown_days: int
    steps: tuple[Step, ...]
    description: str = ""
    channels: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class RunState:
    """Mirror of the `journey_runs` row, passed in and out as plain data."""

    step_index: int = 0
    status: str = "active"
    next_run_at: datetime | None = None
    entered_at: datetime | None = None


@dataclass
class Action:
    type: str            # send | tag | end
    channel: str | None = None
    template: str | None = None
    include_offer: bool = False
    tag: str | None = None


@dataclass
class Advance:
    actions: list[Action]
    state: RunState
    exit_reason: str | None = None


# --------------------------------------------------------------------------
# field access + comparison
# --------------------------------------------------------------------------
_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "is_true": lambda a, b: bool(a) is True,
    "is_false": lambda a, b: bool(a) is False,
}


def compare(actual: Any, op: str, expected: Any) -> bool:
    if op not in _OPS:
        raise ValueError(f"unknown operator: {op}")
    return bool(_OPS[op](actual, expected))


def get_field(p: Profile, path: str) -> Any:
    """Resolve a dotted field path against a Profile."""
    if path.startswith("consent."):
        return p.consent.get(path.split(".", 1)[1], False)
    return getattr(p, path, None)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def _cond(raw: dict) -> Condition:
    return Condition(
        field=raw["field"],
        op=raw.get("op", "eq"),
        value=raw.get("value"),
        reason=raw.get("reason"),
    )


def _step(raw: dict) -> Step:
    return Step(
        type=raw["type"],
        hours=int(raw.get("hours", 0)),
        channel=raw.get("channel"),
        template=raw.get("template"),
        include_offer=bool(raw.get("include_offer", False)),
        tag=raw.get("tag"),
    )


def parse_journey(raw: dict) -> Journey:
    entry = raw.get("entry", {}) or {}
    steps = tuple(_step(s) for s in raw["steps"])
    return Journey(
        key=raw["key"],
        name=raw.get("name", raw["key"]),
        priority=int(raw.get("priority", 50)),
        goal=raw.get("goal", "order"),
        description=raw.get("description", ""),
        entry_lifecycles=tuple(entry.get("lifecycle", []) or []),
        entry_all=tuple(_cond(c) for c in (entry.get("all") or [])),
        entry_any=tuple(_cond(c) for c in (entry.get("any") or [])),
        exit_conditions=tuple(_cond(c) for c in (raw.get("exit") or [])),
        cooldown_days=int(raw.get("cooldown_days", 90)),
        steps=steps,
        channels=tuple(sorted({s.channel for s in steps if s.channel})),
    )


def load_journeys(directory: str | Path) -> list[Journey]:
    """Load every *.yaml in a directory, ordered by priority (low number wins)."""
    out: list[Journey] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        with open(path, "r", encoding="utf-8") as fh:
            out.append(parse_journey(yaml.safe_load(fh)))
    out.sort(key=lambda j: (j.priority, j.key))
    return out


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------
def is_eligible(p: Profile, j: Journey, now: datetime, policy: Policy = Policy()) -> tuple[bool, str]:
    """Entry rules. Returns (eligible, reason_if_not)."""
    if p.suppressed:
        return False, "suppressed"
    if not any(p.consent.values()):
        return False, "no_consent"
    if j.entry_lifecycles and p.lifecycle not in j.entry_lifecycles:
        return False, f"lifecycle:{p.lifecycle}"
    for c in j.entry_all:
        if not c.test(p):
            return False, f"entry_all:{c.field}"
    if j.entry_any and not any(c.test(p) for c in j.entry_any):
        return False, "entry_any:none_matched"
    return True, "ok"


def exit_reason(p: Profile, j: Journey) -> str | None:
    """Exit rules win over progression. Goal met, opted out, or disqualifying."""
    for c in j.exit_conditions:
        if c.test(p):
            return c.reason or f"exit:{c.field}"
    return None


def pick_journey(
    p: Profile,
    journeys: Iterable[Journey],
    now: datetime,
    active_keys: set[str] | None = None,
    policy: Policy = Policy(),
) -> tuple[Journey | None, str]:
    """Choose at most one journey for a customer, by priority.

    `one_journey_at_a_time` prevents the classic failure mode: a VIP at risk of
    churning receives three journeys in one week because they match all three.
    """
    active_keys = active_keys or set()
    if policy.one_journey_at_a_time and active_keys:
        return None, "already_in_journey"
    for j in journeys:
        ok, _why = is_eligible(p, j, now, policy)
        if ok:
            return j, "ok"
    return None, "no_match"


# --------------------------------------------------------------------------
# the state machine
# --------------------------------------------------------------------------
def start(p: Profile, j: Journey, now: datetime) -> RunState:
    return RunState(step_index=0, status="active", next_run_at=now, entered_at=now)


def advance(run: RunState, j: Journey, p: Profile, now: datetime) -> Advance:
    """Execute as many non-waiting steps as are due, then stop.

    Pure: no I/O, no clock reads, no database. Given the same inputs it always
    produces the same outputs, which is why the journey tests are short.
    """
    reason = exit_reason(p, j)
    if reason:
        run.status = "exited"
        return Advance(actions=[], state=run, exit_reason=reason)

    if run.next_run_at is not None and now < run.next_run_at:
        return Advance(actions=[], state=run)          # not due yet

    actions: list[Action] = []
    idx = run.step_index
    guard = 0

    while idx < len(j.steps):
        guard += 1
        if guard > 50:                                  # malformed journey safety net
            break
        step = j.steps[idx]

        if step.type == "wait":
            run.next_run_at = now + timedelta(hours=step.hours)
            run.step_index = idx + 1
            return Advance(actions=actions, state=run)

        if step.type == "send":
            actions.append(Action(type="send", channel=step.channel,
                                  template=step.template, include_offer=step.include_offer))
            idx += 1
            run.step_index = idx
            continue

        if step.type == "tag":
            actions.append(Action(type="tag", tag=step.tag))
            idx += 1
            run.step_index = idx
            continue

        if step.type == "end":
            run.status = "completed"
            run.step_index = idx + 1
            return Advance(actions=actions, state=run, exit_reason="completed")

        raise ValueError(f"{j.key}: unknown step type {step.type!r}")

    run.status = "completed"
    return Advance(actions=actions, state=run, exit_reason="completed")


def validate(j: Journey) -> list[str]:
    """Static checks run in CI. Cheap, and they catch the mistakes that actually
    happen: a journey with no exit rule, or a send with no template."""
    errors: list[str] = []
    if not j.steps:
        errors.append(f"{j.key}: no steps")
    if not j.exit_conditions:
        errors.append(f"{j.key}: no exit rules (customers will never leave)")
    if not j.entry_lifecycles and not j.entry_all and not j.entry_any:
        errors.append(f"{j.key}: no entry rules (matches everyone)")
    for i, s in enumerate(j.steps):
        if s.type == "send" and (not s.channel or not s.template):
            errors.append(f"{j.key}: step {i} send without channel/template")
        if s.type not in ("wait", "send", "tag", "end"):
            errors.append(f"{j.key}: step {i} unknown type {s.type}")
    return errors
