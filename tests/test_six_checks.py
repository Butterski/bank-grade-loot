"""The six checks from the talk, as tests.

A design passes a check only if no ordering breaks it.

The weak designs are tested too: each must fail the checks it is known to
fail. A check that cannot catch the bug it was written for proves nothing.
"""

import pytest

from loot import report
from loot.checks import (
    CHECKS,
    check_01_two_buyers,
    check_05_event_arrives_twice,
    check_06_payment_without_grant,
    double_spend,
    lock_order,
)
from loot.shop import SHOP, WALLET
from loot.trade import TRADE, TRADE_DESIGNS

# For each check: the designs that must hold. Every other design must fail it.
TRANSACTIONAL = {"optimistic-rows-ignored", "optimistic", "lock-and-check", "result-stored-last", "reference"}
HOLDS = {
    "01": {"optimistic", "lock-and-check", "result-stored-last", "reference"},
    "02": {"reference"},
    "03": TRANSACTIONAL | {"naive-in-transaction"},
    "04": {"result-stored-last", "reference"},
    "05": {"once-per-purchase"},
    "06": {"reference"},
}

CASES = [(check, design.name) for check in CHECKS for design in report.designs_for(check)]


@pytest.mark.parametrize("check, design", CASES)
def test_every_design_against_every_check(check, design):
    verdict = report.run_check(check, design)
    if design in HOLDS[check]:
        assert verdict.holds, f"{verdict.broken_total} of {verdict.orderings} break it: {dict(verdict.broken)}"
    else:
        assert not verdict.holds, "this design is supposed to fail here: the check has lost its teeth"


def test_only_the_reference_designs_pass_everything():
    passes_all = {
        design
        for design in {name for _, name in CASES}
        if all(design in HOLDS[check] for check, name in CASES if name == design)
    }
    assert passes_all == {"reference", "once-per-purchase"}


# --- the numbers quoted in the talk -----------------------------------------


def test_the_naive_trade_duplicates_the_sword_in_910_of_914_orderings():
    verdict = check_01_two_buyers(TRADE["naive"])
    assert (verdict.broken_total, verdict.orderings) == (910, 914)
    assert set(verdict.broken) == {"duplicate: 2 x Sword 17"}


def test_a_transaction_alone_still_duplicates_the_sword():
    verdict = check_01_two_buyers(TRADE["naive-in-transaction"])
    assert (verdict.broken_total, verdict.orderings) == (238, 240)
    assert set(verdict.broken) == {"duplicate: 2 x Sword 17"}


def test_ignoring_the_row_count_takes_the_coins_and_gives_no_sword():
    verdict = check_01_two_buyers(TRADE["optimistic-rows-ignored"])
    assert set(verdict.broken) == {"paid, no sword"}


def test_storing_the_result_last_survives_a_late_retry_but_not_two_copies_in_flight():
    in_flight = report.run_check("02", "result-stored-last")
    assert (in_flight.broken_total, in_flight.orderings) == (20, 22)
    assert set(in_flight.broken) == {"wrong answer to the repeated request"}
    assert report.run_check("04", "result-stored-last").holds


def test_looking_first_passes_only_when_the_messages_do_not_overlap():
    verdict = check_05_event_arrives_twice(WALLET["look-first"])
    assert (verdict.broken_total, verdict.orderings) == (56, 60)


def test_event_ids_alone_do_not_survive_a_second_sender():
    verdict = check_05_event_arrives_twice(WALLET["once-per-event"])
    assert set(verdict.broken) == {"200 gems, same purchase under a new event ID"}


def test_the_reference_shop_holds_in_every_ordering_with_two_stops():
    verdict = check_06_payment_without_grant(SHOP["reference"], max_crashes=2)
    assert verdict.holds
    assert verdict.orderings == 13_610


def test_only_the_designs_that_guard_the_wallet_survive_a_double_spend():
    holds = {design.name for design in TRADE_DESIGNS if double_spend(design).holds}
    assert holds == TRANSACTIONAL
    naive = double_spend(TRADE["naive"])
    assert set(naive.broken) == {"negative balance"}
    assert (naive.broken_total, naive.orderings) == (420, 464)


def test_an_outbox_without_a_careful_wallet_grants_twice():
    verdict = check_06_payment_without_grant(SHOP["outbox"])
    assert "200 gems for one payment" in verdict.broken


def test_a_fixed_lock_order_removes_the_deadlock():
    assert lock_order(wallets_in_fixed_order=False).broken["deadlock"] == 6
    assert lock_order(wallets_in_fixed_order=True).holds


# --- the examples are what people will read ---------------------------------


def test_the_example_for_the_naive_trade_ends_with_two_swords():
    run = check_01_two_buyers(TRADE["naive"]).example()
    assert run.problem == "duplicate: 2 x Sword 17"
    assert run.reply("Stan").ok and run.reply("Kenny").ok
    assert "0 rows" in report.trace_text(run)


def test_show_can_pick_a_problem_by_name():
    assert "paid, no gems" in report.show("06", "outbox", "no gems")
    assert "Nothing to show" in report.show("06", "reference")
