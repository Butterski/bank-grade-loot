"""Tries every ordering.

A request handler is a generator that yields database statements. `explore`
runs a group of handlers in every possible order of their statements and, if
asked, stops any of them between any two statements. Each complete ordering
comes back as a `Run`, so a check can look at the final state.

This is exhaustive, not random: if a bad ordering exists in the model, it is
found. The price is that it only works for small scenarios, which is all the
talk needs: two or three requests with a handful of statements each.

A handler must touch shared state only through the statements it yields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generator, Iterator

from .db import ModelError, Statement, UniqueViolation

Handler = Generator[Statement, Any, Any]
Choice = tuple[str, str]  # ("run" | "crash", process name)

# Guards against a handler that never ends, such as a retry loop that can spin forever.
MAX_STEPS = 500
MAX_RUNS = 2_000_000


@dataclass
class Process:
    """One request, or one background worker."""

    name: str
    body: Callable[[], Handler]
    after: Callable[[Run], bool] | None = None  # it may start only once this holds
    crashes: bool = False  # the explorer may stop it between two statements
    restarts: bool = False  # after a stop it starts again from the top


@dataclass
class Step:
    who: str
    sql: str
    result: Any = None
    crash: bool = False


class _Live:
    """A process during one run."""

    def __init__(self, spec: Process):
        self.spec = spec
        self.started = False
        self.ended = False
        self.failed = False
        self.reply: Any = None
        self.crashes = 0
        self.boot()

    def boot(self) -> None:
        self.gen = self.spec.body()
        self.txns: list = []
        self.steps = 0
        try:
            self.pending: Statement | None = next(self.gen)
        except StopIteration as stop:
            self.end(stop.value)

    def end(self, reply: Any) -> None:
        if any(txn.open for txn in self.txns):
            raise ModelError(f"{self.spec.name} finished with an open transaction")
        self.pending = None
        self.ended = True
        self.reply = reply

    def roll_back(self) -> None:
        for txn in self.txns:
            if txn.open:
                txn._rollback()


class Run:
    """One complete ordering: what ran, in which order, and where it ended."""

    def __init__(self, world: Any, processes: list[Process], max_crashes: int = 0):
        self.world = world
        self.crashes_left = max_crashes
        self.trace: list[Step] = []
        self.schedule: tuple[Choice, ...] = ()
        self.problem: str | None = None  # filled in by a check that rejects this run
        self._live = {process.name: _Live(process) for process in processes}

    def choices(self) -> list[Choice]:
        options: list[Choice] = []
        for name, live in self._live.items():
            if live.ended:
                continue
            if not live.started and live.spec.after and not live.spec.after(self):
                continue
            if not live.pending.blocked():
                options.append(("run", name))
            if live.spec.crashes and self.crashes_left and live.steps:
                options.append(("crash", name))
        return options

    def take(self, kind: str, name: str) -> None:
        live = self._live[name]
        self.schedule += ((kind, name),)
        if kind == "crash":
            self._crash(live)
            return
        statement = live.pending
        live.started = True
        live.steps += 1
        error = None
        try:
            result = statement.run()
        except UniqueViolation as exc:
            result, error = f"ERROR: {exc}", exc
        txn = statement.txn
        if txn is not None and not txn.implicit and txn not in live.txns:
            live.txns.append(txn)
        self.trace.append(Step(name, statement.sql, result))
        if len(self.trace) > MAX_STEPS:
            raise ModelError(f"a run did not finish within {MAX_STEPS} statements")
        try:
            live.pending = live.gen.throw(error) if error else live.gen.send(result)
        except StopIteration as stop:
            live.end(stop.value)
        except UniqueViolation:
            # Nobody handled the database error: the request fails, its transaction is gone.
            live.roll_back()
            live.failed = True
            live.end(None)

    def _crash(self, live: _Live) -> None:
        self.crashes_left -= 1
        self.trace.append(Step(live.spec.name, "-- the process stops here --", crash=True))
        live.roll_back()
        try:
            live.gen.close()
        except RuntimeError:
            pass  # the handler tried to carry on after the process was gone; it does not get to
        live.crashes += 1
        if live.spec.restarts:
            live.boot()
        else:
            live.txns = []
            live.end(None)

    # --- what a check may ask about a finished run ---

    @property
    def stuck(self) -> bool:
        """Nothing can run, yet somebody is not finished: a deadlock."""
        return any(not live.ended for live in self._live.values())

    @property
    def switches(self) -> int:
        """How often the trace jumps from one process to another."""
        names = [step.who for step in self.trace]
        return sum(1 for a, b in zip(names, names[1:]) if a != b)

    def ended(self, name: str) -> bool:
        return self._live[name].ended

    def reply(self, name: str) -> Any:
        return self._live[name].reply

    def stopped(self, name: str) -> int:
        """How many times the explorer stopped this process."""
        return self._live[name].crashes


Scenario = Callable[[], tuple[Any, list[Process]]]


def explore(scenario: Scenario, max_crashes: int = 0) -> Iterator[Run]:
    """Yield one finished `Run` for every possible ordering of the scenario.

    `scenario` must build a fresh world on every call, because each ordering
    is executed from the beginning.
    """
    todo: list[tuple[Choice, ...]] = [()]
    finished = 0
    while todo:
        finished += 1
        if finished > MAX_RUNS:
            raise ModelError(f"more than {MAX_RUNS:,} orderings: the scenario is too large to check")
        world, processes = scenario()
        run = Run(world, processes, max_crashes)
        for choice in todo.pop():
            run.take(*choice)
        while True:
            options = run.choices()
            if not options:
                break
            todo.extend(run.schedule + (other,) for other in reversed(options[1:]))
            run.take(*options[0])
        yield run


def atomically(handler: Handler) -> Any:
    """Run a whole handler as a single step.

    Used when one side calls another service and the orderings inside that
    service are already covered by a separate check.
    """
    try:
        statement = next(handler)
        while True:
            if statement.blocked():
                raise ModelError(f"an atomic call would have to wait: {statement.sql}")
            statement = handler.send(statement.run())
    except StopIteration as stop:
        return stop.value
