import type { AgentCommandResult, Draft, JobStatus, Lead, LeadStatus, ScrapeSource, ServiceCredit } from "./types";

export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") || "http://localhost:8000";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });

  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      data = { detail: text };
    }
  }

  if (!response.ok) {
    let detail = response.statusText;
    if (typeof data === "object" && data && "detail" in data) {
      const raw = (data as { detail: unknown }).detail;
      if (typeof raw === "string") detail = raw;
      else if (Array.isArray(raw) && raw[0] && typeof raw[0] === "object" && "msg" in raw[0]) {
        detail = String((raw[0] as { msg: unknown }).msg);
      } else {
        detail = String(raw);
      }
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }

  return data as T;
}

type SavedLead = {
  id: number;
  business_name: string;
  email: string;
  website: string;
  rating: string;
  contact_name?: string;
  job_title?: string;
  source?: string;
  status: string;
  ai_draft?: string;
  sent_at: string | null;
};

function mapStatus(status: string): LeadStatus {
  const value = status.trim().toLowerCase();
  if (value === "opened") return "opened";
  if (value === "sent") return "sent";
  if (value === "failed") return "failed";
  if (value === "dry-run tested" || value === "dryrun" || value === "dry-run") return "dryrun";
  return "pending";
}

function mapSavedLead(row: SavedLead): Lead {
  return {
    id: String(row.id),
    name: row.business_name,
    website: row.website,
    email: row.email,
    rating: row.rating || "",
    contactName: row.contact_name || "",
    title: row.job_title || "",
    source: row.source || "",
    status: mapStatus(row.status),
    aiDraft: row.ai_draft || "",
  };
}

export function parseAiDraft(aiDraft: string): Draft {
  const text = (aiDraft || "").trim();
  if (!text) return { subject: "", body: "" };
  const lines = text.split("\n");
  const first = (lines[0] || "").trim();
  if (first.toLowerCase().startsWith("subject:")) {
    return {
      subject: first.slice(first.indexOf(":") + 1).trim(),
      body: lines.slice(1).join("\n").replace(/^\n/, ""),
    };
  }
  return { subject: "", body: text };
}

export function isCampaignEligible(lead: Lead): boolean {
  return lead.status !== "sent" && lead.status !== "opened";
}

export async function fetchSavedLeads(): Promise<Lead[]> {
  const rows = await request<SavedLead[]>("/api/leads", { method: "GET" });
  return rows.map(mapSavedLead);
}

export async function fetchJobs(): Promise<JobStatus> {
  return request("/api/jobs", { method: "GET" });
}

export async function scrapeLeads(
  query: string,
  source: ScrapeSource = "auto",
): Promise<{ message: string; query: string; source?: string }> {
  return request("/api/scrape", {
    method: "POST",
    body: JSON.stringify({ query, source }),
  });
}

export async function generateDraft(leadId: string): Promise<Draft & { ai_draft?: string }> {
  return request(`/api/generate-draft/${leadId}`, { method: "POST" });
}

export async function updateDraft(leadId: string, draft: Draft): Promise<void> {
  await request("/api/update-draft", {
    method: "POST",
    body: JSON.stringify({
      lead_id: Number(leadId),
      subject: draft.subject,
      body: draft.body,
    }),
  });
}

export async function startCampaign(
  leadIds: string[],
  dryRun: boolean,
): Promise<{ message: string; warnings: string[] }> {
  const data = await request<{ message: string; warnings?: string[] }>("/api/start-campaign", {
    method: "POST",
    body: JSON.stringify({
      lead_ids: leadIds.map((id) => Number(id)),
      dry_run: dryRun,
    }),
  });
  return { message: data.message || "Campaign started", warnings: data.warnings || [] };
}

export async function fetchCredits(): Promise<ServiceCredit[]> {
  return request("/api/credits", { method: "GET" });
}

export async function runAgentCommand(prompt: string): Promise<AgentCommandResult> {
  return request("/api/agent/command", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
}
