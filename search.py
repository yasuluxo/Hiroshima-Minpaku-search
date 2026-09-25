import os
import smtplib
from email.mime.text import MIMEText

body = """
【広島民泊物件テスト】

GitHub Actionsからメール送信成功！

次はSUUMO検索を実装します。
"""

msg = MIMEText(body)
msg["Subject"] = "広島 民泊物件 テスト"
msg["From"] = os.environ["EMAIL_USER"]
msg["To"] = os.environ["EMAIL_TO"]

with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
    smtp.login(os.environ["EMAIL_USER"], os.environ["EMAIL_PASS"])
    smtp.send_message(msg)

print("Mail Sent")
