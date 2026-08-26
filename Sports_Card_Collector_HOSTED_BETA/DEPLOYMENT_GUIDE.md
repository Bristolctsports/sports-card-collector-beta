# AI Sports Card Collector — Hosted Private Beta

This package is the version for sending testers **one website link**.

## What testers do

1. Open your website link.
2. Create an account with email/password.
3. Scan cards from their phone, tablet or computer.
4. Their cards and photos are private to their account.
5. They never see or enter your OpenAI API key.

## What you configure once

You need:

- An OpenAI API key (server-side only)
- A free/paid Supabase project for login, private database and private photo storage
- A hosting service capable of running Streamlit (for example Streamlit Community Cloud or another Python host)

## 1. Create a Supabase project

Create a project in Supabase.

Open **SQL Editor**, paste the entire contents of `SUPABASE_SETUP.sql`, and run it.

This creates:
- `cards` table
- Row Level Security so users can only access their own cards
- Private `card-photos` storage bucket
- Storage policies so users can only access their own photos

In Supabase project settings, find:
- Project URL
- anon/public API key

Do **not** use the Supabase service-role key in this app.

For a small invite-only beta, you may either:
- Keep email confirmation on (safer), or
- Temporarily turn email confirmation off so testers can create accounts immediately.

## 2. Add server secrets

Do not put secrets in `app.py`.

Your host should have these private secrets:

OPENAI_API_KEY
OPENAI_MODEL
SUPABASE_URL
SUPABASE_ANON_KEY
PHOTO_BUCKET

Use `.streamlit/secrets.toml.example` only as a template. Never publish a real `secrets.toml`.

## 3. Deploy

The repository/folder needs:

- `app.py`
- `requirements.txt`
- `.streamlit/config.toml`

Set the app entry file to `app.py`.

After deployment, the host gives you one HTTPS website address. That is the link you send testers.

## 4. Tester experience

The hosted app supports:
- Account creation/sign-in
- Private per-user collection
- iPhone/iPad/desktop camera or photo upload
- AI card identification
- Card-number image + checklist cross-check
- Find Value with exact-match protection
- Duplicate detection
- Quantity updates
- Card photo storage
- Search/filter/sort
- Card detail view
- Delete with confirmation
- CSV export

## Security model

- Your OpenAI API key lives only in server secrets.
- Testers do not receive your key.
- Supabase Row Level Security isolates collections by user ID.
- The card-photo bucket is private.
- The app uses each logged-in user's token when reading/writing their data.

## Beta warning

This is beta software. AI card identification and pricing can be wrong. Testers should verify important card details and values before buying, selling, grading or insuring cards.
