"""The owner's yes: a plain yes to one specific question, tied to it by a one-time confirmation code.

Anything that can't be undone or that reaches someone else (sending, deleting, paying) waits for the owner. The
command refuses with the exact question in `ask_owner` and a code in `confirm` that belongs to that action and its
target, for a day, and works once. The agent asks the question word for word; only if the owner says yes does it
run the command again with `--owner-confirmed "<their exact words>" --confirm <code>`.

Only a plain yes counts, and it must be the whole answer: "yes", "yep", "yeah", "yup", "confirm", "go ahead" or
"do it", with "please", commas, full stops, exclamation marks and the question's own words ("yes, send it"). A no
anywhere is refused as a no. Everything else is asked again: "ok", "sure", an emoji, a question, a condition or a
delay ("yes, next week"), a joke ("yes lol jk"), and a yes naming something else. Searching for a yes word and
refusing a list of bad words can't win; reviewers found all of these getting through that way.

A code proves the toolkit asked that question, not that the owner saw it: the agent is trusted to ask them.

In a command:

    words = confirm.owner_yes(home, f"invoice.send|{number}", f"Send invoice {number} to {customer}?",
                              args.owner_confirmed, args.confirm, why="Sending it reaches the customer.",
                              echo=("send", "it", number))
    ... make the change and save it (nothing on --dry-run) ...
    if not args.dry_run:
        confirm.use(home, args.confirm)  # each code works once
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import re
import secrets
import time
import unicodedata
from pathlib import Path

from .util import JTPError, read_json, write_json

CODE_HOURS = 24
CLOCK_SLACK = 60  # minutes: a code still works if the computer's clock is set back a little
SALT = "job-to-paid-confirm"
USED_FILE = "used-codes.json"

# A plain yes opens the answer ("please" may come first); after it, only these and the question's own words.
_YES = ("yes", "yep", "yeah", "yup", "confirm", "confirmed", "go ahead", "do it", "please do")
_THEN = _YES + ("please",)
_ON_ITS_OWN = ("oui", "ja", "si", "sim")  # a yes in another language, as the whole answer

_NO = re.compile(r"\b(?:no+|nope|nah|naw|not|never|none|nothing|dont|doesnt|didnt|isnt|wont|cant|cannot|shouldnt|"
                 r"wouldnt|aint|do not|cancel|stop|abort|undo|negative|non|nein|nee|nej|nei|nao|nie)\b|\w+n't\b")

# Words, or runs of other characters: "yes, send it!" -> yes , send it !
_TOKEN = re.compile(r"[^\W_]+(?:['-][^\W_]+)*|[^\w\s]+|\S")
_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")
_PUNCT = re.compile(r"[,.]|!+")

ASK_HINT = ('Ask the owner the question in ask_owner, word for word, and wait. Only if they say yes, run this again '
            'with --owner-confirmed "<their exact words>" --confirm <the code in confirm>. Never answer it yourself.')


def _minutes() -> int:
    return int(time.time() // 60)


def _fold(text) -> str:
    """Lower case, accents and lookalike widths removed, quotes straightened, spaces collapsed."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", plain.replace("’", "'").replace("‘", "'")).strip()


def _sign(home: Path, action: str, expires: int, nonce: str) -> str:
    """Ties a code to this data folder, the action and its target, the expiry and its own random part (so every
    question asked gets a new code, even twice in a minute)."""
    key = hashlib.sha256(f"{SALT}|{Path(home).expanduser().resolve()}".encode()).digest()
    return hmac.new(key, f"{action}|{expires}|{nonce}".encode(), hashlib.sha256).hexdigest()[:10]


def code_for(home: Path, action: str) -> str:
    expires, nonce = _minutes() + CODE_HOURS * 60, secrets.token_hex(3)
    return f"{expires:x}-{nonce}-{_sign(home, action, expires, nonce)}"


def _key(code) -> str:
    return str(code or "").strip().lower()


def _code_ok(home: Path, action: str, code) -> bool:
    match = re.fullmatch(r"([0-9a-f]{1,12})-([0-9a-f]{6})-([0-9a-f]{10})", _key(code))
    if not match:
        return False
    expires = int(match.group(1), 16)
    if not _minutes() <= expires <= _minutes() + CODE_HOURS * 60 + CLOCK_SLACK:
        return False
    return hmac.compare_digest(match.group(3), _sign(home, action, expires, match.group(2)))


