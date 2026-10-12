"""The arithmetic, and the save-and-crash histories."""

import pytest

from loot import numbers, saves
from loot.__main__ import main


def test_one_in_a_thousand_needs_4603_attempts_for_99_percent():
    assert numbers.attempts_needed(0.001) == 4603
    assert numbers.chance_of_at_least_one(0.001, 4603) >= 0.99
    assert numbers.chance_of_at_least_one(0.001, 4602) < 0.99


def test_two_percent_needs_228_attempts():
    assert numbers.attempts_needed(0.02) == 228


def test_the_race_chance_is_window_over_spread():
    assert numbers.race_chance(1, 50) == 0.02
    assert numbers.race_chance(80, 50) == 1.0
    assert numbers.simulate_race(1, 50) == pytest.approx(0.02, abs=0.002)


def test_nine_billion_is_34_doublings_from_one():
    assert numbers.after_rounds(1, 33) == 8_589_934_592
    assert numbers.rounds_to_reach(1, 9_000_000_000) == 34
    assert numbers.rounds_to_reach(64, 64) == 0


def test_halving_the_detection_time_takes_the_square_root_of_the_damage():
    full = numbers.growth_before_detection(180, 5)
    half = numbers.growth_before_detection(90, 5)
    assert full == 2**36
    assert half**2 == full


def test_the_number_of_orderings():
    assert numbers.orderings(6, 6) == 924
    assert numbers.orderings(3, 3) == 20
    assert numbers.orderings(2, 2, 2) == 90


def test_saving_each_character_on_its_own_can_duplicate_or_destroy_the_sword():
    counted = saves.outcomes(saves.after_restart_separate_saves)
    assert sum(counted.values()) == 16
    assert counted["duplicate: 2 x Sword 17"] == 2
    assert counted["the sword is gone"] == 2
    assert counted["the trade survived"] == 2


def test_the_buyer_saved_and_the_seller_not_means_two_swords():
    history = (saves.TRADE, saves.SAVE_BUYER)
    disk = saves.after_restart_separate_saves(history)
    assert saves.judge(history, disk) == "duplicate: 2 x Sword 17"


def test_a_trade_in_one_transaction_never_breaks_the_rules():
    assert saves.broken(saves.after_restart_trade_in_one_transaction) == []
    assert len(saves.broken(saves.after_restart_separate_saves)) == 4


@pytest.mark.parametrize("command", [["numbers"], ["saves"], ["show", "01", "naive"], ["show", "06", "reference"]])
def test_the_commands_run(command, capsys):
    assert main(command) == 0
    assert capsys.readouterr().out.strip()
