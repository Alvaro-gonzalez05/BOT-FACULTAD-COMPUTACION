"""The bot's brain: pick a direction that wins without ever risking a crash.

Three layers, checked in order, each one a backstop for the one above:

1. **Legality** -- moves that walk into a wall, a body or the opponent are
   dropped before anything else runs.
2. **Look-ahead** -- a paranoid alpha-beta search picks the move that scores
   best assuming the opponent plays as well as it can.
3. **Survival veto** -- the chosen move must leave enough room to unwind (the
   flood fill has to see at least as many cells as the snake is long, or the
   tail has to stay reachable). If it does not, the veto swaps in the safest
   legal move instead.

Layer 3 is what makes the bot boring in the good way: it will give up a piece of
food rather than take one that seals it inside a pocket.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .board import Direction
from .engine import Position
from .heuristics import can_reach_own_tail, decay_map, reachable_space
from .search import Search, Weights


@dataclass(frozen=True)
class Decision:
    """What the strategy chose and why -- handy for logs and tests."""

    direction: Direction
    value: float
    depth: int
    nodes: int
    elapsed: float
    vetoed: bool = False
    doomed: bool = False

    def describe(self) -> str:
        flags = "".join(["!" if self.vetoed else "", "?" if self.doomed else ""])
        return (
            f"{self.direction.wire_name:<5} value={self.value:+.0f} depth={self.depth} "
            f"nodes={self.nodes} {self.elapsed * 1000:.0f}ms {flags}".rstrip()
        )


@dataclass
class SnakeStrategy:
    """Decides one move per turn. Stateless between turns by design."""

    time_budget: float = 0.40
    max_depth: int = 14
    weights: Weights = field(default_factory=Weights)
    safety_margin: int = 1

    def __post_init__(self) -> None:
        self.search = Search(
            time_budget=self.time_budget, max_depth=self.max_depth, weights=self.weights
        )

    def decide(self, position: Position) -> Decision:
        legal = [direction for direction, _ in position.legal_moves()]
        if not legal:
            # Every direction crashes. Pick one and go down fighting -- an
            # illegal move would also be penalised, and the opponent may still
            # crash first.
            return Decision(Direction.UP, float("-inf"), 0, 0, 0.0, doomed=True)

        move, value = self.search.best_move(position)
        stats = self.search.stats
        if move is None:
            move = legal[0]

        safe = [direction for direction in legal if self._survives(position.step(direction))]
        vetoed = False
        if safe and move not in safe:
            move = max(safe, key=lambda d: self._room_after(position.step(d)))
            vetoed = True
        elif not safe:
            # Nothing is provably safe: take the move that leaves the most room.
            move = max(legal, key=lambda d: self._room_after(position.step(d)))
            vetoed = True

        return Decision(
            direction=move,
            value=value,
            depth=stats.depth,
            nodes=stats.nodes,
            elapsed=stats.elapsed,
            vetoed=vetoed,
        )

    # -- survival veto ---------------------------------------------------

    def _survives(self, child: Position) -> bool:
        """Is there still a way out after this move?"""
        if child.crashed == "me":
            return False
        if child.crashed == "opp":
            return True
        needed = len(child.me) + self.safety_margin
        if self._room_after(child) >= needed:
            return True
        # A snake that can still touch its own tail can always keep circling.
        return can_reach_own_tail(child)

    def _room_after(self, child: Position) -> int:
        if child.crashed == "me":
            return -1
        decay = decay_map(child.me, child.opp)
        limit = child.rows * child.cols
        return reachable_space(child.rows, child.cols, child.me.head, decay, limit=limit)
