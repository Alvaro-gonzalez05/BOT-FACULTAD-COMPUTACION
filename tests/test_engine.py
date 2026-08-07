from snakebot.board import Direction
from snakebot.engine import (
    CRASH_PENALTY,
    FOOD_REWARD,
    OPPONENT_CRASH_REWARD,
    SURVIVE_REWARD,
    Position,
    Snake,
)


def position(**overrides) -> Position:
    base = dict(
        rows=5,
        cols=5,
        me=Snake(((2, 2), (2, 1), (2, 0))),
        opp=Snake(((0, 4), (1, 4))),
        food=frozenset({(2, 3)}),
    )
    base.update(overrides)
    return Position(**base)


def test_moving_onto_food_grows_and_scores():
    after = position().step(Direction.RIGHT)
    assert len(after.me) == 4
    assert after.me.head == (2, 3)
    assert after.my_score == FOOD_REWARD
    assert (2, 3) not in after.food
    assert after.my_turn is False


def test_a_plain_move_drops_the_tail():
    after = position().step(Direction.UP)
    assert after.me.body == ((1, 2), (2, 2), (2, 1))
    assert after.my_score == SURVIVE_REWARD


def test_walls_are_fatal():
    state = position(me=Snake(((0, 0),)))
    after = state.step(Direction.UP)
    assert after.crashed == "me"
    assert after.my_score == CRASH_PENALTY
    assert after.opp_score == OPPONENT_CRASH_REWARD


def test_reversing_into_your_own_neck_is_fatal():
    after = position().step(Direction.LEFT)
    assert after.crashed == "me"


def test_hitting_the_opponent_is_fatal_for_the_mover():
    state = position(me=Snake(((0, 3), (1, 3))))
    after = state.step(Direction.RIGHT)
    assert after.crashed == "me"


def test_legal_moves_exclude_walls_and_bodies():
    directions = {direction for direction, _ in position().legal_moves()}
    assert directions == {Direction.UP, Direction.DOWN, Direction.RIGHT}


def test_flipping_swaps_both_players():
    flipped = position().flipped()
    assert flipped.me.head == (0, 4)
    assert flipped.opp.head == (2, 2)
    assert flipped.my_turn is False


def test_a_boxed_in_snake_has_no_legal_moves():
    state = Position(
        rows=3,
        cols=3,
        me=Snake(((0, 0), (0, 1), (1, 1), (1, 0))),
        opp=Snake(((2, 2),)),
        food=frozenset(),
    )
    assert list(state.legal_moves()) == []
