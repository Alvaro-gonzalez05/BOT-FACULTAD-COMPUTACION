"""A faithful, side-effect-free model of the Snake match rules.

The same engine drives the look-ahead search and the offline simulator, so a
move that is legal in the search is legal on the server.

Scoring, from the game's *how to play* page:

* eating food -> ``+100`` and the snake grows,
* surviving a move -> ``+1``,
* crashing (wall, own body, opponent) -> ``-500`` for the crasher and
  ``+1000`` for the other player,
* if nobody crashes, the higher score wins when the moves run out.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterator

from .board import ALL_DIRECTIONS, Board, Cell, Direction

FOOD_REWARD = 100
SURVIVE_REWARD = 1
CRASH_PENALTY = -500
OPPONENT_CRASH_REWARD = 1000


@dataclass(frozen=True)
class Snake:
    """An ordered snake: ``body[0]`` is the head, ``body[-1]`` the tail."""

    body: tuple[Cell, ...]

    @property
    def head(self) -> Cell:
        return self.body[0]

    @property
    def tail(self) -> Cell:
        return self.body[-1]

    @property
    def neck(self) -> Cell | None:
        return self.body[1] if len(self.body) > 1 else None

    def __len__(self) -> int:
        return len(self.body)

    def cells(self) -> frozenset[Cell]:
        return frozenset(self.body)

    def advance(self, target: Cell, grow: bool) -> "Snake":
        body = (target,) + self.body
        if not grow:
            body = body[:-1]
        return Snake(body)


@dataclass(frozen=True)
class Position:
    """A complete search state: both snakes, the food, and whose turn it is."""

    rows: int
    cols: int
    me: Snake
    opp: Snake
    food: frozenset[Cell]
    my_turn: bool = True
    my_score: int = 0
    opp_score: int = 0
    remaining_moves: int = 300
    crashed: str | None = None  # "me" | "opp" | None

    # -- queries ---------------------------------------------------------

    @property
    def over(self) -> bool:
        return self.crashed is not None or self.remaining_moves <= 0

    def mover(self) -> Snake:
        return self.me if self.my_turn else self.opp

    def blocked_cells(self) -> frozenset[Cell]:
        """Cells a snake may not move into this turn.

        Tails are included: the server validates a move against the board as it
        stands, so we never gamble on a tail vacating in the same tick.
        """
        return self.me.cells() | self.opp.cells()

    def legal_moves(self) -> Iterator[tuple[Direction, Cell]]:
        """Directions that do not kill the side to move, with their targets."""
        blocked = self.blocked_cells()
        head = self.mover().head
        for direction in ALL_DIRECTIONS:
            target = (head[0] + direction.d_row, head[1] + direction.d_col)
            if not self.in_bounds(target) or target in blocked:
                continue
            yield direction, target

    def in_bounds(self, cell: Cell) -> bool:
        return 0 <= cell[0] < self.rows and 0 <= cell[1] < self.cols

    # -- transitions -----------------------------------------------------

    def step(self, direction: Direction) -> "Position":
        """Apply ``direction`` for the side to move and hand the turn over."""
        snake = self.mover()
        head = snake.head
        target = (head[0] + direction.d_row, head[1] + direction.d_col)
        side = "me" if self.my_turn else "opp"

        if not self.in_bounds(target) or target in self.blocked_cells():
            return self._crash(side)

        grow = target in self.food
        moved = snake.advance(target, grow)
        gained = FOOD_REWARD if grow else SURVIVE_REWARD
        food = self.food - {target} if grow else self.food

        if self.my_turn:
            return replace(
                self,
                me=moved,
                food=food,
                my_score=self.my_score + gained,
                my_turn=False,
                remaining_moves=self.remaining_moves - 1,
            )
        return replace(
            self,
            opp=moved,
            food=food,
            opp_score=self.opp_score + gained,
            my_turn=True,
            remaining_moves=self.remaining_moves - 1,
        )

    def _crash(self, side: str) -> "Position":
        if side == "me":
            return replace(
                self,
                crashed="me",
                my_score=self.my_score + CRASH_PENALTY,
                opp_score=self.opp_score + OPPONENT_CRASH_REWARD,
                my_turn=False,
                remaining_moves=self.remaining_moves - 1,
            )
        return replace(
            self,
            crashed="opp",
            opp_score=self.opp_score + CRASH_PENALTY,
            my_score=self.my_score + OPPONENT_CRASH_REWARD,
            my_turn=True,
            remaining_moves=self.remaining_moves - 1,
        )

    def flipped(self) -> "Position":
        """The same position seen from the opponent's chair.

        Lets one strategy implementation play both sides in the simulator.
        """
        return replace(
            self,
            me=self.opp,
            opp=self.me,
            my_score=self.opp_score,
            opp_score=self.my_score,
            my_turn=not self.my_turn,
            crashed={"me": "opp", "opp": "me"}.get(self.crashed or "", None),
        )

    # -- interop ---------------------------------------------------------

    def to_board(self, my_side: str) -> Board:
        opp_side = "B" if my_side == "A" else "A"
        return Board(
            rows=self.rows,
            cols=self.cols,
            heads={my_side: self.me.head, opp_side: self.opp.head},
            bodies={
                my_side: frozenset(self.me.body[1:]),
                opp_side: frozenset(self.opp.body[1:]),
            },
            food=self.food,
        )

    def render(self, my_side: str = "A") -> str:
        return self.to_board(my_side).render()
