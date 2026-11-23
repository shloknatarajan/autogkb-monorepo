import type { JobResponse } from '@/lib/api';

const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  'https://autogkb-api.up.railway.app';

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
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const error = new Error(err.detail ?? `Request failed: ${res.status}`) as Error & {
      status?: number;
    };
    error.status = res.status;
    throw error;
  }
  return res.json();
}
