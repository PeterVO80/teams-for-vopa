"""Two-way link between the team planning and Outlook (Microsoft 365).

* **Outlook -> planning**: each employee's Outlook appointments are copied into
  ``calendar_events`` and shown in the team planning.
* **Planning -> Outlook**: every planning entry becomes an all-day
  appointment in the employee's Outlook calendar (category "VOPA Teams"),
  updated or removed when the planning changes.

Authentication
--------------
The application uses an app registration in Microsoft Entra ID (Azure AD)
with the *application* permission ``Calendars.ReadWrite`` (admin consent),
and signs in with the client-credentials flow. No employee has to log in;
their mailbox is found by the e-mail address of their user account. The link
is active only when ``MS365_TENANT_ID``, ``MS365_CLIENT_ID`` and
``MS365_CLIENT_SECRET`` are set. See README.md for the setup steps.

When does it run?
-----------------
* ``flask outlook-sync --loop 300``: a separate container that synchronises
  everyone every 5 minutes (reads calendars, writes pending planning, cleans
  up appointments of deleted planning).
* Right after a planning change, the affected entry is written straight away
  in a background thread, so it appears in Outlook within seconds. If that
  fails (network, Microsoft), the next periodic run picks it up: an entry is
  "pending" as long as ``outlook_synced_at`` is older than ``updated_at``.
"""

from __future__ import annotations

import json
import re
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from flask import current_app

from ..extensions import db
from ..models import AppSetting, Assignment, CalendarEvent, OutlookLink, User
from ..models.base import utcnow

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
#: Category put on the appointments written by this application.
CATEGORY = "VOPA Teams"
#: Outlook category colours: (Graph preset, Dutch name, English name, approx. colour).
CATEGORY_PALETTE = [
    ("preset0", "rood", "red", "#e7a1a2"), ("preset1", "oranje", "orange", "#f9ba89"),
    ("preset2", "bruin", "brown", "#f7dd8f"), ("preset3", "geel", "yellow", "#fcfa90"),
    ("preset4", "groen", "green", "#78d168"), ("preset5", "blauwgroen", "teal", "#9fdcc9"),
    ("preset6", "olijf", "olive", "#c6d2b0"), ("preset7", "blauw", "blue", "#9db7e8"),
    ("preset8", "paars", "purple", "#b5a1e2"), ("preset9", "cranberry", "cranberry", "#daaec2"),
    ("preset10", "staal", "steel", "#dad9dc"), ("preset11", "donker staal", "darksteel", "#6b7994"),
    ("preset12", "grijs", "gray", "#bfbfbf"), ("preset13", "donkergrijs", "darkgray", "#6f6f6f"),
    ("preset14", "zwart", "black", "#4f4f4f"), ("preset15", "donkerrood", "darkred", "#c11a25"),
    ("preset16", "donkeroranje", "darkorange", "#e2620d"), ("preset17", "donkerbruin", "darkbrown", "#c79930"),
    ("preset18", "donkergeel", "darkyellow", "#b9b300"), ("preset19", "donkergroen", "darkgreen", "#368f2b"),
    ("preset20", "donker blauwgroen", "darkteal", "#329b7a"), ("preset21", "donker olijf", "darkolive", "#778b45"),
    ("preset22", "donkerblauw", "darkblue", "#2858a5"), ("preset23", "donkerpaars", "darkpurple", "#5c3fa3"),
    ("preset24", "donker cranberry", "darkcranberry", "#93446b"),
]
CATEGORY_COLORS = {name.replace(" ", ""): preset for preset, nl, en, _ in CATEGORY_PALETTE for name in (nl, en)}
#: AppSetting key of the colour chosen on the Outlook page (overrides MS365_CATEGORY_COLOR).
COLOR_SETTING = "outlook.category_color"
#: Synchronisation window around today.
DAYS_BACK, DAYS_AHEAD = 30, 183


class GraphError(Exception):
    """A Microsoft Graph request failed (message is shown to administrators)."""


# ------------------------------------------------------------------ settings
def settings() -> dict:
    config = current_app.config
    return {
        "tenant": config.get("MS365_TENANT_ID") or "",
        "client_id": config.get("MS365_CLIENT_ID") or "",
        "secret": config.get("MS365_CLIENT_SECRET") or "",
        "timezone": config.get("MS365_TIMEZONE") or "Romance Standard Time",
        "app_url": config.get("APP_URL") or "",
        "category_color": category_color(),
    }


