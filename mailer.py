import os
import smtplib
from email.mime.text import MIMEText


def yen(value):
    if value is None:
        return "不明"
    return f"{value:,}円"


def property_text(p, number):
    lines = []

    lines.append(
        f"{number}. {p.get('score', '')} "
        f"{p.get('area', '')} {yen(p.get('rent'))}"
    )

    lines.append(f"   {p.get('title', '')}")

    lines.append(
        f"   間取り：{p.get('layout', '')} / "
        f"面積：{p.get('size', '')}㎡"
    )

    if p.get("comment"):
        lines.append(
            f"   注目点：{p.get('comment')}"
        )

    if p.get("old_price"):
        lines.append(
            f"   旧家賃：{yen(p.get('old_price'))}"
        )

    lines.append(
        f"   {p.get('url', '')}"
    )

    lines.append("")

    return lines


def send_report(data):

    email_user = os.environ["EMAIL_USER"]
    email_pass = os.environ["EMAIL_PASS"]
    email_to = os.environ["EMAIL_TO"]

    lines = []

    lines.append("広島 民泊候補物件 第7弾レポート")
    lines.append("=" * 42)
    lines.append("")

    lines.append(
        f"全サイト取得後の重複除去："
        f"{data.get('total_retrieved', 0)}件"
    )

    lines.append(
        f"家賃10万円以下の候補："
        f"{data.get('candidates', 0)}件"
    )

    lines.append(
        f"今回の新着："
        f"{len(data.get('new', []))}件"
    )

    lines.append(
        f"家賃値下げ："
        f"{len(data.get('price_down', []))}件"
    )

    lines.append("")

    # --------------------------------
    # サイト別取得状況
    # --------------------------------

    lines.append("【サイト別取得状況】")
    lines.append("-" * 42)

    diagnostics = data.get("diagnostics", [])

    for item in diagnostics:

        lines.append(
            f"{item.get('source', '')} / "
            f"{item.get('area', '')}："
            f"raw={item.get('raw', 0)} "
            f"parsed={item.get('parsed', 0)} "
            f"status={item.get('status', '')}"
        )

        if item.get("error"):
            lines.append(
                f"  エラー：{item.get('error')}"
            )

    # --------------------------------
    # 取得エラー
    # --------------------------------

    failures = []

    for item in diagnostics:

        if item.get("status") != "OK":
            failures.append(item)

    if failures:

        lines.append("")
        lines.append(
            "⚠ 取得0件・タイムアウト・エラーがあります。"
        )

        lines.append(
            "これは「物件が0件」ではなく、"
            "「取得できなかった可能性」があります。"
        )

    # --------------------------------
    # 新着
    # --------------------------------

    lines.append("")
    lines.append("【新着】")
    lines.append("-" * 42)

    new_items = data.get("new", [])

    if not new_items:

        lines.append("該当なし")

    else:

        number = 1

        for p in new_items[:15]:

            lines.extend(
                property_text(p, number)
            )

            number += 1

    # --------------------------------
    # 値下げ
    # --------------------------------

    lines.append("")
    lines.append("【値下げ】")
    lines.append("-" * 42)

    price_down = data.get("price_down", [])

    if not price_down:

        lines.append("該当なし")

    else:

        number = 1

        for p in price_down[:15]:

            lines.extend(
                property_text(p, number)
            )

            number += 1

    # --------------------------------
    # 新着も値下げもない場合
    # 現在取得できている候補を表示
    # --------------------------------

    if not new_items and not price_down:

        lines.append("")
        lines.append(
            "【現在取得できている候補（サンプル）】"
        )

        lines.append("-" * 42)

        samples = data.get("sample", [])

        if not samples:

            lines.append("候補なし")

        else:

            number = 1

            for p in samples[:10]:

                lines.extend(
                    property_text(p, number)
                )

                number += 1

    # --------------------------------
    # 注意事項
    # --------------------------------

    lines.append("")
    lines.append("=" * 42)

    lines.append(
        "※「新着0件」と「取得0件」は別扱いです。"
    )

    lines.append(
        "※民泊利用の可否は物件ごとに"
        "貸主・管理会社・自治体等へ確認してください。"
    )

    body = "\n".join(lines)

    # --------------------------------
    # Gmail送信
    # --------------------------------

    message = MIMEText(
        body,
        "plain",
        "utf-8"
    )

    message["Subject"] = (
        f"広島民泊 第7弾 "
        f"候補{data.get('candidates', 0)}件 / "
        f"新着{len(new_items)}件"
    )

    message["From"] = email_user
    message["To"] = email_to

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465
    ) as smtp:

        smtp.login(
            email_user,
            email_pass
        )

        smtp.send_message(message)