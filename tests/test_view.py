"""The live tactical view and the layers behind it."""

from snakebot import view
from snakebot.engine import Position, Snake
from snakebot.heuristics import shortest_path


def test_the_shortest_path_goes_around_a_wall_of_body():
    #  . . . .
    #  . # # .     '#' is blocked; the route has to go around it
    #  . . . .
    blocked = {(1, 1), (1, 2), (1, 3)}
    path = shortest_path(3, 4, (0, 0), (2, 0), blocked)
    assert path[0] == (0, 0) and path[-1] == (2, 0)
    assert not set(path) & blocked
    # Straight down the first column is available and is the shortest route.
    assert len(path) == 3


def test_there_is_no_path_through_a_sealed_wall():
    blocked = {(1, 0), (1, 1), (1, 2)}
    assert shortest_path(3, 3, (0, 0), (2, 2), blocked) == []


def sealed_pocket() -> Position:
    """Our body seals the opponent into a 3-cell pocket against the top wall.

    The sealing cells sit near *our head*, so they stay blocked far longer than
    the opponent can survive -- a body wall decays from the tail end, and a trap
    only counts if it outlasts the snake inside it.
    """
    return Position(
        rows=10,
        cols=10,
        me=Snake(
            ((0, 3), (1, 3), (1, 2), (1, 1), (1, 0), (2, 0), (3, 0),
             (4, 0), (5, 0), (6, 0), (7, 0), (8, 0))
        ),
        opp=Snake(((0, 0), (0, 1), (0, 2))),
        food=frozenset({(5, 5)}),
    )


def test_the_layers_see_a_sealed_in_opponent():
    layers = view.Layers.of(sealed_pocket())
    assert layers.opp_room == 0
    assert layers.they_are_trapped
    assert not layers.i_am_trapped
    assert "THEY ARE SEALED IN" in view.render(sealed_pocket(), colour=False)


def test_a_body_wall_that_decays_in_time_is_not_a_trap():
    """A short snake's body stops blocking almost immediately, so it seals nothing."""
    position = Position(
        rows=10,
        cols=10,
        me=Snake(((0, 3), (1, 3), (1, 2))),  # too short to hold the wall
        opp=Snake(((0, 0), (0, 1), (0, 2))),
        food=frozenset({(5, 5)}),
    )
    layers = view.Layers.of(position)
    assert not layers.they_are_trapped


def test_the_view_marks_both_heads_the_food_and_the_route():
    position = Position(
        rows=5,
        cols=5,
        me=Snake(((2, 2), (2, 1))),
        opp=Snake(((0, 4), (1, 4))),
        food=frozenset({(2, 4)}),
    )
    drawing = view.render(position, colour=False)
    assert "@" in drawing  # our head
    assert "X" in drawing  # their head
    assert "*" in drawing  # the apple
    assert "·" in drawing  # the route we would take to it
    assert "apple 2v" in drawing


def test_the_view_says_plainly_when_we_are_the_ones_boxed_in():
    position = Position(
        rows=5,
        cols=5,
        me=Snake(((0, 0), (0, 1), (1, 1), (1, 0))),
        opp=Snake(((4, 4), (4, 3))),
        food=frozenset({(2, 2)}),
    )
    layers = view.Layers.of(position)
    drawing = view.render(position, colour=False)
    if layers.i_am_trapped:
        assert "WE ARE SEALED IN" in drawing


def test_colour_is_off_when_the_output_is_not_a_terminal():
    position = Position(
        rows=4, cols=4, me=Snake(((0, 0),)), opp=Snake(((3, 3),)), food=frozenset()
    )
    assert "\x1b[" not in view.render(position, colour=False)
    assert "\x1b[" in view.render(position, colour=True)
