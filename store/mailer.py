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
