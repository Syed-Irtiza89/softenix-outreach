export type ScrapeSource = "auto" | "duckduckgo" | "no_website" | "yellowpages" | "google_maps";

export type LeadStatus = "pending" | "generating" | "sent" | "opened" | "failed" | "dryrun";

export type Lead = {
  id: string;
  name: string;
  website: string;
  email: string;
  rating: string;
  contactName: string;
  title: string;
  source: string;
  status: LeadStatus;
  aiDraft: string;
};

export type Draft = {
  subject: string;
  body: string;
};

export type ToastTone = "success" | "error" | "info";

export type ToastMessage = {
  id: number;
  tone: ToastTone;
  text: string;
};

export type ServiceCredit = {
  service: string;
  monthly_limit: number;
  daily_limit: number;
  used_month: number;
  used_today: number;
  remaining_month: number;
  remaining_day: number;
};

export type AgentCommandResult = {
  message: string;
  tool: string;
  saved: number;
  skipped: number;
  leads: Array<{
    contact_name?: string;
    job_title?: string;
    email?: string;
    business_name?: string;
    website?: string;
    source?: string;
  }>;
  tech_stack: string[];
  credits: ServiceCredit | null;
  credits_all: ServiceCredit[];
};

export type JobStatus = {
  scrape_running: boolean;
  campaign_running: boolean;
  last_scrape_source?: string;
  last_scrape_saved?: number;
  last_scrape_skipped?: number;
  last_scrape_error?: string;
  auto_send?: boolean;
  smtp_ready?: boolean;
  sent_today?: number;
  daily_cap?: number;
  in_send_window?: boolean;
  next_send_window?: string;
  autosend_error?: string;
  eastern_now?: string;
};
