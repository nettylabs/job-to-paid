"""Customers, the price book, and jobs (with their work log)."""

from __future__ import annotations

import re

from . import profile as profile_mod
from .money import q
from .util import JTPError, dec_str, iso, now_stamp, parse_date, to_decimal, trim_decimal

JOB_STATUSES = (
    "lead",
    "quoted",
    "won",
    "scheduled",
    "in_progress",
    "complete",
    "invoiced",
    "paid",
    "closed",
    "lost",
    "cancelled",
)
OPEN_STATUSES = ("lead", "quoted", "won", "scheduled", "in_progress", "complete", "invoiced")
CONTACT_CHANNELS = ("email", "sms", "whatsapp", "phone", "other")
LOG_TYPES = ("time", "material", "expense", "note", "photo")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _matches(search: str, *fields) -> bool:
    words = [w for w in (search or "").lower().split() if w]
    haystack = " ".join(str(f or "") for f in fields).lower()
    return all(w in haystack for w in words)


# --------------------------------------------------------------- customers

CUSTOMER_FIELDS = (
    "name",
    "company",
    "email",
    "phone",
    "address",
    "site_addresses",
    "tax_id",
    "notes",
    "preferred_contact",
    "language",
    "whatsapp",
)


def _clean_customer_fields(data: dict) -> dict:
    out = {}
    for key in CUSTOMER_FIELDS:
        if key not in data or data[key] is None:
            continue
        value = data[key]
        if key in ("address", "site_addresses"):
            if isinstance(value, str):
                value = (
                    [part.strip() for part in re.split(r"[\n;]", value) if part.strip()]
                    if key == "address"
                    else [value.strip()]
                )
            elif not isinstance(value, list):
                raise JTPError(f"customer {key} must be text or a list of lines.")
        elif key == "preferred_contact":
            value = str(value).lower()
            if value not in CONTACT_CHANNELS:
                raise JTPError(f"preferred_contact must be one of: {', '.join(CONTACT_CHANNELS)}.")
        else:
            value = str(value).strip()
        out[key] = value
    return out


def customer_add(store, data: dict) -> dict:
    fields = _clean_customer_fields(data)
    if not fields.get("name"):
        raise JTPError("A customer needs a name.", hint='jtp customer add --name "Jane Doe" --phone "+1 555 0100"')
    customers = store.customers()
    email = (fields.get("email") or "").lower()
    phone = _digits(fields.get("phone"))
    for cust in customers:
        same_email = email and (cust.get("email") or "").lower() == email
        same_phone = len(phone) >= 7 and _digits(cust.get("phone")) == phone
        if same_email or same_phone:
            return {
                "customer": cust,
                "existing": True,
                "note": f"{cust['name']} already exists as {cust['id']}; used that record.",
            }
    cust = {"id": store.next_id("customer"), **fields, "created_at": now_stamp()}
    customers.append(cust)
    store.save_customers(customers)
    store.log("customer.add", cust["id"], name=cust["name"])
    return {"customer": cust, "existing": False}


def customer_update(store, ident: str, data: dict) -> dict:
    customers = store.customers()
    target = store.customer(ident)
    fields = _clean_customer_fields(data)
    for cust in customers:
        if cust["id"] == target["id"]:
            cust.update(fields)
            cust["updated_at"] = now_stamp()
            target = cust
    store.save_customers(customers)
    store.log("customer.update", target["id"], fields=sorted(fields))
    return target


def customer_list(store, search: str | None = None) -> list:
    out = []
    for cust in store.customers():
        if search and not _matches(
            search,
            cust.get("name"),
            cust.get("company"),
            cust.get("email"),
            cust.get("phone"),
            " ".join(cust.get("address") or []),
            " ".join(cust.get("site_addresses") or []),
        ):
            continue
        out.append(cust)
    return out


# --------------------------------------------------------------- price book


def pricebook_add(store, data: dict) -> dict:
    name = str(data.get("name") or "").strip()
    if not name:
        raise JTPError("A price book item needs a name.")
    kind = (data.get("kind") or "material").lower()
    if kind == "labor":
        kind = "labour"
    if kind not in ("labour", "material", "equipment", "subcontract", "fee", "other"):
        raise JTPError(f"Unknown kind {kind!r} for a price book item.")
    item = {"name": name, "kind": kind, "unit": str(data.get("unit") or ("hr" if kind == "labour" else "ea"))}
    for key in ("cost", "price", "markup_pct"):
        if data.get(key) not in (None, ""):
            value = to_decimal(data[key], key)
            if value < 0:
                raise JTPError(f"{key} can't be negative.")
            item[key] = dec_str(value)
    if "price" not in item and "cost" not in item:
        raise JTPError(f"Price book item {name!r} needs a cost, a price, or both.")
    for key in ("sku", "supplier", "notes"):
        if data.get(key):
            item[key] = str(data[key]).strip()
    if isinstance(data.get("tags"), list):
        item["tags"] = [str(t) for t in data["tags"]]
    elif data.get("tags"):
        item["tags"] = [t.strip() for t in str(data["tags"]).split(",") if t.strip()]
    if isinstance(data.get("tax_codes"), list):
        item["tax_codes"] = list(data["tax_codes"])
    items = store.pricebook()
    for existing in items:
        if existing["name"].lower() == name.lower():
            existing.update(item)
            existing["updated_at"] = now_stamp()
            store.save_pricebook(items)
            store.log("pricebook.update", existing["id"], name=name)
            return {"item": existing, "updated": True}
    item = {"id": store.next_id("pricebook"), **item, "created_at": now_stamp()}
    items.append(item)
    store.save_pricebook(items)
    store.log("pricebook.add", item["id"], name=name)
    return {"item": item, "updated": False}


