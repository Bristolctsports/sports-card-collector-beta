-- Sports Card Collector Hosted Beta - Supabase setup
-- Run this once in Supabase SQL Editor.

create extension if not exists pgcrypto;

create table if not exists public.cards (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
    sport text,
    player text,
    year text,
    manufacturer text,
    set_name text,
    card_number text,
    rookie text,
    parallel_variation text,
    serial_number text,
    autograph text,
    relic text,
    grading_company text,
    grade text,
    condition text,
    quantity integer not null default 1 check (quantity > 0),
    purchase_price numeric,
    estimated_value numeric,
    last_sold_comp numeric,
    comp_date text,
    confidence numeric,
    needs_review boolean default false,
    front_photo_path text,
    back_photo_path text,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.cards enable row level security;

drop policy if exists "Users can read own cards" on public.cards;
create policy "Users can read own cards"
on public.cards for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own cards" on public.cards;
create policy "Users can insert own cards"
on public.cards for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can update own cards" on public.cards;
create policy "Users can update own cards"
on public.cards for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own cards" on public.cards;
create policy "Users can delete own cards"
on public.cards for delete
using (auth.uid() = user_id);

-- Private storage bucket for card photos.
insert into storage.buckets (id, name, public)
values ('card-photos', 'card-photos', false)
on conflict (id) do update set public = false;

drop policy if exists "Users can view own card photos" on storage.objects;
create policy "Users can view own card photos"
on storage.objects for select
using (
    bucket_id = 'card-photos'
    and (storage.foldername(name))[1] = auth.uid()::text
);

drop policy if exists "Users can upload own card photos" on storage.objects;
create policy "Users can upload own card photos"
on storage.objects for insert
with check (
    bucket_id = 'card-photos'
    and (storage.foldername(name))[1] = auth.uid()::text
);

drop policy if exists "Users can update own card photos" on storage.objects;
create policy "Users can update own card photos"
on storage.objects for update
using (
    bucket_id = 'card-photos'
    and (storage.foldername(name))[1] = auth.uid()::text
);

drop policy if exists "Users can delete own card photos" on storage.objects;
create policy "Users can delete own card photos"
on storage.objects for delete
using (
    bucket_id = 'card-photos'
    and (storage.foldername(name))[1] = auth.uid()::text
);

create index if not exists cards_user_id_idx on public.cards(user_id);
create index if not exists cards_player_idx on public.cards(player);
create index if not exists cards_set_name_idx on public.cards(set_name);
