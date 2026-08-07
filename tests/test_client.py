"""End-to-end checks of the event loop against a fake websocket.

This is the seam that cannot be exercised against the real server without
burning a match, so it gets its own fake: feed it events, read back the actions.
"""

import asyncio
import json

import pytest

from snakebot.client import BotClient, ClientConfig

BOARD = "\n".join(
    ["|aaA    |", "|       |", "|   *   |", "|       |", "|       |", "|       |", "|Bbb    |"]
)


class FakeWebsocket:
    """Yields a canned script of server messages and records what we send."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    def __aiter__(self):
        async def generate():
            for message in self._messages:
                yield json.dumps(message)

        return generate()

    async def send(self, payload):
        self.sent.append(json.loads(payload))


def your_turn(turn_token="t_1", **overrides):
    data = {
        "board": BOARD,
        "side": "A",
        "remaining_moves": 300,
        "player_1": "me@x.com",
        "score_1": 0,
        "player_2": "you@x.com",
        "score_2": 0,
        "game_id": "g_1",
        "turn_token": turn_token,
    }
    data.update(overrides)
    return {"event": "your_turn", "data": data}


def make_client(tmp_path, **overrides):
    config = ClientConfig(
        token="fake",
        max_depth=4,
        log_dir=str(tmp_path),
        print_board=False,
        my_email="me@x.com",
        # Kept inside tmp_path: the default is a relative path, and without this
        # a test run writes fixture opponents into the real project's book.
        opponent_book=str(tmp_path / "opponents.json"),
        **{"time_budget": 0.02, **overrides},
    )
    return BotClient(config)


async def drive(client, messages):
    websocket = FakeWebsocket(messages)
    client._websocket = websocket
    await client._consume(websocket)
    if client._tasks:
        await asyncio.gather(*list(client._tasks))
    return websocket


def test_a_turn_becomes_a_legal_move(tmp_path):
    client = make_client(tmp_path)
    websocket = asyncio.run(drive(client, [your_turn()]))

    assert len(websocket.sent) == 1
    message = websocket.sent[0]
    assert message["action"] == "move"
    assert message["data"]["game_id"] == "g_1"
    assert message["data"]["turn_token"] == "t_1"
    # The snake runs right along the top row, so up and left are both fatal.
    assert message["data"]["direction"] in {"down", "right"}


def test_challenges_are_accepted(tmp_path):
    client = make_client(tmp_path)
    websocket = asyncio.run(
        drive(client, [{"event": "challenge", "data": {"opponent": "you@x.com", "challenge_id": "c_1"}}])
    )
    assert websocket.sent == [{"action": "accept_challenge", "data": {"challenge_id": "c_1"}}]


def test_challenges_from_strangers_are_ignored_when_filtered(tmp_path):
    client = make_client(tmp_path, accept_from={"friend@x.com"})
    websocket = asyncio.run(
        drive(client, [{"event": "challenge", "data": {"opponent": "rando@x.com", "challenge_id": "c_2"}}])
    )
    assert websocket.sent == []


def test_two_matches_are_tracked_independently(tmp_path):
    client = make_client(tmp_path)
    websocket = asyncio.run(
        drive(
            client,
            [
                your_turn(game_id="g_1", turn_token="t_a"),
                your_turn(game_id="g_2", turn_token="t_b"),
            ],
        )
    )
    assert {m["data"]["game_id"] for m in websocket.sent} == {"g_1", "g_2"}
    assert set(client._trackers) == {("g_1", "A"), ("g_2", "A")}


def test_self_play_keeps_the_two_sides_of_one_game_apart(tmp_path):
    """Both players of a self-play match arrive on one socket under one game_id."""
    client = make_client(tmp_path)
    websocket = asyncio.run(
        drive(
            client,
            [
                your_turn(game_id="g_1", turn_token="t_a", side="A"),
                your_turn(game_id="g_1", turn_token="t_b", side="B"),
            ],
        )
    )
    assert set(client._trackers) == {("g_1", "A"), ("g_1", "B")}
    assert client._trackers[("g_1", "A")].my_side == "A"
    assert client._trackers[("g_1", "B")].my_side == "B"
    # Each side is answered with its own turn token, never crossed over.
    assert [m["data"]["turn_token"] for m in websocket.sent] == ["t_a", "t_b"]


def test_self_play_game_over_clears_both_sides(tmp_path):
    client = make_client(tmp_path)
    asyncio.run(
        drive(
            client,
            [
                your_turn(game_id="g_1", turn_token="t_a", side="A"),
                your_turn(game_id="g_1", turn_token="t_b", side="B"),
                {"event": "game_over", "data": {"game_id": "g_1", "score_1": 10, "score_2": 10}},
            ],
        )
    )
    assert client._trackers == {}
    assert client._strategies == {}


def test_game_over_writes_the_log_and_scores_the_match(tmp_path):
    client = make_client(tmp_path)
    asyncio.run(
        drive(
            client,
            [
                your_turn(),
                {
                    "event": "game_over",
                    "data": {
                        "game_id": "g_1",
                        "winner": "me@x.com",
                        "player_1": "me@x.com",
                        "score_1": 500,
                        "player_2": "you@x.com",
                        "score_2": -500,
                    },
                },
            ],
        )
    )
    assert client.scoreboard.wins == 1
    assert client._trackers == {}

    transcript = (tmp_path / "game_g_1.log").read_text(encoding="utf-8").splitlines()
    assert transcript[0].startswith("< ")
    assert transcript[1].startswith("> ")
    assert '"game_over"' in transcript[-1]


def test_online_users_are_remembered(tmp_path):
    client = make_client(tmp_path)
    asyncio.run(drive(client, [{"event": "list_users", "data": {"users": ["a@x.com", "b@x.com"]}}]))
    assert client.online_users == ["a@x.com", "b@x.com"]


def test_a_malformed_message_does_not_kill_the_loop(tmp_path):
    client = make_client(tmp_path)

    class Broken(FakeWebsocket):
        def __aiter__(self):
            async def generate():
                yield "{not json"
                yield json.dumps(your_turn())

            return generate()

    websocket = Broken([])
    client._websocket = websocket
    asyncio.run(_consume_and_settle(client, websocket))
    assert len(websocket.sent) == 1


async def _consume_and_settle(client, websocket):
    await client._consume(websocket)
    if client._tasks:
        await asyncio.gather(*list(client._tasks))


def test_a_board_we_cannot_read_still_answers(tmp_path):
    client = make_client(tmp_path)
    websocket = asyncio.run(drive(client, [your_turn(board="|   |\n|   |")]))
    assert len(websocket.sent) == 1
    assert websocket.sent[0]["data"]["direction"] in {"up", "down", "left", "right"}


@pytest.mark.parametrize("event", ["update_user_list", "error", "something_new"])
def test_unknown_and_informational_events_send_nothing(tmp_path, event):
    client = make_client(tmp_path)
    websocket = asyncio.run(drive(client, [{"event": event, "data": {}}]))
    assert websocket.sent == []


def test_the_thinking_budget_is_shared_across_concurrent_games(tmp_path):
    """Four live matches on one machine made a 150ms budget take 900ms.

    The searches were all queueing for the same cores, and a late move is
    penalised by the server, so each game only gets a share of the budget.
    """
    client = make_client(tmp_path, time_budget=0.4)
    asyncio.run(
        drive(
            client,
            [
                your_turn(game_id="g_1", turn_token="t_a"),
                your_turn(game_id="g_2", turn_token="t_b"),
                your_turn(game_id="g_3", turn_token="t_c"),
                your_turn(game_id="g_4", turn_token="t_d"),
            ],
        )
    )
    budgets = [s.search.time_budget for s in client._strategies.values()]
    assert budgets, "no games were tracked"
    # Four games in flight: nobody should still be asking for the whole budget.
    assert min(budgets) < 0.4
    assert all(b >= 0.02 for b in budgets), "the budget must not collapse to nothing"


def test_a_single_game_keeps_the_whole_budget(tmp_path):
    client = make_client(tmp_path, time_budget=0.4)
    asyncio.run(drive(client, [your_turn(game_id="g_1", turn_token="t_a")]))
    assert client._strategies[("g_1", "A")].search.time_budget == 0.4


def test_the_web_challenger_says_plainly_when_the_session_is_no_good():
    """A dead cookie must produce a sentence, not a stack trace on the page."""
    import pytest

    from snakebot.challenge_web import ChallengeError, WebChallenger

    challenger = WebChallenger(session="")
    assert not challenger.configured
    with pytest.raises(ChallengeError):
        WebChallenger(session="x")._bots_from("<html>no form here</html>")


def test_the_web_challenger_reads_both_dropdowns():
    from snakebot.challenge_web import WebChallenger

    page = """
      <input name="csrfmiddlewaretoken" value="tok123">
      <select name="bot1"><option value="0">Alvarinho</option></select>
      <select name="bot2">
        <option value="0">Alvarinho</option>
        <option value="2">rival</option>
      </select>
    """
    bots = WebChallenger(session="x")._bots_from(page)
    assert bots["bot1"] == {"Alvarinho": "0"}
    assert bots["bot2"] == {"Alvarinho": "0", "rival": "2"}
