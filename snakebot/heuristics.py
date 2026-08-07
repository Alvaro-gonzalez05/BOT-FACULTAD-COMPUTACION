"""Board metrics the strategy reasons with: free space, territory, distances.

Everything here is pure: it takes a :class:`~snakebot.engine.Position` (or plain
grid data) and returns numbers. That keeps the strategy easy to unit test.
"""

from __future__ import annotations

from collections import deque

from .board import ALL_DIRECTIONS, Cell
from .engine import Position, Snake

INFINITE = 10**6


def decay_map(*snakes: Snake) -> dict[Cell, int]:
    """When each occupied cell frees up, in ticks from now.

    A snake of length ``L`` vacates ``body[i]`` after ``L - i`` of its own moves
    (the tail leaves first). Treating that as a per-cell "free from tick N" lets
    the flood fill walk through space that will have opened up by the time the
    snake gets there, which is what makes a long snake able to follow its own
    tail instead of declaring the corridor a dead end.
    """
    decay: dict[Cell, int] = {}
    for snake in snakes:
        length = len(snake)
        for index, cell in enumerate(snake.body):
            free_at = length - index
            previous = decay.get(cell)
            if previous is None or free_at < previous:
                decay[cell] = free_at
    return decay


def reachable_space(
    rows: int,
    cols: int,
    start: Cell,
    decay: dict[Cell, int],
    limit: int | None = None,
) -> int:
    """How many cells a head standing on ``start`` can still move through.

    ``start`` itself is not counted -- it is the head's own cell, so it is
    always occupied. A cell ``tick`` steps away is enterable when it has freed
    up by then, which is what lets the fill walk into space the snake's own tail
    is about to vacate.
    """
    seen = {start}
    queue: deque[tuple[Cell, int]] = deque([(start, 0)])
    count = 0
    while queue:
        (r, c), tick = queue.popleft()
        if limit is not None and count >= limit:
            return count
        for direction in ALL_DIRECTIONS:
            nxt = (r + direction.d_row, c + direction.d_col)
            if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols) or nxt in seen:
                continue
            if decay.get(nxt, 0) > tick + 1:
                continue
            seen.add(nxt)
            count += 1
            queue.append((nxt, tick + 1))
    return count


def distance_map(
    rows: int,
    cols: int,
    start: Cell,
    blocked: frozenset[Cell] | set[Cell],
) -> dict[Cell, int]:
    """Breadth-first distances from ``start`` over cells that are not blocked."""
    distances = {start: 0}
    queue: deque[Cell] = deque([start])
    while queue:
        r, c = queue.popleft()
        step = distances[(r, c)] + 1
        for direction in ALL_DIRECTIONS:
            nxt = (r + direction.d_row, c + direction.d_col)
            if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols):
                continue
            if nxt in distances or nxt in blocked:
                continue
            distances[nxt] = step
            queue.append(nxt)
    return distances


def shortest_path(
    rows: int,
    cols: int,
    start: Cell,
    goal: Cell,
    blocked: frozenset[Cell] | set[Cell],
) -> list[Cell]:
    """The cells from ``start`` to ``goal``, or ``[]`` when there is no route.

    Walks the breadth-first distances backwards from the goal, so the result is
    always one of the shortest routes.
    """
    distances = distance_map(rows, cols, start, blocked)
    if goal not in distances:
        return []
    path = [goal]
    cell = goal
    while cell != start:
        step = distances[cell] - 1
        for direction in ALL_DIRECTIONS:
            previous = (cell[0] + direction.d_row, cell[1] + direction.d_col)
            if distances.get(previous) == step:
                path.append(previous)
                cell = previous
                break
        else:  # pragma: no cover - the distance map guarantees a predecessor
            return []
    path.reverse()
    return path


def territory(position: Position) -> tuple[int, int]:
    """Voronoi split of the board: cells each snake reaches first.

    Contested cells go to whoever is about to move, since they get there first.

    ``Search.evaluate`` computes this inline rather than calling here: it needs
    both distance maps for the food terms anyway, and re-running the two
    breadth-first sweeps per leaf would roughly double the cost of a node. This
    version exists for tests and for poking at a position by hand.
    """
    blocked = position.blocked_cells()
    mine = distance_map(position.rows, position.cols, position.me.head, blocked - {position.me.head})
    theirs = distance_map(position.rows, position.cols, position.opp.head, blocked - {position.opp.head})

    my_cells = 0
    opp_cells = 0
    for cell, my_distance in mine.items():
        their_distance = theirs.get(cell, INFINITE)
        if my_distance < their_distance:
            my_cells += 1
        elif their_distance < my_distance:
            opp_cells += 1
        elif position.my_turn:
            my_cells += 1
        else:
            opp_cells += 1
    for cell in theirs:
        if cell not in mine:
            opp_cells += 1
    return my_cells, opp_cells


def nearest_food_distance(position: Position, mine: bool) -> int:
    """Steps from a head to the closest food, or :data:`INFINITE` if unreachable."""
    if not position.food:
        return INFINITE
    snake = position.me if mine else position.opp
    blocked = position.blocked_cells() - {snake.head}
    distances = distance_map(position.rows, position.cols, snake.head, blocked)
    return min((distances.get(cell, INFINITE) for cell in position.food), default=INFINITE)


def my_space(position: Position, limit: int | None = None) -> int:
    """Free space my head can still expand into."""
    decay = decay_map(position.me, position.opp)
    return reachable_space(position.rows, position.cols, position.me.head, decay, limit)


def opponent_space(position: Position, limit: int | None = None) -> int:
    decay = decay_map(position.me, position.opp)
    return reachable_space(position.rows, position.cols, position.opp.head, decay, limit)


def can_reach_own_tail(position: Position) -> bool:
    """The classic survival invariant: if I can still chase my tail, I'm alive.

    The tail cell is treated as free because by the time the head arrives the
    tail has moved on.
    """
    snake = position.me
    if len(snake) < 3:
        return True
    blocked = (position.blocked_cells() - {snake.tail, snake.head})
    distances = distance_map(position.rows, position.cols, snake.head, blocked)
    return snake.tail in distances
