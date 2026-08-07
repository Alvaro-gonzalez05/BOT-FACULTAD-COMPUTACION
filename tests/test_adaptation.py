"""Playing differently against different opponents -- carefully.

The thing these tests defend is that a profile describes a *moving target*. The
opponents are people editing their bots between now and the tournament, so a
read on them is always slightly stale and must never be trusted enough to hurt.
"""

from snakebot.opponents import MAX_ADJUSTMENT, RECENT_WINDOW, Book, MatchSample, Profile, adapt
from snakebot.search import Weights


def profile_with(crash_rate: float, matches: int = RECENT_WINDOW) -> Profile:
    crashes = round(crash_rate * matches)
    return Profile(
        name="rival",
        samples=[
            MatchSample(game_id=f"g{i}", turns=100, they_crashed=i < crashes,
                        food_chases=50, food_chances=100)
            for i in range(matches)
        ],
    )


def test_against_someone_who_never_crashes_it_gets_hungrier():
    """That match ends on move count, so it is decided on apples."""
    tuned = adapt(Weights(), profile_with(crash_rate=0.0))
    assert tuned.food_distance > Weights().food_distance
    assert tuned.food_race > Weights().food_race


def test_against_someone_who_kills_itself_it_stops_chasing_food():
    """No need to out-eat a bot that is about to drive into a wall."""
    tuned = adapt(Weights(), profile_with(crash_rate=1.0))
    assert tuned.food_distance < Weights().food_distance
    assert tuned.opponent_choke > Weights().opponent_choke


def test_one_match_barely_moves_anything():
    """A single game is an anecdote, not a read."""
    barely = adapt(Weights(), profile_with(crash_rate=1.0, matches=1))
    fully = adapt(Weights(), profile_with(crash_rate=1.0, matches=RECENT_WINDOW))
    assert abs(barely.food_distance - Weights().food_distance) < abs(
        fully.food_distance - Weights().food_distance
    )


def test_an_unknown_opponent_is_played_with_the_plain_weights():
    assert adapt(Weights(), Profile(name="stranger")) == Weights()


def test_no_profile_can_weaken_the_promise_not_to_crash():
    """A read on the opponent may change what we want, never what we refuse."""
    for rate in (0.0, 0.25, 0.5, 0.75, 1.0):
        tuned = adapt(Weights(), profile_with(crash_rate=rate))
        assert tuned.trapped_penalty == Weights().trapped_penalty
        assert tuned.space == Weights().space
        assert tuned.territory == Weights().territory


def test_adjustments_stay_within_their_stated_bound():
    for rate in (0.0, 0.5, 1.0):
        tuned = adapt(Weights(), profile_with(crash_rate=rate))
        for name in ("food_distance", "food_race", "opponent_choke", "trapped_bonus"):
            base = getattr(Weights(), name)
            assert abs(getattr(tuned, name) - base) <= base * MAX_ADJUSTMENT + 1e-9


def test_a_rival_that_improves_is_re_read_and_the_old_reputation_fades():
    """The point of the recent window, and the reason totals were wrong.

    A bot that crashed every game and then stopped must stop being treated as a
    bot that crashes -- otherwise the profile keeps preparing for a rival that
    no longer exists.
    """
    profile = Profile(name="lucas")
    for i in range(RECENT_WINDOW):           # a bad early run
        profile.add(MatchSample(game_id=f"old{i}", turns=10, they_crashed=True))
    assert profile.crash_rate == 1.0

    for i in range(RECENT_WINDOW):           # they fixed their bot
        profile.add(MatchSample(game_id=f"new{i}", turns=150, they_crashed=False))
    assert profile.crash_rate == 0.0, "the old reputation never faded"

    # And the play adapts with it: from patient to hungry.
    assert adapt(Weights(), profile).food_distance > Weights().food_distance


def test_the_history_kept_on_disk_stays_bounded():
    profile = Profile(name="rival")
    for i in range(500):
        profile.add(MatchSample(game_id=f"g{i}"))
    assert len(profile.samples) <= RECENT_WINDOW * 3


def test_an_old_format_book_still_loads(tmp_path):
    """The first version stored lifetime totals; those files must not break."""
    legacy = tmp_path / "opponents.json"
    legacy.write_text(
        '{"profiles": {"bob": {"name": "bob", "matches": 4, "losses_by_their_crash": 2,'
        ' "losses_by_our_crash": 0, "total_turns": 400, "food_chases": 200,'
        ' "food_chances": 400, "match_ids": ["a", "b", "c", "d"]}}}',
        encoding="utf-8",
    )
    profile = Book.load(legacy).get("bob")
    assert profile.matches == 4
    assert profile.crash_rate == 0.5
    assert profile.greed == 0.5
