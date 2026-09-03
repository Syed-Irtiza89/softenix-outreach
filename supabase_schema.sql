-- Softenix Solution — outreach_leads
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor → New query).

create table if not exists public.outreach_leads (
    id uuid primary key default gen_random_uuid(),
    business_name text not null,
    email text not null,
    website text,
    google_review_score text,
    observation text,
    status text not null default 'Pending'
        check (status in (
            'Pending',
            'Sent',
            'Failed',
            'Follow-up 1 Sent',
            'Follow-up Failed',
            'Replied',
            'Do Not Contact',
            'Interested',
            'Not Interested',
            'Meeting Requested',
            'Out of Office',
            'Spam'
        )),
    sent_at timestamptz,
    followup_sent_at timestamptz,
    error_log text,
    subject text,
    message_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists outreach_leads_email_lower_idx
    on public.outreach_leads (lower(email));

create index if not exists outreach_leads_status_idx
    on public.outreach_leads (status);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists outreach_leads_set_updated_at on public.outreach_leads;
create trigger outreach_leads_set_updated_at
    before update on public.outreach_leads
    for each row
    execute procedure public.set_updated_at();

alter table public.outreach_leads enable row level security;

-- The Python script should use the service_role key, which bypasses RLS.
-- Do not expose that key in a browser or public repo.

comment on table public.outreach_leads is
    'Cold outreach queue. outreach.py sends Pending; followup.py bumps Sent rows after 3 days.';

-- If this table already existed, add follow-up columns and widen the status check:
alter table public.outreach_leads add column if not exists followup_sent_at timestamptz;
alter table public.outreach_leads add column if not exists message_id text;

alter table public.outreach_leads drop constraint if exists outreach_leads_status_check;
alter table public.outreach_leads add constraint outreach_leads_status_check
    check (status in (
        'Pending',
        'Sent',
        'Failed',
        'Follow-up 1 Sent',
        'Follow-up Failed',
        'Replied',
        'Do Not Contact',
        'Interested',
        'Not Interested',
        'Meeting Requested',
        'Out of Office',
        'Spam'
    ));

-- Optional sample rows (replace with real leads before sending):
-- insert into public.outreach_leads
--     (business_name, email, website, google_review_score, observation, status)
-- values
--     ('Harbor & Pine Cafe', 'owner@example.com', 'https://harborandpine.example', '4.6', 'No website listed on Google Maps', 'Pending');
