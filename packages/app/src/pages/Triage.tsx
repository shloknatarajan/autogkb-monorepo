import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { toast } from 'sonner';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  createTriageSession, listTriageSessions, getTriageSession,
  submitTriageArticle, updateTriageArticleDecision, openTriageStream, openJobStream,
  type TriageArticle, type TriageSession, type TriageSessionListItem,
} from '@/lib/api';

const PROJECTS = [
  { id: '68f6813df2b49b9358c64421', name: 'General PGx' },
  { id: '68f682f7da47ae09aeaa9182', name: 'Pediatric PGx' },
];

// Label → badge color
function labelVariant(label: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (label === 'relevant') return 'default';      // green-ish (primary)
  if (label === 'borderline') return 'secondary';  // yellow-ish
  return 'destructive';                            // red
}

const Triage: React.FC = () => {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<TriageSessionListItem[]>([]);
  const [selectedSession, setSelectedSession] = useState<TriageSession | null>(null);
  const [loadingSession, setLoadingSession] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [filter, setFilter] = useState<'all' | 'pending' | 'relevant' | 'borderline' | 'not_relevant'>('all');
  const [streamStatus, setStreamStatus] = useState<string | null>(null);

  // Load sessions list on mount
  useEffect(() => {
    listTriageSessions().then(setSessions);
  }, []);

  // Load a session's full data
  const loadSession = useCallback(async (sessionId: string) => {
    setLoadingSession(true);
    try {
      const session = await getTriageSession(sessionId);
      setSelectedSession(session);
    } catch {
      toast.error('Failed to load session');
    } finally {
      setLoadingSession(false);
    }
  }, []);

  // Auto-select most recent session on load
  useEffect(() => {
    if (sessions.length > 0 && !selectedSession) {
      loadSession(sessions[0].id);
    }
  }, [sessions, selectedSession, loadSession]);

  // Fetch this week's articles for a project
  const handleFetch = async (projectId: string, projectName: string) => {
    setFetching(true);
    setStreamStatus('Starting...');
    try {
      const { session_id } = await createTriageSession(projectId, projectName);

      // Refresh session list
      const updatedSessions = await listTriageSessions();
      setSessions(updatedSessions);
      setSelectedSession(null);

      // Connect to SSE stream
      const es = openTriageStream(session_id);
      es.onmessage = async (e) => {
        const data = JSON.parse(e.data);
        setStreamStatus(`Scoring... ${data.article_count} articles scored`);
        if (data.status === 'completed' || data.status === 'error') {
          es.close();
          setStreamStatus(null);
          setFetching(false);
          if (data.status === 'completed') {
            toast.success(`Scored ${data.article_count} articles`);
            const session = await getTriageSession(session_id);
            setSelectedSession(session);
            setSessions(await listTriageSessions());
          } else {
            toast.error('Triage scoring failed');
          }
        }
      };
      es.onerror = () => {
        es.close();
        setStreamStatus(null);
        setFetching(false);
        toast.error('Connection error during scoring');
      };
    } catch (err: unknown) {
      setFetching(false);
      setStreamStatus(null);
      const message = err instanceof Error ? err.message : 'Failed to start triage';
      toast.error(message);
    }
  };

  // Submit article to pipeline
  const handleSubmit = async (pmid: string) => {
    if (!selectedSession) return;
    try {
      const { job_id } = await submitTriageArticle(selectedSession.id, pmid);
      toast.success(`Submitted PMID ${pmid} to pipeline`);
      // Update local state optimistically
      setSelectedSession(prev => prev ? {
        ...prev,
        articles: prev.articles.map(a =>
          a.pmid === pmid ? { ...a, decision: 'submitted', job_id } : a
        ),
      } : null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Submit failed';
      toast.error(message);
    }
  };

  // Dismiss article
  const handleDismiss = async (pmid: string) => {
    if (!selectedSession) return;
    try {
      await updateTriageArticleDecision(selectedSession.id, pmid, 'dismissed');
      setSelectedSession(prev => prev ? {
        ...prev,
        articles: prev.articles.map(a =>
          a.pmid === pmid ? { ...a, decision: 'dismissed' } : a
        ),
      } : null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Dismiss failed';
      toast.error(message);
    }
  };

  // Filtered articles
  const filteredArticles = (selectedSession?.articles ?? []).filter(a => {
    if (filter === 'all') return true;
    if (filter === 'pending') return a.decision === 'pending';
    return a.triage_label === filter;
  });

  return (
    <div className="min-h-screen bg-gradient-subtle">
      {/* Header */}
      <header className="bg-transparent">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <button onClick={() => navigate('/')} className="text-muted-foreground hover:text-foreground text-sm">
                &larr; Dashboard
              </button>
              <span className="text-muted-foreground">/</span>
              <h1 className="text-lg font-semibold text-foreground">Literature Triage</h1>
            </div>
            {/* Fetch dropdown */}
            <div className="flex items-center gap-2">
              {streamStatus && (
                <span className="text-sm text-muted-foreground animate-pulse">{streamStatus}</span>
              )}
              {PROJECTS.map(p => (
                <Button
                  key={p.id}
                  size="sm"
                  variant="outline"
                  disabled={fetching}
                  onClick={() => handleFetch(p.id, p.name)}
                >
                  {fetching ? 'Fetching...' : `Fetch ${p.name}`}
                </Button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
        <div className="flex gap-6 h-[calc(100vh-120px)]">
          {/* Sidebar — sessions */}
          <div className="w-56 flex-shrink-0">
            <h2 className="text-sm font-medium text-muted-foreground mb-2 uppercase tracking-wide">Sessions</h2>
            <ScrollArea className="h-full">
              <div className="space-y-1">
                {sessions.map(s => (
                  <button
                    key={s.id}
                    onClick={() => loadSession(s.id)}
                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${
                      selectedSession?.id === s.id
                        ? 'bg-primary/10 text-primary font-medium'
                        : 'text-foreground hover:bg-muted'
                    }`}
                  >
                    <div className="font-medium">{s.project_name}</div>
                    <div className="text-xs text-muted-foreground">{s.week_date} &middot; {s.article_count} articles</div>
                    {s.status !== 'completed' && (
                      <Badge variant="outline" className="mt-1 text-xs">{s.status}</Badge>
                    )}
                  </button>
                ))}
                {sessions.length === 0 && (
                  <p className="text-xs text-muted-foreground px-3 py-2">No sessions yet</p>
                )}
              </div>
            </ScrollArea>
          </div>

          <Separator orientation="vertical" />

          {/* Main panel — articles */}
          <div className="flex-1 min-w-0">
            {loadingSession && (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">Loading...</p>
              </div>
            )}
            {!loadingSession && !selectedSession && (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">Select a session or fetch this week&apos;s articles</p>
              </div>
            )}
            {!loadingSession && selectedSession && (
              <>
                {/* Session header + filter */}
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-lg font-semibold">{selectedSession.project_name}</h2>
                    <p className="text-sm text-muted-foreground">
                      Week of {selectedSession.week_date} &middot; {selectedSession.articles.length} articles
                    </p>
                  </div>
                  <div className="flex gap-2">
                    {(['all', 'pending', 'relevant', 'borderline', 'not_relevant'] as const).map(f => (
                      <Button
                        key={f}
                        size="sm"
                        variant={filter === f ? 'default' : 'outline'}
                        onClick={() => setFilter(f)}
                        className="capitalize text-xs"
                      >
                        {f === 'not_relevant' ? 'Not Relevant' : f.charAt(0).toUpperCase() + f.slice(1)}
                      </Button>
                    ))}
                  </div>
                </div>

                {/* Articles list */}
                <ScrollArea className="h-[calc(100%-80px)]">
                  <div className="space-y-3 pr-2">
                    {filteredArticles.map(article => (
                      <ArticleRow
                        key={article.pmid}
                        article={article}
                        onSubmit={handleSubmit}
                        onDismiss={handleDismiss}
                        onViewPipeline={(pmid) => navigate(`/viewer/${pmid}`)}
                      />
                    ))}
                    {filteredArticles.length === 0 && (
                      <p className="text-sm text-muted-foreground text-center py-8">No articles match this filter</p>
                    )}
                  </div>
                </ScrollArea>
              </>
            )}
          </div>
        </div>
      </main>
    </div>
  );
};

interface ArticleRowProps {
  article: TriageArticle;
  onSubmit: (pmid: string) => Promise<void>;
  onDismiss: (pmid: string) => Promise<void>;
  onViewPipeline: (pmid: string) => void;
}

const ArticleRow: React.FC<ArticleRowProps> = ({ article, onSubmit, onDismiss, onViewPipeline }) => {
  const [loading, setLoading] = useState(false);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [jobProgress, setJobProgress] = useState<string>('');
  const [expanded, setExpanded] = useState(false);
  const isDismissed = article.decision === 'dismissed';
  const isCompleted = jobStatus === 'completed';
  const isFailed = jobStatus === 'failed';

  // Open SSE stream for job progress once submitted
  useEffect(() => {
    if (article.decision !== 'submitted' || !article.job_id) return;
    const es = openJobStream(article.job_id);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setJobStatus(data.status);
        setJobProgress(data.progress ?? '');
        if (data.status === 'completed' || data.status === 'failed') es.close();
      } catch { /* ignore parse errors */ }
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [article.decision, article.job_id]);

  const handleAction = async (fn: () => Promise<void>) => {
    setLoading(true);
    try { await fn(); } finally { setLoading(false); }
  };

  return (
    <div className={`border rounded-lg p-4 transition-opacity ${isDismissed ? 'opacity-40' : ''}`}>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={labelVariant(article.triage_label)}>
            {article.triage_label === 'not_relevant' ? 'Not Relevant' :
             article.triage_label.charAt(0).toUpperCase() + article.triage_label.slice(1)}
          </Badge>
          <span className="text-xs text-muted-foreground">VA score: {article.triage_score}</span>
          <span className="text-xs text-muted-foreground">LS: {article.litsuggest_score.toFixed(3)}</span>
          <span className="text-xs text-muted-foreground font-mono">PMID {article.pmid}</span>
          {article.pmcid && (
            <span className="text-xs text-muted-foreground font-mono">{article.pmcid}</span>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          {article.decision === 'submitted' ? (
            isCompleted ? (
              <Button size="sm" variant="outline" onClick={() => onViewPipeline(article.pmid)}>
                View in Pipeline
              </Button>
            ) : isFailed ? (
              <span className="text-xs text-destructive self-center">Pipeline error</span>
            ) : (
              <span className="text-xs text-muted-foreground self-center animate-pulse">
                {jobProgress || 'Processing…'}
              </span>
            )
          ) : (
            <>
              {!isDismissed && (
                article.pmcid ? (
                  <Button
                    size="sm"
                    disabled={loading}
                    onClick={() => handleAction(() => onSubmit(article.pmid))}
                  >
                    Submit to Pipeline
                  </Button>
                ) : (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="cursor-not-allowed">
                        <Button size="sm" disabled className="pointer-events-none">
                          Submit to Pipeline
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      No full text available (not in PubMed Central)
                    </TooltipContent>
                  </Tooltip>
                )
              )}
              {!isDismissed && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={loading}
                  onClick={() => handleAction(() => onDismiss(article.pmid))}
                >
                  Dismiss
                </Button>
              )}
            </>
          )}
        </div>
      </div>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <a
              href={`https://pubmed.ncbi.nlm.nih.gov/${article.pmid}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium mb-1 hover:underline decoration-muted-foreground/50 underline-offset-2 block"
            >
              {article.title ?? '(No title)'}
            </a>
            {!expanded && (
              <p className="text-xs text-muted-foreground italic line-clamp-2">&ldquo;{article.reasoning}&rdquo;</p>
            )}
          </div>
          <CollapsibleTrigger asChild>
            <button
              className="flex-shrink-0 mt-0.5 text-muted-foreground hover:text-foreground transition-colors"
              aria-label={expanded ? 'Collapse' : 'Expand abstract and reasoning'}
            >
              <ChevronDown
                className={`h-4 w-4 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
              />
            </button>
          </CollapsibleTrigger>
        </div>

        <CollapsibleContent>
          <div className="mt-3 space-y-3 border-t pt-3">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Reasoning</p>
              <p className="text-xs text-foreground italic">&ldquo;{article.reasoning}&rdquo;</p>
            </div>
            {article.abstract && (
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Abstract</p>
                <p className="text-xs text-muted-foreground leading-relaxed">{article.abstract}</p>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
};

export default Triage;
