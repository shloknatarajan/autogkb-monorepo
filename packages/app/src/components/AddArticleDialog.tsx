import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertCircle, Loader2 } from 'lucide-react';
import { analyzeArticle, getJob, STATUS_LABELS, type JobResponse } from '@/lib/api';

interface AddArticleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (pmcid: string, jobData: JobResponse) => void;
}

type DialogState = 'idle' | 'loading' | 'error';

const AddArticleDialog: React.FC<AddArticleDialogProps> = ({
  open,
  onOpenChange,
  onSuccess,
}) => {
  const [pmcid, setPmcid] = useState('');
  const [dialogState, setDialogState] = useState<DialogState>('idle');
  const [statusLabel, setStatusLabel] = useState('');
  const [progressText, setProgressText] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState('');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const resetForm = useCallback(() => {
    stopPolling();
    setPmcid('');
    setDialogState('idle');
    setStatusLabel('');
    setProgressText(null);
    setErrorMessage('');
  }, [stopPolling]);

  const handleClose = useCallback(() => {
    resetForm();
    onOpenChange(false);
  }, [resetForm, onOpenChange]);

  // Clean up polling on unmount or when dialog closes
  useEffect(() => {
    if (!open) {
      stopPolling();
    }
    return () => {
      stopPolling();
    };
  }, [open, stopPolling]);

  const handleSubmit = async () => {
    const trimmed = pmcid.trim();
    if (!trimmed) return;

    setDialogState('loading');
    setStatusLabel(STATUS_LABELS['pending'] ?? 'Starting...');
    setProgressText(null);
    setErrorMessage('');

    try {
      const job = await analyzeArticle(trimmed);
      const jobId = job.job_id;
      const jobPmcid = job.pmcid;

      // Update with initial response
      setStatusLabel(STATUS_LABELS[job.status] ?? job.status);
      setProgressText(job.progress);

      // Check if already completed or failed
      if (job.status === 'completed') {
        stopPolling();
        setDialogState('idle');
        onSuccess?.(jobPmcid, job);
        handleClose();
        return;
      }
      if (job.status === 'failed') {
        stopPolling();
        setErrorMessage(job.error ?? 'An unknown error occurred.');
        setDialogState('error');
        return;
      }

      // Start polling
      intervalRef.current = setInterval(async () => {
        try {
          const updated = await getJob(jobId);
          setStatusLabel(STATUS_LABELS[updated.status] ?? updated.status);
          setProgressText(updated.progress);

          if (updated.status === 'completed') {
            stopPolling();
            setDialogState('idle');
            onSuccess?.(jobPmcid, updated);
            handleClose();
          } else if (updated.status === 'failed') {
            stopPolling();
            setErrorMessage(updated.error ?? 'An unknown error occurred.');
            setDialogState('error');
          }
        } catch (pollError) {
          stopPolling();
          setErrorMessage(
            pollError instanceof Error ? pollError.message : 'Failed to check job status.'
          );
          setDialogState('error');
        }
      }, 2000);
    } catch (submitError) {
      stopPolling();
      setErrorMessage(
        submitError instanceof Error ? submitError.message : 'Failed to start analysis.'
      );
      setDialogState('error');
    }
  };

  const handleTryAgain = () => {
    setDialogState('idle');
    setErrorMessage('');
    setStatusLabel('');
    setProgressText(null);
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => {
      if (!isOpen) {
        handleClose();
      }
    }}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Add New Article</DialogTitle>
          <DialogDescription>
            Enter a PubMed Central ID (PMCID) to fetch and annotate a new article.
            This process typically takes 10-30 seconds.
          </DialogDescription>
        </DialogHeader>

        {dialogState === 'idle' && (
          <>
            <div className="grid gap-4 py-4">
              <div className="grid gap-2">
                <Label htmlFor="pmcid">PubMed Central ID (PMCID)</Label>
                <Input
                  id="pmcid"
                  placeholder="e.g., PMC5508045"
                  value={pmcid}
                  onChange={(e) => setPmcid(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleSubmit();
                    }
                  }}
                />
                <p className="text-sm text-muted-foreground">
                  Enter the PMCID (e.g., PMC5508045 or just the numeric ID)
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleSubmit}
                disabled={!pmcid.trim()}
              >
                Start Analysis
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'loading' && (
          <>
            <div className="flex flex-col items-center gap-4 py-8">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <div className="text-center">
                <p className="text-sm font-medium text-foreground">
                  {statusLabel}
                </p>
                {progressText && (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {progressText}
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'error' && (
          <>
            <div className="grid gap-4 py-4">
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{errorMessage}</AlertDescription>
              </Alert>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button type="button" onClick={handleTryAgain}>
                Try Again
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default AddArticleDialog;
