"""A real websocket round trip against a local stand-in for the match server.

Spins up a server that speaks the documented protocol, points the bot at it,
and checks the bot connects, accepts the challenge, plays legal moves for a
whole match, and writes a transcript. This is the only test that exercises the
transport, the tracker and the strategy together.
"""

import asyncio
import json

import pytest
import websockets

from snakebot.board import Board, Direction
from snakebot.client import BotClient, ClientConfig
from snakebot.engine import Position, Snake
from snakebot.simulator import greedy_policy, starting_position

TURNS = 12


class ScriptedServer:
    """Plays a real (small) match, validating every move the bot sends."""

    def __init__(self, turns: int = TURNS) -> None:
        self.turns = turns
        self.moves: list[str] = []
        self.accepted = False
        self.illegal: list[str] = []
        self.finished = asyncio.Event()

    async def handle(self, websocket) -> None:
        await websocket.send(json.dumps({"event": "list_users", "data": {"users": ["rival@x.com"]}}))
        await websocket.send(
            json.dumps({"event": "challenge", "data": {"opponent": "rival@x.com", "challenge_id": "c_1"}})
        )
        reply = json.loads(await websocket.recv())
        self.accepted = reply["action"] == "accept_challenge"

        state = starting_position(remaining_moves=self.turns * 2)
        opponent = greedy_policy()

        for turn in range(self.turns):
            token = f"t_{turn}"
            await websocket.send(
                json.dumps({"event": "your_turn", "data": self._turn_data(state, token)})
            )
            action = json.loads(await websocket.recv())
            assert action["action"] == "move"
            assert action["data"]["turn_token"] == token
            assert action["data"]["game_id"] == "g_1"

            name = action["data"]["direction"]
            self.moves.append(name)
            direction = Direction.from_wire(name)
            if direction not in {d for d, _ in state.legal_moves()}:
                self.illegal.append(f"turn {turn}: {name}")
                break

            state = state.step(direction)
            state = state.step(opponent(state.flipped()))

        await websocket.send(
            json.dumps(
                {
                    "event": "game_over",
                    "data": {
                        "game_id": "g_1",
                        "winner": "me@x.com",
                        "player_1": "me@x.com",
                        "score_1": state.my_score,
                        "player_2": "rival@x.com",
                        "score_2": state.opp_score,
                    },
                }
            )
        )
        await asyncio.sleep(0.1)
        self.finished.set()

    @staticmethod
    def _turn_data(state: Position, token: str) -> dict:
        return {
            "board": state.render("A"),
            "rows": state.rows,
            "cols": state.cols,
            "side": "A",
            "remaining_moves": state.remaining_moves,
            "player_1": "me@x.com",
            "score_1": state.my_score,
            "player_2": "rival@x.com",
            "score_2": state.opp_score,
            "game_id": "g_1",
            "turn_token": token,
        }


async def _play_against(server: ScriptedServer, tmp_path) -> BotClient:
    async with websockets.serve(server.handle, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        client = BotClient(
            ClientConfig(
                token="test-token",
                server=f"ws://127.0.0.1:{port}/ws",
                time_budget=0.02,
                max_depth=4,
                log_dir=str(tmp_path),
                opponent_book=str(tmp_path / "opponents.json"),
                print_board=False,
                my_email="me@x.com",
            )
        )
        runner = asyncio.ensure_future(client.run())
        try:
            await asyncio.wait_for(server.finished.wait(), timeout=30)
        finally:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
    return client


def test_the_bot_plays_a_whole_match_over_a_real_websocket(tmp_path):
    server = ScriptedServer()
    client = asyncio.run(_play_against(server, tmp_path))

    assert server.accepted, "the bot never accepted the challenge"
    assert server.illegal == [], f"the bot sent an illegal move: {server.illegal}"
    assert len(server.moves) == TURNS
    assert set(server.moves) <= {"up", "down", "left", "right"}
    assert client.scoreboard.wins == 1

    transcript = (tmp_path / "game_g_1.log").read_text(encoding="utf-8").splitlines()
    assert len(transcript) == TURNS * 2 + 1  # one event and one action per turn, plus game_over


def test_the_board_the_server_renders_is_the_board_we_parse():
    state = starting_position()
    board = Board.parse(state.render("A"))
    assert board.heads["A"] == state.me.head
    assert board.heads["B"] == state.opp.head
    assert board.food == state.food


def test_it_reconnects_after_the_server_drops_it(tmp_path):
    """The first connection dies mid-handshake; the bot must come back."""
    attempts: list[int] = []

    async def flaky(websocket):
        attempts.append(1)
        if len(attempts) == 1:
            await websocket.close()
            return
        await websocket.send(json.dumps({"event": "list_users", "data": {"users": ["a@x.com"]}}))
        await asyncio.sleep(0.5)

    async def scenario():
        async with websockets.serve(flaky, "127.0.0.1", 0) as ws_server:
            port = ws_server.sockets[0].getsockname()[1]
            client = BotClient(
                ClientConfig(
                    token="t",
                    server=f"ws://127.0.0.1:{port}/ws",
                    log_dir=str(tmp_path),
                    opponent_book=str(tmp_path / "opponents.json"),
                    print_board=False,
                    min_backoff=0.05,
                    max_backoff=0.1,
                )
            )
            runner = asyncio.ensure_future(client.run())
            for _ in range(100):
                await asyncio.sleep(0.05)
                if client.online_users:
                    break
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
            return client

    client = asyncio.run(scenario())
    assert len(attempts) >= 2, "the bot did not reconnect"
    assert client.online_users == ["a@x.com"]


def test_a_position_rendered_and_reparsed_keeps_the_snake_order(tmp_path):
    """Round trip through the wire format without losing which end is the tail."""
    from snakebot.tracker import GameTracker

    state = Position(
        rows=7,
        cols=7,
        me=Snake(((3, 3), (3, 2), (3, 1), (2, 1))),
        opp=Snake(((0, 6), (1, 6))),
        food=frozenset({(6, 0)}),
    )
    tracker = GameTracker(game_id="g")
    observed = tracker.observe({"board": state.render("A"), "side": "A", "remaining_moves": 50})
    assert observed.me.body == state.me.body
    assert observed.opp.body == state.opp.body
    assert observed.food == state.food
