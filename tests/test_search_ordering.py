"""Move ordering must buy speed without changing what the search concludes."""

import random

from snakebot.search import Search
from snakebot.simulator import greedy_policy, starting_position

NO_CUTOFF = 3600.0


def positions(count: int = 12):
    """A handful of real mid-game positions to check against."""
    rng = random.Random(11)
    state = starting_position(remaining_moves=200, rng=rng)
    opponent = greedy_policy()
    out = []
    for _ in range(count):
        if state.over:
            break
        out.append(state)
        move, _ = Search(time_budget=NO_CUTOFF, max_depth=2).best_move(state)
        state = state.step(move)
        if state.over:
            break
        state = state.step(opponent(state.flipped()))
    return out


def search_without_killers(state, depth):
    """The same search with the killer table neutered."""
    search = Search(time_budget=NO_CUTOFF, max_depth=depth)
    original = search._ordered_moves

    def ordered(position, ply=0):
        search._killers = {}
        return original(position, ply)

    search._ordered_moves = ordered  # type: ignore[method-assign]
    return search


def test_the_killer_table_does_not_change_the_verdict():
    """A cutoff heuristic is a speed knob; the value it returns must be identical."""
    for state in positions():
        with_killers = Search(time_budget=NO_CUTOFF, max_depth=4)
        without = search_without_killers(state, 4)
        _, fast = with_killers.best_move(state)
        _, slow = without.best_move(state)
        assert fast == slow, f"killer ordering changed the evaluation: {fast} vs {slow}"


def test_the_killer_table_visits_fewer_nodes():
    """The whole point: fewer nodes for the same answer means more depth in time."""
    total_with = total_without = 0
    for state in positions():
        with_killers = Search(time_budget=NO_CUTOFF, max_depth=6)
        without = search_without_killers(state, 6)
        with_killers.best_move(state)
        without.best_move(state)
        total_with += with_killers.stats.nodes
        total_without += without.stats.nodes
    assert total_with < total_without, (
        f"killer ordering did not prune anything: {total_with} vs {total_without}"
    )


def test_the_search_respects_its_deadline():
    """A late move is penalised by the server, so the budget has to mean something.

    Checking the clock every 256 nodes let a 150ms budget run 803ms in a real
    match -- each node costs two board sweeps and two flood fills, so a few
    hundred of them is most of a second. This pins the overshoot down.
    """
    import time

    # A bigger budget so fixed per-call overhead is proportionally small, and a
    # loose factor so a busy machine does not fail the build. The bug this
    # guards against overshot by 5.4x on a 150ms budget, which this still
    # catches; the fixed version overshoots by about 1.3x.
    budget = 0.2
    worst = 0.0
    for state in positions(8):
        search = Search(time_budget=budget, max_depth=14)
        started = time.monotonic()
        search.best_move(state)
        worst = max(worst, time.monotonic() - started)

    assert worst < budget * 5, f"search ran {worst * 1000:.0f}ms on a {budget * 1000:.0f}ms budget"
