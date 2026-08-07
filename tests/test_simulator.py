import random

from snakebot.simulator import (
    greedy_policy,
    play_match,
    random_policy,
    starting_position,
    strategy_policy,
)
from snakebot.strategy import SnakeStrategy

FAST = dict(time_budget=0.02, max_depth=4)


def test_the_opening_position_is_symmetric_and_legal():
    state = starting_position(rng=random.Random(0))
    assert len(state.me) == len(state.opp) == 3
    assert not (set(state.me.body) & set(state.opp.body))
    # Three apples, matching the real server (see simulator.starting_position).
    assert len(state.food) == 3
    assert list(state.legal_moves())


def test_it_beats_a_random_opponent_without_crashing():
    rng = random.Random(7)
    strategy = SnakeStrategy(**FAST)
    for _ in range(3):
        result = play_match(
            strategy_policy(strategy),
            random_policy(rng),
            position=starting_position(remaining_moves=120, rng=rng),
            rng=rng,
        )
        assert result.crashed != "me"


def test_it_does_not_crash_against_a_greedy_opponent():
    rng = random.Random(11)
    strategy = SnakeStrategy(**FAST)
    result = play_match(
        strategy_policy(strategy),
        greedy_policy(),
        position=starting_position(remaining_moves=120, rng=rng),
        rng=rng,
    )
    assert result.crashed != "me"


def test_a_match_always_terminates():
    rng = random.Random(3)
    result = play_match(
        random_policy(rng),
        random_policy(rng),
        position=starting_position(remaining_moves=40, rng=rng),
        rng=rng,
    )
    assert result.turns <= 40
    assert result.winner in {"me", "opp", "draw"}


def test_the_survivor_opponent_does_not_crash():
    """The property that makes it a useful sparring partner at all.

    Two hand-written one-ply "safe and greedy" rules crashed in three and four
    matches out of twelve, which is why this one reuses the real strategy: a
    move can be safe this step and doomed two steps later, and only a search
    with the survival veto reliably sees that.
    """
    import random

    from snakebot.simulator import greedy_policy, play_match, starting_position, survivor_policy

    crashes = 0
    for seed in range(6):
        rng = random.Random(500 + seed)
        result = play_match(
            survivor_policy(),
            greedy_policy(),
            position=starting_position(remaining_moves=150, rng=rng),
            rng=rng,
        )
        if result.crashed == "me":
            crashes += 1
    assert crashes == 0, f"the survivor crashed in {crashes} of 6 matches"


def test_a_rival_shaped_partner_plays_by_different_rules_to_ours():
    """The property that makes the rival gauntlet worth trusting.

    `survivor_policy` reuses the real strategy, so matches against it ended
    level seven times in ten and it could not separate anything. These are
    plain heuristic bots, and being a different kind of player is the point.
    """
    import inspect

    from snakebot.simulator import rival_policy

    source = inspect.getsource(rival_policy)
    assert "SnakeStrategy" not in source
    assert "Search" not in source


def test_carelessness_actually_changes_how_often_a_rival_dies():
    """The dial has to move, or the profiles cannot be reproduced.

    A first version kept a safety bias on careless turns and `care` was inert:
    1.0, 0.95 and 0.85 all produced an identical crash rate because both
    branches picked the same square.
    """
    import random

    from snakebot.simulator import greedy_policy, play_match, rival_policy, starting_position

    def crash_rate(care: float) -> float:
        crashes = 0
        for seed in range(8):
            rng = random.Random(600 + seed)
            result = play_match(
                rival_policy(greed=0.6, care=care, seed=seed),
                greedy_policy(),
                position=starting_position(remaining_moves=150, rng=rng),
                rng=rng,
            )
            if result.crashed == "me":
                crashes += 1
        return crashes / 8

    assert crash_rate(0.8) > crash_rate(1.0)


def test_a_profile_maps_to_a_matching_care_setting():
    from snakebot.opponents import MatchSample, Profile
    from snakebot.simulator import care_for_crash_rate, rival_from_profile

    careful = Profile(name="steady", samples=[MatchSample(game_id=f"g{i}") for i in range(8)])
    reckless = Profile(
        name="reckless",
        samples=[MatchSample(game_id=f"g{i}", they_crashed=True) for i in range(8)],
    )
    assert careful.crash_rate == 0.0 and reckless.crash_rate == 1.0
    assert care_for_crash_rate(careful.crash_rate) > care_for_crash_rate(reckless.crash_rate)
    assert callable(rival_from_profile(careful))
