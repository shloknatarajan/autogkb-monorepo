const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'https://autogkb-api.up.railway.app';

export interface JobResponse {
  job_id: string;
  pmcid: string;
  status: 'pending' | 'fetching_article' | 'extracting_variants' | 'generating_sentences' | 'finding_citations' | 'generating_summary' | 'completed' | 'failed';
  progress: string | null;
  annotation_data: Record<string, unknown> | null;
  markdown_content: string | null;
  error: string | null;
  created_at: string | null;
}

export const STATUS_LABELS: Record<string, string> = {
  pending: 'Queued...',
  fetching_article: 'Fetching article from PubMed...',
  extracting_variants: 'Extracting genetic variants...',
  generating_sentences: 'Generating association sentences...',
  finding_citations: 'Finding supporting citations...',
  generating_summary: 'Generating summary...',
  completed: 'Analysis complete!',
  failed: 'Analysis failed',
};

export interface PmcidEntry {
  pmcid: string;
  title: string | null;
  summary: string | null;
}

export async function listPmcids(): Promise<PmcidEntry[]> {
  const res = await fetch(`${API_URL}/pmcids`);
  if (!res.ok) return [];
  return res.json();
}

export async function analyzeArticle(pmcid: string, force = false): Promise<JobResponse> {
  const res = await fetch(`${API_URL}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pmcid, force }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function getJob(jobId: string): Promise<JobResponse> {
  const res = await fetch(`${API_URL}/jobs/${jobId}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function getJobByPmcid(pmcid: string): Promise<JobResponse | null> {
  const res = await fetch(`${API_URL}/jobs/pmcid/${pmcid}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}
