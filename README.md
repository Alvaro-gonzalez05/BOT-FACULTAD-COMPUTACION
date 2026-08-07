# Code Challenge — Snake bot

A competition bot for [The Code Challenge](https://codechallenge.up.railway.app)
Snake game. It connects to the match server over a websocket, auto-accepts
challenges, and plays every board with a time-boxed look-ahead search.

Its one non-negotiable rule: **never crash**. Crashing costs `-500` and hands the
opponent `+1000`, which is worth more than any amount of food, so every move is
run past a survival check before it is sent.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python run.py play <YOUR_BOT_TOKEN>
```

Get the token from **My Bots → Show token** on the site. You can also put it in
the `CODECHALLENGE_TOKEN` environment variable and just run `python run.py play`.

The bot then sits there waiting. Go to **Challenge** on the site, pick your bot,
an online opponent and *Snake* — the bot accepts and plays on its own. To start
the match from the bot instead:

```bash
python run.py play <YOUR_BOT_TOKEN> --challenge rival@example.com
```

### Check it before you play

No token needed — this plays full matches offline against a baseline bot and
exits non-zero if our bot ever crashed:

```bash
python run.py simulate --matches 20 --opponent greedy
```

Current result over 20 full 300-move matches (`--time-budget 0.15 --max-depth 10
--seed 99`): **20W / 0L / 0D, 0 matches lost by crashing**. Many of them end
early with the *opponent* crashing — squeezing its room is a real way to win,
not just a tiebreaker.

**This benchmark has run out of road.** In a 16-match deterministic sweep, every
weight setting tried scored 15 or 16 wins with zero losses; the greedy baseline
simply cannot tell a good bot from a great one any more. Treat small differences
here as noise. That is what the ladder below is for.

### Tuning by self-play

```bash
python run.py ladder --rounds 12 --matches 6
```

Each round builds a challenger — the champion with one to three weights nudged —
and makes it play the champion over a batch of games. It is promoted only if it
wins by a real margin, and the winner is written to `champion.json`, which
`play` then picks up automatically. Run it again next week and it carries on
from where it stopped.

**Budget it by depth.** One 150-move game costs about 4s at `--depth 2`, 25s at
4, and 66s at 6, and a round plays `2 × --matches` of them across `--workers`
processes. The default of 4 keeps a session to a few minutes; 6 transfers better
to real play (which searches deeper still) and is worth starting before bed.

Three traps this avoids, all of which quietly invalidate self-play tuning:

- **Side bias.** Every pairing is played twice on the *same* seed with the
  players swapped, so a weight set that only wins from the left has proved
  nothing. A test pins this down: identical weights must come out dead even.
- **Noise promoted as progress.** Promotion needs a margin, not a single lucky
  game. `--margin` sets how big; half a point is one drawn game and is well
  inside the noise, so the default asks for a full game clear.
- **Points bought with crashes.** A challenger that crashes more often than the
  champion is rejected whatever it scored. This is not hypothetical — a real
  round produced `6.5-3.5 of 10 (crashes 1 vs 0)`, a clear points win that would
  have been promoted without the guard, paid for with the one property the bot
  actually guarantees.

**The gauntlet is not optional, and it is now automatic.** A ten-round session
promoted three challengers and produced a champion that lost **8-4 to the very
weights it started from** — self-play had drifted somewhere generally weaker
(`length` had crept from 20 to 32, which pays against a clone of yourself and
nowhere else). The ladder now plays the result against the fixed greedy baseline
before saving, compares it with where it started, and refuses to write a
champion that scores worse. It exits non-zero and keeps the old weights.

That check is the only thing standing between "the bot improved" and "the bot
and its sparring partner walked off a cliff together".

**Validate deeper than you tune.** The first version of that guard ran at the
tuning depth of 4, passed a champion 7-6, and that champion then lost 9-12 to
the weights it replaced once both searched at depth 6. Weights that win a
shallow search need not win a deep one, and the bot plays deep. The gauntlet now
runs at depth 6 over 16 matches regardless of `--depth`, and a champion has to
be *strictly* better with no extra losses — on a tie the old weights stay, since
a change that cannot be shown to help is how weights wander somewhere worse one
harmless-looking step at a time.

**Where this stands honestly:** two full sessions have now been run, and neither
beat the hand-tuned defaults — both were caught and discarded. The defaults
score 12W / 0L / 0D against the baseline at depth 6 and that is what ships. The
ladder's value so far has been refusing to make the bot worse, not making it
better.

`trapped_penalty` is deliberately not tunable. It is the term that stops the bot
killing itself, and letting a random search weaken it would trade the one
property we actually promised for a few points of margin.

Known limitation: a champion promoted by luck is never re-tested, so the ladder
can drift. Re-run it with a different `--seed` occasionally and check the win
rate against the greedy baseline has not fallen.

### Learning who you are playing

```bash
python run.py learn          # fold logs/ into opponents.json
python run.py learn --show   # just read the book back
```

Every finished match updates `opponents.json` automatically, and the bot prints
what it knows when a match against a familiar name begins:

```
candelariaaybar: 1 matches, crashes on its own 100%, chases apples 50%, lasts 1 turns
```

**The profile changes how the bot plays.** Two dials, both driven by how often
the opponent has crashed *lately*:

- **Hunger.** A bot that never crashes will still be alive when the moves run
  out, so that match is decided on apples and we have to compete for them. One
  that kills itself does not need to be out-eaten, and chasing food against it
  only takes on risk for nothing.
- **Pressure.** Squeezing the opponent's room pays off in proportion to how
  likely they are to mishandle it.

Against `santiagogil` (never crashes, eight matches of evidence) that moves
`food_distance` from 14 to 21 and halves the squeeze; against an opponent seen
once, it barely moves at all. Nothing here touches the terms that keep *us*
alive — a read on the opponent can change what the bot wants, never what it
refuses to do.

**Opponents are a moving target, and the profile is built for that.** They are
people editing their bots between now and the tournament: the `lucaisgro` of
today is not the one you will face. So a profile judges an opponent on their
last eight matches, not their lifetime — a bot that crashed constantly last week
and has since been fixed stops being treated as a bot that crashes. Adjustments
also scale with how much of that window is filled, so one match is an anecdote
and moves almost nothing.

**Why this and not a model that memorises positions.** With snakes of length six
there are on the order of 10^22 distinct boards, so a stored position
essentially never recurs — a lookup table would stay empty forever no matter how
many games it saw. What *does* repeat is the opponents: the same handful of bots,
with stable habits, match after match. Measuring those habits costs a few hundred
bytes each and is the part of "learning" that actually pays.

### Watching a match back

```bash
python run.py replay logs/game_<id>.log     # a real match, exactly as it happened
python run.py replay                        # a fresh simulated one
```

Both animate in place with the layers drawn on, so you can watch the moment a
match turned.

## What the real server taught us

Three things that offline play could never have shown, each of which had a bug
behind it:

- **The board carries three apples at once, not one.** All the early tuning
  assumed a single contested apple, which taught the bot to race for whatever
  was nearest. With three in play the right move is usually to take a different
  one. `simulator.starting_position` now opens with three.
- **A late move is penalised, and the limit is not published.** A turn measured
  at 896ms against a 400ms budget, on a machine busy with a tuning run, was
  followed thirty seconds later by the server dropping the connection. The
  default budget is now 150ms, the warning threshold scales with it, and the
  rule of thumb is simple: **do not run `ladder` while the bot is playing.**
- **Scoring is confirmed.** A finished match went `-499` for the opponent and
  `+1001` for us: `-500` for crashing, `+1000` for the other side, `+1` a move
  for staying alive.

## What 54 real matches say

```
42W 12L 0D
  matches we lost by crashing:      0
  matches the opponent crashed in:  9
  matches that ran out of moves:   45
```

Every single loss was on points with nobody crashing, and most were two or three
apples short. Survival is solved; appetite is not. That is the one number to
work on, and it means a change that makes the bot safer is a change aimed at the
wrong problem.

### The rival gauntlet

```bash
python run.py rivals          # play the sparring partners built from opponents.json
```

Every offline opponent had run out of usefulness. Greedy kills itself, so
matches end before an endgame exists and every candidate beat it. `survivor`
reuses the real strategy and mirrors it, so seven matches in ten end level.
Self-play compares the bot to itself and drifts. None of them could separate a
good idea from a bad one.

So the sparring partners are now built from measurements of the actual rivals.
`opponents.json` records, per opponent and over their recent matches, how often
they crash and how hard they chase apples; `rival_from_profile` turns that into
a plain heuristic bot with a matching *greed* and *carelessness*. Being a
different kind of player from us — no search, no shared evaluation — is the
whole point, and is what a test enforces.

Two calibration notes. Carelessness is modelled as skipping the survival check
on a given turn, because that is how bots really die, one unchecked move at a
time; an early version kept a safety bias on careless turns and the dial was
inert, with 1.0, 0.95 and 0.85 all producing the same crash rate. And the crash
rate floors at about 6%: this is a heuristic, not a searcher, so an opponent
measured below that just gets the most careful setting.

### Two ideas that sounded right and were measured wrong

Both came out of the correct diagnosis above — every loss is on points — and
both made the bot worse. The problem is real; reaching for food more eagerly is
not the cure.

- **Endgame hunger** (chase harder when behind, coast when ahead): **35W-10L**
  against the rival gauntlet, where leaving it out scored **41W-7L** — worse
  against every single rival. Against the greedy baseline it had looked like a
  wash, and one lucky seed set had it at 16W-0L.
- **Valuing the second-nearest apple**: 10W/6L against 13W/3L without it. It
  drags the snake towards where the apples are rather than where it can safely
  be.

The gauntlet is the reason both were caught.

## Concurrency: the bug that was costing matches

The client was written to keep the *full* thinking budget in every concurrent
game, on the reasoning that the deadline is wall-clock so each search answers on
time regardless of how many are running. That reasoning was wrong. The searches
all queue for the same cores, so four live matches turned a 150ms budget into
measured turns of 600-900ms — and the server penalises a late move.

Two fixes, both measured against real matches:

- **Check the clock every 8 nodes, not every 256.** Each node costs two
  breadth-first sweeps and two flood fills, so a few hundred of them is most of
  a second. Worst observed search on a 150ms budget: **803ms before, 193ms
  after.**
- **Share the budget across live games.** Each match now gets
  `time_budget / active_games`.

Measured over real matches against other people's bots:

| | Matches | Slow-turn warnings | Per match | Record |
| --- | --- | --- | --- | --- |
| Before | 18 | 31 | 1.7 | 9W / 9L |
| After | 9 | 4 | 0.44 | **7W / 2L** |

## Open problem: it lost badly on the real server

Two live matches on 2026-08-04 (`match_details/216` and `217`) ended
**-2757** and **-2670** against opponents on 552 and 335. Offline it does not
lose at all, so something about the real server is not modelled here. This is
the most important open item in the repo.

What is known:

- A single crash is only `-500`, so a score near `-2750` means a penalty
  repeated over many turns, not one bad move. The platform docs say *"a wrong
  turn_token, an illegal move, or a timeout is penalized"* but do not give the
  amount or the per-move time limit.
- The server sent **no `error` events** — those are logged at ERROR level and
  none appeared — so whatever went wrong went wrong quietly.
- Both matches ran **concurrently**, which is the one condition never exercised
  offline.

What is not known: which of illegal moves, wrong tokens or timeouts caused it.
There is no evidence either way, because the transcripts were lost — they were
buffered until game over and the process died first. That bug is fixed (they now
stream to disk as they happen), and turn latency is now measured and warned
about, so **the next real match will say what happened**. Get one and read
`logs/game_<id>.log` before trusting any of the tuning below in a live game.

## How it plays

Three layers decide every move, each one a backstop for the one above:

| Layer | What it does |
| --- | --- |
| **Legality** | Drops any direction that walks into a wall, a body, or the opponent. |
| **Look-ahead** | Paranoid alpha-beta over the alternating turns, iterative deepening inside a wall-clock budget. Assumes the opponent plays its best reply. |
| **Survival veto** | The chosen move must leave room to unwind — flood fill has to see at least as many free cells as the snake is long, or the tail must stay reachable. Otherwise the safest legal move is substituted. |

The search evaluates a position with:

- **Territory** — a Voronoi split of the board: cells I reach before the
  opponent does. Taking space is how you starve a rival snake.
- **Free space** — a flood fill that knows *when* each body cell frees up, so a
  long snake can see that a corridor opens behind its own tail.
- **Opponent choke** — the same fill from their head, negated. Shrinking their
  room is a legitimate way to win, since a boxed-in snake crashes on its own.
- **The seal** — the biggest prize on the board. When the opponent's reachable
  room drops below its own length it has no way to unwind and *must* crash:
  `-500` for them, `+1000` for us. That is a 1500-point swing against an apple's
  100, but it usually lies deeper than the search can see, so the evaluation
  scores the half-built trap directly. A body wall counts only if it outlasts
  the snake inside it — the flood fill knows when each cell frees up, so a short
  snake's "wall" is correctly worth nothing.
- **Food distance, length, score** — the tiebreakers that win a match on points
  when nobody crashes.

### Watching it play

`play` prints a live tactical view each turn — not the raw letters the server
sends, but what the bot is reasoning about:

```
X x x @ · · . . . .
o o o o . · . . . .
o . . . . · . . . .
o . . . . * . . . .
score 0-0  me 99/12  them 0/3  apple 7v-  left 300  THEY ARE SEALED IN
```

`@`/`o` is us, `X`/`x` is them, `*` is the apple and `·` is our shortest route
to it. `me 99/12` reads *room / length* — when room drops to length, that snake
is finished, and the view says so in words. Pass `--quiet-board` to turn it off.

### The things that quietly go wrong, and what handles them

- **The board never tells you which end is the tail.** Order matters — it is
  what says which cell frees up next. `tracker.py` rebuilds the order once with
  a path walk from the head, then keeps it in sync every turn, re-deriving it
  from scratch only if the board stops matching.
- **Direction words could be mapped the other way.** We know what we sent and we
  see where the head landed, so the tracker learns the real mapping on the first
  move and remaps every later one. If `"up"` means `row + 1` on this server, the
  bot corrects itself instead of driving into a wall.
- **Tournaments run several boards at once.** Each `game_id` gets its own
  tracker and strategy, each turn is its own task, and the thinking budget is
  divided between the boards that are live right now.
- **A bug must not become a forfeit.** If parsing or the search raises, the turn
  still answers with a legal direction. A timeout is penalised; a mediocre move
  is not.
- **Connections drop.** Reconnects with capped exponential backoff. The server
  sends the full board every turn, so nothing is lost.

## Layout

```
run.py                 CLI: play / simulate
snakebot/
  board.py             board string -> cells, directions
  engine.py            the rules: moves, growth, scoring, crashes  (pure)
  heuristics.py        flood fill, Voronoi territory, distances    (pure)
  search.py            paranoid alpha-beta, iterative deepening, time budget
  strategy.py          legality -> search -> survival veto
  tracker.py           ordered snakes + direction calibration, per match
  protocol.py          the JSON wire format
  client.py            websocket, reconnects, concurrent matches
  simulator.py         offline arena and baseline opponents
tests/                 pytest suite
```

`engine.py` and `heuristics.py` have no I/O and no state, which is why the
strategy is testable: every test in `tests/test_strategy.py` is a hand-built
board with one right answer.

## Options

```
python run.py play --help
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--time-budget` | `0.40` | Seconds of thinking per move. Raise it if the server's limit is generous; it is split across concurrent matches. |
| `--max-depth` | `14` | Ply cap. The time budget usually binds first. |
| `--challenge OPPONENT` | – | Challenge someone on connect. Repeatable. |
| `--accept-from OPPONENT` | everyone | Only accept challenges from these accounts. |
| `--email you@example.com` | – | Makes the win/loss record exact. |
| `--log-dir` | `logs` | `game_<id>.log` transcripts, same format as the reference client. |
| `--quiet-board` | off | Stop printing the live board. |

## Tests

```bash
python -m pytest
```

## Tuning

The evaluation weights live in `snakebot.search.Weights` — one dataclass, no
magic numbers scattered around. To try a variant, pass a different `Weights` to
`SnakeStrategy` and run `run.py simulate` to see whether it actually beats the
current bot.

The defaults were picked that way, and two results are worth keeping in mind if
you change them:

- **Territory has to stay small.** At weight 6 the bot played beautiful
  space-control snake and finished matches having eaten *nothing* — its score
  was exactly the survival bonus. Every step down (6 → 3 → 1) won more matches.
- **But it cannot be zero.** Setting territory to 0 was the only configuration
  that made the bot crash into things. Squeezing the opponent's room and keeping
  your own are the same computation; turn it off entirely and the search stops
  noticing that it is being walled in.

One caveat on measuring: a time-based budget makes the reachable depth depend on
CPU load, so the same seeds give different results run to run. Compare variants
at a **fixed `max_depth` with an unreachably large `time_budget`** and the
benchmark becomes reproducible.

## A note on "never loses"

Nothing can guarantee a win: the opponent may simply eat more food and take it
on points, and the server's food spawns are random. What the bot does guarantee
is that it will not hand the match over — it does not walk into walls, it does
not seal itself in, and it does not time out. `run.py simulate` reports the
number of matches lost by crashing, and that is the number to keep at zero.
