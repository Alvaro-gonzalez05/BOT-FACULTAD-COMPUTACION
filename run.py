#!/usr/bin/env python3
"""Entry point for the Code Challenge Snake bot.

    python run.py play <BOT_TOKEN>                # connect and wait for challenges
    python run.py play <BOT_TOKEN> --challenge someone@example.com
    python run.py simulate --matches 50           # offline self-check, no token
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
import time
from pathlib import Path

from snakebot.client import DEFAULT_SERVER, BotClient, ClientConfig
from snakebot.search import Weights
from snakebot.simulator import (
    greedy_policy,
    play_match,
    random_policy,
    starting_position,
    strategy_policy,
)
from snakebot.strategy import SnakeStrategy

TOKEN_ENV_VAR = "CODECHALLENGE_TOKEN"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="A Code Challenge Snake bot that plays not to lose.",
    )
    subcommands = parser.add_subparsers(dest="command")

    play = subcommands.add_parser("play", help="connect to the server and play")
    play.add_argument(
        "token",
        nargs="?",
        default=os.environ.get(TOKEN_ENV_VAR),
        help=f"bot token from My Bots (or set {TOKEN_ENV_VAR})",
    )
    play.add_argument("--server", default=DEFAULT_SERVER, help="websocket URL")
    play.add_argument(
        "--challenge",
        action="append",
        default=[],
        metavar="OPPONENT",
        help="challenge this opponent on connect (repeatable)",
    )
    play.add_argument("--game", default="snake", help="game to challenge with")
    play.add_argument(
        "--accept-from",
        action="append",
        default=None,
        metavar="OPPONENT",
        help="only accept challenges from these opponents (default: everyone)",
    )
    play.add_argument("--email", default=None, help="your account email, for accurate win/loss stats")
    play.add_argument(
        "--time-budget",
        type=float,
        default=0.15,
        help="seconds of thinking per move; keep headroom, a late move is penalised",
    )
    play.add_argument("--max-depth", type=int, default=14, help="maximum search depth in plies")
    play.add_argument("--log-dir", default="logs", help="where to write game_<id>.log")
    play.add_argument("--quiet-board", action="store_true", help="do not print the live board")
    play.add_argument(
        "--champion",
        default="champion.json",
        help="tuned weights from `ladder`; the built-in defaults are used if absent",
    )
    play.add_argument(
        "--default-weights",
        action="store_true",
        help="ignore champion.json and play with the built-in weights",
    )
    play.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    simulate = subcommands.add_parser("simulate", help="play offline matches against a baseline")
    simulate.add_argument("--matches", type=int, default=20)
    simulate.add_argument(
        "--opponent",
        choices=("greedy", "random", "self"),
        default="greedy",
        help="who to play against",
    )
    simulate.add_argument("--time-budget", type=float, default=0.05)
    simulate.add_argument("--max-depth", type=int, default=8)
    simulate.add_argument("--moves", type=int, default=300, help="moves per match")
    simulate.add_argument("--seed", type=int, default=1234)
    simulate.add_argument("--show", action="store_true", help="print the final board of each match")

    ladder = subcommands.add_parser(
        "ladder", help="tune the weights by playing against the last champion"
    )
    ladder.add_argument("--rounds", type=int, default=10, help="challengers to try")
    ladder.add_argument("--matches", type=int, default=8, help="seeds per duel (each played twice)")
    ladder.add_argument("--moves", type=int, default=200, help="moves per match")
    ladder.add_argument("--seed", type=int, default=0)
    ladder.add_argument(
        "--margin",
        type=float,
        default=1.0,
        help="points beyond a dead heat a challenger must win by to be promoted",
    )
    ladder.add_argument(
        "--champion", default="champion.json", help="where the reigning weights live"
    )
    ladder.add_argument(
        "--dry-run", action="store_true", help="do not write the champion back to disk"
    )
    ladder.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="games to play in parallel",
    )
    ladder.add_argument(
        "--depth",
        type=int,
        default=4,
        help="search depth while tuning; the whole cost of a run lives here "
        "(a 150-move game costs ~4s at 2, ~25s at 4, ~66s at 6)",
    )

    rivals = subcommands.add_parser(
        "rivals", help="play sparring partners built from the real opponents' profiles"
    )
    rivals.add_argument("--matches", type=int, default=8, help="matches against each rival")
    rivals.add_argument("--moves", type=int, default=250)
    rivals.add_argument("--max-depth", type=int, default=6)
    rivals.add_argument("--seed", type=int, default=4200)
    rivals.add_argument("--book", default="opponents.json")
    rivals.add_argument(
        "--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1),
        help="matches to play in parallel",
    )
    rivals.add_argument(
        "--min-matches", type=int, default=5,
        help="only build a sparring partner from opponents seen at least this often",
    )

    learn = subcommands.add_parser(
        "learn", help="fold match transcripts into what we know about each opponent"
    )
    learn.add_argument("--log-dir", default="logs", help="where the transcripts are")
    learn.add_argument("--book", default="opponents.json", help="where the profiles live")
    learn.add_argument("--show", action="store_true", help="just print the book, learn nothing")

    replay = subcommands.add_parser("replay", help="watch a match back with the tactical layers")
    replay.add_argument(
        "transcript",
        nargs="?",
        help="a logs/game_<id>.log to replay; omit to play a fresh simulated match",
    )
    replay.add_argument("--delay", type=float, default=0.15, help="seconds between frames")
    replay.add_argument("--seed", type=int, default=0, help="seed for a simulated match")
    replay.add_argument("--moves", type=int, default=200)
    replay.add_argument("--max-depth", type=int, default=6)
    replay.add_argument(
        "--champion", default="champion.json", help="weights to play the simulated match with"
    )
    replay.add_argument("--no-animate", action="store_true", help="print every frame in sequence")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "play"

    if command == "simulate":
        return run_simulation(args)
    if command == "ladder":
        return run_ladder_command(args)
    if command == "rivals":
        return run_rivals(args)
    if command == "learn":
        return run_learn(args)
    if command == "replay":
        return run_replay(args)
    return run_play(args, parser)


def run_ladder_command(args: argparse.Namespace) -> int:
    from snakebot.ladder import Champion, gauntlet, run_ladder

    champion = Champion.load(args.champion)
    started_from = champion
    print(f"champion: round {champion.round}, from {args.champion}")
    print(f"{'round':>5}  {'result':<32} {'verdict'}", flush=True)

    promotions = 0
    for index, result, promoted, champion in run_ladder(
        champion,
        rounds=args.rounds,
        matches=args.matches,
        moves=args.moves,
        margin=args.margin,
        seed=args.seed,
        workers=args.workers,
        depth=args.depth,
    ):
        verdict = "PROMOTED" if promoted else "rejected"
        promotions += promoted
        # Flushed: a ladder run takes minutes, and buffered progress looks hung.
        print(f"{index:>5}  {str(result):<32} {verdict}", flush=True)

    print(f"\n{promotions} of {args.rounds} challengers promoted")

    if champion.weights == started_from.weights:
        print("no change to the champion")
        return 0

    # Self-play cannot see the whole line drifting: a challenger only proves it
    # beats *this* champion, and both can wander somewhere generally weaker
    # together. A ten-round session did exactly that and ended up losing 8-4 to
    # the weights it started from. An outside opponent is what notices.
    # Deliberately not args.depth: the gauntlet searches deeper than the tuning
    # did, because that is closer to how the bot actually plays.
    print("checking the result against the greedy baseline (deeper)...", flush=True)
    tuned = gauntlet(champion.weights, workers=args.workers)
    before = gauntlet(started_from.weights, workers=args.workers)
    print(f"  tuned     {tuned[0]}W {tuned[1]}L {tuned[2]}D")
    print(f"  previous  {before[0]}W {before[1]}L {before[2]}D")

    # Strictly better, and no more losses. On a tie we keep what we have: a
    # change that cannot be shown to help is churn, and churn is how the weights
    # wander somewhere worse one harmless-looking step at a time.
    if not (tuned[0] > before[0] and tuned[1] <= before[1]):
        print("\nREJECTED: not demonstrably better. Keeping the old weights.")
        return 1

    if args.dry_run:
        print("dry run: champion not written")
    else:
        print(f"champion (round {champion.round}) saved to {champion.save(args.champion)}")
    for name, value in sorted(vars(champion.weights).items()):
        print(f"  {name:<18} {value}")
    return 0


def run_rivals(args: argparse.Namespace) -> int:
    """The benchmark that can actually tell a good change from a bad one."""
    from collections import defaultdict
    from concurrent.futures import ProcessPoolExecutor

    from snakebot.opponents import Book
    from snakebot.simulator import _rival_match, care_for_crash_rate

    book = Book.load(args.book)
    named = sorted(n for n, p in book.profiles.items() if p.matches >= args.min_matches)
    if not named:
        print(f"no opponent in {args.book} has {args.min_matches}+ matches yet - play some first")
        return 1

    weights = Weights()
    jobs = []
    for name in named:
        profile = book.get(name)
        care = care_for_crash_rate(profile.crash_rate)
        for index in range(args.matches):
            jobs.append(
                (name, profile.greed, care, index, args.moves, args.max_depth, args.seed, weights)
            )

    print(f"{len(jobs)} matches against {len(named)} rivals...", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        outcomes = list(pool.map(_rival_match, jobs))

    tally = defaultdict(lambda: [0, 0, 0, 0, 0])
    for name, winner, crashed, margin in outcomes:
        row = tally[name]
        row[0] += winner == "me"
        row[1] += winner == "opp"
        row[2] += winner == "draw"
        row[3] += crashed == "me"
        row[4] += margin

    total = [0, 0, 0, 0]
    print()
    print(f"{'rival':<26}{'record':>12}{'crashes':>9}{'margin':>9}")
    for name in named:
        wins, losses, draws, crashes, margin = tally[name]
        total[0] += wins; total[1] += losses; total[2] += draws; total[3] += crashes
        record = f"{wins}W {losses}L {draws}D"
        print(f"{name:<26}{record:>12}{crashes:>9}{margin / args.matches:>+9.0f}")

    overall = f"{total[0]}W {total[1]}L {total[2]}D"
    print()
    print(f"{'TOTAL':<26}{overall:>12}{total[3]:>9}")
    if total[3]:
        print()
        print("WARNING: the bot crashed. That is the one number that should stay at zero.")
        return 1
    return 0


def run_learn(args: argparse.Namespace) -> int:
    from snakebot.opponents import Book

    book = Book.load(args.book)
    if not args.show:
        seen = book.observe_directory(args.log_dir)
        new = len(set(seen))
        book.save(args.book)
        print(f"read {len(seen)} transcripts from {args.log_dir}/, {new} opponents touched\n")

    if not book.profiles:
        print("nothing learned yet - play a match first")
        return 0
    for profile in sorted(book.profiles.values(), key=lambda p: -p.matches):
        print(f"  {profile.summary()}")
    return 0


def run_replay(args: argparse.Namespace) -> int:
    from snakebot.ladder import Champion
    from snakebot.replay import frames_from_transcript, play, record_simulated_match

    if args.transcript:
        frames = list(frames_from_transcript(args.transcript))
        if not frames:
            print(f"no turns found in {args.transcript}")
            return 1
    else:
        weights = Champion.load(args.champion).weights
        print("playing a fresh match with the champion weights...")
        frames = record_simulated_match(
            weights, weights, seed=args.seed, moves=args.moves, depth=args.max_depth
        )

    shown = play(frames, delay=args.delay, animate=False if args.no_animate else None)
    print(f"\n{shown} turns")
    return 0


def run_play(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not getattr(args, "token", None):
        parser.error(
            "a bot token is required: pass it as an argument or set "
            f"{TOKEN_ENV_VAR}. Get it from My Bots -> Show token."
        )

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    weights = Weights()
    if not args.default_weights and Path(args.champion).exists():
        from snakebot.ladder import Champion

        champion = Champion.load(args.champion)
        weights = champion.weights
        logging.info(
            "playing with the tuned weights from %s (round %s)", args.champion, champion.round
        )

    config = ClientConfig(
        token=args.token,
        server=args.server,
        time_budget=args.time_budget,
        max_depth=args.max_depth,
        weights=weights,
        accept_from=set(args.accept_from) if args.accept_from else None,
        my_email=args.email,
        log_dir=args.log_dir,
        print_board=not args.quiet_board,
    )
    client = BotClient(config)
    challenges = [(opponent, args.game) for opponent in args.challenge]

    try:
        asyncio.run(client.run(challenges))
    except KeyboardInterrupt:
        print(f"\nstopped. record: {client.scoreboard}")
    return 0


def run_simulation(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    mine = SnakeStrategy(time_budget=args.time_budget, max_depth=args.max_depth)
    my_policy = strategy_policy(mine)

    if args.opponent == "random":
        opponent = random_policy(rng)
    elif args.opponent == "self":
        opponent = strategy_policy(
            SnakeStrategy(time_budget=args.time_budget, max_depth=args.max_depth)
        )
    else:
        opponent = greedy_policy()

    wins = losses = draws = crashes = 0
    started = time.monotonic()

    for match in range(1, args.matches + 1):
        position = starting_position(remaining_moves=args.moves, rng=rng)
        result = play_match(my_policy, opponent, position=position, rng=rng)
        if result.crashed == "me":
            crashes += 1
        if result.winner == "me":
            wins += 1
        elif result.winner == "opp":
            losses += 1
        else:
            draws += 1
        print(
            f"match {match:>3}: {result.winner:<5} "
            f"score {result.my_score:>6} - {result.opp_score:<6} "
            f"turns {result.turns:>3}"
            + (f"  CRASHED: {result.crashed}" if result.crashed else "")
        )

    elapsed = time.monotonic() - started
    print(
        f"\nvs {args.opponent}: {wins}W / {losses}L / {draws}D over {args.matches} matches"
        f"  ({elapsed:.1f}s)"
    )
    print(f"matches lost by crashing into something: {crashes}")
    return 0 if crashes == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
