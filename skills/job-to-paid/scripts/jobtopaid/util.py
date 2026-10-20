"""Shared helpers: errors, number and date parsing, safe JSON files, locking."""

from __future__ import annotations

import contextlib
import datetime as _dt
import functools
import json
import os
import re
import tempfile
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path


class JTPError(Exception):
    """An error the user or agent can act on. `hint` says what to do next."""

    def __init__(self, message: str, hint: str | None = None, code: str = "error", ask_owner: str | None = None,
                 confirm: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        self.ask_owner = ask_owner  # a question only the owner can answer
        self.confirm = confirm  # the code that must come back with the owner's yes to ask_owner (confirm.py)


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------

_THOUSANDS_RE = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_PLAIN_RE = re.compile(r"^-?(\d+(\.\d+)?|\.\d+)$")


def to_decimal(value, field: str = "value") -> Decimal:
    """Parse a number strictly. Accepts 1234.5, "1234.50", "$1,234.50".

    Rejects ambiguous input such as "1.234,50" instead of guessing, because a
    wrong guess on money is worse than a clear error.
    """
    if isinstance(value, bool) or value is None:
        raise JTPError(f"{field} is missing or not a number.", hint="Pass a plain number such as 1234.50.")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    text = str(value).strip().replace(" ", "").replace(" ", "")
    # Drop currency symbols or codes stuck to either end ("$12", "12 EUR").
    text = re.sub(r"^[^\d\-.]+", "", text)
    text = re.sub(r"[^\d.]+$", "", text)
    if _THOUSANDS_RE.match(text):
        text = text.replace(",", "")
    if not _PLAIN_RE.match(text):
        raise JTPError(
            f"{field} {value!r} is not a plain number.",
            hint="Use digits with a dot for decimals and no thousands separators, e.g. 1234.50.",
        )
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise JTPError(f"{field} {value!r} is not a number.") from exc


def dec_str(value: Decimal) -> str:
    """Plain string for a Decimal without exponent notation ("1E+2" -> "100")."""
    text = format(value, "f")
    return "0" if text in ("-0", "-0.0") else text


def trim_decimal(value: Decimal) -> str:
    """Shortest plain string: 2.50 -> "2.5", 3.000 -> "3"."""
    if value == value.to_integral_value():
        return dec_str(value.quantize(Decimal(1)))
    return dec_str(value.normalize())


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


# Only the unit tests turn this on, so that JTP_TODAY can stand in for the date there. Ordinary commands ignore
# JTP_TODAY: moving "today" would let a dated record (an estimate taken off an invoice, the owner's yes) lapse early
# or be dated in the past.
TEST_DATES = False


def today(tz_name: str | None = None) -> _dt.date:
    """Today's date in the business's time zone, from the computer's clock (JTP_TODAY in the unit tests only)."""
    override = os.environ.get("JTP_TODAY") if TEST_DATES else None
    if override:
        try:
            return _dt.date.fromisoformat(override.strip())
        except ValueError as exc:
            raise JTPError(f"JTP_TODAY={override!r} is not a YYYY-MM-DD date.") from exc
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return _dt.datetime.now(ZoneInfo(tz_name)).date()
        except Exception:  # unknown zone or no tz database: fall back to local time
            pass
    return _dt.date.today()


@functools.lru_cache(maxsize=1)
def _zones() -> frozenset:
    try:
        from zoneinfo import available_timezones

        return frozenset(available_timezones())
    except Exception:  # no tz database on this computer
        return frozenset()


def zone_name(name) -> str | None:
    """A time zone name as the tz database spells it ("america/vancouver" is "America/Vancouver"), or None when it
    isn't one ("Mars/Olympus"), so today() would quietly use the computer's local time. A computer with no tz
    database can't check, so the name is kept as given."""
    text = str(name or "").strip()
    if not text or not _zones() or text in _zones():
        return text
    return next((zone for zone in sorted(_zones()) if zone.lower() == text.lower()), None)


def parse_date(value, base: _dt.date) -> _dt.date:
    """Parse YYYY-MM-DD, today, yesterday, tomorrow, or +N / -N days."""
    if isinstance(value, _dt.date):
        return value
    text = str(value or "").strip().lower()
    if text in ("", "today", "now"):
        return base
    if text == "yesterday":
        return base - _dt.timedelta(days=1)
    if text == "tomorrow":
        return base + _dt.timedelta(days=1)
    match = re.fullmatch(r"([+-]\d+)d?", text)
    if match:
        return base + _dt.timedelta(days=int(match.group(1)))
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError as exc:
        raise JTPError(
            f"{value!r} is not a date.",
            hint="Use YYYY-MM-DD (e.g. 2026-10-15), today, yesterday, tomorrow, or +7 for 7 days from today.",
        ) from exc


def add_months(day: _dt.date, months: int) -> _dt.date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    for candidate in (day.day, 30, 29, 28):
        try:
            return _dt.date(year, month, min(day.day, candidate))
        except ValueError:
            continue
    raise ValueError("unreachable")  # pragma: no cover


def iso(day: _dt.date | None) -> str | None:
    return day.isoformat() if day else None


def from_iso(text: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(text) if text else None


def now_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise JTPError(
            f"{path} is not valid JSON ({exc.msg} at line {exc.lineno}).",
            hint="Fix the file by hand or restore it from a backup (jtp backup makes one).",
        ) from exc


def write_json(path: Path, data) -> None:
    """Write JSON atomically so a crash never leaves half a file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 20.0, stale_after: float = 120.0):
    """Cross-platform lock file so a routine and a chat can't write at once."""
    start = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > stale_after:
                    os.unlink(path)
                    continue
            except FileNotFoundError:
                continue
            if time.time() - start > timeout:
                raise JTPError(
                    "The data folder is busy: another Job to Paid command is running.",
                    hint=f"Wait a few seconds and retry. If nothing else is running, delete {path}.",
                ) from None
            time.sleep(0.1)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def safe_id_part(text: str) -> str:
    """Keep document number prefixes filesystem-safe."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", text or "")
    return cleaned


# Letters from other scripts, and a few Latin ones, that look like plain Latin letters. Folded to them when
# comparing what people type (line descriptions, customer names), so a lookalike can't make a copy look new.
LOOKALIKE_LETTERS = str.maketrans({
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y",
    "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w",
    # Greek
    "α": "a", "β": "b", "ε": "e", "η": "n", "ι": "i", "κ": "k", "μ": "m", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
    "υ": "u", "χ": "x",
    # Armenian
    "օ": "o", "ս": "u", "հ": "h", "ո": "n", "ց": "g", "զ": "q",
    # Latin letters that look like others
    "ɑ": "a", "ɡ": "g", "ı": "i", "ȷ": "j", "ɩ": "i", "ʋ": "u",
    # Latin small capitals
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f", "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k",
    "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ꞯ": "q", "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v",
    "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
})


def fold_letters(text) -> str:
    """Text folded for comparing what people type: accents dropped, case and lookalike letters ignored ("Jane Doé"
    and "Jаne Doe" with a Cyrillic а are both "jane doe")."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold().translate(LOOKALIKE_LETTERS)
