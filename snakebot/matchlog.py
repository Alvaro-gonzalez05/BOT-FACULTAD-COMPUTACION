"""Per-match transcripts, in the same format as the reference client.

One ``game_<id>.log`` per match with every event received (``<``) and action
sent (``>``), so a lost match can be replayed and debugged offline.

Lines are written as they happen rather than buffered until game over. A live
match against a real opponent was lost this way: the process died mid-game and
took both transcripts with it. Appending as we go also means the file is
readable *while* the match is running.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)


class MatchLog:
    """Streams the transcript of every live match straight to disk."""

    def __init__(self, directory: str | Path = "logs") -> None:
        self.directory = Path(directory)
        self._files: dict[str, TextIO] = {}
        self._broken: set[str] = set()

    def event(self, game_id: str | None, message: dict[str, Any]) -> None:
        self._append(game_id, "< " + json.dumps(message))

    def action(self, game_id: str | None, message: dict[str, Any]) -> None:
        self._append(game_id, "> " + json.dumps(message))

    def path_for(self, game_id: str) -> Path:
        return self.directory / f"game_{game_id}.log"

    def _append(self, game_id: str | None, line: str) -> None:
        if not game_id or game_id in self._broken:
            return
        handle = self._files.get(game_id)
        if handle is None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                handle = self.path_for(game_id).open("w", encoding="utf-8")
            except OSError as error:
                # Never let logging take the match down -- give up on this game.
                log.warning("could not open the match log for %s: %s", game_id, error)
                self._broken.add(game_id)
                return
            self._files[game_id] = handle
        try:
            handle.write(line + "\n")
            handle.flush()
        except OSError as error:
            log.warning("could not write the match log for %s: %s", game_id, error)
            self._broken.add(game_id)

    def flush(self, game_id: str | None) -> Path | None:
        """Close out the transcript for ``game_id`` and return where it landed."""
        if not game_id:
            return None
        handle = self._files.pop(game_id, None)
        if handle is None:
            return None
        try:
            handle.close()
        except OSError:
            pass
        return self.path_for(game_id)

    def close(self) -> None:
        for game_id in list(self._files):
            self.flush(game_id)
