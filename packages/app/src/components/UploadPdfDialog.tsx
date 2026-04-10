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
import { AlertCircle, Info, Loader2, Upload } from 'lucide-react';
import { uploadPdf, getJob, STATUS_LABELS, type JobResponse } from '@/lib/api';

interface UploadPdfDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (pmid: string, jobData: JobResponse) => void;
}

type DialogState = 'idle' | 'loading' | 'error';

const UploadPdfDialog: React.FC<UploadPdfDialogProps> = ({
  open,
  onOpenChange,
  onSuccess,
}) => {
  const [pmid, setPmid] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [dialogState, setDialogState] = useState<DialogState>('idle');
  const [statusLabel, setStatusLabel] = useState('');
  const [progressText, setProgressText] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [openAccessPmcid, setOpenAccessPmcid] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const resetForm = useCallback(() => {
    stopPolling();
    setPmid('');
    setFile(null);
    setDialogState('idle');
    setStatusLabel('');
    setProgressText(null);
    setErrorMessage('');
    setOpenAccessPmcid(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, [stopPolling]);

  const handleClose = useCallback(() => {
    resetForm();
    onOpenChange(false);
  }, [resetForm, onOpenChange]);

  useEffect(() => {
    if (!open) {
      stopPolling();
    }
    return () => {
      stopPolling();
    };
  }, [open, stopPolling]);

  const handleSubmit = async () => {
    const trimmedPmid = pmid.trim();
    if (!trimmedPmid || !file) return;

    setDialogState('loading');
    setStatusLabel('Uploading PDF...');
    setProgressText(null);
    setErrorMessage('');

    try {
      const job = await uploadPdf(file, trimmedPmid);
      const jobId = job.job_id;

      // Detect if backend switched to open access pipeline
      if (job.source === 'pmc' && job.pmcid) {
        setOpenAccessPmcid(job.pmcid);
      }

      setStatusLabel(STATUS_LABELS[job.status] ?? job.status);
      setProgressText(job.progress);

      if (job.status === 'completed') {
        stopPolling();
        setDialogState('idle');
        onSuccess?.(trimmedPmid, job);
        handleClose();
        return;
      }
      if (job.status === 'failed') {
        stopPolling();
        setErrorMessage(job.error ?? 'An unknown error occurred.');
        setDialogState('error');
        return;
      }

      intervalRef.current = setInterval(async () => {
        try {
          const updated = await getJob(jobId);
          setStatusLabel(STATUS_LABELS[updated.status] ?? updated.status);
          setProgressText(updated.progress);

          if (updated.status === 'completed') {
            stopPolling();
            setDialogState('idle');
            onSuccess?.(trimmedPmid, updated);
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
        submitError instanceof Error ? submitError.message : 'Failed to upload PDF.'
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

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0] ?? null;
    if (selected && !selected.name.toLowerCase().endsWith('.pdf')) {
      setErrorMessage('Please select a PDF file.');
      setDialogState('error');
      return;
    }
    setFile(selected);
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => {
      if (!isOpen) {
        handleClose();
      }
    }}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Upload PDF</DialogTitle>
          <DialogDescription>
            Upload a PDF article and provide its PubMed ID (PMID) to run the annotation pipeline.
            This process typically takes 1-3 minutes.
          </DialogDescription>
        </DialogHeader>

        {dialogState === 'idle' && (
          <>
            <div className="grid gap-4 py-4">
              <div className="grid gap-2">
                <Label htmlFor="pmid">PubMed ID (PMID)</Label>
                <Input
                  id="pmid"
                  placeholder="e.g., 38234567"
                  value={pmid}
                  onChange={(e) => setPmid(e.target.value)}
                />
                <p className="text-sm text-muted-foreground">
                  Enter the numeric PMID for this article
                </p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="pdf-file">PDF File</Label>
                <Input
                  id="pdf-file"
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  onChange={handleFileChange}
                />
                {file && (
                  <p className="text-sm text-muted-foreground">
                    Selected: {file.name} ({(file.size / 1024 / 1024).toFixed(1)} MB)
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleSubmit}
                disabled={!pmid.trim() || !file}
              >
                <Upload className="w-4 h-4 mr-2" />
                Upload & Analyze
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'loading' && (
          <>
            <div className="flex flex-col items-center gap-4 py-8">
              {openAccessPmcid && (
                <Alert className="border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-100">
                  <Info className="h-4 w-4 !text-blue-600 dark:!text-blue-400" />
                  <AlertDescription>
                    Open access version found ({openAccessPmcid}) — using the
                    published version for better quality.
                  </AlertDescription>
                </Alert>
              )}
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

export default UploadPdfDialog;
