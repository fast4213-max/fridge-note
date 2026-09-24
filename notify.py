"""
notify.py - 期限チェックとDiscord通知（Supabase版）

GitHub Actions から実行される。
環境変数:
  SUPABASE_URL         : SupabaseプロジェクトURL
  SUPABASE_SERVICE_KEY : Supabase Service Role Key
  DISCORD_WEBHOOK_URL  : Discord Webhook URL
"""

import os
import sys
import time
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

# GitHub Actions のランナーはUTCで動作するため、日本時間の日付を明示的に使う
# （UTC基準のままだと日本時間0時台の実行で「今日」が1日ずれる）
JST = ZoneInfo("Asia/Tokyo")

# ── 定数 ──────────────────────────────────────────────
REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "DISCORD_WEBHOOK_URL")
_missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if _missing:
    # Secrets 未設定だと KeyError のトレースバックだけで原因が分かりにくいため明示する
    sys.exit(f"環境変数が未設定です: {', '.join(_missing)}（GitHub Secrets を確認してください）")

SUPABASE_URL         = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
DISCORD_WEBHOOK_URL  = os.environ["DISCORD_WEBHOOK_URL"]
TABLE_NAME           = "food_items"

# 通知タイミング（日前）。緊急度が高い順（小さい順）に定義する
# → get_notify_target で最も緊急な通知を優先して返すため
NOTIFY_DAYS  = [0, 1, 3, 7, 30]
NOTIFY_FLAGS = {
    30: "notified30",
    7:  "notified7",
    3:  "notified3",
    1:  "notified1",
    0:  "notified0",   # 当日（期限切れ含む）
}

# 1回の実行で送れる最大件数（超えた分は次回へ繰り越し）
MAX_NOTIFY = 30

# Discordは1メッセージに最大10個のEmbedを載せられる（合計6000文字まで）
# → 10件ずつまとめて送ることで、30件でも送信は3回で済む
EMBEDS_PER_MESSAGE = 10

# Discord送信間隔（レートリミット対策）
SEND_INTERVAL_SEC = 1.0

# Discord Embed カラー
COLOR_URGENT  = 0xC62828  # 当日・期限切れ（赤）
COLOR_WARN    = 0xBF5000  # 1〜3日前（オレンジ）
COLOR_CAUTION = 0x8A6D00  # 4〜7日前（黄）
COLOR_NOTICE  = 0x1565C0  # 8〜30日前（青）

ZONE_NAMES = {"fridge": "冷蔵庫", "freezer": "冷凍庫", "shelf": "常温棚"}

# ── ロギング ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


# ── Supabase ヘルパー ─────────────────────────────────

def sb_headers() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }

def sb_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


