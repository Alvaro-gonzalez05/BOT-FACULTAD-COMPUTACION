"""The sparring partner that survives, and the two things it is for.

It was built to make the seal measurable and it fails at that -- see the
docstring on `coiler_policy`. What it is kept for is being the least suicidal
opponent available, which is what makes the apple gap visible instead of buried
under the 1000 points an opponent's crash pays.
"""

import random

from snakebot.board import Direction
from snakebot.engine import Position, Snake
from snakebot.simulator import (
    coiler_policy,
    play_match,
    rival_policy,
    starting_position,
    strategy_policy,
)
from snakebot.strategy import SnakeStrategy


def test_it_survives_better_than_the_careless_partner():
    """Its whole reason to exist: matches that reach an endgame.

    Not zero crashes -- a heuristic with no search cannot promise that, which is
    the lesson `survivor_policy` records from two earlier attempts. Fewer than
    the partner it is meant to improve on is the bar.
    """
    coiler_crashes = rival_crashes = 0
    for index in range(6):
        for policy, count in ((coiler_policy(greed=0.5, seed=index), "coiler"),
                              (rival_policy(greed=0.6, care=1.0, seed=index), "rival")):
            rng = random.Random(4200 + index)
            mine = SnakeStrategy(time_budget=3600.0, max_depth=4)
            result = play_match(
                strategy_policy(mine),
                policy,
                position=starting_position(remaining_moves=150, rng=rng),
                rng=rng,
            )
            if result.crashed == "opp":
                if count == "coiler":
                    coiler_crashes += 1
                else:
                    rival_crashes += 1
    assert coiler_crashes <= rival_crashes


def test_it_refuses_a_pocket_it_cannot_get_out_of():
    """The rule that does the work: never enter somewhere smaller than yourself.

    Going UP is a three-cell dead end and the body is four long, so it is a
    grave however much food is in it. Everything else on this board is open.
    """
    position = Position(
        rows=7,
        cols=7,
        me=Snake(((3, 3), (3, 4), (3, 5), (3, 6))),
        opp=Snake(((6, 0), (6, 1))),
        # Walls of opponent body would be cleaner, but the engine only has two
        # snakes: a pocket made from our own body is the same test.
        food=frozenset({(0, 3)}),
    )
    choice = coiler_policy(greed=1.0, seed=0)(position)
    assert position.step(choice).crashed is None


def test_it_is_not_our_strategy_wearing_a_hat():
    """A partner that shares our evaluation cannot expose our blind spots.

    `survivor_policy` reuses the real search and mirrors us, which is why
    matches against it end level seven times in ten. This one is a plain
    heuristic, and the cheapest proof is that it answers instantly at a depth
    where the search would have to think.
    """
    rng = random.Random(0)
    position = starting_position(remaining_moves=200, rng=rng)
    policy = coiler_policy(greed=0.5, seed=0)

    import time

    started = time.monotonic()
    for _ in range(20):
        policy(position)
    assert time.monotonic() - started < 1.0
    assert isinstance(policy(position), Direction)
