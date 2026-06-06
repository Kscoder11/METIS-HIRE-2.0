"""
Email Notification Utility
===========================
Sends transactional emails to candidates and HR recruiters for key
recruitment lifecycle events.

Supports TWO transport backends:
  1. **Brevo API** (preferred on Render / cloud) — set BREVO_API_KEY
  2. **SMTP**      (Gmail App Password)         — set SMTP_USERNAME + SMTP_PASSWORD

Configuration (backend/.env):
    # Option A – Brevo (recommended for cloud)
    BREVO_API_KEY = xkeysib-...

    # Option B – Gmail SMTP
    SMTP_SERVER   = smtp.gmail.com
    SMTP_PORT     = 587
    SMTP_USERNAME = your-email@gmail.com
    SMTP_PASSWORD = your-app-password
    EMAIL_FROM    = "Metis Hire <your-email@gmail.com>"

If neither is configured the module prints a warning and returns
gracefully (no crash) — safe for development.
"""

import os
import json
import smtplib
import logging
import urllib.request
import urllib.error
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, List

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────
_SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
_SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
_SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
_EMAIL_FROM = os.getenv("EMAIL_FROM", "Metis Hire <noreply@metishire.com>")

_BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")

_SMTP_ENABLED = bool(_SMTP_USERNAME and _SMTP_PASSWORD)
_BREVO_ENABLED = bool(_BREVO_API_KEY)
_ENABLED = _SMTP_ENABLED or _BREVO_ENABLED

if not _ENABLED:
    print(
        "[EMAIL] SMTP credentials not configured. "
        "Set SMTP_USERNAME and SMTP_PASSWORD in backend/.env to enable email notifications."
    )
elif _BREVO_ENABLED:
    print("[EMAIL] Using Brevo API for email delivery.")
else:
    print(f"[EMAIL] Using SMTP ({_SMTP_SERVER}:{_SMTP_PORT}) for email delivery.")
# ─────────────────────────────────────────────────────────────────────────────


