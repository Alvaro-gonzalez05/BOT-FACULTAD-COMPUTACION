"""A live tactical view of a match, drawn one turn at a time.

The board the server sends is just letters. This draws what the bot is actually
*thinking about* on top of it: where both snakes are, the shortest route to the
apple, how much room each snake has left, and whether either of them is already
sealed in. When a match goes wrong, this is what shows you why.

Colour is ANSI and switches itself off when the output is not a terminal, so
piping the bot to a file still gives readable text.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from .engine import Position
from .heuristics import (
    INFINITE,
    decay_map,
    distance_map,
    reachable_space,
    shortest_path,
)

RESET = "\x1b[0m"
COLOURS = {
    "me": "\x1b[92m",       # bright green -- us
    "my_head": "\x1b[1;92m",
    "opp": "\x1b[91m",      # bright red -- them
    "opp_head": "\x1b[1;91m",
    "food": "\x1b[1;93m",   # yellow
    "path": "\x1b[90m",     # dim -- our route to the apple
    "warn": "\x1b[1;91m",
    "good": "\x1b[1;92m",
    "dim": "\x1b[90m",
}


def colour_enabled(stream=sys.stdout) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


@dataclass(frozen=True)
class Layers:
    """Everything the view knows about one turn."""

    my_room: int
    opp_room: int
    my_length: int
    opp_length: int
    my_food_distance: int
    opp_food_distance: int
    path: list[tuple[int, int]]
    i_am_trapped: bool
    they_are_trapped: bool
    winning_the_race: bool

    @classmethod
    def of(cls, position: Position) -> "Layers":
        rows, cols = position.rows, position.cols
        blocked = position.blocked_cells()
        decay = decay_map(position.me, position.opp)

        my_length = len(position.me)
        opp_length = len(position.opp)
        my_room = reachable_space(rows, cols, position.me.head, decay, limit=rows * cols)
        opp_room = reachable_space(rows, cols, position.opp.head, decay, limit=rows * cols)

        my_distances = distance_map(rows, cols, position.me.head, blocked - {position.me.head})
        opp_distances = distance_map(rows, cols, position.opp.head, blocked - {position.opp.head})

        target, my_food = None, INFINITE
        for food in position.food:
            distance = my_distances.get(food, INFINITE)
            if distance < my_food:
                target, my_food = food, distance
        opp_food = min(
            (opp_distances.get(f, INFINITE) for f in position.food), default=INFINITE
        )

        path: list[tuple[int, int]] = []
        if target is not None:
            path = shortest_path(
                rows, cols, position.me.head, target, blocked - {position.me.head, target}
            )

        return cls(
            my_room=my_room,
            opp_room=opp_room,
            my_length=my_length,
            opp_length=opp_length,
            my_food_distance=my_food,
            opp_food_distance=opp_food,
            path=path,
            i_am_trapped=my_room <= my_length,
            they_are_trapped=opp_room <= opp_length,
            winning_the_race=my_food < opp_food,
        )


def render(position: Position, *, colour: bool | None = None, note: str = "") -> str:
    """The board plus the tactical layers, ready to print."""
    layers = Layers.of(position)
    use_colour = colour_enabled() if colour is None else colour

    def paint(text: str, key: str) -> str:
        return f"{COLOURS[key]}{text}{RESET}" if use_colour else text

    my_body = set(position.me.body[1:])
    opp_body = set(position.opp.body[1:])
    on_path = set(layers.path[1:]) - position.food

    lines = []
    for row in range(position.rows):
        cells = []
        for col in range(position.cols):
            cell = (row, col)
            if cell == position.me.head:
                cells.append(paint("@", "my_head"))
            elif cell == position.opp.head:
                cells.append(paint("X", "opp_head"))
            elif cell in position.food:
                cells.append(paint("*", "food"))
            elif cell in my_body:
                cells.append(paint("o", "me"))
            elif cell in opp_body:
                cells.append(paint("x", "opp"))
            elif cell in on_path:
                cells.append(paint("·", "path"))
            else:
                cells.append(paint(".", "dim"))
        lines.append(" ".join(cells))

    lines.append(_status(layers, position, paint))
    if note:
        lines.append(paint(note, "dim"))
    return "\n".join(lines)


def _status(layers: Layers, position: Position, paint) -> str:
    def room(value: int, length: int, trapped: bool) -> str:
        text = f"{value}/{length}"
        return paint(text, "warn") if trapped else text

    def distance(value: int) -> str:
        return "-" if value >= INFINITE else str(value)

    parts = [
        f"score {position.my_score}-{position.opp_score}",
        f"me {room(layers.my_room, layers.my_length, layers.i_am_trapped)}",
        f"them {room(layers.opp_room, layers.opp_length, layers.they_are_trapped)}",
        f"apple {distance(layers.my_food_distance)}v{distance(layers.opp_food_distance)}",
        f"left {position.remaining_moves}",
    ]
    if layers.they_are_trapped:
        parts.append(paint("THEY ARE SEALED IN", "good"))
    if layers.i_am_trapped:
        parts.append(paint("WE ARE SEALED IN", "warn"))
    elif layers.winning_the_race:
        parts.append(paint("apple is ours", "good"))
    return "  ".join(parts)
