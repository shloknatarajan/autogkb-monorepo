import React, { useRef, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { ArrowLeft, RefreshCw, Loader2 } from 'lucide-react';
import { regenerateArticle, getJob, STATUS_LABELS } from '@/lib/api';
import { toast } from 'sonner';

interface ViewerHeaderProps {
  pmid: string;
}

export const ViewerHeader: React.FC<ViewerHeaderProps> = ({ pmid }) => {
  const navigate = useNavigate();
  const [regenerating, setRegenerating] = useState(false);
  const [statusLabel, setStatusLabel] = useState('');
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
      const job = await regenerateArticle(pmid);

      if (job.status === 'completed') {
        stopPolling();
        setRegenerating(false);
        navigate(`/viewer/${pmid}`, { state: { dynamicData: job } });
        return;
      }
      if (job.status === 'failed') {
        stopPolling();
        setRegenerating(false);
        toast.error(job.error ?? 'Regeneration failed.');
        return;
      }

      setStatusLabel(STATUS_LABELS[job.status] ?? job.status);

      intervalRef.current = setInterval(async () => {
        try {
          const updated = await getJob(job.job_id);
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
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {regenerating && (
              <span className="text-sm text-muted-foreground">{statusLabel}</span>
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