def _send_via_brevo(to: str, subject: str, html_body: str) -> bool:
    """Send email using Brevo (Sendinblue) HTTP API — no SMTP ports needed."""
    try:
        payload = json.dumps({
            "sender": {"name": "Metis Hire", "email": _SMTP_USERNAME or "metishire2.0@gmail.com"},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html_body,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload,
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "api-key": _BREVO_API_KEY,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                print(f"[EMAIL] Sent via Brevo to {to}: {subject}")
                return True
            else:
                print(f"[EMAIL] Brevo returned status {resp.status}")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[EMAIL] Brevo API error {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[EMAIL] Brevo send failed: {e}")
        return False


def _send_via_smtp(
    to: str, subject: str, html_body: str,
    attachments: Optional[List[tuple]] = None
) -> bool:
    """Send email using SMTP (Gmail)."""
    try:
        msg = MIMEMultipart("mixed")
        msg["From"] = _EMAIL_FROM
        msg["To"] = to
        msg["Subject"] = subject

        msg.attach(MIMEText(html_body, "html"))

        if attachments:
            for filename, file_bytes in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(file_bytes)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
                msg.attach(part)

        with smtplib.SMTP(_SMTP_SERVER, _SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(_SMTP_USERNAME, _SMTP_PASSWORD)
            server.sendmail(_EMAIL_FROM, to, msg.as_string())

        print(f"[EMAIL] Sent via SMTP to {to}: {subject}")
        return True

    except Exception as e:
        print(f"[EMAIL] SMTP send failed to {to}: {e}")
        return False


def _send(
    to: str,
    subject: str,
    html_body: str,
    attachments: Optional[List[tuple]] = None
) -> bool:
    """
    Send an email using the best available transport.

    Priority: Brevo API → SMTP → skip.
    """
    if not _ENABLED:
        print(f"[EMAIL] (not sent — no transport configured) To: {to} | Subject: {subject}")
        return False

    # Try Brevo first (works on Render where SMTP ports are blocked)
    if _BREVO_ENABLED:
        return _send_via_brevo(to, subject, html_body)

    # Fallback to SMTP
    if _SMTP_ENABLED:
        return _send_via_smtp(to, subject, html_body, attachments)

    return False


# ── Public notification functions ─────────────────────────────────────────────

def send_application_received(
    candidate_email: str,
    candidate_name: str,
    job_title: str,
    resume_score: int
) -> bool:
    """Notify candidate that their application was received and evaluated."""
    subject = f"Your application for '{job_title}' — Received"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #1a1a2e; max-width:600px; margin:auto;">
      <div style="background: linear-gradient(135deg,#6c63ff,#4ecdc4); padding:30px; border-radius:12px 12px 0 0;">
        <h1 style="color:white; margin:0;">Metis Hire</h1>
      </div>
      <div style="padding:30px; background:#f8f9fa; border-radius:0 0 12px 12px;">
        <h2>Hi {candidate_name},</h2>
        <p>We've received your application for <strong>{job_title}</strong> and your resume has been evaluated.</p>
        <div style="background:#fff; border-left:4px solid #6c63ff; padding:16px; margin:20px 0; border-radius:6px;">
          <p style="margin:0;"><strong>Resume Score:</strong> {resume_score}/100</p>
        </div>
        <p>If you qualify, you'll be invited to the AI interview round. Stay tuned!</p>
        <p style="color:#666; font-size:12px;">Metis Hire — Intelligent Recruitment</p>
      </div>
    </body></html>
    """
    return _send(candidate_email, subject, html)


def send_interview_scheduled(
    candidate_email: str,
    candidate_name: str,
    job_title: str,
    ics_bytes: Optional[bytes] = None
) -> bool:
    """
    Notify candidate that their AI interview is ready to begin.

    Args:
        ics_bytes: Optional .ics calendar file bytes to attach
    """
    subject = f"Your AI Interview for '{job_title}' is Ready"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #1a1a2e; max-width:600px; margin:auto;">
      <div style="background: linear-gradient(135deg,#6c63ff,#4ecdc4); padding:30px; border-radius:12px 12px 0 0;">
        <h1 style="color:white; margin:0;">Metis Hire</h1>
      </div>
      <div style="padding:30px; background:#f8f9fa; border-radius:0 0 12px 12px;">
        <h2>Hi {candidate_name},</h2>
        <p>🎉 Congratulations! You've been shortlisted for the AI interview round for <strong>{job_title}</strong>.</p>
        <div style="background:#fff; border-left:4px solid #4ecdc4; padding:16px; margin:20px 0; border-radius:6px;">
          <p style="margin:0 0 8px;"><strong>What to expect:</strong></p>
          <ul style="margin:0; padding-left:20px;">
            <li>10 contextual questions based on your resume and the job</li>
            <li>Voice or text input — your choice</li>
            <li>Takes approximately 20–30 minutes</li>
          </ul>
        </div>
        <p>Log in to your Metis Hire account and click <strong>"Start Interview"</strong> to begin.</p>
        {('<p style="color:#888;font-size:12px;">📅 A calendar invite is attached for your convenience.</p>' if ics_bytes else '')}
        <p style="color:#666; font-size:12px;">Metis Hire — Intelligent Recruitment</p>
      </div>
    </body></html>
    """
    attachments = [("interview_invite.ics", ics_bytes)] if ics_bytes else None
    return _send(candidate_email, subject, html, attachments=attachments)


def send_application_accepted(
    candidate_email: str,
    candidate_name: str,
    job_title: str,
    final_score: Optional[float] = None
) -> bool:
    """Notify candidate that their application has been accepted."""
    subject = f"🎉 Congratulations — Offer for '{job_title}'"
    score_line = f"<p><strong>Final Score:</strong> {final_score:.1f}/100</p>" if final_score else ""
    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #1a1a2e; max-width:600px; margin:auto;">
      <div style="background: linear-gradient(135deg,#11998e,#38ef7d); padding:30px; border-radius:12px 12px 0 0;">
        <h1 style="color:white; margin:0;">Metis Hire</h1>
      </div>
      <div style="padding:30px; background:#f8f9fa; border-radius:0 0 12px 12px;">
        <h2>Hi {candidate_name},</h2>
        <p>🎊 We're thrilled to let you know that you have been <strong>selected</strong> for <strong>{job_title}</strong>!</p>
        {score_line}
        <p>The HR team will be in touch shortly with next steps. Congratulations!</p>
        <p style="color:#666; font-size:12px;">Metis Hire — Intelligent Recruitment</p>
      </div>
    </body></html>
    """
    return _send(candidate_email, subject, html)


def send_application_rejected(
    candidate_email: str,
    candidate_name: str,
    job_title: str
) -> bool:
    """Notify candidate that their application was not successful."""
    subject = f"Update on your application for '{job_title}'"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #1a1a2e; max-width:600px; margin:auto;">
      <div style="background: linear-gradient(135deg,#6c63ff,#4ecdc4); padding:30px; border-radius:12px 12px 0 0;">
        <h1 style="color:white; margin:0;">Metis Hire</h1>
      </div>
      <div style="padding:30px; background:#f8f9fa; border-radius:0 0 12px 12px;">
        <h2>Hi {candidate_name},</h2>
        <p>Thank you for your interest in <strong>{job_title}</strong> and for the time you invested in the process.</p>
        <p>After careful consideration, we regret to inform you that we will not be moving forward with your application at this time.</p>
        <p>We encourage you to apply for future openings that match your profile. Best of luck!</p>
        <p style="color:#666; font-size:12px;">Metis Hire — Intelligent Recruitment</p>
      </div>
    </body></html>
    """
    return _send(candidate_email, subject, html)


def send_interview_results_ready(
    candidate_email: str,
    candidate_name: str,
    job_title: str
) -> bool:
    """Notify candidate that interview results are available in their dashboard."""
    subject = f"Your Interview Results for '{job_title}' are Ready"
    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #1a1a2e; max-width:600px; margin:auto;">
      <div style="background: linear-gradient(135deg,#6c63ff,#4ecdc4); padding:30px; border-radius:12px 12px 0 0;">
        <h1 style="color:white; margin:0;">Metis Hire</h1>
      </div>
      <div style="padding:30px; background:#f8f9fa; border-radius:0 0 12px 12px;">
        <h2>Hi {candidate_name},</h2>
        <p>Your AI interview for <strong>{job_title}</strong> has been evaluated. You can view your results in your dashboard.</p>
        <p style="color:#666; font-size:12px;">Metis Hire — Intelligent Recruitment</p>
      </div>
    </body></html>
    """
    return _send(candidate_email, subject, html)
