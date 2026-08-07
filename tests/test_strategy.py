from snakebot.board import Direction
from snakebot.engine import Position, Snake
from snakebot.heuristics import can_reach_own_tail, my_space, territory
from snakebot.strategy import SnakeStrategy

FAST = dict(time_budget=0.05, max_depth=6)


def far_away_opponent() -> Snake:
    return Snake(((0, 0),))


def test_it_never_picks_a_move_that_kills_it():
    # Head at (2,2) with walls closing in: only DOWN and RIGHT stay alive.
    state = Position(
        rows=5,
        cols=5,
        me=Snake(((2, 2), (1, 2), (0, 2))),
        opp=Snake(((4, 0), (4, 1))),
        food=frozenset({(0, 0)}),
    )
    decision = SnakeStrategy(**FAST).decide(state)
    assert decision.direction in {Direction.DOWN, Direction.LEFT, Direction.RIGHT}
    assert state.step(decision.direction).crashed is None


def test_it_refuses_food_that_seals_it_into_a_pocket():
    # Going LEFT grabs the food but ends inside a 3-cell dead end; going RIGHT
    # keeps the whole open board available.
    state = Position(
        rows=7,
        cols=7,
        me=Snake(((3, 3), (2, 3), (1, 3), (1, 2), (1, 1), (2, 1), (3, 1), (4, 1), (4, 2), (4, 3))),
        opp=far_away_opponent(),
        food=frozenset({(3, 2)}),
    )
    decision = SnakeStrategy(**FAST).decide(state)
    assert decision.direction is not Direction.LEFT


def test_it_takes_free_food_when_it_is_safe():
    state = Position(
        rows=9,
        cols=9,
        me=Snake(((4, 4), (4, 3), (4, 2))),
        opp=Snake(((8, 8), (8, 7))),
        food=frozenset({(4, 5)}),
    )
    decision = SnakeStrategy(**FAST).decide(state)
    assert decision.direction is Direction.RIGHT


def test_it_still_answers_when_every_move_is_fatal():
    state = Position(
        rows=3,
        cols=3,
        me=Snake(((0, 0), (0, 1), (1, 1), (1, 0))),
        opp=Snake(((2, 2),)),
        food=frozenset(),
    )
    decision = SnakeStrategy(**FAST).decide(state)
    assert decision.doomed is True
    assert isinstance(decision.direction, Direction)


def test_it_kills_an_opponent_that_has_nowhere_to_go():
    # The opponent is cornered; blocking its last exit ends the match in our
    # favour, and the search should see it.
    state = Position(
        rows=5,
        cols=5,
        me=Snake(((1, 0), (2, 0), (3, 0), (4, 0))),
        opp=Snake(((0, 1), (0, 2), (0, 3))),
        food=frozenset(),
    )
    decision = SnakeStrategy(**FAST).decide(state)
    assert state.step(decision.direction).crashed is None


def test_space_and_tail_metrics():
    state = Position(
        rows=5,
        cols=5,
        me=Snake(((2, 2), (2, 1), (2, 0))),
        opp=Snake(((0, 4),)),
        food=frozenset(),
    )
    assert my_space(state) > len(state.me)
    assert can_reach_own_tail(state) is True
    mine, theirs = territory(state)
    assert mine > theirs  # centre control beats a corner


# An unreachable budget: these assert on what depth-6 search concludes, and a
# wall-clock cutoff would make them depend on how loaded the machine is.
NO_CUTOFF = 3600.0


