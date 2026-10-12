"""The arithmetic behind three sentences in the talk.

1. "It only happens one time in a thousand" is not a defence.
2. A dupe doubles. That is why the numbers end in billions.
3. With a dupe, the time until somebody notices is in the exponent.

Everything here is a formula you can check by hand. `simulate_race` is the
only function that uses random numbers, and it exists to check a formula.
"""

from __future__ import annotations

import math
import random


def chance_of_at_least_one(p: float, attempts: int) -> float:
    """Chance that an event with probability `p` per attempt happens at least once."""
    return 1 - (1 - p) ** attempts


def attempts_needed(p: float, confidence: float = 0.99) -> int:
    """Smallest number of attempts that gives at least `confidence` of one success."""
    if not 0 < p < 1 or not 0 < confidence < 1:
        raise ValueError("p and confidence must be between 0 and 1")
    return math.ceil(math.log(1 - confidence) / math.log(1 - p))


def race_chance(window_ms: float, spread_ms: float) -> float:
    """Chance that two requests overlap, for one attempt.

    The first request reads at some moment and removes the item `window_ms`
    later. The second request starts anywhere within `spread_ms` before or
    after the first. Both read the old state when the two starts are closer
    than the window, so the chance is window / spread.
    """
    return min(1.0, window_ms / spread_ms)


def simulate_race(window_ms: float, spread_ms: float, trials: int = 200_000, seed: int = 17) -> float:
    """The same chance, measured by throwing random start times."""
    rng = random.Random(seed)
    hits = sum(1 for _ in range(trials) if abs(rng.uniform(-spread_ms, spread_ms)) < window_ms)
    return hits / trials


def after_rounds(start: int, rounds: int) -> int:
    """Holdings after `rounds` of a dupe that doubles what you hold."""
    return start * 2**rounds


def rounds_to_reach(start: int, target: int) -> int:
    """How many doublings turn `start` into at least `target`."""
    if start <= 0:
        raise ValueError("start must be positive")
    rounds = 0
    while start < target:
        start *= 2
        rounds += 1
    return rounds


def growth_before_detection(minutes_to_detect: float, minutes_per_round: float) -> float:
    """By how much a doubling dupe multiplies the stake before somebody notices.

    This is an upper bound for a pure doubling loop. Real loops are slowed
    down by inventory size, cooldowns and human hands.
    """
    return 2 ** (minutes_to_detect / minutes_per_round)


def orderings(*statements: int) -> int:
    """In how many orders can requests with this many statements each interleave?"""
    total = math.factorial(sum(statements))
    for count in statements:
        total //= math.factorial(count)
    return total
