"""Start a match by driving the website's own Challenge form.

The documented ``challenge`` websocket action has never produced a match on this
server -- not between two different bots, not in self-play -- so the only way to
*send* a challenge is the form on the site. That form is a Django POST needing a
CSRF token and a logged-in session, which a bot process does not have.

So it borrows yours. Put the ``sessionid`` cookie from your browser in ``.env``
as ``CODECHALLENGE_SESSION`` and the control panel's button starts working. The
value is read from that file, sent only to the site it came from, and never
logged -- but it *is* a session credential, and ``.env`` is gitignored for that
reason. Anyone holding it can act as you on the site until it expires.

Only the standard library, to keep ``pip install`` at one dependency.
"""

from __future__ import annotations

import http.cookiejar
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("snakebot.challenge")

BASE = "https://codechallenge.up.railway.app"
CHALLENGE_PATH = "/challenge"

_CSRF = re.compile(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"')
# <select name="bot2"> ... <option value="3">name</option> ... </select>
_SELECT = re.compile(r'<select[^>]*name="(bot1|bot2)"[^>]*>(.*?)</select>', re.S)
_OPTION = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>\s*([^<]*?)\s*</option>', re.S)


class ChallengeError(RuntimeError):
    """The site would not start the match, with a reason worth showing."""


@dataclass
class WebChallenger:
    """Sends challenges through the website, using a browser session cookie."""

    session: str
    base: str = BASE
    _bots: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.session)

    def _open(self, url: str, data: bytes | None = None, csrf: str = "") -> tuple[str, str]:
        """Fetch a page, carrying the session. Returns (body, csrftoken cookie)."""
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        cookies = f"sessionid={self.session}"
        if csrf:
            cookies += f"; csrftoken={csrf}"
            # Django rejects an HTTPS POST whose Referer is not the same origin.
            request.add_header("Referer", url)
            request.add_header("Origin", self.base)
        request.add_header("Cookie", cookies)
        request.add_header("User-Agent", "snakebot-dashboard")

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(request, timeout=15) as response:
            body = response.read().decode("utf-8", "replace")
        token = next((c.value for c in jar if c.name == "csrftoken"), csrf)
        return body, token

    def _bots_from(self, body: str) -> dict[str, dict[str, str]]:
        """Parse the two dropdowns out of the challenge page."""
        if "csrfmiddlewaretoken" not in body:
            raise ChallengeError("not logged in - the session cookie is missing or expired")
        return {
            name: {label: value for value, label in _OPTION.findall(options) if label}
            for name, options in _SELECT.findall(body)
        }

    def refresh(self) -> dict[str, dict[str, str]]:
        """Read the two dropdowns: our bots, and everyone currently online."""
        body, _ = self._open(self.base + CHALLENGE_PATH)
        self._bots = self._bots_from(body)
        return self._bots

    def challenge(self, opponent: str, my_bot: str | None = None, game: str = "snake") -> str:
        """Start ``my_bot`` versus ``opponent``. Returns a line worth showing."""
        body, csrf_cookie = self._open(self.base + CHALLENGE_PATH)
        match = _CSRF.search(body)
        if not match:
            raise ChallengeError("not logged in - the session cookie is missing or expired")
        token = match.group(1)

        self._bots = self._bots_from(body)
        mine = self._bots.get("bot1", {})
        theirs = self._bots.get("bot2", {})
        if not mine:
            raise ChallengeError("no bot of yours is online")
        if opponent not in theirs:
            raise ChallengeError(f"{opponent} is not online")

        chosen = my_bot if my_bot in mine else next(iter(mine))
        payload = urllib.parse.urlencode(
            {
                "csrfmiddlewaretoken": token,
                "bot1": mine[chosen],
                "bot2": theirs[opponent],
                "game": game,
            }
        ).encode()

        try:
            self._open(self.base + CHALLENGE_PATH, data=payload, csrf=csrf_cookie or token)
        except urllib.error.HTTPError as error:
            raise ChallengeError(f"the site refused the challenge (HTTP {error.code})") from error
        except urllib.error.URLError as error:
            raise ChallengeError(f"could not reach the site: {error.reason}") from error

        log.info("challenged %s as %s through the website", opponent, chosen)
        return f"{chosen} vs {opponent}"
