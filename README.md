# れいぞうこノート セットアップ手順書

所要時間の目安：**約30〜40分**

---

## 必要なもの（すべて無料）

| サービス | 用途 |
|---|---|
| Supabase | データベース（食品データの保存） |
| GitHub | ファイルホスティング・自動実行 |
| Discord | 期限通知の受け取り |
| cron-job.org | 12時間ごとの定期実行 |

---

## STEP 1：Supabase のセットアップ

### 1-1. アカウント作成

1. https://supabase.com を開く
2. 「**Start your project**」→ GitHubアカウントでサインアップ（推奨）

### 1-2. プロジェクト作成

1. ダッシュボードで「**New project**」をクリック
2. 以下を入力：
   - Name：`fridge-note`
   - Database Password：任意の強いパスワード（メモしておく）
   - Region：`Northeast Asia (Tokyo)`
3. 「**Create new project**」→ 1〜2分待つ

### 1-3. テーブルを作る

1. 左メニューの「**SQL Editor**」→「**New query**」
2. `setup.sql` の内容を全文貼り付けて「**Run**」
3. 「Success」と表示されればOK

### 1-4. 接続情報を控える

左メニュー「**Project Settings**」→「**API**」を開き、以下をメモ：

| 名前 | 場所 | 用途 |
|---|---|---|
| Project URL | 「Project URL」欄 | index.html・GitHub Secretsに使う |
| anon public key | 「anon public」行 | index.html に書き込む |
| service_role key | 「service_role」行 | GitHub Secretsのみに登録（公開禁止） |

---

## STEP 2：index.html に接続情報を書き込む

`index.html` をテキストエディタで開き、先頭付近を以下のように書き換える：

```javascript
const SUPABASE_URL  = 'https://XXXXXXXXXXXXXXXX.supabase.co'; // ← Project URL
const SUPABASE_ANON = 'eyJXXXXXXXXXXXXXX...';                // ← anon public key
```

書き換えたら保存する。

> ℹ️ anon keyはURLとセットで使うもので、それ単体では悪用できない設計です。
> GitHub PagesでHTMLが公開されていても問題ありません。

---

## STEP 3：GitHub リポジトリを作る

1. https://github.com にログイン
2. 右上「**+**」→「**New repository**」
3. 設定：
   - Repository name：`fridge-note`
   - Visibility：**Public**（必須）
   - ✅「Add a README file」にチェック
4. 「**Create repository**」をクリック

---

## STEP 4：ファイルをアップロードする

### 4-1. index.html と notify.py をアップロード

1. リポジトリのトップで「**Add file**」→「**Upload files**」
2. `index.html`（STEP2で編集済み）と `notify.py` を一緒にドラッグ＆ドロップ
3. 「**Commit changes**」をクリック

### 4-2. ワークフローファイルを作成

1. 「**Add file**」→「**Create new file**」
2. ファイル名欄に `.github/workflows/notify.yml` と入力
   （スラッシュを入力するとフォルダが自動作成される）
3. `notify.yml` の内容を全文貼り付け
4. 「**Commit changes**」をクリック

---

## STEP 5：GitHub Secrets を登録する

1. リポジトリの「**Settings**」→「**Secrets and variables**」→「**Actions**」
2. 「**New repository secret**」で以下の3つを登録：

| Secret名 | 値 |
|---|---|
| `SUPABASE_URL` | STEP1-4 の Project URL |
| `SUPABASE_SERVICE_KEY` | STEP1-4 の service_role key |
| `DISCORD_WEBHOOK_URL` | STEP6 で作成する Webhook URL |

---

## STEP 6：Discord Webhook を作成する

1. Discordで通知を受け取りたいサーバーを開く
2. 通知用チャンネルの歯車アイコン（チャンネル設定）を開く
3. 「**連携サービス**」→「**ウェブフック**」→「**新しいウェブフック**」
4. 名前を「れいぞうこノート」に変更（任意）
5. 「**ウェブフックURLをコピー**」
6. コピーしたURLを STEP5 の `DISCORD_WEBHOOK_URL` に登録する

---

## STEP 7：GitHub Pages を有効にする

1. リポジトリの「**Settings**」→「**Pages**」
2. Branch を `main` / `/(root)` に設定 → 「**Save**」
3. 数分後に以下のURLでアクセス可能になる：
   `https://[GitHubユーザー名].github.io/fridge-note/`

---

## STEP 8：cron-job.org を設定する

### 8-1. GitHub用PATを発行する（cron専用）

cron-job.orgがGitHub Actionsを起動するためのトークンです。

