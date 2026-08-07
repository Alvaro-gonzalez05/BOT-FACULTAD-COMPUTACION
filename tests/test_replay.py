"""Replaying a match from its transcript."""

import json

from snakebot.replay import frames_from_transcript, play, record_simulated_match
from snakebot.search import Weights
from snakebot.simulator import starting_position

BOARD = starting_position(remaining_moves=300).render("A")


def transcript(tmp_path, lines):
    path = tmp_path / "game_x.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def turn(token, remaining=300):
    return "< " + json.dumps(
        {
            "event": "your_turn",
            "data": {
                "board": BOARD,
                "rows": 15,
                "cols": 15,
                "side": "A",
                "remaining_moves": remaining,
                "game_id": "x",
                "turn_token": token,
                "score_1": 0,
                "score_2": 0,
            },
        }
    )


def move(token, direction):
    return "> " + json.dumps(
        {"action": "move", "data": {"game_id": "x", "turn_token": token, "direction": direction}}
    )


def test_a_transcript_replays_one_frame_per_turn(tmp_path):
    path = transcript(
        tmp_path,
        [
            '< {"event": "list_users", "data": {"users": ["a"]}}',
            turn("t1", 300),
            move("t1", "right"),
            turn("t2", 298),
            move("t2", "up"),
        ],
    )
    frames = list(frames_from_transcript(path))
    assert len(frames) == 2
    assert "right" in frames[0].note
    assert "up" in frames[1].note
    assert frames[0].position.remaining_moves == 300


def test_replaying_survives_a_truncated_transcript(tmp_path):
    """A match that was cut off mid-turn still replays what it has."""
    path = transcript(tmp_path, [turn("t1"), move("t1", "left"), turn("t2"), "> {broken"])
    frames = list(frames_from_transcript(path))
    assert len(frames) == 1


def test_replaying_an_empty_transcript_yields_nothing(tmp_path):
    assert list(frames_from_transcript(transcript(tmp_path, ["", "   "]))) == []


def test_a_simulated_match_is_recorded_frame_by_frame():
    frames = record_simulated_match(Weights(), Weights(), seed=1, moves=20, depth=2)
    assert len(frames) > 1
    assert frames[-1].note  # the last frame carries the result
    assert all(frame.position.rows == 15 for frame in frames)


def test_playback_prints_every_frame(capsys):
    frames = record_simulated_match(Weights(), Weights(), seed=2, moves=10, depth=2)
    shown = play(frames, delay=0, animate=False)
    output = capsys.readouterr().out
    assert shown == len(frames)
    assert output.count("turn ") >= shown
    assert "\x1b[H" not in output  # no screen clearing when not animating