def category_color() -> str | None:
    """Colour of the category: chosen in the app, else MS365_CATEGORY_COLOR."""
    chosen = AppSetting.get(COLOR_SETTING)
    if chosen is not None:
        return chosen or None          # "" = chosen "no colour"
    return category_preset(current_app.config.get("MS365_CATEGORY_COLOR") or "")


def category_preset(value: str) -> str | None:
    """Colour setting -> Graph preset ("oranje" -> "preset1"); None = leave alone."""
    value = value.strip().lower()
    if re.fullmatch(r"preset(\d|1\d|2[0-4])", value):
        return value
    return CATEGORY_COLORS.get(value.replace(" ", "").replace("-", ""))


def is_configured() -> bool:
    s = settings()
    return bool(s["tenant"] and s["client_id"] and s["secret"])


# --------------------------------------------------------------- HTTP client
class GraphClient:
    """Minimal Microsoft Graph client (client-credentials token, JSON, paging).

    Only the standard library is used. The token is cached until shortly
    before it expires; 429/503 answers are retried after ``Retry-After``.
    """

    _token: str | None = None
    _token_expires: float = 0.0
    _lock = threading.Lock()

    def __init__(self, tenant: str, client_id: str, secret: str, timezone: str):
        self.tenant, self.client_id, self.secret, self.timezone = tenant, client_id, secret, timezone

    @classmethod
    def from_settings(cls) -> "GraphClient":
        s = settings()
        return cls(s["tenant"], s["client_id"], s["secret"], s["timezone"])

    def _http(self, request: urllib.request.Request) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return response.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                data = json.loads(body) if body else {}
            except ValueError:
                data = {}
            data["_retry_after"] = exc.headers.get("Retry-After") if exc.headers else None
            return exc.code, data
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GraphError(f"Microsoft 365 niet bereikbaar ({exc}).") from exc

    def token(self) -> str:
        with self._lock:
            if GraphClient._token and time.time() < GraphClient._token_expires - 120:
                return GraphClient._token
            data = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.secret,
                "scope": "https://graph.microsoft.com/.default",
            }).encode()
            status, body = self._http(urllib.request.Request(
                f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", data=data, method="POST"))
            if status != 200 or "access_token" not in body:
                raise GraphError(f"Aanmelden bij Microsoft 365 mislukt: {body.get('error_description', body.get('error', status))}")
            GraphClient._token = body["access_token"]
            GraphClient._token_expires = time.time() + int(body.get("expires_in", 3600))
            return GraphClient._token

    def request(self, method: str, url: str, payload: dict | None = None) -> dict:
        if not url.startswith("http"):
            url = GRAPH + url
        for attempt in range(4):
            request = urllib.request.Request(url, method=method, data=json.dumps(payload).encode() if payload else None)
            request.add_header("Authorization", f"Bearer {self.token()}")
            request.add_header("Content-Type", "application/json")
            request.add_header("Prefer", f'outlook.timezone="{self.timezone}"')
            status, body = self._http(request)
            if status in (429, 503, 504) and attempt < 3:
                time.sleep(min(int(body.get("_retry_after") or 2 ** attempt), 30))
                continue
            if status == 404 and method == "DELETE":
                return {}  # already gone
            if status >= 400:
                error = body.get("error", {})
                raise GraphError(f"{error.get('code', status)}: {error.get('message', '')}".strip(": "))
            return body
        raise GraphError("Microsoft 365 blijft te druk; later opnieuw proberen.")

    def get_all(self, url: str) -> list[dict]:
        """GET a collection, following @odata.nextLink pages."""
        items: list[dict] = []
        while url:
            body = self.request("GET", url)
            items.extend(body.get("value", []))
            url = body.get("@odata.nextLink")
        return items


# ------------------------------------------------------ Outlook -> planning
def _parse(value: dict) -> datetime:
    """Graph dateTime ("2026-11-16T09:30:00.0000000") -> naive local datetime."""
    return datetime.fromisoformat(value["dateTime"][:19])


def pull_user(client: GraphClient, user: User, start: date, end: date,
              skipped: list | None = None) -> int:
    """Replace the cached Outlook appointments of ``user`` in [start, end)."""
    query = urllib.parse.urlencode({
        "startDateTime": f"{start.isoformat()}T00:00:00",
        "endDateTime": f"{end.isoformat()}T00:00:00",
        "$select": "id,subject,start,end,isAllDay,showAs,sensitivity,location,categories,isCancelled",
        "$top": "200",
    })
    events = client.get_all(f"/users/{urllib.parse.quote(user.email)}/calendarView?{query}")

    # Appointments this application wrote itself are planning, not "foreign" Outlook items.
    own_ids = set(db.session.scalars(sa.select(OutlookLink.event_id).where(OutlookLink.user_id == user.id)))
    window = (datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()))
    db.session.execute(sa.delete(CalendarEvent).where(
        CalendarEvent.user_id == user.id, CalendarEvent.end > window[0], CalendarEvent.start < window[1]))
    count = 0
    for event in events:
        if (event["id"] in own_ids or CATEGORY in (event.get("categories") or [])
                or event.get("isCancelled") or event.get("showAs") == "free"):
            if skipped is not None:
                skipped.append(event["id"])
            continue
        db.session.add(CalendarEvent(
            user_id=user.id, external_id=event["id"][:255],
            subject=(event.get("subject") or "")[:300],
            location=((event.get("location") or {}).get("displayName") or None),
            start=_parse(event["start"]), end=_parse(event["end"]),
            is_all_day=bool(event.get("isAllDay")), show_as=event.get("showAs") or "busy",
            is_private=event.get("sensitivity") in ("private", "confidential"),
        ))
        count += 1
    return count


# ------------------------------------------------------ planning -> Outlook
def _runs(assignment: Assignment) -> list[tuple[date, date]]:
    """Consecutive planned days of an entry: one Outlook appointment per run.

    A Monday-to-next-Friday entry without weekends becomes two appointments,
    so the weekend in between is not blocked in Outlook.
    """
    runs, run_start, day = [], None, assignment.start_date
    while day <= assignment.end_date + timedelta(days=1):
        covered = day <= assignment.end_date and assignment.covers(day)
        if covered and run_start is None:
            run_start = day
        elif not covered and run_start is not None:
            runs.append((run_start, day - timedelta(days=1)))
            run_start = None
        day += timedelta(days=1)
    return runs


def _subject(assignment: Assignment, part_time_hours: float | bool = False) -> str:
    """Appointment title: what is planned, the remark, and the hours if part-time.

    E.g. "Kemisol - Blokvorm A fase 2 - FAT bij klant (4u/dag)". Absences
    already carry their remark in the label ("Verlof: tandarts").
    """
    subject = assignment.label
    note = (assignment.note or "").strip()
    if note and note not in subject:
        subject += f" - {note}"
    if part_time_hours:
        subject += f" ({part_time_hours:g}u/dag)"
    return subject[:255]


def _event_payload(assignment: Assignment, first: date, last: date, timezone: str, app_url: str) -> dict:
    workday = current_app.config["WORKDAY_HOURS"]
    hours = assignment.hours_per_day
    subject = _subject(assignment, hours < workday and hours)
    lines = []
    if assignment.project:
        lines.append(f"Project: {assignment.project.code} - {assignment.project.name}")
        if assignment.project.customer:
            lines.append(f"Klant: {assignment.project.customer}")
    lines.append(f"Uren per dag: {hours:g}")
    if assignment.note:
        lines.append(f"Opmerking: {assignment.note}")
    if app_url:
        lines.append(f"Planning: {app_url.rstrip('/')}/team/?start={first.isoformat()}")
    lines.append("Aangemaakt door VOPA Teams - wijzigingen gebeuren in de teamplanning.")
    return {
        "subject": subject,
        "body": {"contentType": "text", "content": "\n".join(lines)},
        "start": {"dateTime": f"{first.isoformat()}T00:00:00", "timeZone": timezone},
        "end": {"dateTime": f"{(last + timedelta(days=1)).isoformat()}T00:00:00", "timeZone": timezone},
        "isAllDay": True,
        "showAs": "oof" if assignment.kind.is_absence else "busy",
        "categories": [CATEGORY],
        "isReminderOn": False,
    }


def push_assignment(client: GraphClient, assignment: Assignment) -> None:
    """Write one planning entry to Outlook (replacing earlier appointments).

    Replacing (delete + create) instead of patching keeps it simple and
    always correct, also when the entry moved to another person or its
    period was split differently.
    """
    s = settings()
    for link in db.session.scalars(sa.select(OutlookLink).where(OutlookLink.assignment_id == assignment.id)).all():
        if link.user.email:
            client.request("DELETE", f"/users/{urllib.parse.quote(link.user.email)}/events/{link.event_id}")
        db.session.delete(link)
    user = assignment.user
    if user.email and user.outlook_sync and user.active:
        for first, last in _runs(assignment):
            created = client.request("POST", f"/users/{urllib.parse.quote(user.email)}/events",
                                     _event_payload(assignment, first, last, s["timezone"], s["app_url"]))
            db.session.add(OutlookLink(assignment_id=assignment.id, user_id=user.id, event_id=created["id"],
                                       start_date=first, end_date=last))
    mark_synced(assignment)


def mark_synced(assignment: Assignment) -> None:
    """Record that an entry is in Outlook, without touching ``updated_at``.

    Setting the attribute through the ORM would bump ``updated_at`` (its
    ``onupdate``), which would make the entry look pending again forever.
    Naming ``updated_at`` explicitly in the UPDATE keeps it unchanged.
    """
    db.session.flush()
    db.session.execute(sa.update(Assignment).where(Assignment.id == assignment.id).values(
        outlook_synced_at=utcnow(), updated_at=Assignment.updated_at))
    db.session.expire(assignment, ["outlook_synced_at", "updated_at"])


#: (mailbox, colour) -> when this process last checked it. A newly chosen
#: colour is applied by every process at its next run; a successful check is
#: repeated every few hours (e.g. someone changed the colour in Outlook), a
#: failed one every hour (e.g. the permission was granted in the meantime).
_category_done: dict[tuple[str, str], tuple[float, bool]] = {}
CATEGORY_RECHECK_OK, CATEGORY_RECHECK_FAILED = 6 * 3600, 3600


def ensure_category(client: GraphClient, user: User) -> None:
    """Create the "VOPA Teams" category in the user's Outlook with the set colour.

    Outlook categories (and their colour) belong to each mailbox. This needs
    the application permission ``MailboxSettings.ReadWrite``; without it a
    :class:`GraphError` is raised and the category keeps Outlook's own colour.
    The outcome is stored in ``user.outlook_category`` for the status page.
    """
    color = settings()["category_color"]
    if not color or not user.email:
        return
    key = (user.email.lower(), color)
    checked = _category_done.get(key)
    if checked and time.time() - checked[0] < (CATEGORY_RECHECK_OK if checked[1] else CATEGORY_RECHECK_FAILED):
        return
    name = next((nl for preset, nl, *_ in CATEGORY_PALETTE if preset == color), color)
    try:
        base = f"/users/{urllib.parse.quote(user.email)}/outlook/masterCategories"
        existing = [c for c in client.get_all(base)
                    if (c.get("displayName") or "").strip().lower() == CATEGORY.lower()]
        if not existing:
            client.request("POST", base, {"displayName": CATEGORY, "color": color})
            done = f"aangemaakt in {name}"
        else:
            for category in existing:            # also "VOPA teams" in another letter case
                if category.get("color") != color:
                    client.request("PATCH", f"{base}/{category['id']}", {"color": color})
            done = f"{name}"
        user.outlook_category = f"{done} ({datetime.now():%d/%m %H:%M})"
        _category_done[key] = (time.time(), True)
    except GraphError as exc:
        user.outlook_category = f"Fout: {exc}"[:300]
        _category_done[key] = (time.time(), False)
        raise


def remove_orphans(client: GraphClient) -> int:
    """Delete the Outlook appointments of planning entries that were deleted."""
    orphans = db.session.scalars(sa.select(OutlookLink).where(OutlookLink.assignment_id.is_(None))).all()
    for link in orphans:
        if link.user.email:
            client.request("DELETE", f"/users/{urllib.parse.quote(link.user.email)}/events/{link.event_id}")
        db.session.delete(link)
    return len(orphans)


def pending_assignments(start: date, end: date) -> list[Assignment]:
    """Planning entries in the window not (or no longer) in Outlook.

    Pending is: never written, changed since the last write, or - for people
    whose calendar is linked - not in Outlook at all. The last rule repairs
    entries that were processed while the person had no e-mail address yet
    or had the link switched off.
    """
    has_link = sa.exists().where(OutlookLink.assignment_id == Assignment.id)
    linked_user = sa.and_(User.active.is_(True), User.outlook_sync.is_(True),
                          User.email.is_not(None), User.email != "")
    return db.session.scalars(sa.select(Assignment).join(User, Assignment.user_id == User.id).where(
        Assignment.end_date >= start, Assignment.start_date < end,
        sa.or_(Assignment.outlook_synced_at.is_(None), Assignment.outlook_synced_at < Assignment.updated_at,
               sa.and_(linked_user, ~has_link)),
    )).all()


def mark_user_pending(user: User) -> None:
    """Rewrite all of a person's planning (e.g. after a new e-mail address)."""
    db.session.execute(sa.update(Assignment).where(Assignment.user_id == user.id).values(
        outlook_synced_at=None, updated_at=Assignment.updated_at))


def window() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=DAYS_BACK), today + timedelta(days=DAYS_AHEAD)


