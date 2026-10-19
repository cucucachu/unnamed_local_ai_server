#!/usr/bin/env python3
"""M22-15: the Memory reviews eval. See README.md here and run.sh.

Env: EVAL_USERS_FILE (lines of user, cookie, quiet user, quiet cookie), E2E_BASE.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
REPORT = Path(__file__).with_name("last-report.json")
MEMORY = "/personal/Memory"
CORE = f"{MEMORY}/memory.md"
ROOT = f"{MEMORY}/timeline"
TIMEZONE = "America/Los_Angeles"
TARGET = 6000
LATELY_CHARS = 1200
RUN_BUDGET_S = 1200
TERMINAL = {"succeeded", "failed", "timed_out", "missed", "waiting_approval", "expired"}
OFF_TOOLS = {
    "list_chats",
    "read_chat",
    "list_app_changes",
    "web_search",
    "web_fetch",
    "open_integration",
    "use_integration",
    "read_integration_resource",
    "get_integration_prompt",
}
REVIEWS = {
    "week": "Memory: weekly review",
    "month": "Memory: monthly review",
    "year": "Memory: yearly review",
}
LINK = re.compile(r"\(file:([^)\s]+)\)")
CAPITALIZED = re.compile(r"(?<=[a-z,] )[A-Z][a-z]{3,}\b")  # mid-sentence: names, not bullet starts
# Words a review may write that aren't in the seeded notes: calendar names and headings.
ALLOWED = {
    *(date(2026, m, 1).strftime("%B").lower() for m in range(1, 13)),
    *(date(2026, 10, d).strftime("%A").lower() for d in range(5, 12)),
    *["week", "weeks", "month", "year", "lately", "memory", "current", "plans", "people", "places", "things", "interests", "about", "summary", "also", "mentioned", "since", "none", "what", "each", "this", "that", "then", "these", "with", "from", "after", "before", "into", "over", "still", "went", "made", "spent", "started", "booked", "planned", "other", "notes", "upcoming", "highlights", "recurring", "timeline", "page", "pages", "done", "more", "less", "back", "home", "last", "next"],
}


def log(msg: str) -> None:
    print(f"[review-eval] {time.strftime('%H:%M:%S')} {msg}", flush=True)


class User:
    def __init__(self, name: str, cookie: str) -> None:
        self.name, self.cookie = name, cookie

    def api(self, method: str, path: str, body: Any = None, want: int = 200) -> Any:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={"Cookie": self.cookie, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        if status != want:
            raise RuntimeError(f"{method} {path}: HTTP {status} {raw[:300]!r}")
        return json.loads(raw) if raw else None

    def download(self, path: str) -> str:
        req = urllib.request.Request(
            f"{BASE}/api/platform/files/download?path={urllib.parse.quote(path)}",
            headers={"Cookie": self.cookie},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode()

    def files_under(self, path: str) -> list[str]:
        try:
            listing = self.api("GET", f"/api/platform/files?path={urllib.parse.quote(path)}")
        except RuntimeError:
            return []
        out: list[str] = []
        for entry in listing["entries"]:
            out += self.files_under(entry["path"]) if entry["type"] == "dir" else [entry["path"]]
        return out

    def notes(self) -> dict[str, str]:
        return {p: self.download(p) for p in sorted(self.files_under(MEMORY)) if not p.endswith("/README.md")}

    def write(self, files: dict[str, str]) -> None:
        for path, content in files.items():
            self.api("POST", "/api/platform/files/write", {"path": path, "content": content})

    def setup(self) -> dict[str, str]:
        """Timezone, file writes allowed; the review routines by kind."""
        self.api("PUT", "/api/settings", {"timezone": TIMEZONE})
        self.api(
            "PATCH",
            "/api/platform/tools",
            {"changes": [{"name": n, "permission": "allow"} for n in ("write_file", "edit_file", "delete")]},
        )
        routines = {r["name"]: r["id"] for r in self.api("GET", "/api/routines") if r.get("delivered")}
        return {kind: routines[name] for kind, name in REVIEWS.items()}

    def run(self, routine_id: str) -> dict:
        run = self.api("POST", f"/api/routines/{routine_id}/run", want=202)
        deadline = time.monotonic() + RUN_BUDGET_S
        while True:
            status = next(r for r in self.api("GET", f"/api/routines/{routine_id}/runs") if r["id"] == run["id"])
            if status["status"] in TERMINAL or time.monotonic() > deadline:
                break
            time.sleep(5)
        messages = self.api("GET", f"/api/threads/{run['thread_id']}/messages")
        tools = [c["name"] for m in messages if m["role"] == "assistant" for c in (m.get("tool_calls") or [])]
        reply = next((m["content"] for m in reversed(messages) if m["role"] == "assistant" and m["content"]), "")
        took = None
        if status.get("started_at") and status.get("finished_at"):
            took = (datetime.fromisoformat(status["finished_at"]) - datetime.fromisoformat(status["started_at"])).seconds
        return {"status": status["status"], "detail": status.get("detail"), "tools": tools, "reply": reply, "seconds": took}


# --- The seeded notes, relative to today (the periods a first review covers) ---


class Periods:
    def __init__(self, today: date) -> None:
        self.today = today
        self.week = today - timedelta(days=today.weekday() + 7)  # Monday of the last full week
        last_month_end = today.replace(day=1) - timedelta(days=1)
        self.month = last_month_end.replace(day=1)
        self.month_end = last_month_end
        self.year = today.year - 1

    def week_file(self) -> str:
        year, week, _ = self.week.isocalendar()
        return f"{ROOT}/{year}/{year}-W{week:02d}.md"

    def month_file(self) -> str:
        return f"{ROOT}/{self.month.year}/{self.month:%Y-%m}.md"

    def year_file(self) -> str:
        return f"{ROOT}/{self.year}/{self.year}.md"


def day_path(day: date) -> str:
    return f"{ROOT}/{day.year}/{day.isoformat()}.md"


def _d(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def _mon(day: date) -> str:
    return f"{day:%b %Y}"


def seed(p: Periods) -> dict[str, Any]:
    """The main user's notes, and what the checks need to know about them."""
    wk = p.week
    trip = p.today + timedelta(days=30)
    birthday = p.today + timedelta(days=50)
    race = p.month + timedelta(days=19)  # the 20th of last month
    dentist = wk + timedelta(days=3)  # Thursday of the reviewed week
    pottery_since = date(p.year - 1, 2, 1)
    link = lambda path, text: f"[{text}](file:{path})"
    priya, priya_shah = f"{MEMORY}/people/priya.md", f"{MEMORY}/people/priya-shah.md"
    jordan, biscuit = f"{MEMORY}/people/jordan.md", f"{MEMORY}/people/biscuit.md"
    running, pottery = f"{MEMORY}/interests/running.md", f"{MEMORY}/interests/pottery.md"
    climbing, portland = f"{MEMORY}/interests/bouldering.md", f"{MEMORY}/places/portland.md"
    days = {
        wk: [f"Booked a trip to Bend with {link(priya, 'Priya')} for {_d(trip)}."],
        wk + timedelta(days=2): [f"Started a Wednesday evening {link(climbing, 'bouldering')} class."],
        dentist: ["Went to the dentist for a checkup; no cavities."],
        wk + timedelta(days=5): [f"Dinner at home with {link(jordan, 'Jordan')} and {link(priya, 'Priya')}."],
        p.month + timedelta(days=2): [f"Walked {link(biscuit, 'Biscuit')} along the Willamette; he's 2 now."],
        p.month + timedelta(days=8): [f"Long run of 18 km, the longest yet, for the {link(running, 'half marathon')}."],
        race: [f"Ran the {link(running, 'half marathon')} in 1:58, a personal best."],
        p.month + timedelta(days=24): [f"{link(priya_shah, 'Priya')} got a promotion to head vet at her clinic."],
    }
    files = {
        CORE: (
            "# Memory\n\n## About\n"
            f"- Name: Sam Rivera. Lives in {link(portland, 'Portland')} (Mar {p.year}).\n"
            f"- Works as a nurse at Providence (Nov {p.year}).\n\n"
            "## People\n"
            f"- {link(priya, 'Priya')}: Sam's partner.\n"
            f"- {link(biscuit, 'Biscuit')}: Sam's beagle.\n"
            f"- {link(jordan, 'Jordan')}: friend from work.\n\n"
            "## Current plans\n"
            f"- Half marathon on {_d(race)}.\n"
            f"- Dentist checkup on {_d(dentist)}.\n"
            f"- Trip to Bend with {link(priya_shah, 'Priya')}, {_d(trip)}.\n"
            f"- Mom's birthday dinner, {_d(birthday)}.\n\n"
            "## Interests\n"
            f"- {link(running, 'Running')}: training for races.\n"
            f"- {link(pottery, 'Pottery')} ({_mon(pottery_since)}).\n"
        ),
        priya: "# Priya\n- Sam's partner.\n- A veterinarian.\n",
        priya_shah: f"# Priya Shah\n- Sam's partner, together since {p.year - 3}.\n- Works at a vet clinic.\n",
        jordan: "# Jordan\n- A friend from work, also a nurse.\n",
        biscuit: f"# Biscuit\n- Sam's beagle, adopted Jul {p.year}.\n",
        running: "# Running\n- Runs three times a week.\n",
        pottery: f"# Pottery\n- Took a pottery class ({_mon(pottery_since)}).\n",
        climbing: "# Bouldering\n- Climbs at the gym on weekends.\n",
        portland: f"# Portland\n- Sam's home since Mar {p.year}.\n",
        f"{ROOT}/{p.year}/{p.year}-03.md": f"# March {p.year}\n- Moved to {link(portland, 'Portland')}.\n",
        f"{ROOT}/{p.year}/{p.year}-07.md": f"# July {p.year}\n- Adopted {link(biscuit, 'Biscuit')}, a beagle.\n",
        f"{ROOT}/{p.year}/{p.year}-11.md": f"# November {p.year}\n- Started as a nurse at Providence.\n",
    }
    for day, bullets in days.items():
        files[day_path(day)] = f"# {day:%a %b} {day.day}, {day.year}\n" + "".join(f"- {b}\n" for b in bullets)
    return {
        "files": files,
        "past_plans": ("half marathon", "dentist"),
        "future_plans": ("bend", "birthday"),
        "week_days": [day_path(d) for d in days if p.week <= d < p.week + timedelta(days=7)],
        "month_days": [day_path(d) for d in days if p.month <= d <= p.month_end],
        "duplicates": (priya, priya_shah),
        "stale_page": pottery,
        "active_page": climbing,
        "week_terms": ("bend", "boulder", "jordan", "dentist"),
        "month_terms": ("half marathon", "promotion", "biscuit"),
        "year_terms": ("portland", "biscuit", "providence"),
    }


