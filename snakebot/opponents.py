"""What we have learned about each opponent, accumulated from real matches.

This is the part of "learning" that actually pays off in this game. Memorising
positions does not: with snakes of length six there are on the order of 10^22
distinct boards, so a stored position practically never comes up twice. But you
face the *same handful of bots* over and over, and how a particular bot behaves
is both stable and cheap to measure.

Every finished match leaves a transcript in ``logs/``. Reading those back gives,
per opponent: how often they crash, how long they last, how hard they chase
apples, and whether they contest the ones we are closer to. That profile is a
few hundred bytes, so it lives in a JSON file next to the code -- no database.

What the profile is used for is deliberately narrow. An opponent that crashes on
its own does not need to be attacked; one that never does has to be squeezed.
Anything beyond that is guesswork until there are more matches to learn from.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .board import Board
from .engine import Position, Snake
from .heuristics import INFINITE, distance_map
from .search import Weights

DEFAULT_PATH = "opponents.json"


@dataclass
class MatchSample:
    """What one match told us about an opponent."""

    game_id: str
    turns: int = 0
    they_crashed: bool = False
    we_crashed: bool = False
    food_chases: int = 0
    food_chances: int = 0


# How many recent matches a profile judges an opponent on. Opponents are people
# improving their bots between now and the tournament, so a rival that crashed
# constantly last week is not the rival you will face. Lifetime averages would
# keep insisting it is; a short window forgets, which is the point.
RECENT_WINDOW = 8


@dataclass
class Profile:
    """One opponent, judged on how they have played *lately*."""

    name: str
    samples: list[MatchSample] = field(default_factory=list)

    @property
    def matches(self) -> int:
        """Lifetime count, for display only -- never for decisions."""
        return len(self.samples)

    @property
    def recent(self) -> list[MatchSample]:
        return self.samples[-RECENT_WINDOW:]

    @property
    def confidence(self) -> float:
        """0 to 1: how much of the recent window we have actually filled.

        One match is an anecdote. This is what stops a single game from
        swinging how the bot plays.
        """
        return min(1.0, len(self.recent) / RECENT_WINDOW)

    @property
    def crash_rate(self) -> float:
        recent = self.recent
        if not recent:
            return 0.0
        return sum(1 for s in recent if s.they_crashed) / len(recent)

    @property
    def greed(self) -> float:
        """0 = ignores apples, 1 = walks straight at the nearest one."""
        chances = sum(s.food_chances for s in self.recent)
        if not chances:
            return 0.5
        return sum(s.food_chases for s in self.recent) / chances

    @property
    def average_turns(self) -> float:
        recent = self.recent
        if not recent:
            return 0.0
        return sum(s.turns for s in recent) / len(recent)

    @property
    def match_ids(self) -> list[str]:
        return [s.game_id for s in self.samples]

    def add(self, sample: MatchSample) -> None:
        self.samples.append(sample)
        # Keep a little history beyond the window so the trend stays visible in
        # the file, but never let it grow without bound.
        del self.samples[: max(0, len(self.samples) - RECENT_WINDOW * 3)]

    def summary(self) -> str:
        if not self.samples:
            return f"{self.name}: never played"
        window = len(self.recent)
        return (
            f"{self.name}: {self.matches} matches (last {window}), crashes on its own "
            f"{self.crash_rate:.0%}, chases apples {self.greed:.0%}, "
            f"lasts {self.average_turns:.0f} turns"
        )


@dataclass
class Book:
    """Every profile we hold, keyed by opponent name."""

    profiles: dict[str, Profile] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Book":
        file = Path(path)
        if not file.exists():
            return cls()
        raw = json.loads(file.read_text(encoding="utf-8"))
        return cls(
            profiles={
                name: _profile_from(name, data)
                for name, data in raw.get("profiles", {}).items()
            }
        )

    def save(self, path: str | Path = DEFAULT_PATH) -> Path:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(
                {"profiles": {name: asdict(p) for name, p in self.profiles.items()}}, indent=2
            ),
            encoding="utf-8",
        )
        return file

    def get(self, name: str) -> Profile:
        return self.profiles.get(name) or Profile(name=name)

    def observe_transcript(self, path: str | Path) -> str | None:
        """Fold one ``game_<id>.log`` into the book. Returns the opponent's name.

        Replaying a transcript is cheap and idempotent-by-match-id, so this can
        be run over the whole ``logs/`` directory as often as you like.
        """
        lines = [
            line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        turns: list[tuple[Position, str]] = []
        opponent: str | None = None
        game_id: str | None = None
        crashed_side: str | None = None
        my_side = "A"

        for line in lines:
            marker, _, payload = line.partition(" ")
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if marker != "<":
                continue
            data = message.get("data", {})
            event = message.get("event")
            if event not in ("your_turn", "game_over"):
                continue

            game_id = game_id or data.get("game_id")
            side = data.get("side") or my_side
            if event == "your_turn":
                my_side = side
            opponent = opponent or _opponent_name(data, my_side)

            board = data.get("board")
            if not board:
                continue
            try:
                position = _position_from(board, my_side, data)
            except Exception:  # noqa: BLE001 - a malformed frame is not worth a crash
                continue
            if event == "your_turn":
                turns.append((position, side))
            else:
                crashed_side = _who_crashed(data, my_side)

        if opponent is None or game_id is None:
            return None

        profile = self.get(opponent)
        if game_id in profile.match_ids:
            return opponent  # already counted

        chases, chances = _measure_greed(turns)
        profile.add(
            MatchSample(
                game_id=game_id,
                turns=len(turns),
                they_crashed=crashed_side == "opp",
                we_crashed=crashed_side == "me",
                food_chases=chases,
                food_chances=chances,
            )
        )
        self.profiles[opponent] = profile
        return opponent

    def observe_directory(self, directory: str | Path) -> list[str]:
        """Fold in every transcript in ``directory``."""
        seen = []
        for path in sorted(Path(directory).glob("game_*.log")):
            name = self.observe_transcript(path)
            if name:
                seen.append(name)
        return seen


def _profile_from(name: str, data: dict) -> Profile:
    """Read a profile, accepting the older running-totals layout.

    The first version stored lifetime sums, which is the wrong shape once you
    accept that opponents improve. An old file collapses to a single sample so
    nothing is lost and nothing is over-trusted.
    """
    if "samples" in data:
        return Profile(
            name=data.get("name", name),
            samples=[MatchSample(**sample) for sample in data.get("samples", [])],
        )
    ids = data.get("match_ids") or []
    matches = data.get("matches", len(ids)) or 0
    if not matches:
        return Profile(name=name)
    crashed = data.get("losses_by_their_crash", 0)
    turns = data.get("total_turns", 0) // max(1, matches)
    chases = data.get("food_chases", 0)
    chances = data.get("food_chances", 0)
    return Profile(
        name=name,
        samples=[
            MatchSample(
                game_id=ids[i] if i < len(ids) else f"legacy-{name}-{i}",
                turns=turns,
                they_crashed=i < crashed,
                food_chases=chases // matches,
                food_chances=chances // matches,
            )
            for i in range(matches)
        ],
    )


def _opponent_name(data: dict, my_side: str) -> str | None:
    # player_1 is side A, player_2 is side B.
    return data.get("player_2" if my_side == "A" else "player_1")


def _position_from(board: str, my_side: str, data: dict) -> Position:
    parsed = Board.parse(board)
    other = "B" if my_side == "A" else "A"
    return Position(
        rows=parsed.rows,
        cols=parsed.cols,
        me=Snake((parsed.heads[my_side],)),
        opp=Snake((parsed.heads[other],)),
        food=frozenset(parsed.food),
        remaining_moves=data.get("remaining_moves", 0) or 0,
    )


def _who_crashed(data: dict, my_side: str) -> str | None:
    """Read the crash off the final scores: crashing costs -500."""
    mine = data.get("score_1" if my_side == "A" else "score_2")
    theirs = data.get("score_2" if my_side == "A" else "score_1")
    if isinstance(theirs, (int, float)) and theirs <= -400:
        return "opp"
    if isinstance(mine, (int, float)) and mine <= -400:
        return "me"
    return None


def _measure_greed(turns: Iterable[tuple[Position, str]]) -> tuple[int, int]:
    """How often the opponent's head got closer to the apple it was nearest to."""
    chases = chances = 0
    previous: int | None = None
    for position, _side in turns:
        if not position.food:
            previous = None
            continue
        distances = distance_map(
            position.rows, position.cols, position.opp.head, frozenset()
        )
        nearest = min((distances.get(f, INFINITE) for f in position.food), default=INFINITE)
        if nearest >= INFINITE:
            previous = None
            continue
        if previous is not None:
            chances += 1
            if nearest < previous:
                chases += 1
        previous = nearest
    return chases, chances


