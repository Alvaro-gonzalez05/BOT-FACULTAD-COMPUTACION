"""A local control panel: start the bot, challenge people, watch every board.

Runs a small web server on localhost. The page lists who is online, lets you
send a challenge, and opens a live window per match -- so five concurrent games
are five boards updating side by side rather than a wall of log lines.

Deliberately dependency-free: a standard-library HTTP server, one HTML page and
a polling fetch. Adding a web framework to a bot whose whole job is to answer a
websocket in 150ms is not a trade worth making, and this way ``pip install`` is
still just ``websockets``.

The server binds to 127.0.0.1 only. It holds your bot token in memory and will
happily start matches, which is not something to expose to a network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .engine import Position
from .view import Layers

log = logging.getLogger("snakebot.dashboard")

HOST = "127.0.0.1"


@dataclass
class LiveBoard:
    """The latest state of one match, as the page needs it."""

    game_id: str
    rival: str = "?"
    side: str = "A"
    turn: int = 0
    rows: int = 15
    cols: int = 15
    my_head: tuple[int, int] | None = None
    my_body: list[list[int]] = field(default_factory=list)
    opp_head: tuple[int, int] | None = None
    opp_body: list[list[int]] = field(default_factory=list)
    food: list[list[int]] = field(default_factory=list)
    path: list[list[int]] = field(default_factory=list)
    my_score: int = 0
    opp_score: int = 0
    remaining: int = 0
    my_room: int = 0
    opp_room: int = 0
    my_length: int = 0
    opp_length: int = 0
    they_are_trapped: bool = False
    i_am_trapped: bool = False
    move: str = ""
    finished: bool = False
    result: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["my_head"] = list(self.my_head) if self.my_head else None
        data["opp_head"] = list(self.opp_head) if self.opp_head else None
        return data


class Dashboard:
    """Shared state between the bot's asyncio loop and the HTTP threads.

    Only ever mutated from the bot's thread and read from the server's, and the
    reads are of whole dicts, so a lock would buy nothing a snapshot does not.
    """

    def __init__(self) -> None:
        self.boards: dict[str, LiveBoard] = {}
        self.online: list[str] = []
        self.record = "0W / 0L / 0D"
        self.connected = False
        self.pending_challenges: list[str] = []
        # Last thing the Challenge button did, shown on the page.
        self.note = ""
        self.can_challenge = False

    # -- called from the bot ---------------------------------------------

    def update(self, game_id: str, rival: str, position: Position, move: str, turn: int) -> None:
        layers = Layers.of(position)
        board = self.boards.setdefault(game_id, LiveBoard(game_id=game_id))
        board.rival = rival
        board.turn = turn
        board.rows, board.cols = position.rows, position.cols
        board.my_head = position.me.head
        board.my_body = [list(c) for c in position.me.body[1:]]
        board.opp_head = position.opp.head
        board.opp_body = [list(c) for c in position.opp.body[1:]]
        board.food = [list(c) for c in sorted(position.food)]
        board.path = [list(c) for c in layers.path[1:]]
        board.my_score, board.opp_score = position.my_score, position.opp_score
        board.remaining = position.remaining_moves
        board.my_room, board.opp_room = layers.my_room, layers.opp_room
        board.my_length, board.opp_length = layers.my_length, layers.opp_length
        board.they_are_trapped = layers.they_are_trapped
        board.i_am_trapped = layers.i_am_trapped
        board.move = move

    def finish(self, game_id: str, result: str) -> None:
        board = self.boards.get(game_id)
        if board:
            board.finished = True
            board.result = result

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "record": self.record,
            "online": list(self.online),
            "boards": [b.as_dict() for b in self.boards.values()],
            "note": self.note,
            "can_challenge": self.can_challenge,
        }


def serve(dashboard: Dashboard, port: int, on_challenge) -> ThreadingHTTPServer:
    """Start the control panel. ``on_challenge(name)`` is called from a thread."""
    page = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # noqa: A003 - silence the default
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            elif route == "/state":
                body = json.dumps(dashboard.snapshot()).encode("utf-8")
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/challenge":
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                opponent = str(payload.get("opponent", "")).strip()
            except (ValueError, TypeError):
                opponent = ""
            if not opponent:
                self._send(400, b'{"error":"no opponent"}', "application/json")
                return
            on_challenge(opponent)
            self._send(200, b'{"ok":true}', "application/json")

    server = ThreadingHTTPServer((HOST, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("control panel on http://%s:%s", HOST, port)
    return server


def challenge_sender(
    client,
    loop: asyncio.AbstractEventLoop,
    game: str = "snake",
    dashboard: "Dashboard | None" = None,
    session: str = "",
):
    """Bridge a button press on the page to whatever actually starts a match.

    The website's form when a session cookie is configured -- that is the only
    route that has ever worked -- and the documented websocket action otherwise,
    which is kept so the button still does *something* on a server that honours
    it.
    """
    from . import protocol
    from .challenge_web import ChallengeError, WebChallenger

    web = WebChallenger(session=session) if session else None
    if dashboard is not None:
        dashboard.can_challenge = True

    def send(opponent: str) -> None:
        if web is not None:
            try:
                started = web.challenge(opponent, game=game)
                if dashboard is not None:
                    dashboard.note = f"challenged {started}"
                return
            except ChallengeError as error:
                # The message is about the site or the session, never the cookie.
                log.warning("challenge failed: %s", error)
                if dashboard is not None:
                    dashboard.note = str(error)
                return
            except Exception as error:  # noqa: BLE001 - a button must not kill the bot
                log.warning("challenge failed: %s", error)
                if dashboard is not None:
                    dashboard.note = "challenge failed - see the console"
                return

        if client._websocket is None:
            if dashboard is not None:
                dashboard.note = "not connected"
            return
        asyncio.run_coroutine_threadsafe(client._send(protocol.challenge(opponent, game)), loop)
        if dashboard is not None:
            dashboard.note = (
                "sent over the websocket - this server usually ignores it. "
                "Set CODECHALLENGE_SESSION in .env to use the website form."
            )

    return send
