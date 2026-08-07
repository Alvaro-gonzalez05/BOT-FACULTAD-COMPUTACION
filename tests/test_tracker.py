from snakebot.board import Direction
from snakebot.tracker import DirectionCalibration, GameTracker


def turn(board: str, **overrides) -> dict:
    payload = {
        "board": board,
        "side": "A",
        "remaining_moves": 300,
        "player_1": "me@x.com",
        "score_1": 0,
        "player_2": "you@x.com",
        "score_2": 0,
        "game_id": "g_1",
        "turn_token": "t_1",
    }
    payload.update(overrides)
    return payload


# A moves right, B moves up.
FIRST = "\n".join(["|aaA  |", "|     |", "|  *  |", "|     |", "|Bbb  |"])
SECOND = "\n".join(["| aaA |", "|     |", "|  *  |", "|B    |", "|bb   |"])


def test_it_orders_the_snake_head_first():
    tracker = GameTracker(game_id="g_1")
    position = tracker.observe(turn(FIRST))
    assert position.me.body == ((0, 2), (0, 1), (0, 0))
    assert position.me.head == (0, 2)
    assert position.me.tail == (0, 0)
    assert position.food == frozenset({(2, 2)})


def test_it_keeps_the_order_in_sync_across_turns():
    tracker = GameTracker(game_id="g_1")
    tracker.observe(turn(FIRST))
    tracker.record_sent(Direction.RIGHT)
    position = tracker.observe(turn(SECOND))
    assert position.me.body == ((0, 3), (0, 2), (0, 1))
    assert position.opp.body == ((3, 0), (4, 0), (4, 1))
    # Two rebuilds, one per snake, both on the opening board: after that the
    # ordering is carried forward instead of being guessed again.
    assert tracker.resyncs == 2


def test_it_tracks_the_opponent_too():
    tracker = GameTracker(game_id="g_1")
    position = tracker.observe(turn(FIRST))
    assert position.opp.head == (4, 0)
    assert position.opp.body == ((4, 0), (4, 1), (4, 2))


def test_scores_follow_the_player_slot():
    tracker = GameTracker(game_id="g_1", my_email="you@x.com")
    position = tracker.observe(turn(FIRST, side="B", score_1=10, score_2=99))
    assert position.my_score == 99
    assert position.opp_score == 10


def test_calibration_learns_an_inverted_axis():
    calibration = DirectionCalibration()
    assert calibration.wire_name(Direction.UP) == "up"
    # We sent "up" but the head moved one row down: the axis is flipped.
    assert calibration.observe(Direction.UP, (1, 0)) is True
    assert calibration.wire_name(Direction.DOWN) == "up"


def test_calibration_ignores_a_desync():
    calibration = DirectionCalibration()
    assert calibration.observe(Direction.UP, (0, 0)) is False
    assert calibration.observe(Direction.UP, (3, 2)) is False
    assert calibration.wire_name(Direction.UP) == "up"