def _expires(code) -> int:
    head = _key(code).split("-")[0]
    return int(head, 16) if re.fullmatch(r"[0-9a-f]{1,12}", head) else 0


def _used(home: Path) -> dict:
    """The used codes that haven't expired yet."""
    used = read_json(Path(home) / USED_FILE, {}) or {}
    return {code: at for code, at in used.items() if _expires(code) >= _minutes()}


def use(home: Path, code) -> None:
    """Record a code as used, once the change it confirmed is saved. Expired ones are forgotten."""
    used = _used(home)
    used[_key(code)] = _minutes()
    write_json(Path(home) / USED_FILE, used)


def reads_as_no(text) -> bool:
    """Whether an answer says no anywhere in it ("absolutely not", "yes, but not yet")."""
    return bool(_NO.search(_fold(text)))


def _words(text: str):
    """The answer as words, or None when it holds anything but words, commas, full stops and exclamation marks (an
    emoji, a question mark, brackets, a dash, an ellipsis)."""
    words = []
    for token in _TOKEN.findall(_fold(text)):
        if _WORD.fullmatch(token):
            words.append(token)
        elif not _PUNCT.fullmatch(token):
            return None
    return words


def _phrases(texts) -> list:
    found = {tuple(_words(text) or ()) for text in texts} - {()}
    return sorted(found, key=len, reverse=True)


def plain_yes(text: str, echo=()) -> bool:
    """Whether the whole answer is a plain yes: one of _YES first (perhaps after "please"), or a yes in another
    language on its own, then only more of _THEN and the question's own words (echo)."""
    words = tuple(_words(text) or ())
    if len(words) == 1 and words[0] in _ON_ITS_OWN:
        return True
    then = _phrases(_THEN) + _phrases(echo)

    @functools.lru_cache(maxsize=None)
    def rest_fits(i: int) -> bool:  # words[i:] is made only of allowed phrases
        return i == len(words) or any(words[i:i + len(p)] == p and rest_fits(i + len(p)) for p in then)

    openings = [p for p in _phrases(_YES) if words[:len(p)] == p]
    if words[:1] == ("please",):
        openings += [("please",) + p for p in _phrases(_YES) if words[1:1 + len(p)] == p]
    return any(rest_fits(len(p)) for p in openings)


def owner_yes(home: Path, action: str, question: str, words, code, *, why: str, echo=(), hint: str = ASK_HINT,
              ask_code: str = "needs_owner") -> str:
    """The owner's exact words, once they're a plain yes to `question` sent back with that question's code (call
    use() after saving). Otherwise refuses: with the question and a fresh code when there's no answer yet (or no
    code, an old, used or other question's one), as a no, or as unclear. `action` names the action and its target,
    e.g. "invoice.send|INV-0004"; `echo` lists words of the question an answer may repeat. `hint` and `ask_code` are
    for the first time the question is asked."""
    text = re.sub(r"\s+", " ", str(words or "")).strip()[:500]

    def ask(message: str, hint: str = ASK_HINT, code_name: str = "needs_owner") -> JTPError:
        return JTPError(message, hint=hint, code=code_name, ask_owner=question, confirm=code_for(home, action))

    if not text:
        raise ask(why, hint, ask_code)
    if reads_as_no(text):
        raise JTPError("The owner's answer reads as a no, so nothing was changed.",
                        hint="Tell them it wasn't done. If they change their mind, ask the question afresh.",
                        code="owner_declined")  # fmt: skip
    if not _code_ok(home, action, code):
        raise ask("That answer isn't tied to this question: the confirmation code is missing, out of date or for "
                  "something else, so nothing was changed.")
    if _key(code) in _used(home):
        raise ask("That confirmation code was already used, so nothing was changed. Each yes counts once.")
    if not plain_yes(text, echo):
        raise ask("The owner's answer isn't a plain yes, so nothing was changed.",
                  hint='Ask the question again and wait for a plain yes as the whole answer, such as "yes" or "yes, '
                  'go ahead". "ok", "sure", an emoji, a question, a joke, or a yes with a condition or a delay isn\'t '
                  "enough.", code_name="unclear_answer")  # fmt: skip
    return text
