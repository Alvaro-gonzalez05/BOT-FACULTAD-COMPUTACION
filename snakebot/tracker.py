"""Turns the server's board strings into a fully ordered game state.

The board only shows *where* the snake cells are, never which end is the tail.
Order matters a lot -- it is what tells us which cell frees up next -- so this
module rebuilds it and then keeps it in sync turn after turn.

It also calibrates the direction words. We know what we sent and we see where
our head ended up, so if this deployment's ``"up"`` happens to mean "row + 1"
the tracker notices on the first move and remaps every later move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .board import ALL_DIRECTIONS, Board, Cell, Direction, adjacent, path_from_head
from .engine import Position, Snake


@dataclass
class DirectionCalibration:
    """Learned mapping from a board delta to the word the server expects."""

    learned: dict[tuple[int, int], str] = field(default_factory=dict)

    def wire_name(self, direction: Direction) -> str:
        return self.learned.get(direction.delta, direction.wire_name)

    def observe(self, sent: Direction, actual_delta: tuple[int, int]) -> bool:
        """Record that sending ``sent`` moved the head by ``actual_delta``.

        Returns ``True`` when this taught us something new.
        """
        if actual_delta == (0, 0) or abs(actual_delta[0]) + abs(actual_delta[1]) != 1:
            return False  # a crash or a desync -- nothing reliable to learn
        if self.learned.get(actual_delta) == sent.wire_name:
            return False
        changed = actual_delta != sent.delta or actual_delta in self.learned
        self.learned[actual_delta] = sent.wire_name
        return changed and actual_delta != sent.delta


@dataclass
class GameTracker:
    """Per-match memory. One instance per ``game_id`` so games run in parallel."""

    game_id: str
    my_side: str = "A"
    my_email: str | None = None
    calibration: DirectionCalibration = field(default_factory=DirectionCalibration)
    my_body: list[Cell] = field(default_factory=list)
    opp_body: list[Cell] = field(default_factory=list)
    last_sent: Direction | None = None
    last_head: Cell | None = None
    turns: int = 0
    resyncs: int = 0

    @property
    def opp_side(self) -> str:
        return "B" if self.my_side == "A" else "A"

    def observe(self, turn_data: dict[str, Any]) -> Position:
        """Fold a ``your_turn`` payload into an ordered :class:`Position`."""
        side = turn_data.get("side") or self.my_side
        if side in ("A", "B"):
            self.my_side = side

        board = Board.parse(
            turn_data["board"],
            rows=turn_data.get("rows"),
            cols=turn_data.get("cols"),
        )
        my_head = board.heads.get(self.my_side)
        opp_head = board.heads.get(self.opp_side)
        if my_head is None:
            raise ValueError(f"my head {self.my_side!r} is not on the board")

        self._calibrate(my_head)
        self.my_body = self._reorder(self.my_body, my_head, board.bodies.get(self.my_side, frozenset()))
        if opp_head is not None:
            self.opp_body = self._reorder(
                self.opp_body, opp_head, board.bodies.get(self.opp_side, frozenset())
            )
        else:
            self.opp_body = []

        self.turns += 1
        self.last_head = my_head

        opponent = Snake(tuple(self.opp_body)) if self.opp_body else Snake((_off_board(board),))
        return Position(
            rows=board.rows,
            cols=board.cols,
            me=Snake(tuple(self.my_body)),
            opp=opponent,
            food=board.food,
            my_turn=True,
            my_score=int(turn_data.get(self._my_score_key(turn_data), 0) or 0),
            opp_score=int(turn_data.get(self._opp_score_key(turn_data), 0) or 0),
            remaining_moves=int(turn_data.get("remaining_moves", 300) or 300),
        )

    def record_sent(self, direction: Direction) -> None:
        self.last_sent = direction

    # -- internals -------------------------------------------------------

    def _calibrate(self, my_head: Cell) -> None:
        if self.last_sent is None or self.last_head is None:
            return
        delta = (my_head[0] - self.last_head[0], my_head[1] - self.last_head[1])
        self.calibration.observe(self.last_sent, delta)

    def _reorder(self, previous: list[Cell], head: Cell, body_cells: frozenset[Cell]) -> list[Cell]:
        """Keep the known ordering if it still explains the board, else rebuild."""
        cells = set(body_cells) | {head}
        target_length = len(cells)

        if previous:
            kept = [cell for cell in previous if cell in cells and cell != head]
            candidate = [head] + kept[: target_length - 1]
            if _is_valid_snake(candidate, cells):
                return candidate

        self.resyncs += 1
        rebuilt = path_from_head(head, body_cells)
        return rebuilt

    def _my_score_key(self, turn_data: dict[str, Any]) -> str:
        return "score_1" if self._i_am_player_1(turn_data) else "score_2"

    def _opp_score_key(self, turn_data: dict[str, Any]) -> str:
        return "score_2" if self._i_am_player_1(turn_data) else "score_1"

    def _i_am_player_1(self, turn_data: dict[str, Any]) -> bool:
        """Prefer the explicit player names; fall back to side A == player 1."""
        if self.my_email:
            if turn_data.get("player_1") == self.my_email:
                return True
            if turn_data.get("player_2") == self.my_email:
                return False
        return self.my_side == "A"


def _is_valid_snake(cells: list[Cell], expected: set[Cell]) -> bool:
    if len(cells) != len(expected) or set(cells) != expected:
        return False
    return all(adjacent(cells[i], cells[i + 1]) for i in range(len(cells) - 1))


def _off_board(board: Board) -> Cell:
    """A placeholder head for a missing opponent, kept far from the playfield."""
    return (-10, -10)


def legal_direction_names(position: Position, calibration: DirectionCalibration) -> list[str]:
    """Wire names for every direction that does not kill us right now."""
    legal = {direction for direction, _ in position.legal_moves()}
    return [calibration.wire_name(d) for d in ALL_DIRECTIONS if d in legal]
