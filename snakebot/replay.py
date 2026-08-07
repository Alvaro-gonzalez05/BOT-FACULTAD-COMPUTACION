"""Watch a match back, frame by frame, with the tactical layers drawn on.

Two sources feed this:

* a ``game_<id>.log`` transcript written during a real match, so you can see
  exactly what the bot saw when it lost, and
* a simulated match recorded here, for watching two weight sets fight.

Playback clears the screen between frames when it is talking to a terminal, so
the board animates in place rather than scrolling past.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import view
from .engine import Position
from .search import Weights
from .simulator import play_match, starting_position, strategy_policy
from .strategy import SnakeStrategy
from .tracker import GameTracker

CLEAR = "\x1b[H\x1b[2J"


@dataclass
class Frame:
    position: Position
    note: str


def frames_from_transcript(path: str | Path) -> Iterator[Frame]:
    """Rebuild the positions from a match transcript.

    The transcript holds the raw boards the server sent, so this replays what
    really happened -- including the turn where it went wrong.
    """
    tracker: GameTracker | None = None
    pending: Position | None = None

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        marker, _, payload = line.partition(" ")
        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            continue

        if marker == "<" and message.get("event") == "your_turn":
            data = message.get("data", {})
            if tracker is None:
                tracker = GameTracker(
                    game_id=data.get("game_id", "replay"), my_side=data.get("side", "A")
                )
            pending = tracker.observe(data)
        elif marker == ">" and message.get("action") == "move" and pending is not None:
            direction = message.get("data", {}).get("direction", "?")
            yield Frame(pending, f"  -> we played {direction}")
            pending = None
        elif marker == "<" and message.get("event") == "game_over":
            data = message.get("data", {})
            if pending is not None:
                yield Frame(pending, "  -> game over")
            winner = data.get("winner")
            note = f"  final: {data.get('score_1')} - {data.get('score_2')}"
            if winner:
                note += f", winner {winner}"
            if pending is None and tracker is not None:
                # No board to draw, but the result still deserves a line.
                print(note)


def record_simulated_match(
    challenger: Weights,
    champion: Weights,
    *,
    seed: int = 0,
    moves: int = 200,
    depth: int = 6,
) -> list[Frame]:
    """Play one match between two weight sets and keep every position."""
    import random

    rng = random.Random(seed)
    position = starting_position(remaining_moves=moves, rng=rng)
    frames: list[Frame] = []

    def capture(state: Position) -> None:
        frames.append(Frame(state, ""))

    def strategy(weights: Weights) -> SnakeStrategy:
        return SnakeStrategy(time_budget=3600.0, max_depth=depth, weights=weights)

    result = play_match(
        strategy_policy(strategy(challenger)),
        strategy_policy(strategy(champion)),
        position=position,
        rng=rng,
        on_turn=capture,
    )
    if frames:
        frames[-1] = Frame(
            frames[-1].position,
            f"  {result.winner} wins {result.my_score}-{result.opp_score}"
            + (f" ({result.crashed} crashed)" if result.crashed else ""),
        )
    return frames


def play(frames, *, delay: float = 0.15, animate: bool | None = None) -> int:
    """Print every frame; returns how many were shown."""
    if animate is None:
        animate = bool(getattr(sys.stdout, "isatty", lambda: False)())

    shown = 0
    for index, frame in enumerate(frames, start=1):
        if animate:
            print(CLEAR, end="")
        print(f"turn {index}")
        print(view.render(frame.position, note=frame.note))
        shown += 1
        if animate and delay:
            time.sleep(delay)
    return shown
