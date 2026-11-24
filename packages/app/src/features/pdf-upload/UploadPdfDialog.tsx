import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
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
import { Checkbox } from '@/components/ui/checkbox';
import { Progress } from '@/components/ui/progress';
import {
  AlertCircle,
  AlertTriangle,
  FileText,
  FolderOpen,
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
  verifyPmid,
  type PdfIdentifierResult,
  type PdfMatchMethod,
  type PmidVerificationResult,
} from './api';
import {
  extractPdfFilesFromDataTransfer,
  extractPdfFilesFromList,
  MAX_BULK_PDFS,
} from './intake';

export interface StartedPdfUpload {
  pmid: string;
  job: JobResponse;
  title?: string | null;
}

interface UploadPdfDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (uploads: StartedPdfUpload[]) => void;
}

type DialogState =
  | 'idle'
  | 'resolving'
  | 'confirm'
  | 'error'
  | 'confirm_regenerate';

interface PmidCheckState {
  pmid: string;
  status: 'checking' | 'done';
  result: PmidVerificationResult | null;
}

interface ReviewItem {
  id: string;
  file: File;
  pmid: string;
  pmcid: string;
  selected: boolean;
  unreadable: boolean;
  resolution: PdfIdentifierResult | null;
  resolveError: string | null;
  pmidCheck: PmidCheckState | null;
}

const TITLE_PREVIEW_MAX_CHARS = 90;
const BULK_TITLE_PREVIEW_MAX_CHARS = 65;
const RESOLVE_CONCURRENCY = 2;
const SUBMIT_CONCURRENCY = 3;
const PMID_VERIFY_DEBOUNCE_MS = 450;
const PMID_RE = /^\d{1,10}$/;
const PMCID_RE = /^(?:PMC)?\d{1,10}$/i;

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

function isStatusInFlight(status: string | null | undefined): boolean {
  return Boolean(status && status !== 'completed' && status !== 'failed' && status !== 'error');
}

function getEffectiveExistingJob(
  item: ReviewItem,
): { id: string; status: string | null } | null {
  const trimmed = item.pmid.trim();
  if (!trimmed) return null;
  if (item.resolution?.existing_job_id && trimmed === item.resolution.pmid) {
    return {
      id: item.resolution.existing_job_id,
      status: item.resolution.existing_job_status,
    };
  }
  if (
    item.pmidCheck?.status === 'done' &&
    item.pmidCheck.pmid === trimmed &&
    item.pmidCheck.result?.existing_job_id
  ) {
    return {
      id: item.pmidCheck.result.existing_job_id,
      status: item.pmidCheck.result.existing_job_status,
    };
  }
  return null;
}

function isPmidNotFound(item: ReviewItem): boolean {
  const trimmed = item.pmid.trim();
  return Boolean(
    trimmed &&
      item.pmidCheck?.status === 'done' &&
      item.pmidCheck.pmid === trimmed &&
      item.pmidCheck.result?.exists === false,
  );
}

function isPmidChecking(item: ReviewItem): boolean {
  const trimmed = item.pmid.trim();
  return Boolean(
    trimmed &&
      item.pmidCheck?.status === 'checking' &&
      item.pmidCheck.pmid === trimmed,
  );
}

function getEffectiveTitle(item: ReviewItem): string | null {
  const trimmed = item.pmid.trim();
  const checkedTitle =
    item.pmidCheck?.status === 'done' &&
    item.pmidCheck.pmid === trimmed &&
    item.pmidCheck.result?.exists
      ? item.pmidCheck.result.title?.trim() || null
      : null;
  if (item.resolution?.pmid && trimmed === item.resolution.pmid) {
    return item.resolution.title?.trim() || checkedTitle;
  }
  return checkedTitle || item.resolution?.title?.trim() || null;
}

async function runWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;

  const runWorker = async () => {
    while (nextIndex < items.length) {
      const current = nextIndex;
      nextIndex += 1;
      results[current] = await worker(items[current], current);
    }
  };

  const workers = Array.from(
    { length: Math.min(concurrency, items.length) },
    () => runWorker(),
  );
  await Promise.all(workers);
  return results;
}