def fetch_all_items() -> list[dict]:
    """food_items テーブルを期限昇順で全件取得する。"""
    res = requests.get(
        sb_url(TABLE_NAME),
        headers=sb_headers(),
        params={"select": "*", "order": "expiry.asc"},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()


def delete_item(item_id: int) -> None:
    """IDで1行削除する。"""
    res = requests.delete(
        sb_url(TABLE_NAME),
        headers=sb_headers(),
        params={"id": f"eq.{item_id}"},
        timeout=10,
    )
    res.raise_for_status()


def update_notify_flags(item_id: int, flags: dict) -> None:
    """通知済みフラグを更新する。例: {"notified1": True}"""
    res = requests.patch(
        sb_url(TABLE_NAME),
        headers=sb_headers(),
        params={"id": f"eq.{item_id}"},
        json=flags,
        timeout=10,
    )
    res.raise_for_status()


# ── 通知判定 ──────────────────────────────────────────

def get_notify_target(item: dict, today: date) -> int | None:
    """
    そのアイテムで今日送るべき通知の「日前」を返す。
    複数のタイミングが重なる場合は最も緊急度の高い1件だけを返す。
    通知不要なら None を返す。

    ポイント：
    - notified30/7/3/1/0 が True のものはスキップ（送信済み）
    - フラグ列がDBに無いもの（notified0 追加前のテーブル）は判定しない
    - diff > days のものはスキップ（まだそのタイミングではない）
      例：diff=10 で days=7 なら、まだ7日前ではないのでスキップ
    """
    try:
        expiry = date.fromisoformat(item["expiry"])
    except (KeyError, TypeError, ValueError):  # 期限が空(None)・不正な形式
        return None

    diff = (expiry - today).days  # 正：まだ先、負：期限切れ

    # NOTIFY_DAYS は [0, 1, 3, 7, 30] の順 → 最も緊急な通知（最小days）を優先して返す
    # 例：diff=2 なら days=0,1はまだ、days=3でマッチ → 3日前通知を返す
    # 例：diff=0 なら days=0でマッチ → 当日通知を返す
    for days in NOTIFY_DAYS:
        flag_key = NOTIFY_FLAGS[days]
        if flag_key not in item:
            # 列が未作成のまま送ると、フラグ更新に失敗して毎回同じ通知が飛ぶため
            continue
        already_sent = item.get(flag_key, False)
        is_time      = diff <= days  # 今日がそのタイミング以降になっている

        if is_time and not already_sent:
            return days  # この通知を送るべき

    return None  # 送るものなし


# ── Discord ───────────────────────────────────────────

def build_embed(item: dict, days_left: int) -> dict:
    """Discord Embed オブジェクトを組み立てる。"""
    zone_name = ZONE_NAMES.get(item.get("zone", ""), item.get("zone", ""))
    name      = item.get("name") or "（名前なし）"
    expiry    = item.get("expiry", "")

    if days_left < 0:
        label = f"⚠️ 期限切れ（{abs(days_left)}日超過）"
        color = COLOR_URGENT
    elif days_left == 0:
        label = "🔴 今日が期限！"
        color = COLOR_URGENT
    elif days_left == 1:
        label = "🔴 明日が期限！"
        color = COLOR_URGENT
    elif days_left <= 3:
        label = f"🟠 期限まであと {days_left} 日"
        color = COLOR_WARN
    elif days_left <= 7:
        label = f"🟡 期限まであと {days_left} 日"
        color = COLOR_CAUTION
    else:
        label = f"🔵 期限まであと {days_left} 日"
        color = COLOR_NOTICE

    # Embedのタイトル上限は256文字。超えると400エラーになり、同じメッセージに
    # まとめた10件すべてが毎回送れなくなるため、長すぎる品名は切り詰める
    title = f"{name}（{zone_name}）"
    if len(title) > 256:
        title = title[:255] + "…"

    return {
        "title":       title,
        "description": label,
        "color":       color,
        "footer":      {"text": f"期限：{expiry}"},
    }


# 送信結果の種類
SEND_OK, SEND_RETRY_LATER, SEND_BAD_PAYLOAD, SEND_FATAL = "ok", "retry_later", "bad_payload", "fatal"


def _webhook_url() -> str:
    """?wait=true を付けて、送信結果（200/エラー内容）を確実に受け取れるようにする。"""
    if "wait=" in DISCORD_WEBHOOK_URL:
        return DISCORD_WEBHOOK_URL
    sep = "&" if "?" in DISCORD_WEBHOOK_URL else "?"
    return f"{DISCORD_WEBHOOK_URL}{sep}wait=true"


def _retry_after(res) -> float:
    try:
        return float(res.json().get("retry_after"))
    except (ValueError, TypeError, AttributeError):
        try:
            return float(res.headers.get("Retry-After", 1))
        except ValueError:
            return 1.0


def send_discord(embeds: list[dict], retries: int = 5) -> str:
    """
    Discord Webhookに送信する（Embedは最大10個まで）。結果の種類を返す。
      SEND_OK          : 送信成功
      SEND_RETRY_LATER : 429・5xx・通信エラーがリトライしても解消しない（次回再送）
      SEND_BAD_PAYLOAD : 400など。中身が不正で何度送っても通らない
      SEND_FATAL       : 401/403/404。Webhook URL が無効
    """
    for attempt in range(retries):
        try:
            res = requests.post(_webhook_url(), json={"embeds": embeds}, timeout=15)
        except requests.RequestException as e:
            log.warning("Discord通信エラー（%d回目）: %s", attempt + 1, e)
            time.sleep(2 ** attempt)
            continue

        if 200 <= res.status_code < 300:
            # 残り送信可能回数が0なら、制限が解除されるまで待ってから次へ進む
            if res.headers.get("X-RateLimit-Remaining") == "0":
                try:
                    wait = float(res.headers.get("X-RateLimit-Reset-After", 1))
                except ValueError:
                    wait = 1.0
                log.info("レートリミット残り0 → %.1f秒待機", wait)
                time.sleep(wait + 0.2)
            return SEND_OK

        if res.status_code == 429:
            # レートリミット：指定秒数待って同じ内容を再送する
            wait = _retry_after(res)
            log.warning("Discordレートリミット → %.1f秒待って再送", wait)
            time.sleep(wait + 0.2)
            continue

        if res.status_code >= 500:
            log.warning("Discordサーバーエラー HTTP %d（%d回目）", res.status_code, attempt + 1)
            time.sleep(2 ** attempt)
            continue

        if res.status_code in (401, 403, 404):
            log.error("Webhook URL が無効です: HTTP %d / %s", res.status_code, res.text[:200])
            return SEND_FATAL

        log.error("Discord送信失敗（内容が不正）: HTTP %d / %s", res.status_code, res.text[:500])
        return SEND_BAD_PAYLOAD

    return SEND_RETRY_LATER


def mark_sent(item: dict, notify_days: int) -> None:
    """
    送信成功 → 送ったフラグ + それより緊急度の低い（日数が大きい）未送信フラグも
    まとめて True にする（例：前日通知を送ったなら3日前・7日前・30日前もスキップ）
    """
    flags_to_set = {}
    for d, flag_key in NOTIFY_FLAGS.items():
        if d >= notify_days and flag_key in item and not item.get(flag_key, False):
            flags_to_set[flag_key] = True
    try:
        update_notify_flags(item["id"], flags_to_set)
    except Exception as e:
        # フラグ更新失敗は次回重複送信の可能性があるが、処理は続行する
        log.warning("フラグ更新エラー（id=%s）: %s", item["id"], e)


# ── メイン ────────────────────────────────────────────

def main():
    log.info("=== 通知処理 開始 ===")
    today = datetime.now(JST).date()
    log.info("実行日: %s (JST)", today.isoformat())

    # ① 全アイテム取得
    try:
        items = fetch_all_items()
    except Exception as e:
        log.error("Supabaseからのデータ取得に失敗: %s", e)
        sys.exit(1)
    log.info("取得件数: %d件", len(items))

    # ② チェック済みアイテムを削除
    checked_items = [i for i in items if i.get("checked")]
    for item in checked_items:
        try:
            delete_item(item["id"])
            log.info("チェック済み削除: %s", item.get("name"))
        except Exception as e:
            # 削除失敗は警告のみ。通知処理は続行する
            log.warning("削除エラー（id=%s）: %s", item["id"], e)
    if checked_items:
        log.info("チェック済み削除: 計%d件", len(checked_items))

    # ③ 通知対象を収集（期限昇順 = すでにfetch時にソート済み）
    active_items = [i for i in items if not i.get("checked")]
    if active_items and "notified0" not in active_items[0]:
        log.warning("notified0 列がありません → 当日通知はスキップします（README の追加SQLを実行してください）")

    # (item, days_left, notify_days) のリストを作る
    targets: list[tuple[dict, int, int]] = []
    for item in active_items:
        notify_days = get_notify_target(item, today)
        if notify_days is None:
            continue
        expiry    = date.fromisoformat(item["expiry"])
        days_left = (expiry - today).days
        targets.append((item, days_left, notify_days))

    log.info("通知対象: %d件（上限%d件）", len(targets), MAX_NOTIFY)

    # ④ Discord送信（最大MAX_NOTIFY件を10件ずつまとめて送る。超えた分は繰り越し・削除しない）
    #    フラグは送信成功した分だけ立てる → 失敗・未送信分は次回あらためて送られる
    sent_count = 0
    bad_count  = 0
    failed     = False
    to_send    = targets[:MAX_NOTIFY]

    for start in range(0, len(to_send), EMBEDS_PER_MESSAGE):
        chunk = to_send[start:start + EMBEDS_PER_MESSAGE]
        if start > 0:
            time.sleep(SEND_INTERVAL_SEC)

        for item, days_left, notify_days in chunk:
            log.info("送信: %s（あと%d日 / %d日前通知）", item.get("name"), days_left, notify_days)

        result = send_discord([build_embed(item, days_left) for item, days_left, _ in chunk])

        if result == SEND_OK:
            for item, _, notify_days in chunk:
                mark_sent(item, notify_days)
            sent_count += len(chunk)
            continue

        if result == SEND_BAD_PAYLOAD:
            # 不正な1件のせいで同じメッセージの全件が毎回止まらないよう、1件ずつ送り直す。
            # 通らなかった1件はフラグを立てずに残す（ログで原因を確認できる）
            log.warning("まとめ送信が拒否されたため1件ずつ送り直します")
            for item, days_left, notify_days in chunk:
                time.sleep(SEND_INTERVAL_SEC)
                r1 = send_discord([build_embed(item, days_left)])
                if r1 == SEND_OK:
                    mark_sent(item, notify_days)
                    sent_count += 1
                elif r1 == SEND_BAD_PAYLOAD:
                    log.error("送信できない品目をスキップ（id=%s / %s）", item.get("id"), item.get("name"))
                    bad_count += 1
                else:
                    result = r1
                    break
            else:
                continue

        # SEND_FATAL / SEND_RETRY_LATER → 中断して残りは次回へ
        log.error("送信失敗 → 処理を中断します（未送信分は次回へ繰り越し）")
        failed = True
        break

    # ⑤ 繰り越し件数をログ出力
    remaining = len(targets) - sent_count - bad_count
    log.info("送信成功: %d件", sent_count)
    if bad_count:
        log.error("内容が不正で送れなかった品目: %d件（上のログを確認してください）", bad_count)
        failed = True
    if remaining > 0:
        log.info("繰り越し（次回送信）: %d件", remaining)
    log.info("=== 通知処理 終了 ===")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
