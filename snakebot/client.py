"""The websocket client: connects, stays connected, and plays every match.

Design notes:

* **Many matches at once.** Tournaments run several boards in parallel, so each
  ``your_turn`` becomes its own task with its own :class:`GameTracker` and its
  own strategy instance.
* **Never miss a turn.** The search runs in a worker thread so the socket keeps
  draining while we think, and any unexpected error still answers with a legal
  move instead of timing out.
* **Always come back.** Dropped connections reconnect with capped exponential
  backoff; the server hands us the full board every turn, so nothing is lost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import websockets

from . import protocol, view
from .board import ALL_DIRECTIONS, Direction
from .engine import Position
from .matchlog import MatchLog
from .protocol import Event
from .search import Weights
from .strategy import Decision, SnakeStrategy
from .tracker import GameTracker

log = logging.getLogger("snakebot")

DEFAULT_SERVER = "wss://codechallenge-server.up.railway.app/ws"


@dataclass
class ClientConfig:
    token: str
    server: str = DEFAULT_SERVER
    # 150ms, not 400. The server penalises a late move and does not publish
    # its limit, and a turn measured at 896ms against a 400ms budget on a
    # busy machine showed how little headroom that left. Depth costs far
    # less than a penalty every turn.
    time_budget: float = 0.15
    max_depth: int = 14
    weights: Weights = field(default_factory=Weights)
    accept_from: set[str] | None = None
    my_email: str | None = None
    log_dir: str = "logs"
    print_board: bool = True
    min_backoff: float = 1.0
    max_backoff: float = 30.0
    ping_timeout: float = 60.0
    # A tripwire, not a known threshold: warn when a turn takes far longer
    # than we asked for, which means the machine is too busy to keep the
    # promise the budget makes.
    slow_turn_factor: float = 3.0
    slow_turn_floor: float = 0.35
    # Where what we learn about each opponent accumulates. Set to None to
    # play without reading or writing it.
    opponent_book: str | None = "opponents.json"


@dataclass
class Scoreboard:
    wins: int = 0
    losses: int = 0
    draws: int = 0

    def record(self, outcome: str) -> None:
        if outcome == "win":
            self.wins += 1
        elif outcome == "loss":
            self.losses += 1
        else:
            self.draws += 1

    def __str__(self) -> str:
        return f"{self.wins}W / {self.losses}L / {self.draws}D"


class BotClient:
    """Owns the connection and routes every event to the right match."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.matchlog = MatchLog(config.log_dir)
        self.scoreboard = Scoreboard()
        self.online_users: list[str] = []
        self._trackers: dict[tuple[str, str], GameTracker] = {}
        self._strategies: dict[tuple[str, str], SnakeStrategy] = {}
        self._send_lock = asyncio.Lock()
        self._websocket: Any = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._game_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self.slowest_turn = 0.0

    # -- lifecycle -------------------------------------------------------

    async def run(self, challenges: Iterable[tuple[str, str]] = ()) -> None:
        """Connect and play forever, reconnecting on any transport failure."""
        pending_challenges = list(challenges)
        backoff = self.config.min_backoff
        uri = protocol.websocket_uri(self.config.server, self.config.token)
        redacted = protocol.websocket_uri(self.config.server, "***")

        while True:
            try:
                log.info("connecting to %s", redacted)
                # A generous ping timeout: the server can go quiet for a while
                # under load, and dropping a live match over one slow pong costs
                # far more than noticing a dead socket a minute later.
                async with websockets.connect(
                    uri, ping_interval=20, ping_timeout=self.config.ping_timeout
                ) as websocket:
                    self._websocket = websocket
                    backoff = self.config.min_backoff
                    log.info("connected - waiting for challenges")
                    for opponent, game in pending_challenges:
                        await self._send(protocol.challenge(opponent, game))
                    pending_challenges = []
                    await self._consume(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - any transport error retries
                log.warning("connection lost (%s); retrying in %.0fs", error, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.config.max_backoff)
            finally:
                self._websocket = None

    async def _consume(self, websocket: Any) -> None:
        async for raw in websocket:
            try:
                event = Event.parse(raw)
            except json.JSONDecodeError:
                log.warning("ignoring malformed message: %.120s", raw)
                continue
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        if event.name == protocol.EVENT_YOUR_TURN:
            self._spawn(self._handle_turn(event), game_id=event.data.get("game_id"))
        elif event.name == protocol.EVENT_CHALLENGE:
            self._spawn(self._handle_challenge(event))
        elif event.name == protocol.EVENT_GAME_OVER:
            # Spawned, not inline: the last turn of this match may still be in
            # flight, and tearing its state down underneath it would leak a
            # tracker (and lose the tail of the transcript).
            self._spawn(self._handle_game_over(event))
        elif event.name in (protocol.EVENT_LIST_USERS, protocol.EVENT_UPDATE_USER_LIST):
            self.online_users = list(event.data.get("users", []))
            log.debug("online: %s", ", ".join(self.online_users) or "(nobody)")
        elif event.name == protocol.EVENT_ERROR:
            log.error("server error: %s", event.data)
        else:
            log.debug("unhandled event %r", event.name)

    def _spawn(self, coro: Any, game_id: str | None = None) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if game_id:
            self._game_tasks.setdefault(game_id, set()).add(task)
            task.add_done_callback(lambda done: self._game_tasks.get(game_id, set()).discard(done))

    async def _drain_game_tasks(self, game_id: str) -> None:
        """Wait for the turns of ``game_id`` that are still being answered."""
        pending = [task for task in self._game_tasks.get(game_id, set()) if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._game_tasks.pop(game_id, None)

    # -- event handlers --------------------------------------------------

    async def _handle_challenge(self, event: Event) -> None:
        opponent = event.data.get("opponent", "?")
        challenge_id = event.data.get("challenge_id")
        if not challenge_id:
            return
        allowed = self.config.accept_from
        if allowed is not None and opponent not in allowed:
            log.info("ignoring challenge from %s (not in the accept list)", opponent)
            return
        log.info("accepting challenge from %s", opponent)
        await self._send(protocol.accept_challenge(challenge_id))

    async def _handle_turn(self, event: Event) -> None:
        data = event.data
        game_id = data.get("game_id", "")
        turn_token = data.get("turn_token", "")
        self.matchlog.event(game_id, event.as_dict())

        direction_name = await self._choose_direction(game_id, data)
        message = protocol.move(game_id, turn_token, direction_name)
        self.matchlog.action(game_id, message)
        await self._send(message)

    async def _choose_direction(self, game_id: str, data: dict[str, Any]) -> str:
        started = time.monotonic()
        try:
            return await self._decide(game_id, data)
        finally:
            elapsed = time.monotonic() - started
            self.slowest_turn = max(self.slowest_turn, elapsed)
            threshold = max(
                self.config.slow_turn_floor,
                self.config.time_budget * self.config.slow_turn_factor,
            )
            if elapsed > threshold:
                # The server penalises a late move, and a penalty every turn is
                # how a match ends up thousands of points down. Say so loudly.
                log.warning(
                    "turn took %.0fms on %s (budget %.0fms) - raise --time-budget "
                    "headroom or lower --max-depth",
                    elapsed * 1000,
                    game_id,
                    self.config.time_budget * 1000,
                )

    async def _decide(self, game_id: str, data: dict[str, Any]) -> str:
        # Keyed by game *and* side: in a self-play test both players are this
        # same connection, so one game_id carries two independent snakes.
        key = (game_id, data.get("side", "A"))
        tracker = self._trackers.get(key)
        if tracker is None:
            tracker = GameTracker(
                game_id=game_id,
                my_side=data.get("side", "A"),
                my_email=self.config.my_email,
            )
            self._trackers[key] = tracker
            rival = data.get("player_2" if data.get("side", "A") == "A" else "player_1", "")
            self._strategies[key] = SnakeStrategy(
                time_budget=self.config.time_budget,
                max_depth=self.config.max_depth,
                weights=self._weights_for(rival),
            )
            rival = data.get("player_2" if data.get("side", "A") == "A" else "player_1", "?")
            log.info(
                "game %s started (side %s) vs %s", game_id, data.get("side", "?"), rival
            )
            self._report_what_we_know(rival)

        strategy = self._strategies[key]
        try:
            position = tracker.observe(data)
        except Exception as error:  # noqa: BLE001 - never forfeit on a parse bug
            log.exception("could not read the board for %s: %s", game_id, error)
            return tracker.calibration.wire_name(random.choice(ALL_DIRECTIONS))

        # Share the budget out across the games in flight. The deadline is
        # wall-clock, which used to look like enough on its own -- but four
        # concurrent matches on one machine turned a 150ms budget into measured
        # turns of 600-900ms, because the searches were all queueing for the
        # same cores. A move that arrives late is penalised, so the budget has
        # to shrink when there is company.
        active = max(1, len({key[0] for key in self._trackers}))
        strategy.search.time_budget = max(0.02, self.config.time_budget / active)

        try:
            decision: Decision = await asyncio.to_thread(strategy.decide, position)
        except Exception as error:  # noqa: BLE001 - fall back to any legal move
            log.exception("search failed on %s: %s", game_id, error)
            decision = Decision(_any_legal(position), 0.0, 0, 0, 0.0, doomed=True)

        tracker.record_sent(decision.direction)
        if self.config.print_board:
            self._print_turn(tracker, position, decision, data)
        return tracker.calibration.wire_name(decision.direction)

    async def _handle_game_over(self, event: Event) -> None:
        data = event.data
        game_id = data.get("game_id", "")
        await self._drain_game_tasks(game_id)

        self.matchlog.event(game_id, event.as_dict())
        path = self.matchlog.flush(game_id)

        # Both sides of a self-play match live under the same game_id.
        sides = [key for key in self._trackers if key[0] == game_id]
        tracker = self._trackers.get(sides[0]) if sides else None
        for key in sides:
            self._trackers.pop(key, None)
            self._strategies.pop(key, None)

        outcome = "draw" if len(sides) > 1 else self._outcome(data, tracker)
        self.scoreboard.record(outcome)
        if path:
            self._learn_from(path)
        log.info(
            "game %s over: %s (%s %s - %s %s) | record %s%s",
            game_id,
            outcome.upper(),
            data.get("player_1", "p1"),
            data.get("score_1", 0),
            data.get("player_2", "p2"),
            data.get("score_2", 0),
            self.scoreboard,
            f" | log {path}" if path else "",
        )

    def _weights_for(self, rival: str) -> Weights:
        """The evaluation, tuned for whoever we are about to play.

        Falls back to the plain weights whenever there is nothing to go on --
        an unknown opponent, a missing book, or a book we cannot read.
        """
        if not self.config.opponent_book or not rival:
            return self.config.weights
        try:
            from .opponents import Book, adapt

            profile = Book.load(self.config.opponent_book).get(rival)
            if not profile.samples:
                return self.config.weights
            return adapt(self.config.weights, profile)
        except Exception as error:  # noqa: BLE001 - never let this stop a match
            log.debug("could not adapt to %s: %s", rival, error)
            return self.config.weights

    def _report_what_we_know(self, rival: str) -> None:
        """Say what past matches taught us about this opponent, if anything."""
        if not self.config.opponent_book or not rival or rival == "?":
            return
        try:
            from .opponents import Book

            profile = Book.load(self.config.opponent_book).get(rival)
            if profile.matches:
                log.info("  %s", profile.summary())
        except Exception as error:  # noqa: BLE001
            log.debug("could not read the opponent book: %s", error)

    def _learn_from(self, path: Any) -> None:
        """Fold the finished match into what we know about that opponent.

        Losing a lesson is not worth losing a session over, so any failure here
        is logged and swallowed.
        """
        if not self.config.opponent_book:
            return
        try:
            from .opponents import Book

            book = Book.load(self.config.opponent_book)
            name = book.observe_transcript(path)
            if name:
                book.save(self.config.opponent_book)
                log.info("learned: %s", book.get(name).summary())
        except Exception as error:  # noqa: BLE001 - never let bookkeeping stop play
            log.warning("could not update the opponent book: %s", error)

    def _outcome(self, data: dict[str, Any], tracker: GameTracker | None) -> str:
        winner = data.get("winner")
        if self.config.my_email and winner:
            return "win" if winner == self.config.my_email else "loss"
        score_1 = data.get("score_1", 0) or 0
        score_2 = data.get("score_2", 0) or 0
        i_am_first = tracker is None or tracker.my_side == "A"
        mine, theirs = (score_1, score_2) if i_am_first else (score_2, score_1)
        if mine > theirs:
            return "win"
        if theirs > mine:
            return "loss"
        return "draw"

    # -- plumbing --------------------------------------------------------

    async def _send(self, message: dict[str, Any]) -> None:
        websocket = self._websocket
        if websocket is None:
            log.warning("dropping %s: not connected", message.get("action"))
            return
        payload = json.dumps(message)
        async with self._send_lock:
            await websocket.send(payload)
        log.debug("> %s", payload)

    def _print_turn(
        self,
        tracker: GameTracker,
        position: Position,
        decision: Decision,
        data: dict[str, Any],
    ) -> None:
        rival = data.get("player_2" if tracker.my_side == "A" else "player_1", "?")
        header = (
            f"[{tracker.game_id[:8]}] turn {tracker.turns} side {tracker.my_side} vs {rival}"
        )
        print(header)
        print(view.render(position, note=f"  -> {decision.describe()}"))


def _any_legal(position: Position) -> Direction:
    for direction, _ in position.legal_moves():
        return direction
    return Direction.UP
