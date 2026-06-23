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
  created_at  timestamptz not null default now()
);

-- Row Level Security を有効化
alter table food_items enable row level security;

-- 家族内全員が読み書きできるポリシー
create policy "allow_all" on food_items
  for all using (true) with check (true);
