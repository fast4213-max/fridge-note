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
from datetime import date

import requests

# ── 定数 ──────────────────────────────────────────────
SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
DISCORD_WEBHOOK_URL  = os.environ["DISCORD_WEBHOOK_URL"]
TABLE_NAME           = "food_items"

# 通知タイミング（日前）。緊急度が高い順（小さい順）に定義する
# → get_notify_target で最も緊急な通知を優先して返すため
NOTIFY_DAYS  = [1, 3, 7, 30]
NOTIFY_FLAGS = {
    30: "notified30",
    7:  "notified7",
    3:  "notified3",
    1:  "notified1",
}

# 1回の実行で送れる最大件数（超えた分は次回へ繰り越し）
MAX_NOTIFY = 10

# Discord送信間隔（レートリミット対策）
SEND_INTERVAL_SEC = 0.5

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
    - notified30/7/3/1 が True のものはスキップ（送信済み）
    - diff > days のものはスキップ（まだそのタイミングではない）
      例：diff=10 で days=7 なら、まだ7日前ではないのでスキップ
    """
    try:
        expiry = date.fromisoformat(item["expiry"])
    except (KeyError, ValueError):
        return None

    diff = (expiry - today).days  # 正：まだ先、負：期限切れ

    # NOTIFY_DAYS は [1, 3, 7, 30] の順 → 最も緊急な通知（最小days）を優先して返す
    # 例：diff=2 なら days=1はまだ（diff > 1）、days=3でマッチ → 3日前通知を返す
    # 例：diff=0 なら days=1でマッチ（diff <= 1）→ 前日通知を返す
    for days in NOTIFY_DAYS:
        flag_key = NOTIFY_FLAGS[days]
        already_sent = item.get(flag_key, False)
        is_time      = diff <= days  # 今日がそのタイミング以降になっている

        if is_time and not already_sent:
            return days  # この通知を送るべき

    return None  # 送るものなし


# ── Discord ───────────────────────────────────────────

def build_embed(item: dict, days_left: int) -> dict:
    """Discord Embed オブジェクトを組み立てる。"""
    zone_name = ZONE_NAMES.get(item.get("zone", ""), item.get("zone", ""))
    name      = item.get("name", "（名前なし）")
    expiry    = item.get("expiry", "")

    if days_left < 0:
        label = f"⚠️ 期限切れ（{abs(days_left)}日超過）"
        color = COLOR_URGENT
    elif days_left == 0:
        label = "🔴 今日が期限です！"
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

    return {
        "title":       f"{name}（{zone_name}）",
        "description": label,
        "color":       color,
        "footer":      {"text": f"期限：{expiry}"},
    }


def send_discord(embed: dict) -> bool:
    """Discord Webhookに送信する。成功:True / 失敗:False"""
    try:
        res = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
        )
        if res.status_code == 204:
            return True
        log.error("Discord送信失敗: HTTP %d / %s", res.status_code, res.text[:200])
        return False
    except requests.RequestException as e:
        log.error("Discord送信例外: %s", e)
        return False


# ── メイン ────────────────────────────────────────────

def main():
    log.info("=== 通知処理 開始 ===")
    today = date.today()
    log.info("実行日: %s", today.isoformat())

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

    # ④ Discord送信（最大MAX_NOTIFY件、超えた分は繰り越し・削除しない）
    sent_count = 0
    failed     = False

    for item, days_left, notify_days in targets[:MAX_NOTIFY]:
        embed = build_embed(item, days_left)
        log.info("送信: %s（あと%d日 / %d日前通知）", item.get("name"), days_left, notify_days)

        if not send_discord(embed):
            log.error("送信失敗 → 処理を中断します")
            failed = True
            break

        # 送信成功 → 送ったフラグ + それより緊急度の低い（日数が大きい）未送信フラグも
        # まとめて True にする（例：前日通知を送ったなら3日前・7日前・30日前もスキップ）
        flags_to_set = {}
        for d, flag_key in NOTIFY_FLAGS.items():
            if d >= notify_days and not item.get(flag_key, False):
                flags_to_set[flag_key] = True
        try:
            update_notify_flags(item["id"], flags_to_set)
        except Exception as e:
            # フラグ更新失敗は次回重複送信の可能性があるが、処理は続行する
            log.warning("フラグ更新エラー（id=%s）: %s", item["id"], e)

        sent_count += 1
        time.sleep(SEND_INTERVAL_SEC)

    # ⑤ 繰り越し件数をログ出力
    remaining = len(targets) - sent_count
    log.info("送信成功: %d件", sent_count)
    if remaining > 0:
        log.info("繰り越し（次回送信）: %d件", remaining)
    log.info("=== 通知処理 終了 ===")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
