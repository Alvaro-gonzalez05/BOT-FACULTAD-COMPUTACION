"""Self-play tuning: the bot's weights improve by beating their own last build.

The greedy baseline stopped being useful once every candidate beat it 15-16
times out of 16 -- a benchmark you always win tells you nothing. So the sparring
partner here is the reigning champion itself. A challenger is the champion with
a few weights nudged; it is promoted only if it actually beats the champion over
a batch of games, and the new champion is written to disk. Run it again tomorrow
and it picks up where it left off.

Two things this is careful about, because both quietly ruin self-play tuning:

* **Side bias.** The opening is not perfectly fair, so every pairing is played
  twice on the same seed with the sides swapped. A weight set that only wins as
  player A has not proved anything.
* **Noise promoted as progress.** A challenger that edges ahead by a single game
  is almost certainly luck. Promotion needs a real margin, and the default batch
  is large enough that one lucky match cannot carry it.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .search import Weights
from .simulator import play_match, starting_position, strategy_policy
from .strategy import SnakeStrategy

# Tuning runs at a fixed depth with no wall-clock cutoff: a time budget makes
# the reachable depth depend on CPU load, and then the same seeds give different
# results run to run, which makes every comparison meaningless.
TUNING_BUDGET = 3600.0

# Depth is the whole cost of a tuning run: one 150-move game costs about 4s at
# depth 2, 25s at depth 4 and 66s at depth 6. Deeper tuning transfers better to
# real play (which searches deeper still), so this is a budget decision, not a
# quality one -- 4 keeps a full session to minutes, 6 is worth it overnight.
TUNING_DEPTH = 4

# Weights that are safe to tune. `trapped_penalty` is deliberately absent: it is
# the "do not kill yourself" term, and letting a random search weaken it trades
# the one property we actually promised for a few points of margin.
TUNABLE = (
    "territory",
    "space",
    "trapped_bonus",
    "food_distance",
    "food_race",
    "length",
    "opponent_choke",
)


@dataclass
class DuelResult:
    """How a challenger did against the champion."""

    challenger_points: float
    champion_points: float
    games: int
    challenger_crashes: int
    champion_crashes: int

    @property
    def challenger_share(self) -> float:
        return self.challenger_points / self.games if self.games else 0.0

    def __str__(self) -> str:
        return (
            f"{self.challenger_points:g}-{self.champion_points:g} of {self.games} "
            f"(crashes {self.challenger_crashes} vs {self.champion_crashes})"
        )


def _strategy(weights: Weights, depth: int = TUNING_DEPTH) -> SnakeStrategy:
    return SnakeStrategy(time_budget=TUNING_BUDGET, max_depth=depth, weights=weights)


def _play_one(job: tuple[Weights, Weights, bool, int, int, int]) -> tuple[float, float, str | None]:
    """One game of a duel. Top level so a process pool can pickle it.

    Depth travels in the job rather than a module global: the games run in
    separate processes, which would not see a global set by the caller.
    """
    challenger, champion, swapped, game_seed, moves, depth = job
    # The same seed for both halves: same opening, same apples, only the players
    # swapped. A different seed here would make this two unrelated games instead
    # of a controlled comparison.
    rng = random.Random(game_seed)
    position = starting_position(remaining_moves=moves, rng=rng)
    first, second = (champion, challenger) if swapped else (challenger, champion)
    result = play_match(
        strategy_policy(_strategy(first, depth)),
        strategy_policy(_strategy(second, depth)),
        position=position,
        rng=rng,
    )

    # "me" is whoever moved first, which swaps with the sides.
    challenger_side = "opp" if swapped else "me"
    champion_side = "me" if swapped else "opp"
    if result.winner == challenger_side:
        points = (1.0, 0.0)
    elif result.winner == champion_side:
        points = (0.0, 1.0)
    else:
        points = (0.5, 0.5)

    crasher = None
    if result.crashed is not None:
        crasher = "challenger" if result.crashed == challenger_side else "champion"
    return points[0], points[1], crasher


def duel(
    challenger: Weights,
    champion: Weights,
    *,
    matches: int = 8,
    moves: int = 200,
    seed: int = 0,
    workers: int = 1,
    depth: int = TUNING_DEPTH,
) -> DuelResult:
    """Play ``matches`` seeds twice each, sides swapped, and count the points.

    ``workers`` above 1 spreads the games over processes. The games are
    independent and each carries its own seed, so the result does not depend on
    how many workers ran it.
    """
    jobs = [
        (challenger, champion, swapped, seed + index, moves, depth)
        for index in range(matches)
        for swapped in (False, True)
    ]

    if workers > 1 and len(jobs) > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(_play_one, jobs))
    else:
        outcomes = [_play_one(job) for job in jobs]

    challenger_points = sum(outcome[0] for outcome in outcomes)
    champion_points = sum(outcome[1] for outcome in outcomes)
    return DuelResult(
        challenger_points=challenger_points,
        champion_points=champion_points,
        games=len(outcomes),
        challenger_crashes=sum(1 for outcome in outcomes if outcome[2] == "challenger"),
        champion_crashes=sum(1 for outcome in outcomes if outcome[2] == "champion"),
    )


def mutate(weights: Weights, rng: random.Random, *, scale: float = 0.35) -> Weights:
    """A challenger: the champion with one to three weights nudged."""
    values = asdict(weights)
    for name in rng.sample(TUNABLE, rng.randint(1, 3)):
        factor = 1.0 + rng.uniform(-scale, scale)
        nudged = values[name] * factor
        if values[name] == 0.0:
            nudged = rng.uniform(0.0, 2.0)
        values[name] = round(max(0.0, nudged), 3)
    return Weights(**values)


@dataclass
class Champion:
    """The reigning weights, plus how they got there."""

    weights: Weights = field(default_factory=Weights)
    round: int = 0
    history: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Champion":
        file = Path(path)
        if not file.exists():
            return cls()
        raw = json.loads(file.read_text(encoding="utf-8"))
        return cls(
            weights=Weights(**raw["weights"]),
            round=raw.get("round", 0),
            history=raw.get("history", []),
        )

    def save(self, path: str | Path) -> Path:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(
                {"weights": asdict(self.weights), "round": self.round, "history": self.history},
                indent=2,
            ),
            encoding="utf-8",
        )
        return file


# The gauntlet searches deeper than the tuning does, and deliberately so. A
# champion validated at the tuning depth of 4 passed its check 7-6 and then lost
# 9-12 to the weights it replaced when both played at depth 6. Weights that win
# a shallow search do not necessarily win a deep one, and the bot plays deep, so
# validating at the tuning depth measures the wrong thing.
GAUNTLET_DEPTH = 6
GAUNTLET_MATCHES = 16


def gauntlet(weights: Weights, *, matches: int = GAUNTLET_MATCHES, moves: int = 150,
             depth: int = GAUNTLET_DEPTH, seed: int = 7000,
             workers: int = 1) -> tuple[int, int, int]:
    """Score ``weights`` against the fixed greedy baseline. Returns (W, L, D).

    Self-play alone cannot see the whole line drifting: a challenger only ever
    proves it beats *this* champion, and both can wander somewhere generally
    weaker together. That is not hypothetical -- a ten-round session promoted
    three challengers, and the result lost 8-4 to the very weights it started
    from. An outside opponent is the only thing that notices.
    """
    jobs = [(weights, depth, moves, seed + index) for index in range(matches)]

    if workers > 1 and matches > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(_gauntlet_one, jobs))
    else:
        outcomes = [_gauntlet_one(job) for job in jobs]

    return (
        sum(1 for o in outcomes if o == "me"),
        sum(1 for o in outcomes if o == "opp"),
        sum(1 for o in outcomes if o not in ("me", "opp")),
    )


def _gauntlet_one(job: tuple[Weights, int, int, int]) -> str:
    """One gauntlet game. Top level so a process pool can pickle it."""
    from .simulator import greedy_policy

    weights, depth, moves, game_seed = job
    rng = random.Random(game_seed)
    result = play_match(
        strategy_policy(_strategy(weights, depth)),
        greedy_policy(),
        position=starting_position(remaining_moves=moves, rng=rng),
        rng=rng,
    )
    return result.winner


def run_ladder(
    champion: Champion,
    *,
    rounds: int = 10,
    matches: int = 8,
    moves: int = 200,
    margin: float = 1.0,
    seed: int = 0,
    workers: int = 1,
    depth: int = TUNING_DEPTH,
    rng: random.Random | None = None,
) -> Iterator[tuple[int, DuelResult, bool, Champion]]:
    """Hill-climb the weights, yielding ``(round, result, promoted, champion)``.

    ``margin`` is how many points beyond a dead heat the challenger must win by.
    Half a point is one drawn game and is well inside the noise, so the default
    asks for a full game clear.
    """
    rng = rng or random.Random(seed)

    for index in range(1, rounds + 1):
        challenger = mutate(champion.weights, rng)
        # Rotate the seeds each round so a challenger cannot win by fitting the
        # handful of openings the previous round happened to use.
        result = duel(
            challenger,
            champion.weights,
            matches=matches,
            moves=moves,
            seed=seed + index * 100,
            workers=workers,
            depth=depth,
        )
        # Winning on points is not enough. The one thing this bot promises is
        # that it does not kill itself, so a challenger that crashes more often
        # than the champion is rejected however many points it scored -- those
        # points came from taking risks we said we would not take.
        promoted = (
            result.challenger_points >= result.games / 2 + margin
            and result.challenger_crashes <= result.champion_crashes
        )
        if promoted:
            champion = Champion(
                weights=challenger,
                round=champion.round + 1,
                history=[*champion.history, f"round {index}: {result}"],
            )
        yield index, result, promoted, champion
