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
  FileText,
  Loader2,
  RefreshCw,
  Upload,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import type { JobResponse } from '@/lib/api';
import {
  resolvePdfIdentifiers,
  uploadPdf,
  type PdfIdentifierResult,
  type PdfMatchMethod,
} from './api';

interface UploadPdfDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (pmid: string, jobData: JobResponse, title?: string | null) => void;
}

type DialogState =
  | 'idle'
  | 'resolving'
  | 'confirm'
  | 'error'
  | 'confirm_regenerate';

const TITLE_PREVIEW_MAX_CHARS = 90;

const MATCH_METHOD_LABELS: Record<PdfMatchMethod, string> = {
  doi_metadata: 'Matched via PDF metadata DOI',
  doi_text: 'Matched via page-1 DOI',
  pmcid_in_text: 'Matched via PMCID',
  pmid_in_text: 'Matched via PMID',
  title_search: 'Matched via title search',
  unresolved: 'Not automatically matched',
};

function truncateTitle(title: string, maxChars = TITLE_PREVIEW_MAX_CHARS): string {
  const cleaned = title.replace(/\s+/g, ' ').trim();
  if (cleaned.length <= maxChars) return cleaned;
  return `${cleaned.slice(0, maxChars).trimEnd()}...`;
}

const UploadPdfDialog: React.FC<UploadPdfDialogProps> = ({
  open,
  onOpenChange,
  onSuccess,
}) => {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const [pmid, setPmid] = useState('');
  const [pmcid, setPmcid] = useState('');
  const [resolution, setResolution] = useState<PdfIdentifierResult | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const [dialogState, setDialogState] = useState<DialogState>('idle');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const resolveAbortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const abortResolve = useCallback(() => {
    if (resolveAbortRef.current) {
      resolveAbortRef.current.abort();
      resolveAbortRef.current = null;
    }
  }, []);

  const resetForm = useCallback(() => {
    abortResolve();
    setFile(null);
    setFileError(null);
    setIsDragging(false);
    setPmid('');
    setPmcid('');
    setResolution(null);
    setResolveError(null);
    setDialogState('idle');
    setSubmitting(false);
    setErrorMessage('');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, [abortResolve]);

  const handleClose = useCallback(() => {
    resetForm();
    onOpenChange(false);
  }, [resetForm, onOpenChange]);

  useEffect(() => {
    if (!open) {
      abortResolve();
    }
    return () => {
      abortResolve();
    };
  }, [open, abortResolve]);

  const selectPdfFile = (selected: File | null) => {
    setFileError(null);
    if (!selected) {
      setFile(null);
      return;
    }
    if (!selected.name.toLowerCase().endsWith('.pdf')) {
      setFile(null);
      setFileError('Only PDF files are supported. Please choose a .pdf file.');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      return;
    }
    setFile(selected);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    selectPdfFile(e.target.files?.[0] ?? null);
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isDragging) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0] ?? null;
    selectPdfFile(dropped);
  };

  const handleClearFile = (e: React.MouseEvent) => {
    e.stopPropagation();
    setFile(null);
    setFileError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleStartResolve = async () => {
    if (!file) return;

    abortResolve();
    setResolution(null);
    setResolveError(null);
    setPmid('');
    setPmcid('');
    setDialogState('resolving');

    const controller = new AbortController();
    resolveAbortRef.current = controller;

    try {
      const result = await resolvePdfIdentifiers(file, controller.signal);
      if (controller.signal.aborted) return;

      setResolution(result);
      setPmid(result.pmid ?? '');
      setPmcid(result.pmcid ?? '');
      setDialogState('confirm');
    } catch (err) {
      if (controller.signal.aborted) return;
      if (err instanceof Error && 'status' in err && err.status === 422) {
        setErrorMessage(err.message);
        setDialogState('error');
        return;
      }
      setResolveError(
        err instanceof Error
          ? err.message
          : 'Could not automatically look up PMID/PMCID for this PDF.'
      );
      setDialogState('confirm');
    } finally {
      if (resolveAbortRef.current === controller) {
        resolveAbortRef.current = null;
      }
    }
  };

  const handleConfirmUpload = async (force = false) => {
    const trimmedPmid = pmid.trim();
    const trimmedPmcid = pmcid.trim();
    if (!trimmedPmid || !file || submitting) return;

    setSubmitting(true);
    setErrorMessage('');

    try {
      const resolvedTitle = resolution?.title?.trim() || undefined;
      const job = await uploadPdf(
        file,
        trimmedPmid,
        force,
        trimmedPmcid || undefined,
        resolvedTitle,
      );

      if (job.status === 'failed') {
        setErrorMessage(job.error ?? 'An unknown error occurred.');
        setDialogState('error');
        return;
      }

      if (job.status !== 'completed') {
        const description =
          job.source === 'pmc' && job.pmcid
            ? `Using open-access version (${job.pmcid}). You'll be notified when parsing finishes.`
            : "Parsing in the background. You'll be notified when it's ready.";
        toast.info(`Parsing article (PMID ${trimmedPmid})...`, {
          description,
          position: 'top-right',
        });
      }

      onSuccess?.(trimmedPmid, job, resolvedTitle ?? file.name);
      handleClose();
    } catch (submitError) {
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
    } finally {
      setSubmitting(false);
    }
  };

  const handleTryAgain = () => {
    setErrorMessage('');
    setDialogState(file && (resolution || resolveError) ? 'confirm' : 'idle');
  };

  const fullTitle = resolution?.title?.trim() || null;
  const previewTitle = fullTitle ? truncateTitle(fullTitle) : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          handleClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>
            {dialogState === 'confirm' ? 'Confirm Article Details' : 'Upload PDF'}
          </DialogTitle>
          <DialogDescription>
            {dialogState === 'confirm'
              ? 'Review or edit the detected PMID and PMCID before starting the analysis.'
              : 'Drop a PDF article to look up its PubMed identifiers and run the annotation pipeline.'}
          </DialogDescription>
        </DialogHeader>

        {dialogState === 'idle' && (
          <>
            <div className="grid gap-4 py-4">
              <input
                id="pdf-file"
                ref={fileInputRef}
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={handleFileChange}
              />

              <div
                role="button"
                tabIndex={0}
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    fileInputRef.current?.click();
                  }
                }}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  isDragging
                    ? 'border-primary bg-primary/5'
                    : file
                      ? 'border-primary/50 bg-muted/30 hover:border-primary'
                      : 'border-muted-foreground/25 hover:border-primary/60 hover:bg-muted/30'
                }`}
              >
                {file ? (
                  <div className="flex w-full items-center justify-between gap-3 rounded-md border bg-background px-3 py-2.5 text-left">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <FileText className="h-5 w-5 shrink-0 text-primary" />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">
                          {file.name}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {(file.size / 1024 / 1024).toFixed(2)} MB · Click to replace
                        </p>
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 shrink-0"
                      onClick={handleClearFile}
                      aria-label="Remove selected file"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                ) : (
                  <>
                    <div className="flex h-11 w-11 items-center justify-center rounded-full bg-muted">
                      <Upload className="h-5 w-5 text-muted-foreground" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-sm font-medium text-foreground">
                        Drag and drop a PDF here, or click to browse
                      </p>
                      <p className="text-xs text-muted-foreground">
                        PDF files only — PMID and PMCID will be detected before uploading
                      </p>
                    </div>
                  </>
                )}
              </div>

              {fileError && (
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>{fileError}</AlertDescription>
                </Alert>
              )}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleStartResolve}
                disabled={!file}
              >
                <Upload className="w-4 h-4 mr-2" />
                Upload
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'resolving' && (
          <>
            <div className="flex flex-col items-center gap-4 py-10">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <div className="text-center">
                <p className="text-sm font-medium text-foreground">
                  Inspecting PDF...
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Extracting title, PMID, and PMCID from {file?.name}
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'confirm' && (
          <>
            <div className="grid gap-4 py-4">
              <div className="grid gap-1.5 rounded-md border bg-muted/40 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Article Title
                  </span>
                  {resolution && (
                    <div className="flex items-center gap-1.5">
                      <Badge variant="secondary" className="text-xs font-normal">
                        {MATCH_METHOD_LABELS[resolution.match_method]}
                      </Badge>
                      {resolution.confidence === 'medium' && (
                        <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100 dark:bg-amber-900/60 dark:text-amber-100 text-xs font-normal">
                          Verify match
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
                <p
                  className="text-sm font-medium text-foreground"
                  title={fullTitle ?? undefined}
                >
                  {previewTitle ?? 'Title could not be extracted'}
                </p>
                {file && (
                  <p className="truncate text-xs text-muted-foreground">
                    File: {file.name}
                  </p>
                )}
              </div>

              {resolution?.existing_job_id && pmid.trim() === resolution.pmid && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    An analysis already exists for PMID {resolution.pmid}. Confirming will ask whether to regenerate it.
                  </AlertDescription>
                </Alert>
              )}

              {resolution && !resolution.pmid && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="grid gap-1 text-xs">
                    <span>
                      {resolution.warnings[0] ??
                        'Could not automatically match this PDF to PubMed. Enter the PMID below to continue.'}
                    </span>
                    {resolution.warnings.slice(1).map((warning, idx) => (
                      <span key={idx}>{warning}</span>
                    ))}
                  </AlertDescription>
                </Alert>
              )}

              {resolution?.pmid && resolution.warnings.length > 0 && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    {resolution.warnings.join(' ')}
                  </AlertDescription>
                </Alert>
              )}

              {resolveError && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    Automatic lookup failed ({resolveError}). Enter the PMID manually to proceed.
                  </AlertDescription>
                </Alert>
              )}

              <div className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="confirm-pmid">PubMed ID (PMID)</Label>
                  <Input
                    id="confirm-pmid"
                    placeholder="e.g., 38234567"
                    value={pmid}
                    onChange={(e) => setPmid(e.target.value)}
                    disabled={submitting}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="confirm-pmcid">
                    PubMed Central ID (PMCID){' '}
                    <span className="font-normal text-muted-foreground">(optional)</span>
                  </Label>
                  <Input
                    id="confirm-pmcid"
                    placeholder={resolution?.pmcid ? 'e.g., PMC10987654' : 'N/A'}
                    value={pmcid}
                    onChange={(e) => setPmcid(e.target.value)}
                    disabled={submitting}
                  />
                  {pmcid.trim() && (
                    <p className="text-xs text-muted-foreground">
                      When a PMCID is provided, the open-access PMC full text will be used for higher quality.
                    </p>
                  )}
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={handleClose}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={() => handleConfirmUpload()}
                disabled={!pmid.trim() || submitting}
              >
                {submitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                {submitting ? 'Starting...' : 'Confirm'}
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
              <Button
                type="button"
                variant="outline"
                onClick={handleClose}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={() => handleConfirmUpload(true)}
                disabled={submitting}
              >
                {submitting ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4 mr-2" />
                )}
                {submitting ? 'Starting...' : 'Regenerate Analysis'}
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
