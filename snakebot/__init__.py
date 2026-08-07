"""A competition bot for the Code Challenge Snake game.

The pieces fit together like this::

    client.BotClient        websocket, reconnects, one match per game_id
      -> tracker.GameTracker   board string -> ordered snakes (+ direction calibration)
        -> strategy.SnakeStrategy  legality -> alpha-beta look-ahead -> survival veto
          -> search.Search         paranoid, time-boxed, iterative deepening
            -> engine.Position     the game rules, pure and side-effect free
              -> heuristics        space, territory, distances
"""

from .board import Board, Direction
from .client import BotClient, ClientConfig
from .engine import Position, Snake
from .search import Search, Weights
from .strategy import Decision, SnakeStrategy
from .tracker import GameTracker

__all__ = [
    "Board",
    "BotClient",
    "ClientConfig",
    "Decision",
    "Direction",
    "GameTracker",
    "Position",
    "Search",
    "Snake",
    "SnakeStrategy",
    "Weights",
]

__version__ = "1.0.0"
