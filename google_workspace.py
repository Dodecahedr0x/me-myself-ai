"""Read Workspace activity and submit reviewed primary-calendar events."""

from datetime import datetime, timezone
import hashlib
from urllib.parse import quote

import connectors


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def pages(url, token, params, field):
    seen = set()
    while True:
        data, _ = connectors._http(url, token, params)
        yield from data.get(field, [])
        cursor = data.get("nextPageToken")
        if not cursor:
            return
        if cursor in seen:
            raise ValueError("Google repeated a page token.")
        seen.add(cursor)
        params = dict(params, pageToken=cursor)


def activity(service, account, token, start, end):
    if service == "Calendar":
        rows = pages(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            token,
            dict(updatedMin=utc(start), maxResults=250),
            "items",
        )
    elif service == "Drive":
        rows = pages(
            "https://www.googleapis.com/drive/v3/files",
            token,
            dict(
                q=f"trashed = false and modifiedTime >= '{utc(start)}' and modifiedTime <= '{utc(end)}'",
                fields="nextPageToken,files(id,name,description,mimeType,modifiedTime,webViewLink)",
                pageSize=100,
            ),
            "files",
        )
    elif service == "Tasks":
        lists = pages(
            "https://tasks.googleapis.com/tasks/v1/users/@me/lists", token, dict(maxResults=100), "items"
        )
        rows = (
            dict(row, listId=lst["id"])
            for lst in lists
            for row in pages(
                "https://tasks.googleapis.com/tasks/v1/lists/" + quote(lst["id"], safe="") + "/tasks",
                token,
                dict(updatedMin=utc(start), showCompleted="true", showHidden="true", maxResults=100),
                "items",
            )
        )
    elif service == "Contacts":
        rows = contact_changes(token, start)
    else:
        raise ValueError("Unknown Google service.")
    for r in rows:
        if r.get("status") in {"cancelled", "completed"} or r.get("extendedProperties", {}).get(
            "private", {}
        ).get("memyselfai"):
            continue
        stamp = datetime.fromisoformat(
            (r.get("updated") or r["modifiedTime"]).replace("Z", "+00:00")
        ).timestamp()
        if not start <= stamp <= end:
            continue
        title = r.get("summary") or r.get("name") or r.get("title") or "Untitled"
        context = {"google_service": service, "resource": r["id"]}
        if r.get("mimeType"):
            context["mime_type"] = r["mimeType"]
        detail = [r.get("description") or r.get("notes") or "", r.get("mimeType", "")]
        if service == "Calendar":
            for field in ("start", "end"):
                when = r.get(field, {})
                detail.append(field + " " + str(when.get("dateTime") or when.get("date") or ""))
        if r.get("due"):
            detail.append("Due " + r["due"])
        yield dict(
            key="workspace/" + service + "/" + r.get("listId", "") + "/" + r["id"],
            timestamp=stamp,
            title=title,
            body="\n".join(x for x in detail if x)[:20000],
            account=account,
            url=r.get("htmlLink") or r.get("webViewLink", ""),
            destination=service + " · " + title,
            authored=False,
            owned=False,
            context=context,
        )


def contact_changes(token, start):
    people = pages(
        "https://people.googleapis.com/v1/people/me/connections",
        token,
        dict(
            personFields="metadata,names,emailAddresses", sortOrder="LAST_MODIFIED_DESCENDING", pageSize=1000
        ),
        "connections",
    )
    for person in people:
        latest = max(
            (s.get("updateTime", "") for s in person.get("metadata", {}).get("sources", [])), default=""
        )
        if not latest:
            continue
        if datetime.fromisoformat(latest.replace("Z", "+00:00")).timestamp() < start:
            continue
        name = next(
            (n.get("displayName") for n in person.get("names", []) if n.get("displayName")), "Contact"
        )
        emails = [e["value"] for e in person.get("emailAddresses", []) if e.get("value")]
        yield dict(id=person["resourceName"], title=name, updated=latest, notes=", ".join(emails))


