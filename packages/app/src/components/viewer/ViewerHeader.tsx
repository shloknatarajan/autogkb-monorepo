import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { ArrowLeft, RefreshCw, Loader2 } from 'lucide-react';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  type AnnotationMethod, type JobResponse,
  listAnnotationMethods, regenerateArticle, getJob, STATUS_LABELS,
} from '@/lib/api';
import { toast } from 'sonner';

interface ViewerHeaderProps {
  pmid: string;
  job?: JobResponse | null;
}

/** "grounded_v2 · gpt-6-astra · 20 Nov 2026, 14:05" for the run being shown. */
function describeRun(job?: JobResponse | null): string | null {
  if (!job || job.status !== 'completed') return null;
  const meta = job.generation_metadata;
  // Runs stored before methods were named all came from the sentence pipeline.
  const method = meta?.annotation_method
    ?? (meta?.sentence_generation_method ? 'sentences_v5' : undefined);
  const model = meta?.annotation_model ?? meta?.sentence_model;
  const stamp = meta?.completed_at ?? job.updated_at ?? job.created_at;
  const date = stamp ? new Date(stamp) : null;
  const when = date && !Number.isNaN(date.getTime())
    ? date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : null;
  const parts = [method, model, when].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}

export const ViewerHeader: React.FC<ViewerHeaderProps> = ({ pmid, job }) => {
  const navigate = useNavigate();
  const [regenerating, setRegenerating] = useState(false);
  const [statusLabel, setStatusLabel] = useState('');
  const [methods, setMethods] = useState<AnnotationMethod[]>([]);
  const [method, setMethod] = useState<string | undefined>(undefined);
  const runLabel = describeRun(job);

  useEffect(() => {
    let cancelled = false;
    listAnnotationMethods()
      .then((available) => {
        if (cancelled) return;
        setMethods(available);
        setMethod((current) => current ?? available.find((m) => m.default)?.name);
      })
      .catch(() => {
        // Older API without method selection: regenerate uses the server default.
      });
    return () => { cancelled = true; };
  }, []);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const handleRegenerate = async () => {
    setRegenerating(true);
    setStatusLabel(STATUS_LABELS['pending'] ?? 'Starting...');

    try {
      const started = await regenerateArticle(pmid, method);

      if (started.status === 'completed') {
        stopPolling();
        setRegenerating(false);
        navigate(`/viewer/${pmid}`, { state: { dynamicData: started } });
        return;
      }
      if (started.status === 'failed') {
        stopPolling();
        setRegenerating(false);
        toast.error(started.error ?? 'Regeneration failed.');
        return;
      }

      setStatusLabel(STATUS_LABELS[started.status] ?? started.status);

      intervalRef.current = setInterval(async () => {
        try {
          const updated = await getJob(started.job_id);
          setStatusLabel(STATUS_LABELS[updated.status] ?? updated.status);

          if (updated.status === 'completed') {
            stopPolling();
            setRegenerating(false);
            navigate(`/viewer/${pmid}`, { state: { dynamicData: updated } });
          } else if (updated.status === 'failed') {
            stopPolling();
            setRegenerating(false);
            toast.error(updated.error ?? 'Regeneration failed.');
          }
        } catch {
          stopPolling();
          setRegenerating(false);
          toast.error('Failed to check regeneration status.');
        }
      }, 2000);
    } catch (err) {
      stopPolling();
      setRegenerating(false);
      toast.error(err instanceof Error ? err.message : 'Failed to start regeneration.');
    }
  };

  return (
    <header className="bg-card shadow-soft border-b">
      <div className="max-w-full mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <div className="flex items-center space-x-4">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate('/dashboard')}
              className="hover:bg-accent transition-smooth"
            >
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back
            </Button>
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 rounded-lg flex items-center justify-center">
                <img src="/favicon.ico" alt="PMC Icon" className="w-8 h-8 rounded-lg" />
              </div>
              <div>
                <h1 className="text-lg font-bold !text-black dark:!text-white">{pmid}</h1>
                {runLabel && (
                  <p className="text-xs text-muted-foreground" title="Annotation method, model, and completion time of the run shown">
                    {runLabel}
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {regenerating && (
              <span className="text-sm text-muted-foreground">{statusLabel}</span>
            )}
            {methods.length > 1 && (
              <Select value={method} onValueChange={setMethod} disabled={regenerating}>
                <SelectTrigger className="h-9 w-[170px]" aria-label="Annotation method">
                  <SelectValue placeholder="Method" />
                </SelectTrigger>
                <SelectContent>
                  {methods.map((m) => (
                    <SelectItem key={m.name} value={m.name} title={m.description}>
                      {m.name}{m.default ? ' (default)' : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={handleRegenerate}
              disabled={regenerating}
              className="hover:bg-accent transition-smooth"
            >
              {regenerating
                ? <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                : <RefreshCw className="w-4 h-4 mr-2" />}
              Regenerate
            </Button>
          </div>
        </div>
      </div>
    </header>
  );
};