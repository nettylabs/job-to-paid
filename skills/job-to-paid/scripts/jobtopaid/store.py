"""Where records live: one folder of plain JSON files the owner can read,
back up, or hand to an accountant.

    <home>/profile.json        business settings
    <home>/counters.json       next document numbers
    <home>/customers.json
    <home>/pricebook.json
    <home>/jobs/J-0001.json
    <home>/docs/<type>/Q-0001.json   (quote, invoice, change_order, credit_note)
    <home>/output/              rendered PDFs and HTML
    <home>/activity.jsonl       append-only audit trail
    <home>/clock.json           the latest date anything was saved, so dates never go backwards
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import profile as profile_mod
from .money import currency_display
from .util import JTPError, file_lock, from_iso, iso, now_stamp, read_json, safe_id_part, today, write_json

DOC_TYPES = ("quote", "invoice", "change_order", "credit_note")
COUNTER_KEYS = ("quote", "invoice", "change_order", "credit_note", "job", "customer", "payment", "pricebook")
CLOCK_FILE = "clock.json"
_UNREAD = object()


def default_home() -> Path:
    env = os.environ.get("JTP_HOME")
    if env:
        return Path(env).expanduser()
    workspace = Path("/workspace")
    if workspace.is_dir() and os.access(str(workspace), os.W_OK):
        return workspace / "job-to-paid"
    return Path.home() / "job-to-paid"


class Store:
    def __init__(self, home: Path):
        self.home = Path(home).expanduser().resolve()
        self._profile = None
        self._clock_data = _UNREAD

    # ------------------------------------------------------------------ paths
    @property
    def profile_path(self) -> Path:
        return self.home / "profile.json"

    @property
    def output_dir(self) -> Path:
        return self.home / "output"

    def exists(self) -> bool:
        return self.profile_path.is_file()

    def require(self) -> None:
        if not self.exists():
            raise JTPError(
                f"No Job to Paid data folder at {self.home}.",
                hint="Run: jtp init   (or set JTP_HOME / --home to the right folder)",
                code="not_initialized",
            )

    def lock(self):
        return file_lock(self.home / ".lock")

    # ---------------------------------------------------------------- profile
    def profile(self) -> dict:
        if self._profile is None:
            self.require()
            self._profile = profile_mod.merged(read_json(self.profile_path, {}))
        return self._profile

    def save_profile(self, data: dict) -> None:
        write_json(self.profile_path, data)
        self._profile = profile_mod.merged(data)

    def today(self):
        """The business's date: the computer's, in its time zone (locale.timezone). Documents are dated by it."""
        return today(self.profile()["locale"].get("timezone") if self.exists() else None)

    def record_day(self):
        """The date an estimate that leaves its invoice is stamped with: today, but never earlier than the latest date
        anything was saved here. Nothing lowers it, so setting the clock back can't date one in the past, where it
        would lapse early. The owner's yeses lapse 30 days after it, so it can't bring a lapsed one back either."""
        day, latest = self.today(), self.latest_day()
        return latest if latest and latest > day else day

    def latest_day(self):
        """The latest date anything was saved here (clock.json), or None. An unreadable file counts as missing."""
        if self._clock_data is _UNREAD:
            try:
                self._clock_data = from_iso((read_json(self.home / CLOCK_FILE, {}) or {}).get("latest"))
            except (JTPError, AttributeError, TypeError, ValueError):
                self._clock_data = None
        return self._clock_data

    def _note_day(self) -> None:
        day, latest = self.today(), self.latest_day()
        if not latest or day > latest:
            write_json(self.home / CLOCK_FILE, {"latest": iso(day)})
            self._clock_data = day

    def money(self) -> dict:
        return currency_display(self.profile()["locale"])

    @property
    def places(self) -> int:
        return self.money()["places"]

    # --------------------------------------------------------------- counters
    def counters(self) -> dict:
        data = read_json(self.home / "counters.json", {}) or {}
        for key in COUNTER_KEYS:
            data.setdefault(key, 1)
        return data

    def next_id(self, kind: str, peek: bool = False) -> str:
        counters = self.counters()
        number = int(counters[kind])
        numbering = self.profile()["numbering"]
        if kind == "pricebook":
            prefix, pad = "PB-", 4
        else:
            prefix = safe_id_part(numbering.get(f"{kind}_prefix", kind[:3].upper() + "-"))
            pad = int(numbering.get("padding", 4))
        ident = f"{prefix}{number:0{pad}d}"
        if not peek:
            counters[kind] = number + 1
            write_json(self.home / "counters.json", counters)
        return ident

    def release_last_id(self, kind: str, ident: str) -> bool:
        """Give a number back if it was the most recent one (keeps sequences gap-free)."""
        counters = self.counters()
        number = int(counters[kind]) - 1
        if number < 1:
            return False
        numbering = self.profile()["numbering"]
        prefix = safe_id_part(numbering.get(f"{kind}_prefix", ""))
        pad = int(numbering.get("padding", 4))
        if ident != f"{prefix}{number:0{pad}d}":
            return False
        counters[kind] = number
        write_json(self.home / "counters.json", counters)
        return True

    def set_counter(self, kind: str, value: int) -> None:
        if kind not in COUNTER_KEYS:
            raise JTPError(f"Unknown counter {kind!r}.", hint=f"Use one of: {', '.join(COUNTER_KEYS)}")
        if value < 1:
            raise JTPError("Numbers start at 1 or higher.")
        counters = self.counters()
        counters[kind] = int(value)
        write_json(self.home / "counters.json", counters)

    # -------------------------------------------------------------- customers
    def customers(self) -> list:
        return (read_json(self.home / "customers.json", {}) or {}).get("customers", [])

    def save_customers(self, customers: list) -> None:
        write_json(self.home / "customers.json", {"customers": customers})

    def customer(self, ident: str) -> dict:
        for cust in self.customers():
            if cust["id"].lower() == str(ident).lower():
                return cust
        raise JTPError(f"No customer {ident}.", hint="Find one with: jtp customer list --search <name>")

    # -------------------------------------------------------------- pricebook
    def pricebook(self) -> list:
        return (read_json(self.home / "pricebook.json", {}) or {}).get("items", [])

    def save_pricebook(self, items: list) -> None:
        write_json(self.home / "pricebook.json", {"items": items})

    # ------------------------------------------------------------------- jobs
    def job_path(self, ident: str) -> Path:
        return self.home / "jobs" / f"{safe_id_part(ident)}.json"

    def job(self, ident: str) -> dict:
        data = read_json(self.job_path(ident))
        if data is None:
            # tolerate lowercase ids from chat
            for path in (self.home / "jobs").glob("*.json"):
                if path.stem.lower() == str(ident).lower():
                    return read_json(path)
            raise JTPError(f"No job {ident}.", hint="Find one with: jtp job list --search <words>")
        return data

    def save_job(self, job: dict) -> None:
        job["updated_at"] = now_stamp()
        write_json(self.job_path(job["id"]), job)

    def jobs(self) -> list:
        folder = self.home / "jobs"
        if not folder.is_dir():
            return []
        return [read_json(p) for p in sorted(folder.glob("*.json"))]

    # ------------------------------------------------------------------- docs
    def doc_path(self, doc_type: str, ident: str) -> Path:
        return self.home / "docs" / doc_type / f"{safe_id_part(ident)}.json"

    def doc(self, ident: str, doc_type: str | None = None) -> dict:
        types = [doc_type] if doc_type else list(DOC_TYPES)
        for kind in types:
            data = read_json(self.doc_path(kind, ident))
            if data is not None:
                return data
        for kind in types:
            folder = self.home / "docs" / kind
            if folder.is_dir():
                for path in folder.glob("*.json"):
                    if path.stem.lower() == str(ident).lower():
                        return read_json(path)
        what = doc_type.replace("_", " ") if doc_type else "document"
        raise JTPError(f"No {what} {ident}.", hint="List them with: jtp quote list / jtp invoice list")

    def save_doc(self, doc: dict) -> None:
        doc["updated_at"] = now_stamp()
        write_json(self.doc_path(doc["type"], doc["id"]), doc)

    def delete_doc(self, doc: dict) -> None:
        path = self.doc_path(doc["type"], doc["id"])
        if path.exists():
            path.unlink()

    def docs(self, doc_type: str) -> list:
        folder = self.home / "docs" / doc_type
        if not folder.is_dir():
            return []
        return [read_json(p) for p in sorted(folder.glob("*.json"))]

    # ------------------------------------------------------------------ audit
    def log(self, action: str, ident: str | None = None, **details) -> None:
        entry = {"ts": now_stamp(), "action": action}
        if ident:
            entry["id"] = ident
        if details:
            entry.update(details)
        self.home.mkdir(parents=True, exist_ok=True)
        with open(self.home / "activity.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._note_day()  # every change is logged: keep its date, so later dates are never earlier
