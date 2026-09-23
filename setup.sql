-- ────────────────────────────────────────────
--  れいぞうこノート - テーブル初期セットアップ
--  Supabase の SQL Editor で一度だけ実行する
-- ────────────────────────────────────────────

-- テーブル作成
create table food_items (
  id          bigint primary key generated always as identity,
  name        text        not null,
  expiry      date        not null,
  zone        text        not null default 'fridge',
  checked     boolean     not null default false,
  notified30  boolean     not null default false,
  notified7   boolean     not null default false,
  notified3   boolean     not null default false,
  notified1   boolean     not null default false,
  notified0   boolean     not null default false,
  created_at  timestamptz not null default now()
);

-- Row Level Security を有効化
alter table food_items enable row level security;

-- 家族内全員が読み書きできるポリシー
create policy "allow_all" on food_items
  for all using (true) with check (true);

-- ────────────────────────────────────────────
--  【既存テーブル向け】当日通知の列を追加する
--  すでに上の create table を実行済みの場合はここだけ実行する
-- ────────────────────────────────────────────
-- alter table food_items add column if not exists notified0 boolean not null default false;
-- -- 既に期限を過ぎている品目に「期限切れ」通知がまとめて飛ばないよう送信済み扱いにする
-- update food_items set notified0 = true
--   where expiry < (now() at time zone 'Asia/Tokyo')::date;
