"""Board geometry and parsing for the Code Challenge Snake game.

The server sends the board as a single string whose rows are wrapped in
``|...|`` and joined by newlines::

    |               |
    |  aaA          |
    |      *        |

``A``/``B`` are the snake heads, ``a``/``b`` their bodies, ``*`` is food and a
space is an empty cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator

HEAD_CHARS = ("A", "B")
FOOD_CHAR = "*"
EMPTY_CHAR = " "


class Direction(Enum):
    """A move as the server names it, plus its ``(d_row, d_col)`` delta."""

    UP = ("up", -1, 0)
    DOWN = ("down", 1, 0)
    LEFT = ("left", 0, -1)
    RIGHT = ("right", 0, 1)

    def __init__(self, wire_name: str, d_row: int, d_col: int) -> None:
        self.wire_name = wire_name
        self.d_row = d_row
        self.d_col = d_col

    @property
    def delta(self) -> tuple[int, int]:
        return (self.d_row, self.d_col)

    @property
    def opposite(self) -> "Direction":
        return _OPPOSITES[self]

    @classmethod
    def from_delta(cls, d_row: int, d_col: int) -> "Direction":
        for direction in cls:
            if direction.delta == (d_row, d_col):
                return direction
        raise ValueError(f"no direction for delta {(d_row, d_col)}")

    @classmethod
    def from_wire(cls, name: str) -> "Direction":
        for direction in cls:
            if direction.wire_name == name:
                return direction
        raise ValueError(f"unknown direction {name!r}")


_OPPOSITES = {
    Direction.UP: Direction.DOWN,
    Direction.DOWN: Direction.UP,
    Direction.LEFT: Direction.RIGHT,
    Direction.RIGHT: Direction.LEFT,
}

ALL_DIRECTIONS: tuple[Direction, ...] = tuple(Direction)

Cell = tuple[int, int]


@dataclass(frozen=True)
class Board:
    """An immutable snapshot of one board string."""

    rows: int
    cols: int
    heads: dict[str, Cell]
    bodies: dict[str, frozenset[Cell]]
    food: frozenset[Cell]

    @classmethod
    def parse(cls, board_str: str, rows: int | None = None, cols: int | None = None) -> "Board":
        grid = _split_rows(board_str)
        if not grid:
            raise ValueError("empty board")

        n_rows = rows if rows else len(grid)
        n_cols = cols if cols else max(len(line) for line in grid)

        heads: dict[str, Cell] = {}
        bodies: dict[str, set[Cell]] = {side: set() for side in HEAD_CHARS}
        food: set[Cell] = set()

        for r, line in enumerate(grid[:n_rows]):
            for c, char in enumerate(line[:n_cols]):
                cell = (r, c)
                if char in HEAD_CHARS:
                    heads[char] = cell
                elif char.upper() in HEAD_CHARS and char.islower():
                    bodies[char.upper()].add(cell)
                elif char == FOOD_CHAR:
                    food.add(cell)

        return cls(
            rows=n_rows,
            cols=n_cols,
            heads=heads,
            bodies={side: frozenset(cells) for side, cells in bodies.items()},
            food=frozenset(food),
        )

    def occupied(self) -> set[Cell]:
        """Every cell taken by a snake (heads and bodies of both players)."""
        cells: set[Cell] = set(self.heads.values())
        for body in self.bodies.values():
            cells |= body
        return cells

    def snake_cells(self, side: str) -> set[Cell]:
        cells = set(self.bodies.get(side, frozenset()))
        head = self.heads.get(side)
        if head is not None:
            cells.add(head)
        return cells

    def in_bounds(self, cell: Cell) -> bool:
        r, c = cell
        return 0 <= r < self.rows and 0 <= c < self.cols

    def render(self) -> str:
        grid = [[EMPTY_CHAR] * self.cols for _ in range(self.rows)]
        for cell in self.food:
            grid[cell[0]][cell[1]] = FOOD_CHAR
        for side, body in self.bodies.items():
            for r, c in body:
                grid[r][c] = side.lower()
        for side, (r, c) in self.heads.items():
            grid[r][c] = side
        return "\n".join("|" + "".join(row) + "|" for row in grid)


def _split_rows(board_str: str) -> list[str]:
    """Turn the wire board into a list of row strings without the ``|`` frame."""
    lines: list[str] = []
    for raw in board_str.splitlines():
        line = raw.rstrip("\r")
        if not line.strip("| \t"):
            # A fully empty row is still a real row -- keep it if it is framed.
            if not line.startswith("|"):
                continue
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        lines.append(line)
    if not lines:
        return []
    width = max(len(line) for line in lines)
    return [line.ljust(width) for line in lines]


def neighbours(cell: Cell, rows: int, cols: int) -> Iterator[tuple[Direction, Cell]]:
    r, c = cell
    for direction in ALL_DIRECTIONS:
        nr, nc = r + direction.d_row, c + direction.d_col
        if 0 <= nr < rows and 0 <= nc < cols:
            yield direction, (nr, nc)


def manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def adjacent(a: Cell, b: Cell) -> bool:
    return manhattan(a, b) == 1


def path_from_head(head: Cell, body: Iterable[Cell], max_nodes: int = 200_000) -> list[Cell]:
    """Rebuild the ordered snake (head first) from an unordered body blob.

    A snake is a path of orthogonally adjacent cells, so we walk it with a DFS
    that prefers the longest chain -- that resolves the rare case where a coiled
    snake touches itself and two orderings look plausible. ``max_nodes`` keeps a
    pathological coil from stalling a turn; the best chain found so far is used.
    """
    remaining = set(body)
    best: list[Cell] = []
    budget = max_nodes

    def walk(cell: Cell, acc: list[Cell]) -> None:
        nonlocal best, budget
        if len(acc) > len(best):
            best = list(acc)
        if not remaining or budget <= 0:
            return
        for _, nxt in neighbours(cell, 10**9, 10**9):
            if nxt in remaining:
                budget -= 1
                if budget <= 0:
                    return
                remaining.discard(nxt)
                acc.append(nxt)
                walk(nxt, acc)
                acc.pop()
                remaining.add(nxt)

    walk(head, [head])
    # Any cell the walk could not reach (desynced board) is appended so the
    # length still matches what we see.
    leftovers = [cell for cell in body if cell not in set(best)]
    return best + leftovers