def document_excerpt(ctx, token):
    resource = quote(ctx["resource"], safe="")
    mime = ctx.get("mime_type", "")
    if mime == "application/vnd.google-apps.document":
        result, _ = connectors._http(
            "https://docs.googleapis.com/v1/documents/" + resource,
            token,
            {"fields": "title,body(content(paragraph(elements(textRun(content)))))"},
        )
        text = "".join(
            e.get("textRun", {}).get("content", "")
            for p in result.get("body", {}).get("content", [])
            for e in p.get("paragraph", {}).get("elements", [])
        )
    elif mime == "application/vnd.google-apps.spreadsheet":
        result, _ = connectors._http(
            "https://sheets.googleapis.com/v4/spreadsheets/" + resource + "/values/A1:Z50", token
        )
        text = "\n".join("\t".join(str(cell) for cell in row) for row in result.get("values", []))
    elif mime == "application/vnd.google-apps.presentation":
        result, _ = connectors._http(
            "https://slides.googleapis.com/v1/presentations/" + resource,
            token,
            {"fields": "slides(pageElements(shape(text(textElements(textRun(content))))))"},
        )
        text = "".join(
            t.get("textRun", {}).get("content", "")
            for slide in result.get("slides", [])
            for el in slide.get("pageElements", [])
            for t in el.get("shape", {}).get("text", {}).get("textElements", [])
        )
    else:
        return ""
    return text[:16000]


def calendar_payload(task_id, summary, start, end):
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise ValueError("A calendar event needs a title.")
    if not all(type(t) in (int, float) and 0 < t < 253402300799 for t in (start, end)) or end <= start:
        raise ValueError("A calendar event needs valid start and end times.")
    ident = hashlib.sha256(("calendar/" + task_id).encode()).hexdigest()
    return dict(
        id=ident,
        summary=summary.strip(),
        start={"dateTime": utc(start)},
        end={"dateTime": utc(end)},
        extendedProperties={"private": {"memyselfai": ident}},
    )


def create_event(conn, task):
    if (
        "Calendar" not in conn.get("google_services", [])
        or conn.get("identity") != task["account"]
        or task["destination"] != "Primary calendar"
    ):
        raise connectors.Rejected("Reconnect the reviewed Google Calendar account.")
    import json

    body = json.loads(task["draft"])
    if (
        body != task["calendar_payload"]
        or not body.get("id")
        or body.get("extendedProperties", {}).get("private", {}).get("memyselfai") != body["id"]
    ):
        raise connectors.Rejected("Prepare a new calendar preview for this Google connection.")
    try:
        token = connectors._transport_secret(conn, connectors.validate("email", conn["config"]))
    except Exception as exc:
        raise connectors.Rejected("Could not authenticate the reviewed Google account.") from exc
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    try:
        result, _ = connectors._http(url, token, {"sendUpdates": "none"}, body=body)
    except connectors.Rejected as exc:
        if "HTTP 409" not in str(exc):
            raise
        result, _ = connectors._http(url + "/" + body["id"], token)
        times_match = all(
            datetime.fromisoformat(result.get(field, {}).get("dateTime", "").replace("Z", "+00:00"))
            == datetime.fromisoformat(body[field]["dateTime"].replace("Z", "+00:00"))
            for field in ("start", "end")
            if result.get(field, {}).get("dateTime")
        ) and all(result.get(field, {}).get("dateTime") for field in ("start", "end"))
        if (
            not times_match
            or result.get("summary") != body["summary"]
            or result.get("extendedProperties", {}).get("private") != body["extendedProperties"]["private"]
            or result.get("status") == "cancelled"
        ):
            raise connectors.Rejected("Calendar event ID is already in use.") from exc
    if result.get("id") != body["id"]:
        raise RuntimeError("Google did not confirm the calendar event ID.")
    return result.get("htmlLink") or "Calendar event " + result["id"]
