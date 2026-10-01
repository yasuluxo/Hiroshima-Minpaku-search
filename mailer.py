from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formatdate

from config import EMAIL_PASS, EMAIL_TO, EMAIL_USER


def send_email(subject: str, body: str) -> None:
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        raise RuntimeError("EMAIL_USER / EMAIL_PASS / EMAIL_TO のいずれかが未設定です")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Date"] = formatdate(localtime=True)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.sendmail(EMAIL_USER, [x.strip() for x in EMAIL_TO.split(",") if x.strip()], msg.as_string())