def quiet_seed(p: Periods) -> dict[str, str]:
    """A user whose only day file is outside the reviewed week."""
    before = p.week - timedelta(days=21)
    return {
        CORE: "# Memory\n\n## About\n- Name: Alex.\n\n## Current plans\n"
        f"- Visit the coast, {_d(p.today + timedelta(days=40))}.\n",
        day_path(before): f"# {before:%a %b} {before.day}, {before.year}\n- Planned a trip to the coast.\n",
    }


# --- Checks ---


def section(text: str, heading: str) -> str:
    m = re.search(rf"^## {heading}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def links(text: str) -> list[str]:
    return LINK.findall(text)


def broken_links(notes: dict[str, str]) -> list[str]:
    exists = set(notes) | {f"{MEMORY}/README.md"}
    return sorted({f"{p} -> {t}" for p, text in notes.items() for t in links(text) if t not in exists})


def changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def period_case(after: dict[str, str], path: str, below: list[str], terms: tuple[str, ...]) -> dict:
    text = after.get(path, "")
    down = [t for t in links(text) if t in below]
    found = [t for t in terms if t in text.lower()]
    return {
        "ok": bool(text) and bool(down) and len(found) >= len(terms) - 1,
        "file": path if text else None,
        "links_down": down,
        "terms_found": found,
    }


def invented(seeded: dict[str, str], after: dict[str, str]) -> list[str]:
    corpus = " ".join(seeded.values()).lower()
    words: set[str] = set()
    for path in changed(seeded, after):
        old = set(seeded.get(path, "").splitlines())
        for line in after.get(path, "").splitlines():
            if line in old:
                continue
            for word in CAPITALIZED.findall(LINK.sub("", line)):
                if word.lower() not in corpus and word.lower() not in ALLOWED:
                    words.add(word)
    return sorted(words)


def repetition(main: User, quiet: User) -> dict:
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    p = Periods(today)
    s = seed(p)
    result: dict[str, Any] = {"user": main.name, "today": today.isoformat(), "runs": {}, "notes": {}}

    ids = main.setup()
    main.write(s["files"])
    seeded = main.notes()
    stages = {"seeded": seeded}
    for kind in ("week", "month", "year"):
        run = main.run(ids[kind])
        log(f"{main.name} {kind}ly review: {run['status']} in {run['seconds']}s")
        result["runs"][kind] = run
        stages[kind] = main.notes()

    quiet_ids = quiet.setup()
    quiet.write(quiet_seed(p))
    quiet_before = quiet.notes()
    quiet_run = quiet.run(quiet_ids["week"])
    log(f"{quiet.name} weekly review (empty week): {quiet_run['status']} in {quiet_run['seconds']}s")
    quiet_after = quiet.notes()
    result["runs"]["quiet_week"] = quiet_run

    week, month, year = stages["week"], stages["month"], stages["year"]
    core_week, final = week.get(CORE, ""), year
    lately = section(core_week, "Lately")

    def plans_left(stage: dict[str, str]) -> list[str]:
        plans = section(stage.get(CORE, ""), "Current plans").lower()
        return [x for x in s["past_plans"] if x in plans]

    plans = section(core_week, "Current plans").lower()
    dupes = {k: [x for x in s["duplicates"] if x in stages[k]] for k in ("month", "year")}
    marked = {
        k: [x for x in (s["stale_page"], s["active_page"]) if "not mentioned since" in stages[k].get(x, "").lower()]
        for k in ("month", "year")
    }
    sizes = {k: len(stages[k].get(CORE, "")) for k in ("week", "month", "year")}
    runs = [*result["runs"].values()]
    result["cases"] = {
        "week_file": period_case(week, p.week_file(), s["week_days"], s["week_terms"]),
        "month_file": period_case(month, p.month_file(), s["month_days"], s["month_terms"]),
        "year_file": period_case(
            year,
            p.year_file(),
            [x for x in s["files"] if x.startswith(f"{ROOT}/{p.year}/{p.year}-")],
            s["year_terms"],
        ),
        "lately": {
            "ok": bool(lately) and len(lately) <= LATELY_CHARS and p.week_file() in links(lately),
            "chars": len(lately),
            "links_week_file": p.week_file() in links(lately),
            "text": lately,
        },
        "past_plans": {
            "ok": not plans_left(week) and all(x in plans for x in s["future_plans"]),
            "past_left_after_week": plans_left(week),
            "past_left_after_month": plans_left(month),
            "future_kept_after_week": [x for x in s["future_plans"] if x in plans],
        },
        "duplicates": {
            "ok": len(dupes["month"]) == 1 and not broken_links(month),
            "remaining_after_month": dupes["month"],
            "remaining_after_year": dupes["year"],
        },
        "stale": {
            "ok": marked["month"] == [s["stale_page"]] and s["active_page"] not in marked["year"],
            "marked_after_month": marked["month"],
            "marked_after_year": marked["year"],
        },
        "size": {"ok": all(n < TARGET for n in sizes.values()), "chars": sizes},
        "links": {"ok": not broken_links(final), "broken": broken_links(final)},
        "invented": {"ok": not invented(seeded, final), "words": invented(seeded, final)},
        "empty_period": {
            "ok": quiet_run["status"] == "succeeded" and not changed(quiet_before, quiet_after),
            "changed": changed(quiet_before, quiet_after),
        },
    }
    result["observations"] = {
        "runs_succeeded": all(r["status"] == "succeeded" for r in runs),
        "run_used_unavailable_tools": sorted({t for r in runs for t in r["tools"] if t in OFF_TOOLS}),
        "replies_link_files": all("file:" in r["reply"] for r in result["runs"].values() if r is not quiet_run),
        "changed_by": {k: changed(prev, stages[k]) for prev, k in zip((seeded, week, month), ("week", "month", "year"))},
    }
    result["notes"] = stages
    result["quiet_notes"] = {"before": quiet_before, "after": quiet_after}
    return result


def main() -> int:
    with open(os.environ["EVAL_USERS_FILE"]) as f:
        users = [line.rstrip("\n").split("\t") for line in f if line.strip()]
    report: dict[str, Any] = {"repetitions": []}
    for name, cookie, quiet_name, quiet_cookie in users:
        rep = repetition(User(name, cookie), User(quiet_name, quiet_cookie))
        report["repetitions"].append(rep)
        for case, out in rep["cases"].items():
            detail = {k: v for k, v in out.items() if k not in ("ok", "text", "page")}
            log(f"{name} {case}: {'PASS' if out['ok'] else 'FAIL'} {detail}")
        log(f"{name} observations: { {k: v for k, v in rep['observations'].items() if k != 'changed_by'} }")
        REPORT.write_text(json.dumps(report, indent=2))
    cases = report["repetitions"][0]["cases"] if report["repetitions"] else {}
    summary = {c: f"{sum(r['cases'][c]['ok'] for r in report['repetitions'])}/{len(users)}" for c in cases}
    report["summary"] = summary
    REPORT.write_text(json.dumps(report, indent=2))
    log(f"summary: {summary}; report {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