def pricebook_remove(store, ident: str) -> dict:
    items = store.pricebook()
    keep = [i for i in items if i["id"].lower() != ident.lower()]
    if len(keep) == len(items):
        raise JTPError(f"No price book item {ident}.")
    store.save_pricebook(keep)
    store.log("pricebook.remove", ident)
    return {"removed": ident}


def pricebook_list(store, search: str | None = None) -> list:
    return [
        i
        for i in store.pricebook()
        if not search
        or _matches(
            search, i.get("name"), i.get("sku"), i.get("supplier"), " ".join(i.get("tags") or []), i.get("kind")
        )
    ]


# --------------------------------------------------------------------- jobs


def job_create(
    store,
    customer_id: str,
    title: str,
    site: str | None = None,
    tags=None,
    pricing: str | None = None,
    notes: str | None = None,
    save: bool = True,
) -> dict:
    cust = store.customer(customer_id)
    if not title:
        raise JTPError("A job needs a short title, e.g. 'Replace water heater'.")
    if pricing not in (None, "fixed", "tm"):
        raise JTPError("pricing must be fixed (quoted price) or tm (time and materials).")
    if isinstance(tags, str):
        tags = [t.strip().lower() for t in tags.split(",") if t.strip()]
    site_address = site or (cust.get("site_addresses") or [None])[0] or ", ".join(cust.get("address") or [])
    job = {
        "id": store.next_id("job") if save else "J-DRAFT",
        "customer_id": cust["id"],
        "title": title.strip(),
        "site_address": site_address or "",
        "status": "lead",
        "pricing": pricing,
        "tags": [t.lower() for t in (tags or [])],
        "created": iso(store.today()),
        "completed": None,
        "service_interval_months": None,
        "notes": notes or "",
        "quotes": [],
        "change_orders": [],
        "invoices": [],
        "log": [],
        "next_log": 1,
        "payments": [],
        "created_at": now_stamp(),
    }
    if save:
        store.save_job(job)
        store.log("job.create", job["id"], customer=cust["id"], title=job["title"])
    return job


