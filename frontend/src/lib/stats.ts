const STORAGE_KEY = "softenix_emails_sent_today";

function todayKey(): string {
  return new Date().toISOString().slice(0, 10);
}

export function readSentToday(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return 0;
    const parsed = JSON.parse(raw) as { date?: string; count?: number };
    if (parsed.date !== todayKey()) return 0;
    return Number(parsed.count) || 0;
  } catch {
    return 0;
  }
}

export function bumpSentToday(): number {
  const count = readSentToday() + 1;
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ date: todayKey(), count }));
  return count;
}