# How far a profile is allowed to move a weight, at full confidence. Kept modest
# on purpose: the profile is a hint about a moving target, not a law. An
# opponent is a person who edits their bot between matches, so a read on them is
# always slightly out of date and must never be trusted enough to hurt.
MAX_ADJUSTMENT = 0.5


def adapt(weights: "Weights", profile: Profile) -> "Weights":
    """Tune the evaluation for this particular opponent.

    Two dials, both driven by how often they crash on their own lately:

    * **Hunger.** An opponent that never crashes will still be alive when the
      moves run out, so the match is decided on apples and we have to compete
      for them. One that kills itself does not need to be out-eaten, and
      chasing food against it only takes on risk for nothing.
    * **Pressure.** Squeezing an opponent's room pays off in proportion to how
      likely they are to mishandle it. Against someone who never crashes,
      spending moves on the squeeze is largely wasted.

    Nothing here touches the terms that keep *us* alive. A profile can change
    what the bot wants; it can never change what the bot refuses to do.
    """
    from dataclasses import replace

    strength = MAX_ADJUSTMENT * profile.confidence
    if strength <= 0:
        return weights

    # -1 when they always crash, +1 when they never do.
    survives = (1.0 - profile.crash_rate) * 2 - 1
    hunger = 1.0 + strength * survives
    pressure = 1.0 - strength * survives

    return replace(
        weights,
        food_distance=weights.food_distance * hunger,
        food_race=weights.food_race * hunger,
        opponent_choke=weights.opponent_choke * pressure,
        trapped_bonus=weights.trapped_bonus * pressure,
    )