def job_update(store, ident: str, changes: dict) -> dict:
    job = store.job(ident)
    if "status" in changes and changes["status"]:
        status = changes["status"].lower().replace("-", "_").replace(" ", "_")
        if status not in JOB_STATUSES:
            raise JTPError(f"Unknown job status {status!r}.", hint=f"Use one of: {', '.join(JOB_STATUSES)}")
        job["status"] = status
        if status == "complete" and not job.get("completed"):
            job["completed"] = iso(store.today())
    for key in ("title", "site_address", "notes"):
        if changes.get(key) is not None:
            job[key] = str(changes[key])
    if changes.get("tags") is not None:
        tags = changes["tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        job["tags"] = [t.lower() for t in tags]
    if changes.get("pricing") is not None:
        if changes["pricing"] not in ("fixed", "tm"):
            raise JTPError("pricing must be fixed or tm.")
        job["pricing"] = changes["pricing"]
    if changes.get("service_interval_months") is not None:
        months = int(changes["service_interval_months"])
        job["service_interval_months"] = months if months > 0 else None
    if changes.get("completed"):
        job["completed"] = iso(parse_date(changes["completed"], store.today()))
    store.save_job(job)
    store.log("job.update", job["id"], fields=sorted(k for k, v in changes.items() if v is not None))
    return job


def job_log(store, ident: str, entry_type: str, data: dict) -> dict:
    job = store.job(ident)
    if entry_type not in LOG_TYPES:
        raise JTPError(f"Unknown log type {entry_type!r}.", hint=f"Use one of: {', '.join(LOG_TYPES)}")
    profile = store.profile()
    day = parse_date(data.get("date") or "today", store.today())
    entry = {"id": f"L{job.get('next_log', 1)}", "type": entry_type, "date": iso(day), "logged_at": now_stamp()}
    extra = bool(data.get("extra"))
    default_billable = extra or job.get("pricing") != "fixed"
    billable = data.get("billable")
    billable = default_billable if billable is None else bool(billable)

    if entry_type == "time":
        hours = to_decimal(data.get("hours"), "hours")
        if hours <= 0 or hours > 24:
            raise JTPError("hours must be more than 0 and at most 24 per entry.")
        rate = profile_mod.labour_rate(profile, data.get("rate_key"))
        entry.update(
            {
                "hours": trim_decimal(hours),
                "rate_key": rate["key"],
                "who": data.get("who") or "",
                "note": data.get("note") or "",
                "billable": billable,
                "extra": extra,
                "invoice": None,
            }
        )
    elif entry_type == "material":
        description = str(data.get("description") or "").strip()
        if not description and not data.get("pricebook"):
            raise JTPError("A material entry needs a description or a pricebook item.")
        qty = to_decimal(data.get("qty", 1), "qty")
        if qty <= 0:
            raise JTPError("qty must be more than zero.")
        entry.update(
            {
                "description": description,
                "qty": trim_decimal(qty),
                "unit": data.get("unit") or "ea",
                "billable": billable,
                "extra": extra,
                "invoice": None,
            }
        )
        for key in ("unit_cost", "unit_price", "markup_pct"):
            if data.get(key) not in (None, ""):
                entry[key] = dec_str(to_decimal(data[key], key))
        if data.get("pricebook"):
            entry["pricebook"] = data["pricebook"]
        if not any(k in entry for k in ("unit_cost", "unit_price", "pricebook")):
            raise JTPError("A material entry needs unit_cost (what you paid), unit_price, or a pricebook item.")
        for key in ("receipt", "supplier", "note"):
            if data.get(key):
                entry[key] = str(data[key])
    elif entry_type == "expense":
        description = str(data.get("description") or "").strip()
        if not description:
            raise JTPError("An expense needs a description, e.g. 'Dump fee'.")
        amount = q(to_decimal(data.get("amount"), "amount"), store.places)
        if amount <= 0:
            raise JTPError("amount must be more than zero.")
        entry.update(
            {"description": description, "amount": str(amount), "billable": billable, "extra": extra, "invoice": None}
        )
        if data.get("markup_pct") not in (None, ""):
            entry["markup_pct"] = dec_str(to_decimal(data["markup_pct"], "markup_pct"))
        for key in ("receipt", "supplier", "note"):
            if data.get(key):
                entry[key] = str(data[key])
    elif entry_type == "note":
        text = str(data.get("text") or data.get("note") or "").strip()
        if not text:
            raise JTPError("A note needs text.")
        entry["text"] = text
    elif entry_type == "photo":
        path = str(data.get("path") or "").strip()
        if not path:
            raise JTPError("A photo entry needs the file path.")
        entry.update({"path": path, "caption": data.get("caption") or ""})

    if data.get("who") and "who" not in entry:
        entry["who"] = data["who"]
    job["log"].append(entry)
    job["next_log"] = job.get("next_log", 1) + 1
    if job["status"] in ("lead", "quoted", "won", "scheduled") and entry_type in ("time", "material", "expense"):
        job["status"] = "in_progress"
    store.save_job(job)
    store.log("job.log", job["id"], entry=entry["id"], type=entry_type)
    return {"job": job["id"], "entry": entry}


def job_unlog(store, ident: str, entry_id: str) -> dict:
    job = store.job(ident)
    for entry in job["log"]:
        if entry["id"].lower() == entry_id.lower():
            if entry.get("invoice"):
                raise JTPError(
                    f"{entry_id} is already on invoice {entry['invoice']}.", hint="Void or edit that invoice first."
                )
            job["log"].remove(entry)
            store.save_job(job)
            store.log("job.unlog", job["id"], entry=entry_id)
            return {"job": job["id"], "removed": entry}
    raise JTPError(f"No log entry {entry_id} on {job['id']}.")


def job_list(store, status: str | None = None, search: str | None = None, customer: str | None = None) -> list:
    customers = {c["id"]: c for c in store.customers()}
    out = []
    for job in store.jobs():
        if status == "open" and job["status"] not in OPEN_STATUSES:
            continue
        if status and status not in ("open", "all") and job["status"] != status:
            continue
        if customer and job["customer_id"].lower() != customer.lower():
            continue
        cust = customers.get(job["customer_id"], {})
        if search and not _matches(
            search,
            job.get("title"),
            job.get("site_address"),
            " ".join(job.get("tags") or []),
            cust.get("name"),
            cust.get("company"),
            job.get("id"),
        ):
            continue
        out.append(
            {
                "id": job["id"],
                "title": job["title"],
                "status": job["status"],
                "customer": cust.get("name", job["customer_id"]),
                "customer_id": job["customer_id"],
                "site_address": job.get("site_address", ""),
                "created": job.get("created"),
                "tags": job.get("tags", []),
            }
        )
    return out