1. GitHubで右上アイコン →「**Settings**」→「**Developer settings**」
2. 「**Personal access tokens**」→「**Fine-grained tokens**」→「**Generate new token**」
3. 設定：
   - Token name：`cron-fridge-trigger`
   - Expiration：1年（任意）
   - Repository access：`Only select repositories` → `fridge-note` を選択
   - Repository permissions → **Contents：Read and write**
4. 「**Generate token**」→ 表示されたトークンを**必ずメモ**（この画面限り）

### 8-2. cron-job.org でジョブを作成する

1. https://cron-job.org でアカウント作成（無料）
2. ダッシュボードで「**CREATE CRONJOB**」をクリック
3. 以下のように設定：

#### 基本設定

| 項目 | 値 |
|---|---|
| Title | れいぞうこノート 期限チェック |
| URL | `https://api.github.com/repos/[ユーザー名]/fridge-note/dispatches` |
| Request method | `POST` |

#### Headers タブ（重要）

| Header名 | 値 |
|---|---|
| `Content-Type` | `application/json` |
| `Authorization` | `Bearer [8-1で発行したトークン]` |
| `User-Agent` | `cron-job.org` |
| `Accept` | `application/vnd.github+json` |

> ⚠️ `Authorization` と `User-Agent` は必須です。どちらかが欠けるとGitHub APIが拒否します。

#### Body

```json
{"event_type": "check-expiry"}
```

#### スケジュール

`0 0,12 * * *`（毎日0時・12時）

4. 「**CREATE**」をクリック

---

## STEP 9：動作確認

### Webアプリを確認

1. `https://[ユーザー名].github.io/fridge-note/` を開く
2. 品名・期限を入力して「＋追加」をタップ
3. Supabaseの「**Table Editor**」→「`food_items`」にデータが入っていればOK

### GitHub Actions を手動で動かしてテスト

1. GitHubリポジトリの「**Actions**」タブ
2. 左メニュー「期限チェック・Discord通知」を選択
3. 「**Run workflow**」→「**Run workflow**」をクリック
4. ✅ で完了すれば成功
5. 期限30日以内のアイテムがあればDiscordに通知が届く

### cron-job.org をテスト

1. ダッシュボードでジョブを選択
2. 「**Run now**」をクリック
3. レスポンスが `204 No Content` であれば正常

---

## 通知仕様まとめ

| タイミング | 条件 |
|---|---|
| 30日前 | 期限まで30日以下になったとき（1回のみ） |
| 7日前  | 期限まで7日以下になったとき（1回のみ） |
| 3日前  | 期限まで3日以下になったとき（1回のみ） |
| 前日   | 期限まで1日以下になったとき（1回のみ） |

**登録時の注意：**
期限まで5日のものを登録した場合、30日前・7日前はスキップされ、
3日前になったときに初めて通知が送られます。

**10件上限：**
1回の実行で最大10件まで送信。超えた分は次回（12時間後）に繰り越されます。
削除はされません。

---

## Secrets・設定値まとめ

| 名前 | 用途 | 設定場所 |
|---|---|---|
| `SUPABASE_URL` | notify.py がDBにアクセス | GitHub Secrets |
| `SUPABASE_SERVICE_KEY` | notify.py の書き込み権限 | GitHub Secrets |
| `DISCORD_WEBHOOK_URL` | Discord通知の送信先 | GitHub Secrets |
| PATトークン（別途） | cron-job.org が Actions を起動 | cron-job.org Authorizationヘッダー |

---

## ファイル構成（完成後）

```
fridge-note/（GitHubリポジトリ）
├── index.html          ← Webアプリ（GitHub Pagesで公開）
├── notify.py           ← 通知スクリプト
├── setup.sql           ← Supabaseテーブル作成SQL（初回のみ使用）
└── .github/
    └── workflows/
        └── notify.yml  ← GitHub Actions 定義
```

データはすべてSupabase上に保存されます。

---

## よくあるトラブル

### アプリを開いてもデータが表示されない

- `index.html` の `SUPABASE_URL` と `SUPABASE_ANON` が正しいか確認
- `setup.sql` が実行済みか確認（Supabase Table Editorで `food_items` テーブルがあるか）
- RLSのポリシーが設定されているか確認（`setup.sql` を再実行）

### GitHub Actions が失敗する

- Secretsの名前が完全一致しているか確認（大文字小文字に注意）
- Actionsログの赤い箇所でエラー内容を確認

### Discord通知が届かない

- `DISCORD_WEBHOOK_URL` が正しいか確認
- 期限30日以内のアイテムが登録されているか確認
- Supabaseで `notified30`〜`notified1` がすべて `false` になっているか確認
  （`true` になっていたら手動で `false` に戻すとテスト可能）

### cron-job.org が 401 エラー

- `Authorization: Bearer [PAT]` のトークンが正しいか確認
- PATの有効期限が切れていないか確認
