"""It has to actually eat the apple, not just keep claiming it.

The position below is turn 31 of `logs/game_f62d91b2-9276-11f1-b686-a2aa0928bc73`,
a real match lost 249-348 with nobody crashing. The bot orbited a 2x3 rectangle
for the last 130 turns of it -- 30 distinct cells out of 225, our length never
leaving 3 -- with an apple sitting two steps away the whole time.
"""

from snakebot.board import Direction
from snakebot.engine import Position, Snake
from snakebot.search import Search, Weights
from snakebot.strategy import SnakeStrategy

FAST = dict(time_budget=0.05, max_depth=6)


def test_eating_an_apple_raises_the_evaluation():
    """The invariant the whole appetite problem reduces to.

    An apple carries positional value while it sits there -- closeness, and the
    race for it -- and eating takes that value off the board with it. If the
    positional value is worth more than the 100 points and one segment that
    eating pays, the evaluation's advice is to stand next to food forever. It
    measured -180 before this was fixed: -52.8 standing beside the apple,
    -233.2 having eaten it.
    """
    one_step_away = Position(
        rows=15,
        cols=15,
        me=Snake(((4, 5), (4, 6), (5, 6))),
        opp=Snake(((3, 7), (3, 6), (2, 6), (2, 7), (2, 8))),
        food=frozenset({(4, 4), (13, 10), (14, 3)}),
        my_score=31,
        opp_score=229,
        remaining_moves=238,
    )
    search = Search(time_budget=1.0, max_depth=8, weights=Weights())
    eaten = one_step_away.step(Direction.LEFT)
    assert eaten.my_score == one_step_away.my_score + 100  # it really is an apple
    assert search.evaluate(eaten) > search.evaluate(one_step_away)


def the_turn_it_walked_away() -> Position:
    """Head at (4,6). The apple at (4,4) is two steps west, and unopposed."""
    return Position(
        rows=15,
        cols=15,
        me=Snake(((4, 6), (5, 6), (5, 7))),
        opp=Snake(((3, 7), (3, 6), (2, 6), (2, 7), (2, 8))),
        food=frozenset({(4, 4), (13, 10), (14, 3)}),
        my_score=31,
        opp_score=229,
        remaining_moves=238,
    )


def test_it_walks_towards_an_uncontested_apple_two_steps_away():
    # LEFT is one step from the apple and the opponent is 8 away from it; there
    # is no case for going anywhere else. It played RIGHT in the real match,
    # because stepping closer flipped a *twelve*-step-away apple into the
    # opponent's column of the food race, and that claim was priced like food.
    decision = SnakeStrategy(**FAST).decide(the_turn_it_walked_away())
    assert decision.direction is Direction.LEFT


def test_it_does_not_orbit_when_there_is_food_to_take():
    """Six moves from that position should end with the apple eaten.

    The cycle it actually played was up, right, right, down, left, left -- back
    to where it started, having gained nothing but the survival point. Two moves
    is enough to reach this apple, so six is generous.
    """
    position = the_turn_it_walked_away()
    strategy = SnakeStrategy(**FAST)
    starting_length = len(position.me)

    for _ in range(6):
        decision = strategy.decide(position)
        position = position.step(decision.direction)
        assert position.crashed is None
        # The opponent is a bystander here: it holds still, which only makes the
        # apple safer to take.
        position = position.step(next(iter(position.legal_moves()))[0])

    assert len(position.me) > starting_length
