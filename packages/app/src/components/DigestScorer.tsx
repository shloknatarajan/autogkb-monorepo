import React, { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { AlertCircle, ChevronRight, ExternalLink, Loader2 } from 'lucide-react';
import { scorePapers, analyzePmid, getJob, STATUS_LABELS, type ScoredPaper, type JobResponse } from '@/lib/api';

type AnalyzeState =
  | { status: 'idle' }
  | { status: 'analyzing'; statusLabel: string }
  | { status: 'done'; jobData: JobResponse }
  | { status: 'error'; message: string };

function parsePmids(raw: string): string[] {
  return [...new Set(
    raw
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter((s) => /^\d{1,10}$/.test(s))
  )];
}

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 70
      ? 'bg-green-100 text-green-800 border-green-200'
      : score >= 40
      ? 'bg-yellow-100 text-yellow-800 border-yellow-200'
      : 'bg-red-100 text-red-800 border-red-200';

  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border ${color}`}>
      {score}/100
    </span>
  );
}

function PaperCard({
  paper,
  onAnalyzed,
}: {
  paper: ScoredPaper;
  onAnalyzed: (pmid: string, job: JobResponse) => void;
}) {
  const navigate = useNavigate();
  const [analyzeState, setAnalyzeState] = useState<AnalyzeState>({ status: 'idle' });
  const [abstractOpen, setAbstractOpen] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const handleAnalyze = async () => {
    setAnalyzeState({ status: 'analyzing', statusLabel: STATUS_LABELS['pending'] ?? 'Starting...' });
    try {
      const job = await analyzePmid(paper.pmid);
      if (job.status === 'completed') {
        stopPolling();
        setAnalyzeState({ status: 'done', jobData: job });
        onAnalyzed(paper.pmid, job);
        return;
      }
      if (job.status === 'failed') {
        setAnalyzeState({ status: 'error', message: job.error ?? 'Analysis failed.' });
        return;
      }

      setAnalyzeState({ status: 'analyzing', statusLabel: STATUS_LABELS[job.status] ?? job.status });
      const jobId = job.job_id;

      intervalRef.current = setInterval(async () => {
        try {
          const updated = await getJob(jobId);
          setAnalyzeState({ status: 'analyzing', statusLabel: STATUS_LABELS[updated.status] ?? updated.status });
          if (updated.status === 'completed') {
            stopPolling();
            setAnalyzeState({ status: 'done', jobData: updated });
            onAnalyzed(paper.pmid, updated);
          } else if (updated.status === 'failed') {
            stopPolling();
            setAnalyzeState({ status: 'error', message: updated.error ?? 'Analysis failed.' });
          }
        } catch {
          stopPolling();
          setAnalyzeState({ status: 'error', message: 'Failed to check job status.' });
        }
      }, 2000);
    } catch (e) {
      setAnalyzeState({ status: 'error', message: e instanceof Error ? e.message : 'Failed to start analysis.' });
    }
  };

  const abstractSnippet = paper.abstract
    ? paper.abstract.slice(0, 200) + (paper.abstract.length > 200 ? '…' : '')
    : null;

  return (
    <Card className="border-border bg-card">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="px-2.5 py-1 bg-primary/10 text-primary text-xs font-medium rounded-full">
                PMID {paper.pmid}
              </span>
              <ScoreBadge score={paper.score} />
            </div>
            <CardTitle className="text-base leading-snug line-clamp-2">
              {paper.title ?? 'Title unavailable'}
            </CardTitle>
          </div>

          <div className="flex-shrink-0">
            {analyzeState.status === 'idle' && (
              <Button size="sm" onClick={handleAnalyze}>
                Analyze
              </Button>
            )}
            {analyzeState.status === 'analyzing' && (
              <Button size="sm" disabled>
                <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                {analyzeState.statusLabel}
              </Button>
            )}
            {analyzeState.status === 'done' && (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  navigate(`/viewer/${paper.pmid}`, {
                    state: { dynamicData: analyzeState.jobData },
                  })
                }
              >
                <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
                View Analysis
              </Button>
            )}
            {analyzeState.status === 'error' && (
              <Button size="sm" variant="destructive" onClick={() => setAnalyzeState({ status: 'idle' })}>
                Retry
              </Button>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-2">
        <p className="text-sm text-muted-foreground italic">"{paper.reasoning}"</p>

        {analyzeState.status === 'error' && (
          <Alert variant="destructive" className="py-2">
            <AlertCircle className="h-3.5 w-3.5" />
            <AlertDescription className="text-xs">{analyzeState.message}</AlertDescription>
          </Alert>
        )}

        {abstractSnippet && (
          <Collapsible open={abstractOpen} onOpenChange={setAbstractOpen}>
            <CollapsibleTrigger asChild>
              <button className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
                <ChevronRight
                  className={`h-3.5 w-3.5 transition-transform ${abstractOpen ? 'rotate-90' : ''}`}
                />
                {abstractOpen ? 'Hide abstract' : 'Show abstract'}
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
                {paper.abstract}
              </p>
            </CollapsibleContent>
          </Collapsible>
        )}

        {paper.error && !paper.title && (
          <p className="text-xs text-destructive">Could not fetch paper: {paper.error}</p>
        )}
      </CardContent>
    </Card>
  );
}

const DigestScorer: React.FC = () => {
  const [pmidInput, setPmidInput] = useState('');
  const [results, setResults] = useState<ScoredPaper[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minScore, setMinScore] = useState('0');
  const [sortOrder, setSortOrder] = useState<'score' | 'pmid'>('score');
  const [analyzedJobs, setAnalyzedJobs] = useState<Map<string, JobResponse>>(new Map());

  const handleScore = async () => {
    const pmids = parsePmids(pmidInput);
    if (pmids.length === 0) {
      setError('No valid PMIDs found. PMIDs are numeric (e.g. 32948745).');
      return;
    }
    if (pmids.length > 100) {
      setError('Please enter 100 or fewer PMIDs at a time.');
      return;
    }

    setLoading(true);
    setError(null);
    setResults([]);
    setAnalyzedJobs(new Map());

    try {
      const scored = await scorePapers(pmids);
      setResults(scored);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to score papers.');
    } finally {
      setLoading(false);
    }
  };

  const handleAnalyzed = useCallback((pmid: string, job: JobResponse) => {
    setAnalyzedJobs((prev) => new Map(prev).set(pmid, job));
  }, []);

  const threshold = parseInt(minScore, 10);
  const visibleResults = results
    .filter((p) => p.score >= threshold)
    .sort((a, b) =>
      sortOrder === 'score' ? b.score - a.score : a.pmid.localeCompare(b.pmid)
    );

  const parsedCount = parsePmids(pmidInput).length;

  return (
    <div className="space-y-6">
      {/* Input area */}
      <div className="max-w-2xl mx-auto space-y-3">
        <div className="space-y-1.5">
          <label className="text-sm font-medium text-foreground">
            Paste PMIDs from your weekly digest
          </label>
          <Textarea
            placeholder={"32948745\n38234567\n39812345\n..."}
            value={pmidInput}
            onChange={(e) => setPmidInput(e.target.value)}
            className="min-h-[120px] font-mono text-sm resize-y"
          />
          <p className="text-xs text-muted-foreground">
            One per line, or comma/space separated.
            {parsedCount > 0 && (
              <span className="ml-1 font-medium text-foreground">{parsedCount} PMID{parsedCount !== 1 ? 's' : ''} detected.</span>
            )}
          </p>
        </div>

        <Button onClick={handleScore} disabled={loading || parsedCount === 0} size="lg">
          {loading ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Scoring {parsedCount} papers…
            </>
          ) : (
            'Score Papers'
          )}
        </Button>

        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <p className="text-sm text-muted-foreground">
              Showing{' '}
              <span className="font-medium text-foreground">{visibleResults.length}</span>
              {' '}of{' '}
              <span className="font-medium text-foreground">{results.length}</span>{' '}
              papers
              {analyzedJobs.size > 0 && (
                <span className="ml-1">· {analyzedJobs.size} analyzed</span>
              )}
            </p>

            <div className="flex items-center gap-2">
              <Select value={minScore} onValueChange={setMinScore}>
                <SelectTrigger className="w-36 h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">All scores</SelectItem>
                  <SelectItem value="30">Score ≥ 30</SelectItem>
                  <SelectItem value="50">Score ≥ 50</SelectItem>
                  <SelectItem value="70">Score ≥ 70</SelectItem>
                  <SelectItem value="90">Score ≥ 90</SelectItem>
                </SelectContent>
              </Select>

              <Select value={sortOrder} onValueChange={(v) => setSortOrder(v as 'score' | 'pmid')}>
                <SelectTrigger className="w-32 h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="score">Sort: Score</SelectItem>
                  <SelectItem value="pmid">Sort: PMID</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {visibleResults.length === 0 ? (
            <div className="text-center py-10 text-muted-foreground text-sm">
              No papers match the current score filter.
            </div>
          ) : (
            <div className="space-y-3">
              {visibleResults.map((paper) => (
                <PaperCard
                  key={paper.pmid}
                  paper={paper}
                  onAnalyzed={handleAnalyzed}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default DigestScorer;