const UploadPdfDialog: React.FC<UploadPdfDialogProps> = ({
  open,
  onOpenChange,
  onSuccess,
}) => {
  const [files, setFiles] = useState<File[]>([]);
  const [intakeNotices, setIntakeNotices] = useState<string[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [unpacking, setUnpacking] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [resolveProgress, setResolveProgress] = useState({ done: 0, total: 0 });

  const [dialogState, setDialogState] = useState<DialogState>('idle');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const resolveAbortRef = useRef<AbortController | null>(null);
  const verifyTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(
    new Map(),
  );
  const verifyControllersRef = useRef<Map<string, AbortController>>(new Map());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const abortVerifyChecks = useCallback(() => {
    for (const timer of verifyTimersRef.current.values()) {
      clearTimeout(timer);
    }
    verifyTimersRef.current.clear();
    for (const controller of verifyControllersRef.current.values()) {
      controller.abort();
    }
    verifyControllersRef.current.clear();
  }, []);

  const abortResolve = useCallback(() => {
    if (resolveAbortRef.current) {
      resolveAbortRef.current.abort();
      resolveAbortRef.current = null;
    }
    abortVerifyChecks();
  }, [abortVerifyChecks]);

  const resetForm = useCallback(() => {
    abortResolve();
    setFiles([]);
    setIntakeNotices([]);
    setFileError(null);
    setUnpacking(false);
    setIsDragging(false);
    setReviewItems([]);
    setResolveProgress({ done: 0, total: 0 });
    setDialogState('idle');
    setSubmitting(false);
    setErrorMessage('');
    if (fileInputRef.current) fileInputRef.current.value = '';
    if (folderInputRef.current) folderInputRef.current.value = '';
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

  const addExtractedFiles = useCallback(
    (extracted: File[], skippedCount: number, warnings: string[]) => {
      setFileError(null);
      const notices = [...warnings];
      if (skippedCount > 0) {
        notices.push(
          `Skipped ${skippedCount} non-PDF file${skippedCount === 1 ? '' : 's'}.`,
        );
      }

      if (extracted.length === 0) {
        setIntakeNotices(notices);
        setFileError('No PDF files were found. Please drop or choose .pdf files, a folder, or a .zip archive.');
        return;
      }

      setFiles((prev) => {
        const merged = [...prev, ...extracted];
        if (merged.length > MAX_BULK_PDFS) {
          notices.push(
            `Capped at ${MAX_BULK_PDFS} PDFs per batch (${merged.length - MAX_BULK_PDFS} extra skipped).`,
          );
          return merged.slice(0, MAX_BULK_PDFS);
        }
        return merged;
      });
      setIntakeNotices(notices);
    },
    [],
  );

  const handleFileInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (!selected || selected.length === 0) return;

    setUnpacking(true);
    setFileError(null);
    try {
      const result = await extractPdfFilesFromList(selected);
      addExtractedFiles(result.files, result.skippedCount, result.warnings);
    } finally {
      setUnpacking(false);
      e.target.value = '';
    }
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

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    setUnpacking(true);
    setFileError(null);

    try {
      const result = await extractPdfFilesFromDataTransfer(e.dataTransfer);
      addExtractedFiles(result.files, result.skippedCount, result.warnings);
    } finally {
      setUnpacking(false);
    }
  };

  const handleRemoveFile = (index: number) => {
    setFiles((prev) => prev.filter((_, idx) => idx !== index));
    setFileError(null);
  };

  const handleClearFiles = () => {
    setFiles([]);
    setIntakeNotices([]);
    setFileError(null);
  };

  const handleStartResolve = async () => {
    if (files.length === 0) return;

    abortResolve();
    setReviewItems([]);
    setResolveProgress({ done: 0, total: files.length });
    setDialogState('resolving');

    const controller = new AbortController();
    resolveAbortRef.current = controller;

    try {
      const resolved = await runWithConcurrency(
        files,
        RESOLVE_CONCURRENCY,
        async (file, index): Promise<ReviewItem> => {
          try {
            const result = await resolvePdfIdentifiers(file, controller.signal);
            const hasExisting = Boolean(result.existing_job_id);
            const inFlight = isStatusInFlight(result.existing_job_status);
            return {
              id: `${index}-${file.name}`,
              file,
              pmid: result.pmid ?? '',
              pmcid: result.pmcid ?? '',
              selected: Boolean(result.pmid) && !hasExisting && !inFlight,
              unreadable: false,
              resolution: result,
              resolveError: null,
              pmidCheck: null,
            };
          } catch (err) {
            if (controller.signal.aborted) {
              throw err;
            }
            const status =
              err instanceof Error && 'status' in err
                ? (err as Error & { status?: number }).status
                : undefined;
            if (files.length === 1 && status === 422) {
              throw err;
            }
            return {
              id: `${index}-${file.name}`,
              file,
              pmid: '',
              pmcid: '',
              selected: false,
              unreadable: status === 422,
              resolution: null,
              resolveError:
                err instanceof Error
                  ? err.message
                  : 'Could not automatically look up PMID/PMCID.',
              pmidCheck: null,
            };
          } finally {
            if (!controller.signal.aborted) {
              setResolveProgress((prev) => ({ ...prev, done: prev.done + 1 }));
            }
          }
        },
      );

      if (controller.signal.aborted) return;

      // In bulk mode, if two resolved items share the same PMID, uncheck the duplicate(s) by default
      const seenPmids = new Set<string>();
      const deduped = resolved.map((item) => {
        const trimmed = item.pmid.trim();
        if (item.selected && trimmed) {
          if (seenPmids.has(trimmed)) {
            return { ...item, selected: false };
          }
          seenPmids.add(trimmed);
        }
        return item;
      });

      setReviewItems(deduped);
      setDialogState('confirm');
    } catch (err) {
      if (controller.signal.aborted) return;
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to inspect PDF file.',
      );
      setDialogState('error');
    } finally {
      if (resolveAbortRef.current === controller) {
        resolveAbortRef.current = null;
      }
    }
  };

  const updateReviewItem = useCallback(
    (id: string, patch: Partial<ReviewItem>) => {
      setReviewItems((prev) =>
        prev.map((item) => (item.id === id ? { ...item, ...patch } : item)),
      );
    },
    [],
  );

  const schedulePmidVerify = useCallback(
    (itemId: string, rawPmid: string, resolvedPmid: string | null | undefined) => {
      const prevTimer = verifyTimersRef.current.get(itemId);
      if (prevTimer) {
        clearTimeout(prevTimer);
        verifyTimersRef.current.delete(itemId);
      }
      const prevController = verifyControllersRef.current.get(itemId);
      if (prevController) {
        prevController.abort();
        verifyControllersRef.current.delete(itemId);
      }

      const trimmed = rawPmid.trim();
      if (!PMID_RE.test(trimmed) || (resolvedPmid && trimmed === resolvedPmid)) {
        updateReviewItem(itemId, { pmidCheck: null });
        return;
      }

      updateReviewItem(itemId, {
        pmidCheck: { pmid: trimmed, status: 'checking', result: null },
      });

      const timer = setTimeout(async () => {
        verifyTimersRef.current.delete(itemId);
        const controller = new AbortController();
        verifyControllersRef.current.set(itemId, controller);
        try {
          const result = await verifyPmid(trimmed, controller.signal);
          if (controller.signal.aborted) return;
          setReviewItems((prev) =>
            prev.map((item) => {
              if (item.id !== itemId || item.pmid.trim() !== trimmed) {
                return item;
              }
              const nowInFlight = Boolean(
                result.existing_job_id &&
                  isStatusInFlight(result.existing_job_status),
              );
              return {
                ...item,
                selected: nowInFlight ? false : item.selected,
                pmidCheck: { pmid: trimmed, status: 'done', result },
              };
            }),
          );
        } catch {
          if (controller.signal.aborted) return;
          setReviewItems((prev) =>
            prev.map((item) =>
              item.id === itemId && item.pmid.trim() === trimmed
                ? { ...item, pmidCheck: null }
                : item,
            ),
          );
        } finally {
          if (verifyControllersRef.current.get(itemId) === controller) {
            verifyControllersRef.current.delete(itemId);
          }
        }
      }, PMID_VERIFY_DEBOUNCE_MS);

      verifyTimersRef.current.set(itemId, timer);
    },
    [updateReviewItem],
  );

  const handlePmidInputChange = useCallback(
    (item: ReviewItem, nextValue: string, autoSelectBulk = false) => {
      const trimmed = nextValue.trim();
      const shouldAutoSelect =
        autoSelectBulk &&
        !item.selected &&
        !item.unreadable &&
        PMID_RE.test(trimmed);
      updateReviewItem(item.id, {
        pmid: nextValue,
        ...(shouldAutoSelect ? { selected: true } : {}),
      });
      schedulePmidVerify(item.id, nextValue, item.resolution?.pmid);
    },
    [schedulePmidVerify, updateReviewItem],
  );

  const isBulk = reviewItems.length > 1;
  const singleItem = reviewItems.length === 1 ? reviewItems[0] : null;

  // Duplicate PMIDs among checked rows in bulk mode
  const duplicateSelectedPmids = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of reviewItems) {
      if (!item.selected) continue;
      const key = item.pmid.trim();
      if (!key) continue;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return new Set(
      Array.from(counts.entries())
        .filter(([, count]) => count > 1)
        .map(([key]) => key),
    );
  }, [reviewItems]);

  const selectedItems = useMemo(
    () => reviewItems.filter((item) => item.selected),
    [reviewItems],
  );

  const selectedInvalidCount = useMemo(
    () =>
      selectedItems.filter((item) => {
        const pmidVal = item.pmid.trim();
        const pmcidVal = item.pmcid.trim();
        if (!PMID_RE.test(pmidVal)) return true;
        if (pmcidVal && !PMCID_RE.test(pmcidVal)) return true;
        return false;
      }).length,
    [selectedItems],
  );

  const selectedNotFoundCount = useMemo(
    () => selectedItems.filter((item) => isPmidNotFound(item)).length,
    [selectedItems],
  );

  const eligibleBulkItems = useMemo(
    () =>
      reviewItems.filter((item) => {
        if (item.unreadable) return false;
        const existing = getEffectiveExistingJob(item);
        return !(existing && isStatusInFlight(existing.status));
      }),
    [reviewItems],
  );

  const allEligibleSelected =
    eligibleBulkItems.length > 0 &&
    eligibleBulkItems.every((item) => item.selected);

  const handleToggleAll = (checked: boolean) => {
    setReviewItems((prev) =>
      prev.map((item) => {
        const existing = getEffectiveExistingJob(item);
        const inFlight = Boolean(existing && isStatusInFlight(existing.status));
        if (item.unreadable || inFlight) return { ...item, selected: false };
        return { ...item, selected: checked };
      }),
    );
  };

  const handleConfirmSingle = async (force = false) => {
    if (!singleItem || submitting) return;
    const trimmedPmid = singleItem.pmid.trim();
    const trimmedPmcid = singleItem.pmcid.trim();
    if (!trimmedPmid) return;

    setSubmitting(true);
    setErrorMessage('');

    try {
      const resolvedTitle = getEffectiveTitle(singleItem) ?? undefined;
      const job = await uploadPdf(
        singleItem.file,
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

      onSuccess?.([
        {
          pmid: trimmedPmid,
          job,
          title: resolvedTitle ?? singleItem.file.name,
        },
      ]);
      handleClose();
    } catch (submitError) {
      if (
        submitError instanceof Error &&
        'status' in submitError &&
        submitError.status === 409 &&
        !submitError.message.toLowerCase().includes('already being processed')
      ) {
        setErrorMessage(submitError.message);
        setDialogState('confirm_regenerate');
        return;
      }
      setErrorMessage(
        submitError instanceof Error ? submitError.message : 'Failed to upload PDF.',
      );
      setDialogState('error');
    } finally {
      setSubmitting(false);
    }
  };

  const handleConfirmBulk = async () => {
    if (
      submitting ||
      selectedItems.length === 0 ||
      selectedInvalidCount > 0 ||
      duplicateSelectedPmids.size > 0
    ) {
      return;
    }

    setSubmitting(true);
    setErrorMessage('');

    try {
      const outcomes = await runWithConcurrency(
        selectedItems,
        SUBMIT_CONCURRENCY,
        async (item) => {
          const trimmedPmid = item.pmid.trim();
          const trimmedPmcid = item.pmcid.trim();
          const resolvedTitle = getEffectiveTitle(item) ?? undefined;
          const existing = getEffectiveExistingJob(item);
          const force = existing?.status === 'completed';
          try {
            const job = await uploadPdf(
              item.file,
              trimmedPmid,
              force,
              trimmedPmcid || undefined,
              resolvedTitle,
            );
            return {
              ok: true as const,
              upload: {
                pmid: trimmedPmid,
                job,
                title: resolvedTitle ?? item.file.name,
              },
            };
          } catch (err) {
            return {
              ok: false as const,
              file: item.file.name,
              pmid: trimmedPmid,
              error: err instanceof Error ? err.message : 'Upload failed',
            };
          }
        },
      );

      const started = outcomes
        .filter((o): o is { ok: true; upload: StartedPdfUpload } => o.ok)
        .map((o) => o.upload);
      const failed = outcomes.filter(
        (o): o is { ok: false; file: string; pmid: string; error: string } => !o.ok,
      );

      if (started.length > 0) {
        toast.info(
          started.length === 1
            ? `Parsing article (PMID ${started[0].pmid})...`
            : `Parsing ${started.length} articles...`,
          {
            description:
              "Articles are being parsed in the background. You'll be notified as each finishes.",
            position: 'top-right',
          },
        );
        onSuccess?.(started);
      }

      if (failed.length > 0) {
        toast.error(
          `${failed.length} article${failed.length === 1 ? '' : 's'} could not be started`,
          {
            description: failed
              .slice(0, 3)
              .map((f) => `PMID ${f.pmid}: ${f.error}`)
              .join(' · '),
            position: 'top-right',
          },
        );
      }

      if (started.length > 0) {
        handleClose();
      } else if (failed.length > 0) {
        setErrorMessage(failed.map((f) => `${f.file}: ${f.error}`).join('\n'));
        setDialogState('error');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleTryAgain = () => {
    setErrorMessage('');
    setDialogState(reviewItems.length > 0 ? 'confirm' : 'idle');
  };

  const singleFullTitle = singleItem ? getEffectiveTitle(singleItem) : null;
  const singlePreviewTitle = singleFullTitle
    ? truncateTitle(singleFullTitle)
    : null;
  const singleExisting = singleItem ? getEffectiveExistingJob(singleItem) : null;
  const singleInFlight = Boolean(
    singleExisting && isStatusInFlight(singleExisting.status),
  );
  const singleAlreadyExists = Boolean(
    !singleInFlight && singleExisting?.status === 'completed',
  );
  const singlePmidNotFound = singleItem ? isPmidNotFound(singleItem) : false;
  const singlePmidChecking = singleItem ? isPmidChecking(singleItem) : false;
  const singleVerifiedTitle =
    singleItem &&
    singleItem.pmidCheck?.status === 'done' &&
    singleItem.pmidCheck.pmid === singleItem.pmid.trim() &&
    singleItem.pmidCheck.result?.exists === true
      ? singleItem.pmidCheck.result.title?.trim() || null
      : null;

  const progressPercent =
    resolveProgress.total > 0
      ? Math.round((resolveProgress.done / resolveProgress.total) * 100)
      : 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          handleClose();
        }
      }}
    >
      <DialogContent
        className={
          dialogState === 'confirm' && isBulk
            ? 'sm:max-w-[820px]'
            : 'sm:max-w-[540px]'
        }
      >
        <DialogHeader>
          <DialogTitle>
            {dialogState === 'confirm'
              ? isBulk
                ? `Confirm ${reviewItems.length} Articles`
                : 'Confirm Article Details'
              : 'Upload PDFs'}
          </DialogTitle>
          <DialogDescription>
            {dialogState === 'confirm'
              ? isBulk
                ? 'Review, edit, or uncheck articles before starting analysis. Cancel will not upload anything.'
                : 'Review or edit the detected PMID and PMCID before starting the analysis.'
              : 'Drop one or more PDFs, a folder, or a .zip archive to look up PubMed identifiers before uploading.'}
          </DialogDescription>
        </DialogHeader>

        {dialogState === 'idle' && (
          <>
            <div className="grid gap-4 py-4">
              <input
                id="pdf-file"
                ref={fileInputRef}
                type="file"
                accept=".pdf,.zip"
                multiple
                className="hidden"
                onChange={handleFileInputChange}
              />
              <input
                id="pdf-folder"
                ref={folderInputRef}
                type="file"
                multiple
                // @ts-expect-error webkitdirectory is supported in Chromium/Firefox/Safari
                webkitdirectory=""
                className="hidden"
                onChange={handleFileInputChange}
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
                className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  isDragging
                    ? 'border-primary bg-primary/5'
                    : files.length > 0
                      ? 'border-primary/50 bg-muted/30 hover:border-primary'
                      : 'border-muted-foreground/25 hover:border-primary/60 hover:bg-muted/30'
                }`}
              >
                {unpacking ? (
                  <div className="flex flex-col items-center gap-2 py-3">
                    <Loader2 className="h-7 w-7 animate-spin text-primary" />
                    <p className="text-sm text-muted-foreground">
                      Reading files...
                    </p>
                  </div>
                ) : (
                  <>
                    <div className="flex h-11 w-11 items-center justify-center rounded-full bg-muted">
                      <Upload className="h-5 w-5 text-muted-foreground" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-sm font-medium text-foreground">
                        Drag and drop PDFs, a folder, or a .zip here
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Click to browse PDFs / .zip archives (up to {MAX_BULK_PDFS} PDFs per batch)
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        folderInputRef.current?.click();
                      }}
                    >
                      <FolderOpen className="mr-1.5 h-3.5 w-3.5" />
                      Choose Folder
                    </Button>
                  </>
                )}
              </div>

              {files.length > 0 && (
                <div className="grid gap-2">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      {files.length} PDF{files.length === 1 ? '' : 's'} staged
                    </span>
                    <button
                      type="button"
                      onClick={handleClearFiles}
                      className="text-muted-foreground underline hover:text-foreground"
                    >
                      Clear all
                    </button>
                  </div>
                  <div className="max-h-40 space-y-1.5 overflow-y-auto rounded-md border bg-muted/20 p-2">
                    {files.map((f, idx) => (
                      <div
                        key={`${f.name}-${f.size}-${idx}`}
                        className="flex items-center justify-between gap-2 rounded bg-background px-2.5 py-1.5 text-sm border"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <FileText className="h-4 w-4 shrink-0 text-primary" />
                          <span className="truncate font-medium">{f.name}</span>
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {(f.size / 1024 / 1024).toFixed(2)} MB
                          </span>
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 shrink-0"
                          onClick={() => handleRemoveFile(idx)}
                          aria-label={`Remove ${f.name}`}
                        >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {intakeNotices.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  {intakeNotices.join(' ')}
                </p>
              )}

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
                disabled={files.length === 0 || unpacking}
              >
                <Upload className="w-4 h-4 mr-2" />
                {files.length > 1 ? `Upload (${files.length})` : 'Upload'}
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'resolving' && (
          <>
            <div className="flex flex-col items-center gap-4 py-10">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <div className="w-full max-w-xs space-y-2 text-center">
                <p className="text-sm font-medium text-foreground">
                  {resolveProgress.total > 1
                    ? `Inspecting ${Math.min(resolveProgress.done + 1, resolveProgress.total)} of ${resolveProgress.total} PDFs...`
                    : 'Inspecting PDF...'}
                </p>
                {resolveProgress.total > 1 ? (
                  <Progress value={progressPercent} className="h-2" />
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Extracting title, PMID, and PMCID from {files[0]?.name}
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

        {dialogState === 'confirm' && singleItem && (
          <>
            <div className="grid gap-4 py-4">
              <div className="grid gap-1.5 rounded-md border bg-muted/40 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Article Title
                  </span>
                  {singleItem.resolution && (
                    <div className="flex items-center gap-1.5">
                      <Badge variant="secondary" className="text-xs font-normal">
                        {MATCH_METHOD_LABELS[singleItem.resolution.match_method]}
                      </Badge>
                      {singleItem.resolution.confidence === 'medium' && (
                        <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100 dark:bg-amber-900/60 dark:text-amber-100 text-xs font-normal">
                          Verify match
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
                <p
                  className="text-sm font-medium text-foreground"
                  title={singleFullTitle ?? undefined}
                >
                  {singlePreviewTitle ?? 'Title could not be extracted'}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  File: {singleItem.file.name}
                </p>
              </div>

              {singleInFlight && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    PMID {singleItem.pmid.trim()} is already being processed in the pipeline.
                  </AlertDescription>
                </Alert>
              )}

              {singleAlreadyExists && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    An analysis already exists for PMID {singleItem.pmid.trim()}. Confirming will ask whether to regenerate it.
                  </AlertDescription>
                </Alert>
              )}

              {singlePmidNotFound && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    PMID {singleItem.pmid.trim()} was not found in PubMed. Double-check the ID, or click Confirm if you still want to proceed.
                  </AlertDescription>
                </Alert>
              )}

              {singleItem.resolution && !singleItem.resolution.pmid && !singlePmidNotFound && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="grid gap-1 text-xs">
                    <span>
                      {singleItem.resolution.warnings[0] ??
                        'Could not automatically match this PDF to PubMed. Enter the PMID below to continue.'}
                    </span>
                    {singleItem.resolution.warnings.slice(1).map((warning, idx) => (
                      <span key={idx}>{warning}</span>
                    ))}
                  </AlertDescription>
                </Alert>
              )}

              {singleItem.resolution?.pmid && singleItem.resolution.warnings.length > 0 && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    {singleItem.resolution.warnings.join(' ')}
                  </AlertDescription>
                </Alert>
              )}

              {singleItem.resolveError && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    Automatic lookup failed ({singleItem.resolveError}). Enter the PMID manually to proceed.
                  </AlertDescription>
                </Alert>
              )}

              <div className="grid gap-3">
                <div className="grid gap-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor="confirm-pmid">PubMed ID (PMID)</Label>
                    {singlePmidChecking && (
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Checking PubMed...
                      </span>
                    )}
                  </div>
                  <Input
                    id="confirm-pmid"
                    placeholder="e.g., 38234567"
                    value={singleItem.pmid}
                    onChange={(e) =>
                      handlePmidInputChange(singleItem, e.target.value, false)
                    }
                    disabled={submitting}
                  />
                  {singleVerifiedTitle && (
                    <p
                      className="text-xs text-muted-foreground"
                      title={singleVerifiedTitle}
                    >
                      PubMed match: {truncateTitle(singleVerifiedTitle)}
                    </p>
                  )}
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="confirm-pmcid">
                    PubMed Central ID (PMCID){' '}
                    <span className="font-normal text-muted-foreground">(optional)</span>
                  </Label>
                  <Input
                    id="confirm-pmcid"
                    placeholder={singleItem.resolution?.pmcid ? 'e.g., PMC10987654' : 'N/A'}
                    value={singleItem.pmcid}
                    onChange={(e) =>
                      updateReviewItem(singleItem.id, { pmcid: e.target.value })
                    }
                    disabled={submitting}
                  />
                  {singleItem.pmcid.trim() && (
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
                onClick={() => handleConfirmSingle()}
                disabled={!singleItem.pmid.trim() || singleInFlight || submitting}
              >
                {submitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                {submitting ? 'Starting...' : 'Confirm'}
              </Button>
            </DialogFooter>
          </>
        )}

        {dialogState === 'confirm' && isBulk && (
          <>
            <div className="grid gap-3 py-2">
              {duplicateSelectedPmids.size > 0 && (
                <Alert variant="destructive">
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription className="text-xs">
                    Duplicate PMID selected in batch ({Array.from(duplicateSelectedPmids).join(', ')}). Uncheck or edit duplicates before confirming.
                  </AlertDescription>
                </Alert>
              )}

              {selectedInvalidCount > 0 && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    {selectedInvalidCount} selected row{selectedInvalidCount === 1 ? '' : 's'} {selectedInvalidCount === 1 ? 'needs' : 'need'} a valid numeric PMID (and valid optional PMCID).
                  </AlertDescription>
                </Alert>
              )}

              {selectedNotFoundCount > 0 && (
                <Alert className="border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                  <AlertTriangle className="h-4 w-4 !text-amber-600 dark:!text-amber-400" />
                  <AlertDescription className="text-xs">
                    {selectedNotFoundCount} selected row{selectedNotFoundCount === 1 ? '' : 's'} {selectedNotFoundCount === 1 ? 'has a PMID that was' : 'have PMIDs that were'} not found in PubMed. Double-check before confirming, or proceed anyway.
                  </AlertDescription>
                </Alert>
              )}

              <div className="max-h-[420px] overflow-auto rounded-md border">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 z-10 border-b bg-muted/80 text-xs uppercase tracking-wide text-muted-foreground backdrop-blur">
                    <tr>
                      <th className="w-10 px-3 py-2.5">
                        <Checkbox
                          checked={allEligibleSelected}
                          onCheckedChange={(val) => handleToggleAll(Boolean(val))}
                          aria-label="Select all eligible articles"
                          disabled={submitting || eligibleBulkItems.length === 0}
                        />
                      </th>
                      <th className="px-3 py-2.5">Article / File</th>
                      <th className="w-32 px-3 py-2.5">PMID</th>
                      <th className="w-36 px-3 py-2.5">PMCID</th>
                      <th className="w-36 px-3 py-2.5">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {reviewItems.map((item) => {
                      const trimmedPmid = item.pmid.trim();
                      const existing = getEffectiveExistingJob(item);
                      const inFlight = Boolean(
                        existing && isStatusInFlight(existing.status),
                      );
                      const alreadyCompleted = existing?.status === 'completed';
                      const notFoundInPubMed = isPmidNotFound(item);
                      const checkingPubMed = isPmidChecking(item);
                      const isDup =
                        item.selected && duplicateSelectedPmids.has(trimmedPmid);
                      const fullTitle = getEffectiveTitle(item);
                      const displayTitle = fullTitle
                        ? truncateTitle(fullTitle, BULK_TITLE_PREVIEW_MAX_CHARS)
                        : null;

                      let statusBadge: React.ReactNode;
                      if (item.unreadable) {
                        statusBadge = (
                          <Badge
                            variant="destructive"
                            className="text-xs font-normal"
                            title={item.resolveError ?? 'Could not read PDF file'}
                          >
                            Unreadable PDF
                          </Badge>
                        );
                      } else if (inFlight) {
                        statusBadge = (
                          <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100 text-xs font-normal">
                            In progress
                          </Badge>
                        );
                      } else if (isDup) {
                        statusBadge = (
                          <Badge variant="destructive" className="text-xs font-normal">
                            Duplicate
                          </Badge>
                        );
                      } else if (alreadyCompleted) {
                        statusBadge = (
                          <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100 text-xs font-normal">
                            {item.selected ? 'Regenerate' : 'Already exists'}
                          </Badge>
                        );
                      } else if (checkingPubMed) {
                        statusBadge = (
                          <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            Checking
                          </Badge>
                        );
                      } else if (notFoundInPubMed) {
                        statusBadge = (
                          <Badge
                            className="bg-amber-100 text-amber-900 hover:bg-amber-100 text-xs font-normal"
                            title="This PMID was not found in PubMed. Double-check it, or confirm anyway."
                          >
                            Not in PubMed
                          </Badge>
                        );
                      } else if (item.resolveError && !PMID_RE.test(trimmedPmid)) {
                        statusBadge = (
                          <Badge variant="outline" className="text-xs font-normal text-amber-700">
                            Lookup failed
                          </Badge>
                        );
                      } else if (!PMID_RE.test(trimmedPmid)) {
                        statusBadge = (
                          <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
                            Needs PMID
                          </Badge>
                        );
                      } else if (item.pmcid.trim()) {
                        statusBadge = (
                          <Badge className="bg-blue-100 text-blue-900 hover:bg-blue-100 text-xs font-normal">
                            Open Access
                          </Badge>
                        );
                      } else if (
                        item.resolution?.confidence === 'medium' &&
                        trimmedPmid === item.resolution.pmid
                      ) {
                        statusBadge = (
                          <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100 text-xs font-normal">
                            Verify
                          </Badge>
                        );
                      } else {
                        statusBadge = (
                          <Badge className="bg-emerald-100 text-emerald-900 hover:bg-emerald-100 text-xs font-normal">
                            Ready
                          </Badge>
                        );
                      }

                      return (
                        <tr
                          key={item.id}
                          className={
                            item.unreadable
                              ? 'bg-muted/30 opacity-65'
                              : item.selected
                                ? 'bg-background'
                                : 'bg-muted/20 opacity-80'
                          }
                        >
                          <td className="px-3 py-2.5 align-top">
                            <Checkbox
                              checked={item.selected}
                              disabled={item.unreadable || inFlight || submitting}
                              onCheckedChange={(val) =>
                                updateReviewItem(item.id, { selected: Boolean(val) })
                              }
                              aria-label={`Include ${item.file.name}`}
                            />
                          </td>
                          <td className="px-3 py-2 align-top">
                            <p
                              className="font-medium leading-snug text-foreground"
                              title={fullTitle ?? undefined}
                            >
                              {item.unreadable
                                ? 'Unreadable PDF'
                                : (displayTitle ?? 'Title not detected')}
                            </p>
                            <p className="mt-0.5 truncate max-w-[260px] text-xs text-muted-foreground">
                              {item.file.name}
                            </p>
                            {item.unreadable && item.resolveError && (
                              <p className="mt-0.5 truncate max-w-[260px] text-xs text-destructive" title={item.resolveError}>
                                {item.resolveError}
                              </p>
                            )}
                          </td>
                          <td className="px-3 py-2 align-top">
                            <Input
                              value={item.pmid}
                              placeholder="PMID"
                              disabled={item.unreadable || submitting}
                              className="h-8 font-mono text-xs"
                              onChange={(e) =>
                                handlePmidInputChange(item, e.target.value, true)
                              }
                            />
                          </td>
                          <td className="px-3 py-2 align-top">
                            <Input
                              value={item.pmcid}
                              placeholder={item.resolution?.pmcid ? 'PMC...' : 'N/A'}
                              disabled={item.unreadable || inFlight || submitting}
                              className="h-8 font-mono text-xs"
                              onChange={(e) =>
                                updateReviewItem(item.id, { pmcid: e.target.value })
                              }
                            />
                          </td>
                          <td className="px-3 py-2 align-top">{statusBadge}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
            <DialogFooter className="flex items-center justify-between sm:justify-between">
              <span className="text-xs text-muted-foreground">
                {selectedItems.length} of {reviewItems.length} selected
              </span>
              <div className="flex items-center gap-2">
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
                  onClick={handleConfirmBulk}
                  disabled={
                    submitting ||
                    selectedItems.length === 0 ||
                    selectedInvalidCount > 0 ||
                    duplicateSelectedPmids.size > 0
                  }
                >
                  {submitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  {submitting
                    ? 'Starting...'
                    : `Confirm (${selectedItems.length})`}
                </Button>
              </div>
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
                onClick={() => handleConfirmSingle(true)}
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
                <AlertDescription className="whitespace-pre-line">
                  {errorMessage}
                </AlertDescription>
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
