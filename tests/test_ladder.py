"""The self-play ladder: fair duels, honest promotions, weights that persist."""

import random

from snakebot.ladder import TUNABLE, Champion, duel, mutate, run_ladder
from snakebot.search import Weights


def test_a_weight_set_duelling_itself_comes_out_dead_even():
    """The fairness check the whole ladder rests on.

    Identical weights on the same openings with the sides swapped must score
    exactly level. If this drifts, the ladder is measuring the opening rather
    than the weights, and every promotion after that is an artefact.
    """
    weights = Weights()
    result = duel(weights, weights, matches=3, moves=40, seed=5)
    assert result.challenger_points == result.champion_points
    assert result.games == 6


def test_a_duel_counts_every_game():
    result = duel(Weights(), Weights(territory=3.0), matches=2, moves=30, seed=1)
    assert result.games == 4
    assert result.challenger_points + result.champion_points == 4


def test_running_the_games_in_parallel_changes_nothing():
    """Workers are a speed knob, never a result knob."""
    sequential = duel(Weights(), Weights(territory=3.0), matches=3, moves=40, seed=1, workers=1)
    parallel = duel(Weights(), Weights(territory=3.0), matches=3, moves=40, seed=1, workers=4)
    assert sequential == parallel


def test_mutation_moves_something_and_leaves_the_rest_alone():
    rng = random.Random(7)
    base = Weights()
    changed = mutate(base, rng)
    differences = [
        name for name in vars(base) if getattr(base, name) != getattr(changed, name)
    ]
    assert 1 <= len(differences) <= 3
    assert set(differences) <= set(TUNABLE)


def test_mutation_never_touches_the_do_not_die_weight():
    """`trapped_penalty` is the promise that the bot does not kill itself."""
    rng = random.Random(0)
    weights = Weights()
    for _ in range(200):
        weights = mutate(weights, rng)
    assert weights.trapped_penalty == Weights().trapped_penalty


def test_mutation_keeps_weights_non_negative():
    rng = random.Random(3)
    weights = Weights()
    for _ in range(200):
        weights = mutate(weights, rng)
        assert all(value >= 0 for value in vars(weights).values())


def test_a_champion_round_trips_through_disk(tmp_path):
    champion = Champion(weights=Weights(territory=2.5), round=4, history=["round 1: 9-7 of 16"])
    path = champion.save(tmp_path / "champion.json")
    loaded = Champion.load(path)
    assert loaded.weights == champion.weights
    assert loaded.round == 4
    assert loaded.history == champion.history


def test_a_missing_champion_file_starts_from_the_defaults(tmp_path):
    loaded = Champion.load(tmp_path / "nothing.json")
    assert loaded.weights == Weights()
    assert loaded.round == 0


def test_the_ladder_only_promotes_on_a_real_margin():
    """With an unreachable margin nothing is ever promoted."""
    champion = Champion()
    rounds = list(
        run_ladder(champion, rounds=2, matches=1, moves=20, margin=99.0, rng=random.Random(1))
    )
    assert [promoted for _, _, promoted, _ in rounds] == [False, False]
    assert all(result.weights == Weights() for _, _, _, result in rounds)


def test_a_challenger_that_crashes_more_is_never_promoted(monkeypatch):
    """Points won by taking risks we promised not to take do not count.

    A real ladder round promoted a challenger that crashed once against a
    champion that crashed none. Whatever those points were worth, they were
    bought with the one property the bot actually guarantees.
    """
    import snakebot.ladder as ladder_module

    # A duel the challenger wins on points but loses on crashes.
    def fake_duel(*args, **kwargs):
        return ladder_module.DuelResult(
            challenger_points=11.0,
            champion_points=1.0,
            games=12,
            challenger_crashes=1,
            champion_crashes=0,
        )

    monkeypatch.setattr(ladder_module, "duel", fake_duel)
    rounds = list(
        ladder_module.run_ladder(Champion(), rounds=1, matches=1, rng=random.Random(1))
    )
    _, result, promoted, champion = rounds[0]
    assert result.challenger_points > result.champion_points
    assert not promoted
    assert champion.weights == Weights()


def test_a_clean_win_is_still_promoted(monkeypatch):
    import snakebot.ladder as ladder_module

    def fake_duel(*args, **kwargs):
        return ladder_module.DuelResult(
            challenger_points=9.0,
            champion_points=3.0,
            games=12,
            challenger_crashes=0,
            champion_crashes=1,
        )

    monkeypatch.setattr(ladder_module, "duel", fake_duel)
    rounds = list(
        ladder_module.run_ladder(Champion(), rounds=1, matches=1, rng=random.Random(1))
    )
    assert rounds[0][2] is True


def test_a_promotion_records_where_it_came_from():
    champion = Champion()
    rounds = list(
        run_ladder(champion, rounds=3, matches=1, moves=20, margin=-99.0, rng=random.Random(2))
    )
    # A margin that always passes: every round promotes and the trail is kept.
    final = rounds[-1][3]
    assert final.round == 3
    assert len(final.history) == 3
    assert all("round" in entry for entry in final.history)


def test_the_gauntlet_notices_weights_that_drifted():
    """The check that self-play cannot do for itself.

    A real ten-round session promoted three challengers and ended up losing 8-4
    to the weights it started from. Only an outside opponent sees that, so the
    gauntlet has to rank sane weights above the drifted ones.
    """
    from snakebot.ladder import gauntlet

    sane = gauntlet(Weights(), matches=6, moves=100, depth=2, workers=1)
    drifted = gauntlet(
        Weights(length=31.9, territory=0.7, food_race=54.6, trapped_bonus=0.0),
        matches=6, moves=100, depth=2, workers=1,
    )
    assert sum(sane) == sum(drifted) == 6
    assert sane[0] >= drifted[0]


def test_the_gauntlet_does_not_depend_on_worker_count():
    from snakebot.ladder import gauntlet

    assert gauntlet(Weights(), matches=4, moves=60, depth=2, workers=1) == gauntlet(
        Weights(), matches=4, moves=60, depth=2, workers=3
    )


def test_the_gauntlet_defaults_to_searching_deeper_than_the_tuning():
    """The bug this cost us: validating at tuning depth measures the wrong thing.

    A champion validated at depth 4 passed 7-6, then lost 9-12 to the weights it
    replaced once both searched at depth 6. Weights that win a shallow search do
    not necessarily win a deep one, and the bot plays deep.
    """
    from snakebot.ladder import GAUNTLET_DEPTH, TUNING_DEPTH

    assert GAUNTLET_DEPTH > TUNING_DEPTH
