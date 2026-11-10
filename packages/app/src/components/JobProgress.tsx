import React from 'react';
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import { Progress } from '@/components/ui/progress';
import {
  JOB_PROGRESS,
  JOB_STAGES,
  STATUS_LABELS,
  type JobStatus,
} from '@/lib/api';
import { cn } from '@/lib/utils';

interface JobProgressProps {
  status: JobStatus;
  progress?: string | null;
  compact?: boolean;
  className?: string;
}

const stageIndex = (status: JobStatus) => {
  if (status === 'pending') return -1;
  if (status === 'completed') return JOB_STAGES.length;
  return JOB_STAGES.findIndex((stage) => stage.status === status);
};

export const JobProgress: React.FC<JobProgressProps> = ({
  status,
  progress,
  compact = false,
  className,
}) => {
  const completed = status === 'completed';
  const failed = status === 'failed';
  const currentStage = stageIndex(status);
  const label = progress || STATUS_LABELS[status] || status;

  return (
    <div
      className={cn(
        'rounded-lg border px-3 py-2.5',
        failed ? 'border-destructive/30 bg-destructive/5' : 'border-primary/20 bg-primary/[0.035]',
        className,
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2.5">
        {completed ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
        ) : failed ? (
          <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
        ) : (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary motion-reduce:animate-none" />
        )}
        <span className={cn('font-medium', compact ? 'text-xs' : 'text-sm')}>
          {completed ? 'Curation complete' : failed ? 'Curation failed' : label}
        </span>
        {!completed && !failed && (
          <span className="ml-auto text-xs tabular-nums text-muted-foreground">
            {JOB_PROGRESS[status]}%
          </span>
        )}
      </div>

      {!failed && (
        <Progress
          value={JOB_PROGRESS[status]}
          className={cn('mt-2 bg-primary/10', compact ? 'h-1' : 'h-1.5')}
        />
      )}

      {!compact && !failed && (
        <ol className="mt-3 grid grid-cols-5 gap-1" aria-label="Curation stages">
          {JOB_STAGES.map((stage, index) => {
            const isDone = completed || index < currentStage;
            const isCurrent = !completed && index === currentStage;
            return (
              <li
                key={stage.status}
                className={cn(
                  'border-t-2 pt-2 text-xs',
                  isDone && 'border-emerald-500 text-emerald-700',
                  isCurrent && 'border-primary font-medium text-primary',
                  !isDone && !isCurrent && 'border-border text-muted-foreground',
                )}
              >
                {stage.label}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
};
