from snakebot.board import Board, Direction, path_from_head

BOARD = "\n".join(
    [
        "|               |",
        "|  a            |",
        "|  a            |",
        "|  aaA          |",
        "|               |",
        "|               |",
        "|      bb       |",
        "|      b        |",
        "|   *  b        |",
        "|      bbbbB    |",
        "|              *|",
        "|   *           |",
        "|               |",
        "|               |",
        "|               |",
    ]
)


def test_parses_the_documented_board():
    board = Board.parse(BOARD)
    assert (board.rows, board.cols) == (15, 15)
    assert board.heads == {"A": (3, 4), "B": (9, 10)}
    assert board.food == frozenset({(8, 3), (10, 14), (11, 3)})
    assert (1, 2) in board.bodies["A"]
    assert (6, 6) in board.bodies["B"]


def test_render_round_trips():
    board = Board.parse(BOARD)
    assert Board.parse(board.render()) == board


def test_empty_rows_are_kept():
    board = Board.parse("|   |\n|   |\n| A |")
    assert board.rows == 3
    assert board.heads["A"] == (2, 1)


def test_rebuilds_the_snake_order_from_the_head():
    body = {(1, 2), (2, 2), (3, 2), (3, 3)}
    ordered = path_from_head((3, 4), body)
    assert ordered == [(3, 4), (3, 3), (3, 2), (2, 2), (1, 2)]


def test_direction_deltas_and_opposites():
    assert Direction.UP.delta == (-1, 0)
    assert Direction.RIGHT.opposite is Direction.LEFT
    assert Direction.from_wire("down") is Direction.DOWN
    assert Direction.from_delta(0, -1) is Direction.LEFT
