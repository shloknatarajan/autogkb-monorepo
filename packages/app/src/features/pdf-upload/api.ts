import type { JobResponse } from '@/lib/api';

const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  'https://autogkb-api.up.railway.app';

export type PdfMatchMethod =
  | 'doi_metadata'
  | 'doi_text'
  | 'pmcid_in_text'
  | 'pmid_in_text'
  | 'title_search'
  | 'unresolved';

export type PdfConfidence = 'high' | 'medium' | 'none';

export interface PdfIdentifierResult {
  pmid: string | null;
  pmcid: string | null;
  doi: string | null;
  title: string | null;
  match_method: PdfMatchMethod;
  confidence: PdfConfidence;
  existing_job_id: string | null;
  warnings: string[];
}

async function parseError(res: Response): Promise<Error & { status?: number }> {
  const err = await res.json().catch(() => ({ detail: res.statusText }));
  const error = new Error(err.detail ?? `Request failed: ${res.status}`) as Error & {
    status?: number;
  };
  error.status = res.status;
  return error;
}

export async function resolvePdfIdentifiers(
  file: File,
  signal?: AbortSignal,
): Promise<PdfIdentifierResult> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${API_URL}/upload/resolve`, {
    method: 'POST',
    body: formData,
    signal,
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return res.json();
}

export async function uploadPdf(
  file: File,
  pmid: string,
  force = false,
): Promise<JobResponse> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('pmid', pmid);
  if (force) formData.append('force', 'true');

  const res = await fetch(`${API_URL}/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return res.json();
}
