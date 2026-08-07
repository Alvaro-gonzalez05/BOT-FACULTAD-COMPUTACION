"""An offline arena, so the bot can be proven before it ever meets a human.

Runs full matches between two policies using the same :mod:`snakebot.engine`
rules the search uses, with food respawning like the real server. ``run.py
simulate`` uses it to report a win rate and, more importantly, how many matches
the bot lost *by crashing* -- the number that should stay at zero.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Protocol

from .board import ALL_DIRECTIONS, Cell, Direction
from .engine import Position, Snake
from .heuristics import (
    INFINITE,
    can_reach_own_tail,
    decay_map,
    distance_map,
    reachable_space,
)
from .strategy import SnakeStrategy

Policy = Callable[[Position], Direction]


class _HasDecide(Protocol):
    def decide(self, position: Position) -> object: ...


@dataclass
class MatchResult:
    winner: str  # "me" | "opp" | "draw"
    crashed: str | None
    my_score: int
    opp_score: int
    turns: int


def starting_position(
    rows: int = 15,
    cols: int = 15,
    length: int = 3,
    # Three, from a real board: the server keeps three apples in play at once.
    # Tuning on a one-apple board taught the bot to contest whatever was
    # nearest, which is the wrong instinct when there are others to take.
    food_count: int = 3,
    remaining_moves: int = 300,
    rng: random.Random | None = None,
) -> Position:
    """A symmetric opening: both snakes horizontal, facing the middle."""
    rng = rng or random.Random()
    my_row = rows // 3
    opp_row = 2 * rows // 3
    me = Snake(tuple((my_row, 2 + offset) for offset in range(length))[::-1])
    opp = Snake(tuple((opp_row, cols - 3 - offset) for offset in range(length))[::-1])

    occupied = set(me.body) | set(opp.body)
    food = frozenset(_spawn_food(rows, cols, occupied, food_count, rng))
    return Position(
        rows=rows,
        cols=cols,
        me=me,
        opp=opp,
        food=food,
        remaining_moves=remaining_moves,
    )


def _spawn_food(
    rows: int, cols: int, occupied: set[Cell], count: int, rng: random.Random
) -> list[Cell]:
    free = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in occupied]
    rng.shuffle(free)
    return free[:count]


def play_match(
    my_policy: Policy,
    opp_policy: Policy,
    position: Position | None = None,
    rng: random.Random | None = None,
    on_turn: Callable[[Position], None] | None = None,
) -> MatchResult:
    """Play one match to the end and report who won."""
    rng = rng or random.Random()
    state = position or starting_position(rng=rng)
    turns = 0

    while not state.over:
        policy = my_policy if state.my_turn else opp_policy
        view = state if state.my_turn else state.flipped()
        direction = policy(view)

        before_food = len(state.food)
        state = state.step(direction)
        if len(state.food) < before_food:
            occupied = set(state.me.body) | set(state.opp.body)
            state = _with_food(state, _spawn_food(state.rows, state.cols, occupied, 1, rng))

        turns += 1
        if on_turn is not None:
            on_turn(state)

    if state.crashed == "me":
        winner = "opp"
    elif state.crashed == "opp":
        winner = "me"
    elif state.my_score > state.opp_score:
        winner = "me"
    elif state.opp_score > state.my_score:
        winner = "opp"
    else:
        winner = "draw"

    return MatchResult(
        winner=winner,
        crashed=state.crashed,
        my_score=state.my_score,
        opp_score=state.opp_score,
        turns=turns,
    )


def _with_food(state: Position, cells: list[Cell]) -> Position:
    from dataclasses import replace

    return replace(state, food=state.food | frozenset(cells))


# -- reference opponents -------------------------------------------------


def random_policy(rng: random.Random | None = None) -> Policy:
    """Picks any move that does not kill it immediately."""
    rng = rng or random.Random()

    def choose(position: Position) -> Direction:
        moves = [direction for direction, _ in position.legal_moves()]
        return rng.choice(moves) if moves else Direction.UP

    return choose


def greedy_policy() -> Policy:
    """Beelines for the nearest food, avoiding only immediate death.

    This is what a decent hand-written bot looks like, and it is the baseline
    the search has to beat.
    """

    def choose(position: Position) -> Direction:
        moves = list(position.legal_moves())
        if not moves:
            return Direction.UP
        blocked = position.blocked_cells()
        best = moves[0][0]
        best_distance = INFINITE + 1
        for direction, target in moves:
            distances = distance_map(position.rows, position.cols, target, blocked)
            distance = min((distances.get(f, INFINITE) for f in position.food), default=INFINITE)
            open_neighbours = sum(
                1
                for d in ALL_DIRECTIONS
                if position.in_bounds((target[0] + d.d_row, target[1] + d.d_col))
                and (target[0] + d.d_row, target[1] + d.d_col) not in blocked
            )
            ranked = distance - open_neighbours * 0.5
            if ranked < best_distance:
                best_distance, best = ranked, direction
        return best

    return choose


def strategy_policy(strategy: SnakeStrategy) -> Policy:
    def choose(position: Position) -> Direction:
        return strategy.decide(position).direction

    return choose


def survivor_policy(greed: float = 3.0, depth: int = 4) -> Policy:
    """A hungry opponent that stays alive -- the kind we actually lose to.

    The greedy baseline kills itself, so matches against it end early and never
    exercise the endgame at all. Across 54 real matches this bot crashed zero
    times and lost twelve, every loss on points against opponents who simply
    survived and out-ate us; measuring an appetite change against a baseline
    that dies young measures nothing.

    Two hand-written attempts at a one-ply "safe and greedy" rule crashed in
    three and four matches out of twelve, which disqualifies them as a stand-in
    for an opponent that never crashes: a move can be safe this step and doomed
    two steps later. So this reuses the real strategy -- the search and the
    survival veto are what keep a snake alive -- with food-heavy weights and a
    shallow search to keep it clearly weaker than us.

    Its limitation is worth stating: it shares our evaluation, so it cannot
    expose a blind spot we both have. It is a sparring partner for appetite,
    not an independent judge of strength.
    """
    from .search import Weights

    strategy = SnakeStrategy(
        time_budget=3600.0,
        max_depth=depth,
        weights=Weights(food_distance=14.0 * greed, food_race=60.0 * greed, trapped_bonus=0.0),
    )
    return strategy_policy(strategy)


def rival_policy(greed: float = 0.6, care: float = 1.0, seed: int = 0) -> Policy:
    """A sparring partner shaped like a measured opponent.

    Built from the two traits the opponent book records from real matches:

    * ``greed`` -- how often they head for the nearest apple, matching the
      "chases apples N%" figure;
    * ``care`` -- how reliably they check that a move leaves them a way out,
      which is what the measured crash rate is really telling us.

    Crucially this does *not* use our search. ``survivor_policy`` reuses the
    real strategy and therefore mirrors it, which is why matches against it end
    in draws seven times out of ten and it cannot judge anything. This is a
    plain heuristic bot: a different kind of player, which is the entire point
    of having it.

    Carelessness is modelled as skipping the survival check on a given turn
    rather than as playing worse in general -- that is how real bots die, one
    unchecked move at a time.
    """
    rng = random.Random(seed)

    def choose(position: Position) -> Direction:
        legal = list(position.legal_moves())
        if not legal:
            return Direction.UP

        blocked = position.blocked_cells()
        careful = rng.random() < care
        decay = decay_map(position.me, position.opp) if careful else {}
        length = len(position.me)

        scored: list[tuple[float, Direction]] = []
        fallback: list[tuple[float, Direction]] = []

        for direction, target in legal:
            distances = distance_map(position.rows, position.cols, target, blocked - {target})
            nearest = min((distances.get(f, INFINITE) for f in position.food), default=INFINITE)
            open_neighbours = sum(
                1
                for d in ALL_DIRECTIONS
                if position.in_bounds((target[0] + d.d_row, target[1] + d.d_col))
                and (target[0] + d.d_row, target[1] + d.d_col) not in blocked
            )
            pull = 0.0 if nearest >= INFINITE else -greed * 10.0 * nearest
            score = pull + (1.0 - greed) * open_neighbours * 5.0
            # On a careless turn it just walks at the apple. Keeping any part of
            # the safety instinct here made `care` almost inert -- 1.0, 0.95 and
            # 0.85 all produced the identical crash rate, because the two
            # branches kept choosing the same square.
            fallback.append((pull if not careful else score, direction))

            if careful:
                room = reachable_space(
                    position.rows, position.cols, target, decay,
                    limit=position.rows * position.cols,
                )
                if room < length + 1:
                    continue
                child = position.step(direction)
                if child.crashed is None and not can_reach_own_tail(child):
                    continue
                scored.append((score + room * 0.5, direction))

        pool = scored or fallback
        return max(pool, key=lambda item: item[0])[1]

    return choose


# Measured crash rate -> the `care` value that reproduces it, from 16-match runs
# against the greedy baseline. The floor is about 6%: this is a heuristic bot,
# not a searcher, so it cannot be made to never crash the way the real one is.
# An opponent measured below that is simply given the most careful setting.
CARE_CALIBRATION = ((0.06, 1.00), (0.12, 0.94), (0.19, 0.90), (0.25, 0.80), (0.50, 0.60))


def care_for_crash_rate(crash_rate: float) -> float:
    """The `care` setting whose measured crash rate is closest to ``crash_rate``."""
    return min(CARE_CALIBRATION, key=lambda pair: abs(pair[0] - crash_rate))[1]


def rival_from_profile(profile, seed: int = 0) -> Policy:
    """A sparring partner shaped like one real opponent from the book.

    This is what makes the offline arena worth trusting again. The greedy
    baseline is too weak to separate anything, and `survivor_policy` shares our
    evaluation so closely that seven matches in ten end level. These are built
    from measurements of actual rivals and play by different rules to ours,
    which is the only way an offline result says anything about a live one.

    Their traits come from the opponent's recent matches, so a rival who
    improves is re-created as the player they are now rather than the one they
    were.
    """
    return rival_policy(
        greed=profile.greed,
        care=care_for_crash_rate(profile.crash_rate),
        seed=seed,
    )


def _rival_match(job) -> tuple[str, str, str | None, int]:
    """One gauntlet match. Top level so a process pool can pickle it."""
    from .search import Weights

    name, greed, care, index, moves, depth, seed, weights = job
    rng = random.Random(seed + index)
    me = SnakeStrategy(time_budget=3600.0, max_depth=depth, weights=weights or Weights())
    result = play_match(
        strategy_policy(me),
        rival_policy(greed=greed, care=care, seed=index),
        position=starting_position(remaining_moves=moves, rng=rng),
        rng=rng,
    )
    return name, result.winner, result.crashed, result.my_score - result.opp_score
