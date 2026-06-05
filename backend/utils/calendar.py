"""
Calendar (.ics) File Generator
================================
Generates standard iCalendar (.ics) files for interview appointments.
These files can be opened directly by Google Calendar, Outlook, Apple
Calendar, and virtually any other calendar application.

No external libraries required — built entirely with Python's stdlib.

Usage:
    from utils.calendar import generate_interview_ics

    ics_bytes = generate_interview_ics(
        candidate_name="Jane Doe",
        job_title="Software Engineer",
        start_dt=datetime(2026, 6, 10, 14, 0),   # UTC datetime
        duration_minutes=30,
        location="https://metishire.com/interview",
        organiser_email="hr@company.com"
    )
    # Attach ics_bytes to an email or return as a file download
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional


_METIS_PRODUCT_ID = "-//Metis Hire//Interview Scheduler//EN"


def _ics_dt(dt: datetime) -> str:
    """Format a datetime as iCalendar UTC timestamp (YYYYMMDDTHHMMSSZ)."""
    # Ensure UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """
    Fold long iCalendar lines at 75 octets (RFC 5545 §3.1).

    Lines are split with CRLF + a single leading space/tab on continuations.
    """
    if len(line.encode("utf-8")) <= 75:
        return line
    chunks = []
    while len(line.encode("utf-8")) > 75:
        # Find safe split point (characters, not bytes)
        n = 75
        while len(line[:n].encode("utf-8")) > 75:
            n -= 1
        chunks.append(line[:n])
        line = " " + line[n:]
    chunks.append(line)
    return "\r\n".join(chunks)


def generate_interview_ics(
    candidate_name: str,
    job_title: str,
    start_dt: datetime,
    duration_minutes: int = 30,
    location: str = "https://metishire.com/interview",
    organiser_email: str = "noreply@metishire.com",
    organiser_name: str = "Metis Hire",
    description: Optional[str] = None,
    uid: Optional[str] = None,
) -> bytes:
    """
    Generate an iCalendar (.ics) file for an interview appointment.

    Args:
        candidate_name: Full name of the candidate
        job_title: Title of the job being interviewed for
        start_dt: Interview start datetime (UTC recommended)
        duration_minutes: Interview duration in minutes (default 30)
        location: Video call link or physical location string
        organiser_email: HR contact email shown in calendar invite
        organiser_name: HR contact display name
        description: Custom description; auto-generated if None
        uid: Unique event ID; auto-generated if None

    Returns:
        Raw bytes of a valid .ics file, ready to be attached to an email
        or served as a file download (Content-Type: text/calendar).
    """
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    now_dt = datetime.now(tz=timezone.utc)
    event_uid = uid or f"{uuid.uuid4()}@metishire.com"

    if description is None:
        description = (
            f"AI-powered interview for the role of {job_title} at Metis Hire.\\n"
            f"Candidate: {candidate_name}\\n"
            f"Duration: approximately {duration_minutes} minutes.\\n"
            f"\\nJoin here: {location}"
        )

    summary = f"AI Interview: {job_title}"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_METIS_PRODUCT_ID}",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{_ics_dt(now_dt)}",
        f"DTSTART:{_ics_dt(start_dt)}",
        f"DTEND:{_ics_dt(end_dt)}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{location}",
        f"ORGANIZER;CN={organiser_name}:mailto:{organiser_email}",
        f"ATTENDEE;CN={candidate_name};ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    # Fold and join with CRLF (RFC 5545 requirement)
    ics_text = "\r\n".join(_fold(line) for line in lines)
    return ics_text.encode("utf-8")


def generate_interview_ics_now(
    candidate_name: str,
    job_title: str,
    hours_from_now: int = 24,
    duration_minutes: int = 30,
    **kwargs,
) -> bytes:
    """
    Convenience wrapper: schedule interview `hours_from_now` hours in the future.

    Useful for immediately sending calendar invites when an interview is unlocked,
    without needing a specific scheduled datetime.
    """
    start_dt = datetime.now(tz=timezone.utc) + timedelta(hours=hours_from_now)
    return generate_interview_ics(
        candidate_name=candidate_name,
        job_title=job_title,
        start_dt=start_dt,
        duration_minutes=duration_minutes,
        **kwargs,
    )