# --------------------------------------------------------------- the runs
@dataclass
class SyncReport:
    read: int = 0
    written: int = 0
    removed: int = 0
    skipped: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        text = f"{self.read} Outlook-afspraken gelezen, {self.written} planningen geschreven, {self.removed} verwijderd"
        if self.skipped:
            text += f", {len(self.skipped)} afspraken genegeerd (eigen planning, vrij of geannuleerd)"
        return text + (f", {len(self.errors)} fout(en)" if self.errors else "")


def sync_all(client: GraphClient | None = None) -> SyncReport:
    """Full synchronisation of everyone (the periodic job)."""
    report = SyncReport()
    if not is_configured():
        report.errors.append("Microsoft 365 is niet ingesteld.")
        return report
    client = client or GraphClient.from_settings()
    start, end = window()

    users = db.session.scalars(sa.select(User).where(
        User.active.is_(True), User.outlook_sync.is_(True), User.email.is_not(None), User.email != "")).all()
    for user in users:
        try:
            ensure_category(client, user)
        except GraphError as exc:
            # Not fatal: the appointments are written anyway, only the colour is missing.
            report.errors.append(f"Kleur categorie '{CATEGORY}' ({user.full_name}): {exc} "
                                 "- machtiging MailboxSettings.ReadWrite toegekend?")
    db.session.commit()   # keep the colour status for the status page
    # Planning -> Outlook first, so freshly written appointments are known
    # (and skipped) when the calendars are read.
    try:
        report.removed = remove_orphans(client)
        db.session.commit()
    except GraphError as exc:
        db.session.rollback()
        report.errors.append(f"Opruimen: {exc}")
    # Write errors per user, kept so they are shown on the status page even
    # when reading that user's calendar works.
    write_errors: dict[int, str] = {}
    for assignment in pending_assignments(start, end):
        user_id, name = assignment.user_id, assignment.user.full_name
        try:
            push_assignment(client, assignment)
            db.session.commit()
            report.written += 1
        except GraphError as exc:
            db.session.rollback()
            write_errors.setdefault(user_id, f"Schrijven naar Outlook: {exc}")
            report.errors.append(f"Planning {assignment.id} ({name}): {exc}")

    for user in users:
        try:
            report.read += pull_user(client, user, start, end, report.skipped)
            user.outlook_synced_at = utcnow()
            user.outlook_error = write_errors.get(user.id, "")[:300] or None
            db.session.commit()
        except GraphError as exc:
            db.session.rollback()
            user.outlook_error = f"Lezen uit Outlook: {exc}"[:300]
            db.session.commit()
            report.errors.append(f"{user.full_name}: {exc}")
    return report


