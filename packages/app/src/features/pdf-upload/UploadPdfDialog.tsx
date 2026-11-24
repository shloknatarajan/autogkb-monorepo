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
import { Badge } from '@/components/ui/badge';
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  RefreshCw,
  Upload,
} from 'lucide-react';
import { getJob, STATUS_LABELS, type JobResponse } from '@/lib/api';
import {
  resolvePdfIdentifiers,
  uploadPdf,
  type PdfIdentifierResult,
  type PdfMatchMethod,
} from './api';

interface UploadPdfDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (pmid: string, jobData: JobResponse) => void;
}

type DialogState = 'idle' | 'loading' | 'error' | 'confirm_regenerate';

const MATCH_METHOD_LABELS: Record<PdfMatchMethod, string> = {
  doi_metadata: 'Matched via PDF metadata DOI',
  doi_text: 'Matched via page-1 DOI',
  pmcid_in_text: 'Matched via PMCID',
  pmid_in_text: 'Matched via PMID',
  title_search: 'Matched via title search',
  unresolved: 'Not automatically matched',
};

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
  const [resolving, setResolving] = useState(false);
  const [resolution, setResolution] = useState<PdfIdentifierResult | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resolveAbortRef = useRef<AbortController | null>(null);
  const pmidEditedDuringResolveRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const abortResolve = useCallback(() => {
    if (resolveAbortRef.current) {
      resolveAbortRef.current.abort();
      resolveAbortRef.current = null;
    }
  }, []);

  const resetForm = useCallback(() => {
    stopPolling();
    abortResolve();
    setPmid('');
    setFile(null);
    setDialogState('idle');
    setStatusLabel('');
    setProgressText(null);
    setErrorMessage('');
    setOpenAccessPmcid(null);
    setResolving(false);
    setResolution(null);
    setResolveError(null);
    pmidEditedDuringResolveRef.current = false;
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, [stopPolling, abortResolve]);

  const handleClose = useCallback(() => {
    resetForm();
    onOpenChange(false);
  }, [resetForm, onOpenChange]);

  useEffect(() => {
    if (!open) {
      stopPolling();
      abortResolve();
    }
    return () => {
      stopPolling();
      abortResolve();
    };
  }, [open, stopPolling, abortResolve]);

  const handleSubmit = async (force = false) => {
    const trimmedPmid = pmid.trim();
    if (!trimmedPmid || !file) return;

    abortResolve();
    setResolving(false);
    setDialogState('loading');
    setStatusLabel('Uploading PDF...');
    setProgressText(null);
    setErrorMessage('');

    try {
      const job = await uploadPdf(file, trimmedPmid, force);
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
      if (
        submitError instanceof Error &&
        'status' in submitError &&
        submitError.status === 409
      ) {
        setErrorMessage(submitError.message);
        setDialogState('confirm_regenerate');
        return;
      }
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
    setOpenAccessPmcid(null);
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0] ?? null;
    abortResolve();
    setResolution(null);
    setResolveError(null);
    setResolving(false);

    if (!selected) {
      setFile(null);
      return;
    }

    if (!selected.name.toLowerCase().endsWith('.pdf')) {
      setFile(null);
      setErrorMessage('Please select a PDF file.');
      setDialogState('error');
      return;
    }

    setFile(selected);
    pmidEditedDuringResolveRef.current = false;

    const controller = new AbortController();
    resolveAbortRef.current = controller;
    setResolving(true);

    try {
      const result = await resolvePdfIdentifiers(selected, controller.signal);
      if (controller.signal.aborted) return;

      setResolution(result);
      if (result.pmid && !pmidEditedDuringResolveRef.current) {
        setPmid(result.pmid);
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setResolveError(
        err instanceof Error
          ? err.message
          : 'Could not automatically look up PMID for this PDF.'
      );
    } finally {
      if (resolveAbortRef.current === controller) {
        resolveAbortRef.current = null;
        setResolving(false);
      }
    }
  };

  const handlePmidChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (resolving) {
      pmidEditedDuringResolveRef.current = true;
    }
    setPmid(e.target.value);
  };

  const resolutionMatchesCurrentPmid =
    resolution?.pmid != null && pmid.trim() === resolution.pmid;

  return (
    <Dialog open={open} onOpenChange={(isOpen) => {
      if (!isOpen) {
        handleClose();
      }
    }}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Upload PDF</DialogTitle>
          <DialogDescription>
            Upload a PDF article to automatically look up its PubMed ID (PMID) and run the
            annotation pipeline. This process typically takes 1-3 minutes.
          </DialogDescription>
        </DialogHeader>

        {dialogState === 'idle' && (
          <>
            <div className="grid gap-4 py-4">
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

              {resolving && (
                <div className="flex items-center gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                  <span>Detecting PMID / PMCID from PDF...</span>
                </div>
              )}

              {!resolving && resolution && resolution.pmid && resolutionMatchesCurrentPmid && (
                <div className="grid gap-2 rounded-md border border-emerald-200 bg-emerald-50/70 p-3 text-sm text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100">
                  <div className="flex items-start gap-2">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                    <div className="grid gap-1">
                      <p className="font-medium leading-snug">
                        {resolution.title ?? `PMID ${resolution.pmid}`}
                      </p>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="secondary" className="text-xs font-normal">
                          {MATCH_METHOD_LABELS[resolution.match_method]}
                        </Badge>
                        {resolution.pmcid && (
                          <Badge className="bg-blue-100 text-blue-900 hover:bg-blue-100 dark:bg-blue-900/60 dark:text-blue-100 text-xs font-normal">
                            {resolution.pmcid} · Open Access
                          </Badge>
                        )}
                        {resolution.confidence === 'medium' && (
                          <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100 dark:bg-amber-900/60 dark:text-amber-100 text-xs font-normal">
                            Verify match
                          </Badge>
                        )}
                      </div>
                      {resolution.pmcid && (
                        <p className="text-xs text-muted-foreground">
                          Open-access PMC full text will be used automatically for higher quality.
                        </p>
                      )}
                      {resolution.existing_job_id && (
                        <p className="text-xs text-amber-800 dark:text-amber-300">
                          An analysis already exists for PMID {resolution.pmid}. Submitting will ask to regenerate.
                        </p>
                      )}
                      {resolution.warnings.map((warning, idx) => (
                        <p key={idx} className="text-xs text-amber-800 dark:text-amber-300">
                          {warning}
                        </p>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {!resolving && resolution && !resolution.pmid && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="grid gap-1 text-xs">
                    <span>
                      {resolution.warnings[0] ??
                        'Could not automatically match this PDF to a PubMed record. Please enter the PMID manually.'}
                    </span>
                    {resolution.warnings.slice(1).map((warning, idx) => (
                      <span key={idx}>{warning}</span>
                    ))}
                  </AlertDescription>
                </Alert>
              )}

              {!resolving && resolveError && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    Automatic lookup failed ({resolveError}). You can still enter the PMID manually.
                  </AlertDescription>
                </Alert>
              )}

              <div className="grid gap-2">
                <Label htmlFor="pmid">PubMed ID (PMID)</Label>
                <Input
                  id="pmid"
                  placeholder="e.g., 38234567"
                  value={pmid}
                  onChange={handlePmidChange}
                />
                <p className="text-sm text-muted-foreground">
                  {resolutionMatchesCurrentPmid
                    ? 'Auto-filled from PDF — edit if this is not the right article'
                    : 'Auto-detected on PDF selection, or enter the numeric PMID manually'}
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={() => handleSubmit()}
                disabled={!pmid.trim() || !file || resolving}
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

        {dialogState === 'confirm_regenerate' && (
          <>
            <div className="grid gap-4 py-4">
              <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                <AlertDescription>
                  {errorMessage} Would you like to regenerate it? This will replace the existing analysis.
                </AlertDescription>
              </Alert>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button type="button" onClick={() => handleSubmit(true)}>
                <RefreshCw className="w-4 h-4 mr-2" />
                Regenerate Analysis
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
