"""The wire protocol: JSON events in, JSON actions out.

Events (server -> bot): ``list_users``, ``update_user_list``, ``challenge``,
``your_turn``, ``game_over``, ``error``.
Actions (bot -> server): ``accept_challenge``, ``move``, ``challenge``,
``list_users``, ``abort_game``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

EVENT_LIST_USERS = "list_users"
EVENT_UPDATE_USER_LIST = "update_user_list"
EVENT_CHALLENGE = "challenge"
EVENT_YOUR_TURN = "your_turn"
EVENT_GAME_OVER = "game_over"
EVENT_ERROR = "error"


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any]
    raw: str

    @classmethod
    def parse(cls, raw: str) -> "Event":
        payload = json.loads(raw)
        data = payload.get("data")
        return cls(
            name=payload.get("event", ""),
            data=data if isinstance(data, dict) else {},
            raw=raw,
        )

    def as_dict(self) -> dict[str, Any]:
        return {"event": self.name, "data": self.data}


def action(name: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"action": name, "data": data}


def accept_challenge(challenge_id: str) -> dict[str, Any]:
    return action("accept_challenge", {"challenge_id": challenge_id})


def move(game_id: str, turn_token: str, direction: str) -> dict[str, Any]:
    return action(
        "move",
        {"game_id": game_id, "turn_token": turn_token, "direction": direction},
    )


def challenge(opponent: str, game: str = "snake") -> dict[str, Any]:
    return action("challenge", {"opponent": opponent, "game": game})


def list_users() -> dict[str, Any]:
    return action("list_users", {})


def abort_game(game_id: str, turn_token: str) -> dict[str, Any]:
    return action("abort_game", {"game_id": game_id, "turn_token": turn_token})


def websocket_uri(server: str, token: str) -> str:
    separator = "&" if "?" in server else "?"
    return f"{server}{separator}token={token}"