def push_soon(app, assignment_id: int | None = None) -> None:
    """Write a changed planning entry (or clean up deleted ones) in the background.

    Called right after a planning change so the employee sees it in Outlook
    within seconds. Failures are only logged: the periodic synchronisation
    retries everything that is still pending.
    """
    if not is_configured() or app.config.get("TESTING"):
        return

    def work():
        with app.app_context():
            try:
                client = GraphClient.from_settings()
                if assignment_id is not None:
                    assignment = db.session.get(Assignment, assignment_id)
                    if assignment is not None:
                        push_assignment(client, assignment)
                remove_orphans(client)
                db.session.commit()
            except Exception:  # noqa: BLE001 - background thread: log, never crash
                db.session.rollback()
                log.exception("Outlook: direct schrijven mislukt, volgende synchronisatie probeert opnieuw")

    threading.Thread(target=work, daemon=True).start()


# ------------------------------------------------------- team planning view
def events_by_user(user_ids: list[int], first: date, last: date) -> dict[int, list[CalendarEvent]]:
    """Cached Outlook appointments per user that overlap [first, last]."""
    result: dict[int, list[CalendarEvent]] = {uid: [] for uid in user_ids}
    if not user_ids:
        return result
    rows = db.session.scalars(sa.select(CalendarEvent).where(
        CalendarEvent.user_id.in_(user_ids),
        CalendarEvent.start < datetime.combine(last + timedelta(days=1), datetime.min.time()),
        CalendarEvent.end > datetime.combine(first, datetime.min.time()),
    ).order_by(CalendarEvent.start))
    for event in rows:
        result[event.user_id].append(event)
    return result

