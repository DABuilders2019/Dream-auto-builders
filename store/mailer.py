"""Order emails, sent through any SMTP mail service (Postmark, Resend, SendGrid, Google Workspace...).

Settings: SMTP_HOST, SMTP_PORT (default 587), SMTP_USERNAME, SMTP_PASSWORD, MAIL_FROM
("Dream Auto Builders <orders@yourdomain.com>"), ORDER_NOTIFY_EMAIL (where new-order alerts go).
Without SMTP_HOST and MAIL_FROM, emails are skipped and the order page notes it.
Emails are sent in the background so checkout never waits on the mail server.
"""
import email.message
import logging
import os
import smtplib
import threading

log = logging.getLogger("store.mail")
sent = []          # (to, subject) of emails sent, newest last; used by the tests


def configured():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("MAIL_FROM"))


def notify_address():
    return os.environ.get("ORDER_NOTIFY_EMAIL", "")


def send(to, subject, text, html=None, wait=False):
    if not to or not configured():
        return False
    msg = email.message.EmailMessage()
    msg["From"] = os.environ["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    t = threading.Thread(target=_deliver, args=(msg,), daemon=True)
    t.start()
    if wait:
        t.join(30)
    return True


def _deliver(msg):
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(os.environ["SMTP_HOST"], port, timeout=30) as s:
            if port != 465:
                s.starttls()
            if os.environ.get("SMTP_USERNAME"):
                s.login(os.environ["SMTP_USERNAME"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        sent.append((msg["To"], msg["Subject"]))
    except Exception:          # noqa: BLE001 - a mail problem must never break an order
        log.exception("could not send email %r to %s", msg["Subject"], msg["To"])


def test_send(to):
    """Send a test email right now and say what happened: (ok, message). For Admin > Email."""
    if not configured():
        return False, "SMTP_HOST and MAIL_FROM aren't both set in Render > Environment."
    msg = email.message.EmailMessage()
    msg["From"] = os.environ["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = "Dream Auto Builders test email"
    msg.set_content("This is a test email from your Dream Auto Builders website. If you can read this, email works.")
    host, port = os.environ["SMTP_HOST"].strip(), int((os.environ.get("SMTP_PORT") or "587").strip())
    try:
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(host, port, timeout=20) as s:
            if port != 465:
                s.starttls()
            if os.environ.get("SMTP_USERNAME"):
                s.login(os.environ["SMTP_USERNAME"].strip(), (os.environ.get("SMTP_PASSWORD") or "").strip())
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        return False, f"The email service refused the username/password (check SMTP_USERNAME and the re_ key in SMTP_PASSWORD): {e.smtp_error.decode(errors='replace') if isinstance(e.smtp_error, bytes) else e.smtp_error}"
    except smtplib.SMTPResponseException as e:
        err = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return False, f"The email service refused the email ({e.smtp_code}): {err}"
    except (OSError, smtplib.SMTPException) as e:
        return False, (f"Couldn't connect to {host} on port {port}: {e}. If this is a timeout, Render may be blocking "
                       "email (not allowed on the Free instance type), or SMTP_HOST/SMTP_PORT is wrong.")
    return True, f"Sent from {os.environ['MAIL_FROM']} to {to}. Check that inbox (and spam)."
