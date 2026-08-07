"""Time-boxed alpha-beta search over the alternating Snake turns.

The search is *paranoid*: it assumes the opponent always plays the move that
hurts us most. That is what turns "probably fine" moves into moves that survive
even against a strong bot. Iterative deepening plus a wall-clock budget means we
always have an answer before the server's per-move time limit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .board import Direction
from .engine import Position
from .heuristics import INFINITE, decay_map, distance_map, reachable_space

WIN = 1e9
LOSS = -1e9

# How far off a food race can be before winning it stops meaning much. A race
# settled in two moves is nearly a fact; one "won" by a single step at twelve is
# a promise, and the difference matters because a promise can be held forever
# without ever being cashed.
#
# That is not hypothetical. A real match (logs/game_f62d91b2, lost 249-348 with
# nobody crashing) had the bot orbit a 2x3 rectangle for 130 turns with an apple
# two steps away, because stepping towards it flipped a *thirteen*-step-away
# apple into the opponent's column. Undiscounted, that flip was worth 180 points
# against the 100 an apple actually scores, so the bot maximised its claims
# instead of its score. Nothing else in the evaluation can be farmed by standing
# still: space and territory are bounded by the board, and length and score only
# move when something happens.
RACE_HORIZON = 6.0

# The same idea for "how close am I to the apple I am going for". It used to be
# a *linear* penalty, `-food_distance * cells`, which had a fatal consequence:
# eating moves the nearest apple from one cell away to about twelve, so the term
# collapsed by ~230 while the apple itself paid 100 in score and 20 in length.
# Measured on the position that lost the match: standing next to the apple
# evaluated at -52.8, eating it at -233.2. The bot was not confused, it was
# maximising exactly what we asked for -- and what we asked for said not to eat.
#
# Bounded means the collapse cannot exceed the weight, so eating is always a
# gain. The horizon is short on purpose: at two cells the difference between
# apples is tactical, at twelve it is noise you will re-plan long before living.
FOOD_HORIZON = 4.0


def _race_credit(distances: list[int]) -> float:
    """What a set of won food races is worth, discounted by how far off each is."""
    return sum(RACE_HORIZON / (RACE_HORIZON + d) for d in distances)


def _closeness(distance: int) -> float:
    """1.0 on top of the apple, falling away smoothly, never below 0."""
    return FOOD_HORIZON / (FOOD_HORIZON + distance)


@dataclass(frozen=True)
class Weights:
    """Relative importance of each evaluation term.

    The units are roughly *match points*, which keeps the numbers honest: an
    apple is worth 100.

    The one invariant everything here has to respect: **eating must raise the
    evaluation**. It is easy to break by accident, because an apple carries
    positional value -- being near it, winning the race for it -- and eating it
    takes that value off the board along with the apple. When the positional
    value of an apple exceeds what eating it pays, the bot's best move is to
    stand next to food forever, and that is not a hypothetical: it is what lost
    ``logs/game_f62d91b2``. ``tests/test_appetite.py`` pins the invariant down.

    These defaults come from sweeps against the greedy baseline (see the README):
    ``territory`` at 1 beat 2, 3 and 6 outright, because a bot that chases space
    starves. But dropping it to 0 made the bot *crash* -- keeping a little
    pressure on territory is part of staying alive, not just a tiebreaker.

    ``opponent_choke`` came down from 5 to 2 once ``trapped_bonus`` existed: the
    linear squeeze was standing in for "seal them in", and a crude proxy should
    give way to the real thing. Be aware that the baseline is now too weak to
    separate these settings sharply -- see the README on what it would take to
    tune them properly.
    """

    territory: float = 1.0
    space: float = 4.0
    trapped_penalty: float = 900.0
    trapped_bonus: float = 900.0
    # No longer a per-cell penalty but the size of a bounded closeness bonus --
    # see FOOD_HORIZON. 21 was right when the term was linear (it had been
    # raised by half from 14 to stop the bot losing short matches, 6W-14L over
    # 120 moves against 17W-3L over 300); as a bounded bonus that same 21 would
    # be almost inert, so it is scaled to the range the term now spans.
    food_distance: float = 60.0
    food_race: float = 90.0
    length: float = 20.0
    # Three, not one. The score is the only term that records something that has
    # actually happened, and it has to outweigh the positional value an apple
    # carries while it is still on the board -- otherwise eating reads as a loss.
    # At 1.0 it did: standing beside an apple evaluated 180 points better than
    # taking it.
    score: float = 3.0
    opponent_choke: float = 2.0

    # There is no endgame-hunger dial. Across 54 real matches the bot crashed
    # zero times and lost twelve, all on points and most by two or three apples,
    # which made "chase harder when behind, coast when ahead" look obviously
    # right. Against sparring partners shaped like the actual rivals it lost:
    # 35W-10L with it against 41W-7L without, worse against every single one.
    # Trailing late is exactly when a snake can least afford to take the risks
    # that catching up requires.
    # Nor is there a penalty for closing on an apple already conceded. An audit
    # of 3045 real turns found the bot moving towards the opponent's apple 29%
    # of the time, so pushing it away looked like the missing half of "give up
    # the ones you lose". At a weight of 10 it won on two seed sets (42W-3L
    # against 34W-8L on one) -- and on a third it **crashed three times in 24
    # matches** where every setting without it crashed none. Pushing a snake
    # away from a region is pushing it somewhere, and sometimes that somewhere
    # kills it. No amount of apples is worth the one property this bot promises.
    #
    # Nor is there a `second_apple` weight. Valuing the next apple after the
    # nearest one sounded equally right -- food collection is a route, not a
    # trip -- and measured 10W/6L against 13W/3L without it. Both ideas came
    # from the same correct diagnosis and both made the bot worse: the problem
    # is real, but reaching for food more eagerly is not the cure.


@dataclass
class SearchStats:
    nodes: int = 0
    depth: int = 0
    elapsed: float = 0.0
    timed_out: bool = False


@dataclass
class Search:
    """Alpha-beta with iterative deepening and an explicit time budget."""

    time_budget: float = 0.40
    max_depth: int = 14
    weights: Weights = field(default_factory=Weights)

    def __post_init__(self) -> None:
        self._deadline = 0.0
        self._killers: dict[int, Direction] = {}
        self.stats = SearchStats()

    # -- public API ------------------------------------------------------

    def best_move(self, position: Position) -> tuple[Direction | None, float]:
        """Best direction for the side to move, or ``None`` when every move dies."""
        started = time.monotonic()
        self._deadline = started + self.time_budget
        self._killers = {}
        self.stats = SearchStats()

        candidates = self._ordered_moves(position)
        if not candidates:
            return None, LOSS

        best_move = candidates[0]
        best_value = LOSS
        for depth in range(2, self.max_depth + 1, 2):
            try:
                move, value = self._root(position, candidates, depth)
            except TimeoutError:
                self.stats.timed_out = True
                break
            best_move, best_value = move, value
            self.stats.depth = depth
            if value >= WIN / 2 or value <= LOSS / 2:
                break  # forced result found -- deeper search cannot improve it
            if time.monotonic() >= self._deadline:
                break
            # Search the previous best first next time: it is the most likely
            # to hold up, and an early cutoff is what buys the extra depth.
            candidates.sort(key=lambda direction: direction is not best_move)

        self.stats.elapsed = time.monotonic() - started
        return best_move, best_value

    # -- search core -----------------------------------------------------

    def _root(
        self, position: Position, candidates: list[Direction], depth: int
    ) -> tuple[Direction, float]:
        alpha = LOSS * 2
        best_move = candidates[0]
        best_value = LOSS * 2
        for direction in candidates:
            child = position.step(direction)
            value = self._alphabeta(child, depth - 1, alpha, WIN * 2, ply=1)
            if value > best_value:
                best_value, best_move = value, direction
                alpha = max(alpha, value)
        return best_move, best_value

    def _alphabeta(self, position: Position, depth: int, alpha: float, beta: float, ply: int) -> float:
        self.stats.nodes += 1
        # Check the clock every few nodes, not every few hundred. Each node runs
        # a full evaluation -- two breadth-first sweeps of the board and two
        # flood fills -- so 256 of them ran 800ms past a 150ms budget in a real
        # match. time.monotonic() costs nothing next to that, and a late move is
        # penalised by the server.
        if self.stats.nodes % 8 == 0 and time.monotonic() >= self._deadline:
            raise TimeoutError

        if position.crashed is not None or position.remaining_moves <= 0:
            return self._terminal_value(position, ply)
        if depth <= 0:
            return self.evaluate(position)

        moves = self._ordered_moves(position, ply)
        if not moves:
            # The side to move is boxed in: every direction is a crash, so any
            # of them produces the same terminal position.
            return self._terminal_value(position.step(Direction.UP), ply)

        if position.my_turn:
            value = LOSS * 2
            for direction in moves:
                value = max(value, self._alphabeta(position.step(direction), depth - 1, alpha, beta, ply + 1))
                alpha = max(alpha, value)
                if alpha >= beta:
                    self._killers[ply] = direction
                    break
            return value

        value = WIN * 2
        for direction in moves:
            value = min(value, self._alphabeta(position.step(direction), depth - 1, alpha, beta, ply + 1))
            beta = min(beta, value)
            if alpha >= beta:
                self._killers[ply] = direction
                break
        return value

    def _terminal_value(self, position: Position, ply: int) -> float:
        if position.crashed == "me":
            return LOSS + ply  # dying later is better than dying now
        if position.crashed == "opp":
            return WIN - ply  # winning sooner is better
        # Ran out of moves: the higher score takes the match.
        return float(position.my_score - position.opp_score)

    # -- move ordering ---------------------------------------------------

    def _ordered_moves(self, position: Position, ply: int = 0) -> list[Direction]:
        """Legal moves, most promising first, so alpha-beta prunes early."""
        moves = list(position.legal_moves())
        if not moves:
            return []
        food = position.food
        blocked = position.blocked_cells()

        def key(item: tuple[Direction, tuple[int, int]]) -> tuple[float, float]:
            _, target = item
            open_neighbours = sum(
                1
                for d in Direction
                if position.in_bounds((target[0] + d.d_row, target[1] + d.d_col))
                and (target[0] + d.d_row, target[1] + d.d_col) not in blocked
            )
            food_gap = min(
                (abs(target[0] - f[0]) + abs(target[1] - f[1]) for f in food),
                default=INFINITE,
            )
            return (-open_neighbours, food_gap)

        moves.sort(key=key)
        ordered = [direction for direction, _ in moves]

        # Killer move: whatever caused a cutoff at this ply last time is the
        # best guess for causing one again, and an early cutoff is exactly
        # what buys depth inside a fixed time budget.
        killer = self._killers.get(ply)
        if killer is not None and killer in ordered:
            ordered.remove(killer)
            ordered.insert(0, killer)
        return ordered

    # -- evaluation ------------------------------------------------------

    def evaluate(self, position: Position) -> float:
        """Static score of a quiet position, from *my* point of view."""
        w = self.weights
        blocked = position.blocked_cells()
        rows, cols = position.rows, position.cols

        my_distances = distance_map(rows, cols, position.me.head, blocked - {position.me.head})
        opp_distances = distance_map(rows, cols, position.opp.head, blocked - {position.opp.head})

        my_territory = 0
        opp_territory = 0
        for cell, mine in my_distances.items():
            theirs = opp_distances.get(cell, INFINITE)
            if mine < theirs or (mine == theirs and position.my_turn):
                my_territory += 1
            else:
                opp_territory += 1
        opp_territory += sum(1 for cell in opp_distances if cell not in my_distances)

        decay = decay_map(position.me, position.opp)
        my_length = len(position.me)
        my_room = reachable_space(rows, cols, position.me.head, decay, limit=my_length * 2 + 4)
        opp_room = reachable_space(rows, cols, position.opp.head, decay, limit=len(position.opp) * 2 + 4)

        value = 0.0
        value += w.territory * (my_territory - opp_territory)
        value += w.space * min(my_room, my_length * 2)
        value -= w.opponent_choke * min(opp_room, len(position.opp) * 2)
        value += w.length * (my_length - len(position.opp))
        value += w.score * (position.my_score - position.opp_score)

        if my_room <= my_length:
            # Not enough room left to unwind: this is how bots kill themselves.
            value -= w.trapped_penalty * (my_length - my_room + 1) / my_length

        opp_length = len(position.opp)
        if opp_room <= opp_length:
            # The mirror image, and the single biggest prize on the board: a
            # snake with less room than body has no escape and must crash into
            # something, which is -500 for them and +1000 for us. The search
            # rarely sees that far, so the evaluation has to recognise the trap
            # while it is still being built.
            value += w.trapped_bonus * (opp_length - opp_room + 1) / opp_length

        # Apples: chase the ones we would actually win, give up the rest.
        #
        # The real board carries three apples at once, so "nearest apple" is the
        # wrong target -- the nearest one is often the one the opponent reaches
        # first, and walking to it loses the trip *and* the apple. What matters
        # is the nearest apple we win the race for. Racing for a lost apple is
        # the most common way to waste a game.
        #
        # Whether they are actually heading for it is not knowable from one
        # frame. Distance is the honest proxy, and it is the paranoid one: their
        # distance map already excludes their own neck, so it costs them the
        # turnaround if the apple is behind them.
        # An apple we cannot reach before the moves run out is not an apple.
        # remaining_moves counts plies and the players alternate, so reaching a
        # cell d away takes about 2d of them.
        reachable_in_time = max(0, position.remaining_moves) // 2

        nearest_any = INFINITE
        nearest_theirs = INFINITE
        mine_won: list[int] = []
        theirs_won: list[int] = []
        for food in position.food:
            mine = my_distances.get(food, INFINITE)
            yours = opp_distances.get(food, INFINITE)
            if mine > reachable_in_time and yours > reachable_in_time:
                continue  # neither of us is getting there; it may as well not exist
            nearest_any = min(nearest_any, mine)
            # Ties go to whoever is about to move.
            if mine < yours or (mine == yours and position.my_turn):
                if mine < INFINITE:
                    mine_won.append(mine)
            elif yours < INFINITE:
                theirs_won.append(yours)
                nearest_theirs = min(nearest_theirs, yours)

        mine_won.sort()

        # Walk towards an apple we win; only fall back to the nearest one when
        # we win none, since an opponent that misplays still leaves it there.
        target = mine_won[0] if mine_won else nearest_any
        if target < INFINITE:
            value += w.food_distance * _closeness(target)
        if nearest_theirs < INFINITE:
            value -= w.food_distance * 0.5 * _closeness(nearest_theirs)
        # Owning more of the board's apples than they do, not just being nearest
        # to one of them -- discounted by how far off each race is to settle.
        value += w.food_race * (_race_credit(mine_won) - _race_credit(theirs_won))

        return value
