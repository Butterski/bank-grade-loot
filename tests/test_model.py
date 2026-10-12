"""The database model and the explorer must behave like the real thing, where it matters."""

import pytest

from loot import numbers
from loot.db import Database, ModelError, UniqueViolation
from loot.explore import Process, explore


def one_row() -> Database:
    db = Database("test", items=("id",), claims=("name",))
    db.load("items", {1: {"owner": "A", "version": 1}, 2: {"owner": "B", "version": 1}})
    return db


def runs(make_processes, max_crashes=0):
    def scenario():
        db = one_row()
        return db, make_processes(db)

    return list(explore(scenario, max_crashes))


def test_independent_requests_interleave_in_every_order():
    def reader(db, item):
        for _ in range(3):
            yield db.select("items", item)

    found = runs(lambda db: [Process("a", lambda: reader(db, 1)), Process("b", lambda: reader(db, 2))])
    assert len(found) == numbers.orderings(3, 3) == 20
    assert len({run.schedule for run in found}) == 20


def test_a_row_lock_makes_the_second_transaction_wait():
    def locker(db):
        tx = db.begin()
        yield tx.select_for_update("items", 1)
        yield tx.update("items", 1, add={"version": 1})
        yield tx.commit()

    found = runs(lambda db: [Process("a", lambda: locker(db)), Process("b", lambda: locker(db))])
    assert len(found) == 2  # a then b, or b then a: nothing in between
    assert all(run.world.row("items", 1)["version"] == 3 for run in found)


def test_a_conditional_update_looks_again_after_waiting():
    def take(db, name):
        tx = db.begin()
        changed = yield tx.update("items", 1, to={"owner": name}, where={"owner": "A"})
        yield tx.commit()
        return changed

    found = runs(lambda db: [Process("x", lambda: take(db, "X")), Process("y", lambda: take(db, "Y"))])
    assert all(sorted([run.reply("x"), run.reply("y")]) == [0, 1] for run in found)


def test_other_transactions_do_not_see_changes_before_commit():
    def writer(db):
        tx = db.begin()
        yield tx.update("items", 1, to={"owner": "W"})
        yield tx.commit()

    def reader(db):
        row = yield db.select("items", 1)
        return row["owner"]

    found = runs(lambda db: [Process("w", lambda: writer(db)), Process("r", lambda: reader(db))])
    seen_before_commit = [run.reply("r") for run in found if run.schedule.index(("run", "r")) < 2]
    assert seen_before_commit and set(seen_before_commit) == {"A"}
    assert {run.reply("r") for run in found} == {"A", "W"}


def test_a_stopped_process_loses_its_open_transaction():
    def writer(db):
        tx = db.begin()
        yield tx.update("items", 1, to={"owner": "W"})
        yield tx.update("items", 2, to={"owner": "W"})
        yield tx.commit()

    found = runs(lambda db: [Process("w", lambda: writer(db), crashes=True)], max_crashes=1)
    states = {(run.world.row("items", 1)["owner"], run.world.row("items", 2)["owner"]) for run in found}
    assert states == {("A", "B"), ("W", "W")}  # all or nothing
    assert all(not run.world.locks for run in found)


def test_two_inserts_of_the_same_key_wait_for_each_other():
    def claim(db):
        tx = db.begin()
        mine = yield tx.insert("claims", "T42", on_conflict_do_nothing=True)
        yield tx.commit()
        return mine

    found = runs(lambda db: [Process("a", lambda: claim(db)), Process("b", lambda: claim(db))])
    assert len(found) == 2
    assert all(run.reply("a") + run.reply("b") == 1 for run in found)


def test_an_insert_goes_through_when_the_other_writer_dies():
    def claim(db):
        tx = db.begin()
        mine = yield tx.insert("claims", "T42", on_conflict_do_nothing=True)
        yield tx.commit()
        return mine

    found = runs(
        lambda db: [Process("a", lambda: claim(db), crashes=True), Process("b", lambda: claim(db))], max_crashes=1
    )
    assert all(run.reply("b") == 1 for run in found if run.stopped("a"))
    assert all(len(run.world.rows("claims")) == 1 for run in found)


def test_a_plain_insert_of_an_existing_key_is_an_error():
    def twice(db):
        yield db.insert("claims", "T42")
        try:
            yield db.insert("claims", "T42")
        except UniqueViolation:
            return "refused"
        return "accepted"

    (run,) = runs(lambda db: [Process("a", lambda: twice(db))])
    assert run.reply("a") == "refused"


def test_opposite_lock_orders_are_reported_as_a_deadlock():
    def lock_both(db, first, second):
        tx = db.begin()
        yield tx.select_for_update("items", first)
        yield tx.select_for_update("items", second)
        yield tx.commit()

    found = runs(lambda db: [Process("a", lambda: lock_both(db, 1, 2)), Process("b", lambda: lock_both(db, 2, 1))])
    assert any(run.stuck for run in found) and not all(run.stuck for run in found)


def test_a_handler_that_forgets_to_commit_is_a_bug_in_the_model():
    def sloppy(db):
        tx = db.begin()
        yield tx.update("items", 1, to={"owner": "W"})

    with pytest.raises(ModelError):
        runs(lambda db: [Process("a", lambda: sloppy(db))])
