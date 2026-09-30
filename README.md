# 広島民泊候補検索 第7.1弾

第7弾の修正版。主な変更点：
- 新着メールの15件制限を撤廃し、全件送信
- URLだけでなく住所・家賃・間取り・面積等を使って同一候補を統合
- 旧版のURL型seenキーも確認して移行時の全件新着化を防止
- diagnostics.jsonにページタイトルと本文文字数を保存
- SUUMO / HOME'S / アットホームを4区で確認

GitHub ActionsのSecrets：EMAIL_USER / EMAIL_PASS / EMAIL_TO
