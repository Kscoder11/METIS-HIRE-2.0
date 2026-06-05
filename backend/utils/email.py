"""
Email Notification Utility
===========================
Sends transactional emails to candidates and HR recruiters for key
recruitment lifecycle events.

Configuration (backend/.env):
    SMTP_SERVER   = smtp.gmail.com
    SMTP_PORT     = 587
    SMTP_USERNAME = your-email@gmail.com
    SMTP_PASSWORD = your-app-password     # Gmail App Password, not account password
    EMAIL_FROM    = "Metis Hire <your-email@gmail.com>"

If SMTP credentials are not configured the module prints a warning and
returns gracefully (no crash) — safe for development.
"""

import os
import smtplib
import logging
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
_ENABLED = bool(_SMTP_USERNAME and _SMTP_PASSWORD)

if not _ENABLED:
    logger.warning(
        "[EMAIL] SMTP credentials not configured. "
        "Set SMTP_USERNAME and SMTP_PASSWORD in backend/.env to enable email notifications."
    )
# ─────────────────────────────────────────────────────────────────────────────


def _send(
    to: str,
    subject: str,
    html_body: str,
    attachments: Optional[List[tuple]] = None
) -> bool:
    """
    Low-level email send helper.

    Args:
        to: Recipient email address
        subject: Email subject line
        html_body: HTML body content
        attachments: Optional list of (filename, bytes_content) tuples

    Returns:
        True if sent successfully, False otherwise
    """
    if not _ENABLED:
        logger.info(f"[EMAIL] (not sent — SMTP not configured) To: {to} | Subject: {subject}")
        return False

    try:
        msg = MIMEMultipart("mixed")
        msg["From"] = _EMAIL_FROM
        msg["To"] = to
        msg["Subject"] = subject

        # Attach HTML body
        msg.attach(MIMEText(html_body, "html"))

        # Attach files if any
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

        logger.info(f"[EMAIL] Sent to {to}: {subject}")
        return True

    except Exception as e:
        logger.error(f"[EMAIL] Failed to send to {to}: {e}")
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
