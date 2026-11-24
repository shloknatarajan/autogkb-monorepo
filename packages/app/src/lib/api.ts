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
  updated_at?: string | null;
  generation_metadata?: GenerationMetadata | null;
  triage_session_id?: string | null;
}

/** Provenance of one analysis run. Older runs only have the legacy fields. */
export interface GenerationMetadata {
  annotation_method?: string | null;
  annotation_model?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  elapsed_seconds?: number | null;
  sentence_generation_method?: string | null;
  sentence_model?: string | null;
}

export interface AnnotationMethod {
  name: string;
  description: string;
  model: string;
  default: boolean;
}

export type JobStatus = JobResponse['status'];

export const JOB_STAGES: { status: JobStatus; label: string }[] = [
  { status: 'fetching_article', label: 'Article' },
  { status: 'extracting_variants', label: 'Variants' },
  { status: 'generating_sentences', label: 'Associations' },
  { status: 'finding_citations', label: 'Citations' },
  { status: 'generating_summary', label: 'Summary' },
];

export const JOB_PROGRESS: Record<JobStatus, number> = {
  pending: 5,
  fetching_article: 15,
  extracting_variants: 35,
  generating_sentences: 55,
  finding_citations: 75,
  generating_summary: 90,
  completed: 100,
  failed: 100,
};

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
  job_id?: string | null;
  pmid: string;
  pmcid: string | null;
  source: string;
  status?: string;
  progress?: string | null;
  title: string | null;
  summary: string | null;
}

export interface TriageArticle {
  pmid: string;
  pmcid: string | null;
  title: string | null;
  abstract: string | null;
  publication_date: string | null;
  litsuggest_score: number;
  triage_score: number;
  triage_label: 'relevant' | 'borderline' | 'not_relevant' | 'scoring';
  triage_tags: ('variant_annotations' | 'general_pharmacogenomics')[];
  paper_types: PaperType[];
  paper_type_reasoning: string;
  pgx_information_types: PgxInformationType[];
  pgx_information_reasoning: string;
  drug_tags: DrugTag[];
  gene_tags: GeneTag[];
  phenotype_tags: PhenotypeTag[];
  reasoning: string;
  decision: 'pending' | 'submitted' | 'dismissed';
  job_id: string | null;
  job_source?: string | null;
}

export interface DrugTag {
  name: string;
  id: string | null;
  source: string | null;
  url: string | null;
  resolved: boolean;
  mentions: string[];
}

export interface GeneTag {
  name: string;
  id: string | null;
  source: string | null;
  url: string | null;
  resolved: boolean;
  mentions: string[];
  ncbi_gene_id: string | null;
  hgnc_id: string | null;
}

export interface PhenotypeTag {
  name: string;
  id: string | null;
  source: string | null;
  url: string | null;
  resolved: boolean;
  mentions: string[];
  categories: string[];
  directions: string[];
  drugs: string[];
  genes: string[];
}

export type PaperType =
  | 'methods'
  | 'population_frequency'
  | 'allele_function'
  | 'implementation'
  | 'review'
  | 'phenoconversion';

export type PgxInformationType =
  | 'pd'
  | 'dosage'
  | 'efficacy'
  | 'toxicity'
  | 'other'
  | 'metabolism_pk';

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

export async function listAnnotationMethods(): Promise<AnnotationMethod[]> {
  const res = await fetch(`${API_URL}/annotation-methods`);
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

/** Re-run analysis; `method` picks an annotation method, else the server default. */
export async function regenerateArticle(pmid: string, method?: string): Promise<JobResponse> {
  const res = await fetch(`${API_URL}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(method ? { pmid, method } : { pmid }),
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
): Promise<{
  session_ids: string[];
  latest_session_id: string;
  created_count: number;
  existing_count: number;
  regenerated_count: number;
  active_count: number;
}> {
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

export async function regenerateTriageSession(
  sessionId: string
): Promise<{ session_id: string; regenerating: boolean }> {
  const res = await fetch(`${API_URL}/triage/sessions/${sessionId}/regenerate`, {
    method: 'POST',
  });
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

export interface SubmitAllTriageResult {
  session_id: string;
  submitted: number;
  skipped_dismissed: number;
  skipped_submitted: number;
  skipped_no_full_text: number;
}

export async function submitAllTriageArticles(
  sessionId: string
): Promise<SubmitAllTriageResult> {
  const res = await fetch(`${API_URL}/triage/sessions/${sessionId}/submit-all`, {
    method: 'POST',
  });
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

export function openJobStream(jobId: string): EventSource {
  return new EventSource(`${API_URL}/jobs/${jobId}/stream`);
}
