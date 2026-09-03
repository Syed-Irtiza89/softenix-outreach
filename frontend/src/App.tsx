import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchCredits,
  fetchJobs,
  fetchSavedLeads,
  generateDraft,
  isCampaignEligible,
  parseAiDraft,
  runAgentCommand,
  scrapeLeads,
  startCampaign,
  updateDraft,
} from "./api";
import { AgentCommand } from "./components/AgentCommand";
import { EmailModal } from "./components/EmailModal";
import { Header } from "./components/Header";
import { LeadsTable } from "./components/LeadsTable";
import { SearchSection } from "./components/SearchSection";
import { Toast } from "./components/Toast";
import type { Draft, JobStatus, Lead, ServiceCredit, ToastMessage } from "./types";

const DRY_RUN_KEY = "softenix_dry_run";

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function readDryRun(): boolean {
  try {
    const stored = localStorage.getItem(DRY_RUN_KEY);
    if (stored === null) return true;
    return stored === "true";
  } catch {
    return true;
  }
}

export default function App() {
  const [niche, setNiche] = useState("plumbers");
  const [city, setCity] = useState("USA");
  const [agentPrompt, setAgentPrompt] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [credits, setCredits] = useState<ServiceCredit[]>([]);
  const [searching, setSearching] = useState(false);
  const [agentRunning, setAgentRunning] = useState(false);
  const [techStack, setTechStack] = useState<string[]>([]);
  const [generatingId, setGeneratingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [activeLead, setActiveLead] = useState<Lead | null>(null);
  const [draft, setDraft] = useState<Draft>({ subject: "", body: "" });
  const [saving, setSaving] = useState(false);
  const [campaignStarting, setCampaignStarting] = useState(false);
  const [dryRun, setDryRun] = useState(readDryRun);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [jobs, setJobs] = useState<JobStatus | null>(null);

  const pushToast = useCallback((tone: ToastMessage["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, tone, text }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 4500);
  }, []);

  const refreshCredits = useCallback(async () => {
    const latest = await fetchCredits();
    setCredits(latest);
    return latest;
  }, []);

  const refreshLeads = useCallback(async () => {
    const latest = await fetchSavedLeads();
    setLeads(latest);
    setSelectedIds((current) => current.filter((id) => latest.some((lead) => lead.id === id && isCampaignEligible(lead))));
    return latest;
  }, []);

  useEffect(() => {
    Promise.all([refreshLeads(), refreshCredits()]).catch(() => {
      pushToast("info", "API is not reachable at http://localhost:8000. Start uvicorn, then refresh.");
    });
  }, [pushToast, refreshCredits, refreshLeads]);

  useEffect(() => {
    let cancelled = false;
    async function pollJobs() {
      try {
        const status = await fetchJobs();
        if (cancelled) return;
        setJobs(status);
        if (status.campaign_running) {
          await refreshLeads();
        }
      } catch {
        /* API may be restarting */
      }
    }
    void pollJobs();
    const timer = window.setInterval(() => {
      void pollJobs();
    }, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshLeads]);

  useEffect(() => {
    localStorage.setItem(DRY_RUN_KEY, String(dryRun));
  }, [dryRun]);

  const stats = useMemo(() => {
    const sent = leads.filter((lead) => lead.status === "sent" || lead.status === "opened").length;
    const pending = leads.length - sent;
    return { total: leads.length, pending, sent };
  }, [leads]);

  async function handleFindLeads() {
    setSearching(true);
    try {
      const started = await scrapeLeads(`${niche} in ${city}`, "auto");
      pushToast("info", `${started.message}. Rotating cities until 20 valid emails are saved.`);
      const deadline = Date.now() + 45 * 60 * 1000;
      while (Date.now() < deadline) {
        await refreshLeads();
        const jobs = await fetchJobs();
        if (!jobs.scrape_running) break;
        await sleep(4000);
      }
      const latest = await refreshLeads();
      const jobs = await fetchJobs();
      if (jobs.last_scrape_error) {
        pushToast("error", jobs.last_scrape_error);
      }
      const saved = jobs.last_scrape_saved ?? 0;
      pushToast(
        saved > 0 ? "success" : "info",
        `Scrape finished. ${saved} new valid lead${saved === 1 ? "" : "s"} saved (${latest.length} total in the database).`,
      );
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : "Could not start scrape.");
    } finally {
      setSearching(false);
    }
  }

  async function handleAgentCommand() {
    const command = agentPrompt.trim();
    if (!command) return;
    setAgentRunning(true);
    try {
      const result = await runAgentCommand(command);
      if (result.credits_all.length) setCredits(result.credits_all);
      else await refreshCredits();
      setTechStack(result.tech_stack || []);
      await refreshLeads();
      pushToast(result.saved > 0 || result.tech_stack.length > 0 ? "success" : "info", result.message);
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : "Agent command failed.");
    } finally {
      setAgentRunning(false);
    }
  }

  async function handleGenerate(lead: Lead) {
    setGeneratingId(lead.id);
    setLeads((current) =>
      current.map((item) => (item.id === lead.id ? { ...item, status: "generating" } : item)),
    );
    try {
      const nextDraft = await generateDraft(lead.id);
      setLeads((current) =>
        current.map((item) =>
          item.id === lead.id
            ? {
                ...item,
                status: item.status === "sent" ? item.status : "pending",
                aiDraft: nextDraft.ai_draft || `Subject: ${nextDraft.subject}\n\n${nextDraft.body}`,
              }
            : item,
        ),
      );
      pushToast("success", `AI draft generated for ${lead.name}. Click Review Email to edit.`);
    } catch (error) {
      setLeads((current) =>
        current.map((item) => (item.id === lead.id ? { ...item, status: "failed" } : item)),
      );
      pushToast("error", error instanceof Error ? error.message : "Draft generation failed.");
    } finally {
      setGeneratingId(null);
    }
  }

  function handleReview(lead: Lead) {
    if (!lead.aiDraft.trim()) {
      pushToast("info", `Generate an AI draft for ${lead.name} before reviewing.`);
      return;
    }
    setDraft(parseAiDraft(lead.aiDraft));
    setActiveLead(lead);
  }

  async function handleSaveEdits() {
    if (!activeLead) return;
    setSaving(true);
    try {
      await updateDraft(activeLead.id, draft);
      const stored = `Subject: ${draft.subject}\n\n${draft.body}`;
      setLeads((current) =>
        current.map((item) => (item.id === activeLead.id ? { ...item, aiDraft: stored } : item)),
      );
      pushToast("success", `Draft saved for ${activeLead.name}.`);
      setActiveLead(null);
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : "Could not save draft.");
    } finally {
      setSaving(false);
    }
  }

  function handleToggle(id: string) {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  function handleToggleAll(checked: boolean) {
    if (!checked) {
      setSelectedIds([]);
      return;
    }
    setSelectedIds(leads.filter(isCampaignEligible).map((lead) => lead.id));
  }

  async function handleLaunchCampaign() {
    const ids = selectedIds.filter((id) => {
      const lead = leads.find((item) => item.id === id);
      return lead ? isCampaignEligible(lead) : false;
    });
    if (ids.length === 0) {
      pushToast("info", "Select one or more pending leads first.");
      return;
    }
    setCampaignStarting(true);
    try {
      const result = await startCampaign(ids, dryRun);
      pushToast(
        "success",
        dryRun
          ? `${result.message}. Safety mode is on — statuses will move to Dry-Run.`
          : `${result.message}. Live sends wait for US business hours and pause 5–10 minutes between emails.`,
      );
      for (const warning of result.warnings) {
        pushToast("info", warning);
      }
      const deadline = Date.now() + 90 * 60 * 1000;
      while (Date.now() < deadline) {
        await refreshLeads();
        const jobs = await fetchJobs();
        if (!jobs.campaign_running) break;
        await sleep(4000);
      }
      await refreshLeads();
      pushToast("info", "Campaign job finished.");
      setSelectedIds([]);
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : "Could not start campaign.");
    } finally {
      setCampaignStarting(false);
    }
  }

  return (
    <div className="min-h-svh bg-[#0b0d12] text-slate-200">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_top,rgba(45,212,191,0.08),transparent_34%)]" />
      <main className="relative mx-auto flex w-full max-w-6xl flex-col gap-8 px-5 py-10 sm:px-8">
        <Header
          total={stats.total}
          pending={stats.pending}
          sent={stats.sent}
          dryRun={dryRun}
          campaignStarting={campaignStarting}
          selectedCount={selectedIds.length}
          credits={credits}
          autoSend={Boolean(jobs?.auto_send)}
          smtpReady={Boolean(jobs?.smtp_ready)}
          sentToday={jobs?.sent_today ?? 0}
          dailyCap={jobs?.daily_cap ?? 20}
          inSendWindow={Boolean(jobs?.in_send_window)}
          nextSendWindow={jobs?.next_send_window || ""}
          autosendError={jobs?.autosend_error || ""}
          easternNow={jobs?.eastern_now || ""}
          onDryRunChange={setDryRun}
          onLaunchCampaign={() => {
            void handleLaunchCampaign();
          }}
        />
        <SearchSection
          niche={niche}
          city={city}
          loading={searching}
          onNicheChange={setNiche}
          onCityChange={setCity}
          onSubmit={() => {
            void handleFindLeads();
          }}
        />
        <AgentCommand
          prompt={agentPrompt}
          loading={agentRunning}
          techStack={techStack}
          onPromptChange={setAgentPrompt}
          onSubmit={() => {
            void handleAgentCommand();
          }}
        />
        <LeadsTable
          leads={leads}
          loading={searching || agentRunning}
          generatingId={generatingId}
          selectedIds={selectedIds}
          onToggle={handleToggle}
          onToggleAll={handleToggleAll}
          onGenerate={(lead) => {
            void handleGenerate(lead);
          }}
          onReview={handleReview}
        />
      </main>
      <Toast toasts={toasts} />
      {activeLead ? (
        <EmailModal
          lead={activeLead}
          subject={draft.subject}
          body={draft.body}
          saving={saving}
          onSubjectChange={(value) => setDraft((current) => ({ ...current, subject: value }))}
          onBodyChange={(value) => setDraft((current) => ({ ...current, body: value }))}
          onClose={() => setActiveLead(null)}
          onSave={() => {
            void handleSaveEdits();
          }}
        />
      ) : null}
    </div>
  );
}
