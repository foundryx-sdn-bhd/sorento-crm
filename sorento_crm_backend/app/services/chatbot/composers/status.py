"""Order / document status pieces: the one period question, for the DO list and the SO list."""
from __future__ import annotations

from datetime import date, timedelta


def month(d: date) -> str:
    return d.strftime("%b %Y")


def ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def span(start: date, end: date, days: int) -> str:
    """Whole calendar months are said in months ("6 months"), anything else in days."""
    if start.day == 1 and (end + timedelta(days=1)).day == 1:
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        return f"{months} months"
    return f"{days} days"


def period_question(
    start: date | None,
    end: date | None,
    *,
    today: date,
    subject: str | None,
    noun: str,
    max_days: int,
) -> str | None:
    """The question that asks for a narrower period, or None when the period is fine. `noun` is
    what the list holds ("delivery orders", "sales orders")."""
    if start is None and end is None:
        this_month = today.replace(day=1)
        last_month = (this_month - timedelta(days=1)).replace(day=1)
        return (f"Which period for {subject}?\n" if subject else "Which period?\n") + (
            f"- This month ({month(this_month)})\n"
            f"- Last month ({month(last_month)})\n"
            "Or type a month (e.g. August) or dates (e.g. 15 Sep to 10 Oct)."
        )
    end = end or today
    if start is not None and start > end:
        start, end = end, start  # a parser that swapped the two ends
    days = (end - start).days + 1 if start is not None else None
    if days is not None and days <= max_days:
        return None
    # Suggest the asked range's last month, then its first, within what has happened: a
    # range wholly in the future suggests this month.
    last = min(end, today)
    suggestions = [month(last)]
    lead = ""
    if start is not None:
        if start <= last and month(start) not in suggestions:
            suggestions.append(month(start))
        lead = f"That is {span(start, end, days)} ({ddmmyyyy(start)} to {ddmmyyyy(end)}). "
    return (
        f"{lead}I can show up to {max_days} days of {noun} at a time:\n"
        + "".join(f"- {s}\n" for s in suggestions)
        + "Or type a month or dates."
    )
