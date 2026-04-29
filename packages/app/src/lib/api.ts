const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'https://autogkb-api.up.railway.app';

export interface JobResponse {
  job_id: string;
  pmid: string;
  pmcid: string | null;
  source: string;
  status: 'pending' | 'fetching_article' | 'extracting_variants' | 'generating_sentences' | 'finding_citations' | 'generating_summary' | 'completed' | 'failed';
  progress: string | null;
  annotation_data: Record<string, unknown> | null;
  markdown_content: string | null;
  error: string | null;
  created_at: string | null;
}

export const STATUS_LABELS: Record<string, string> = {
  pending: 'Queued...',
  fetching_article: 'Fetching article...',
  extracting_variants: 'Extracting genetic variants...',
  generating_sentences: 'Generating association sentences...',
  finding_citations: 'Finding supporting citations...',
  generating_summary: 'Generating summary...',
  completed: 'Analysis complete!',
  failed: 'Analysis failed',
};

export interface ArticleEntry {
  pmid: string;
  pmcid: string | null;
  source: string;
  title: string | null;
  summary: string | null;
}

export interface TriageArticle {
  pmid: string;
  title: string | null;
  abstract: string | null;
  litsuggest_score: number;
  triage_score: number;
  triage_label: 'relevant' | 'borderline' | 'not_relevant';
  reasoning: string;
  decision: 'pending' | 'submitted' | 'dismissed';
  job_id: string | null;
}

export interface TriageSessionListItem {
  id: string;
  project_id: string;
  project_name: string;
  week_date: string;
  status: 'pending' | 'scoring' | 'completed' | 'error';
  article_count: number;
  created_at: string;
}

export interface TriageSession extends TriageSessionListItem {
  articles: TriageArticle[];
  error: string | null;
}

export interface TriageStreamEvent {
  session_id: string;
  status: string;
  article_count: number;
  error: string | null;
}

export async function listArticles(): Promise<ArticleEntry[]> {
  const res = await fetch(`${API_URL}/articles`);
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

export async function regenerateArticle(pmid: string): Promise<JobResponse> {
  const res = await fetch(`${API_URL}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pmid }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function uploadPdf(file: File, pmid: string, force = false): Promise<JobResponse> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('pmid', pmid);
  if (force) formData.append('force', 'true');

  const res = await fetch(`${API_URL}/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const error = new Error(err.detail ?? `Request failed: ${res.status}`);
    (error as any).status = res.status;
    throw error;
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

export async function getJobByPmid(pmid: string): Promise<JobResponse | null> {
  const res = await fetch(`${API_URL}/jobs/pmid/${pmid}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

/** @deprecated Use getJobByPmid instead */
export async function getJobByPmcid(pmcid: string): Promise<JobResponse | null> {
  const res = await fetch(`${API_URL}/jobs/pmcid/${pmcid}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

/** @deprecated Use listArticles instead */
export async function listPmcids(): Promise<ArticleEntry[]> {
  return listArticles();
}

export async function createTriageSession(
  projectId: string,
  projectName: string
): Promise<{ session_id: string }> {
  const res = await fetch(`${API_URL}/triage/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: projectId, project_name: projectName }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function listTriageSessions(): Promise<TriageSessionListItem[]> {
  const res = await fetch(`${API_URL}/triage/sessions`);
  if (!res.ok) return [];
  return res.json();
}

export async function getTriageSession(sessionId: string): Promise<TriageSession> {
  const res = await fetch(`${API_URL}/triage/sessions/${sessionId}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function submitTriageArticle(
  sessionId: string,
  pmid: string
): Promise<{ job_id: string; pmid: string }> {
  const res = await fetch(
    `${API_URL}/triage/sessions/${sessionId}/articles/${pmid}/submit`,
    { method: 'POST' }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function updateTriageArticleDecision(
  sessionId: string,
  pmid: string,
  decision: 'pending' | 'dismissed'
): Promise<void> {
  const res = await fetch(
    `${API_URL}/triage/sessions/${sessionId}/articles/${pmid}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `Request failed: ${res.status}`);
  }
}

export function openTriageStream(sessionId: string): EventSource {
  return new EventSource(`${API_URL}/triage/sessions/${sessionId}/stream`);
}
