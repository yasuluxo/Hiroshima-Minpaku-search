# Hiroshima Minpaku Search 8.4

広島市の中区・南区・西区・東区を対象に、SUUMO / HOME'S / at home の賃貸情報を取得し、民泊候補を抽出する自動検索版です。

## 8.4での修正

- 過去に抽出済みの候補も、条件に合えば今回メールへ再掲載します。
- `candidate_history.json` に候補を累積保存します。同一物件は履歴上1件に統合します。
- HOME'S / at home は一覧URLの複数パターン、待機、スクロール、再試行を行い、短本文・リダイレクト系の取得失敗に耐えるようにしました。
- `javascript:void(0)` や相対リンクから実URLを取得できないものは物件URLとして保存・表示しません。
- `seen.json` と `price_history.json` は既存ファイルをそのまま使います。8.4 ZIPには上書き用の空ファイルを入れていません。
- 広島市公式の民泊届出・旅館業許可PDFを毎回取得して照合します。

## GitHubへの反映

既存リポジトリの次のファイルを置き換えてください。

- `search.py`
- `mailer.py`
- `config.py`
- `official_db.py`
- `requirements.txt`
- `README.md`
- `.github/workflows/daily.yml`

`candidate_history.json` が既にある場合は**上書きしないでください**。8.4はその履歴を読み込み、追記・更新します。

次のファイルも既存のものを維持してください。

- `seen.json`
- `price_history.json`

`diagnostics.json` と `official_db.json` は実行時に更新されます。

## GitHub Secrets

- `EMAIL_USER`
- `EMAIL_PASS`
- `EMAIL_TO`

## 注意

集合住宅については、広島市公式DBの住所または建物名が明確に一致したものだけを候補として扱う仕様です。町名だけの一致、建物名の部分一致、部屋番号だけの一致は採用しません。

また、公式DBとの一致は「現在の賃貸募集物件が自動的に民泊可能」という意味ではありません。最終的な用途・管理規約・契約条件等は個別確認が必要です。