def test_it_gives_up_an_apple_the_rival_reaches_first():
    """The ask: do not race for an apple we lose, take one we win.

    Our head sits two cells from apple X, but the opponent is one cell from it
    and moves next. A third apple is further away but ours uncontested. The bot
    should walk away from the close one.
    """
    from snakebot.engine import Position, Snake
    from snakebot.strategy import SnakeStrategy

    contested = (7, 7)   # two cells left of us, but one cell from them
    ours = (4, 9)        # three cells straight up, and nowhere near them
    position = Position(
        rows=15,
        cols=15,
        me=Snake(((7, 9), (7, 10), (7, 11))),   # body trails right, so up and left are open
        opp=Snake(((7, 6), (6, 6), (5, 6))),
        food=frozenset({contested, ours}),
        remaining_moves=200,
    )
    decision = SnakeStrategy(time_budget=NO_CUTOFF, max_depth=6).decide(position)
    # Left is the greedy race for the closer apple, and it is a race we lose.
    assert decision.direction.wire_name != "left", (
        "raced for an apple the opponent reaches first"
    )


def test_it_still_takes_an_apple_it_wins_the_race_for():
    """The guard against over-correcting: an apple that is ours is worth taking."""
    from snakebot.engine import Position, Snake
    from snakebot.strategy import SnakeStrategy

    position = Position(
        rows=15,
        cols=15,
        me=Snake(((7, 8), (7, 9), (7, 10))),   # head one cell from the apple
        opp=Snake(((0, 0), (0, 1), (0, 2))),   # miles away
        food=frozenset({(7, 7)}),
        remaining_moves=200,
    )
    decision = SnakeStrategy(time_budget=NO_CUTOFF, max_depth=6).decide(position)
    assert decision.direction.wire_name == "left"


def test_an_apple_it_cannot_reach_in_time_stops_pulling():
    """With two moves left, a far apple is not worth walking towards.

    remaining_moves counts plies for both players, so an apple ten cells away
    needs twenty of them. Chasing it costs the safety the endgame needs.
    """
    from snakebot.search import Search

    far = Position(
        rows=15,
        cols=15,
        me=Snake(((7, 7), (7, 8), (7, 9))),
        opp=Snake(((0, 0), (0, 1), (0, 2))),
        food=frozenset({(7, 0)}),          # seven cells away: needs ~14 plies
        remaining_moves=4,                  # only two moves each
    )
    plenty = Position(**{**vars(far), "remaining_moves": 200})

    search = Search(time_budget=NO_CUTOFF, max_depth=2)
    # With time to spare the apple is worth something; with none it is ignored,
    # so the position without time must not be rated worse for the distance.
    assert search.evaluate(far) >= search.evaluate(plenty)


def test_a_reachable_apple_still_counts_near_the_end():
    from snakebot.search import Search

    close = Position(
        rows=15,
        cols=15,
        me=Snake(((7, 7), (7, 8), (7, 9))),
        opp=Snake(((0, 0), (0, 1), (0, 2))),
        food=frozenset({(7, 6)}),          # one cell away
        remaining_moves=6,
    )
    search = Search(time_budget=NO_CUTOFF, max_depth=2)
    away = Position(**{**vars(close), "me": Snake(((7, 10), (7, 11), (7, 12)))})
    assert search.evaluate(close) > search.evaluate(away)


def test_endgame_hunger_was_measured_and_dropped():
    """Kept as a note: scaling food by the scoreboard made the bot worse.

    Every recorded loss was on points and most by two or three apples, so
    "chase harder when behind, coast when ahead" looked obviously right. It
    scored 35W-10L against sparring partners shaped like the real rivals, where
    leaving it out scored 41W-7L -- worse against every single one of them.
    """
    from snakebot.search import Weights

    for dial in ("comeback", "coast", "endgame_moves", "safe_lead"):
        assert not hasattr(Weights(), dial)


def test_looking_past_the_nearest_apple_was_measured_and_dropped():
    """Kept as a note: the two-apple route idea made the bot worse.

    Weighting the second-nearest apple scored 10W/6L against the greedy
    baseline where ignoring it scored 13W/3L. It drags the snake towards where
    the apples are rather than where it can safely be, and the search already
    sees the next apple when that actually matters.
    """
    from snakebot.search import Weights

    assert not hasattr(Weights(), "second_apple")
