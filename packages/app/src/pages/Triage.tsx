import React, { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarDays, Check, ChevronDown, Filter as FilterIcon, Loader2, RotateCcw, Search, Send, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { JobProgress } from '@/components/JobProgress';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { toast } from 'sonner';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  createTriageSession, listTriageSessions, getTriageSession, regenerateTriageSession,
  submitTriageArticle, submitAllTriageArticles, updateTriageArticleDecision,
  openTriageStream, openJobStream, getJob,
  type PaperType, type PgxInformationType, type TriageArticle, type TriageSession,
  type TriageSessionListItem, type JobStatus,
} from '@/lib/api';

const PROJECTS = [
  { id: '68f6813df2b49b9358c64421', name: 'General PGx' },
  { id: '68f682f7da47ae09aeaa9182', name: 'Pediatric PGx' },
];

type TriageFilter = 'all' | 'pending' | 'relevant' | 'borderline' | 'not_relevant' |
  'variant_annotations' | 'general_pharmacogenomics' | PaperType | PgxInformationType;

type StatusFilter = 'all' | 'pending' | 'relevant' | 'borderline' | 'not_relevant';
type FacetFilter = Exclude<TriageFilter, StatusFilter>;

interface ScopedArticle {
  article: TriageArticle;
  sessionId: string;
  weekDate: string;
}

const FILTER_LABELS: Record<TriageFilter, string> = {
  all: 'All',
  pending: 'Pending',
  relevant: 'Relevant',
  borderline: 'Borderline',
  not_relevant: 'Not Relevant',
  variant_annotations: 'Variant Annotations',
  general_pharmacogenomics: 'PGx',
  methods: 'Methods',
  population_frequency: 'Population Frequency',
  allele_function: 'Allele Function',
  implementation: 'Implementation',
  review: 'Review',
  phenoconversion: 'Phenoconversion',
  pd: 'PD',
  dosage: 'Dosage',
  efficacy: 'Efficacy',
  toxicity: 'Toxicity',
  other: 'Other',
  metabolism_pk: 'Metabolism/PK',
};

const PAPER_TYPE_FILTERS: PaperType[] = [
  'methods', 'population_frequency', 'allele_function',
  'implementation', 'review', 'phenoconversion',
];

const PGX_INFORMATION_FILTERS: PgxInformationType[] = [
  'pd', 'dosage', 'efficacy', 'toxicity', 'other', 'metabolism_pk',
];

const STATUS_FILTERS: StatusFilter[] = ['all', 'pending', 'relevant', 'borderline', 'not_relevant'];
const CURATABLE_FILTERS: FacetFilter[] = ['variant_annotations', 'general_pharmacogenomics'];

const parseWeekDate = (date: string) => new Date(`${date}T00:00:00Z`);

const formatWeekRange = (date: string) => {
  const start = parseWeekDate(date);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 6);
  const startMonth = start.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
  const endMonth = end.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
  const startDay = start.getUTCDate();
  const endDay = end.getUTCDate();
  const year = end.getUTCFullYear();
  return startMonth === endMonth
    ? `${startMonth} ${startDay}–${endDay}, ${year}`
    : `${startMonth} ${startDay}–${endMonth} ${endDay}, ${year}`;
};

const formatMonth = (date: string) => parseWeekDate(date).toLocaleDateString('en-US', {
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
});

// Label → badge color
function labelVariant(label: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (label === 'relevant') return 'default';      // green-ish (primary)
  if (label === 'borderline') return 'secondary';  // yellow-ish
  return 'destructive';                            // red
}

const Triage: React.FC = () => {
  const navigate = useNavigate();
  const [selectedProjectId, setSelectedProjectId] = useState(PROJECTS[0].id);
  const [allSessions, setAllSessions] = useState<TriageSessionListItem[]>([]);
  const [selectedSession, setSelectedSession] = useState<TriageSession | null>(null);
  const [archiveSessions, setArchiveSessions] = useState<TriageSession[]>([]);
  const [showAllSessions, setShowAllSessions] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [loadingArchive, setLoadingArchive] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [submittingAll, setSubmittingAll] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [facetFilters, setFacetFilters] = useState<FacetFilter[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [articleLimit, setArticleLimit] = useState(100);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [streamStatus, setStreamStatus] = useState<string | null>(null);
  const selectedProjectIdRef = useRef(selectedProjectId);
  const articleElements = useRef(new Map<string, HTMLDivElement>());
  const articlePositions = useRef(new Map<string, number>());

  const selectedProject = PROJECTS.find(project => project.id === selectedProjectId) ?? PROJECTS[0];
  const sessions = useMemo(
    () => allSessions.filter(session => session.project_id === selectedProjectId),
    [allSessions, selectedProjectId],
  );

  // Load sessions list on mount
  const loadSessions = useCallback(async () => {
    const s = await listTriageSessions();
    setAllSessions([...s].sort((a, b) => b.week_date.localeCompare(a.week_date)));
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Load a session's full data
  const loadSession = useCallback(async (sessionId: string) => {
    setLoadingSession(true);
    setShowAllSessions(false);
    try {
      const session = await getTriageSession(sessionId);
      if (session.project_id === selectedProjectIdRef.current) {
        setSelectedSession(session);
      }
    } catch {
      toast.error('Failed to load session');
    } finally {
      setLoadingSession(false);
    }
  }, []);

  const loadAllSessions = useCallback(async () => {
    setShowAllSessions(true);
    setLoadingArchive(true);
    try {
      const availableIds = new Set(sessions.map(session => session.id));
      const loaded = archiveSessions.filter(session => availableIds.has(session.id));
      const loadedIds = new Set(loaded.map(session => session.id));
      const queue = sessions.filter(session => !loadedIds.has(session.id));
      let failures = 0;

      const workers = Array.from({ length: Math.min(6, queue.length) }, async () => {
        while (queue.length > 0) {
          const summary = queue.shift();
          if (!summary) return;
          try {
            loaded.push(await getTriageSession(summary.id));
          } catch {
            failures += 1;
          }
        }
      });
      await Promise.all(workers);
      loaded.sort((a, b) => b.week_date.localeCompare(a.week_date));
      if (loaded.every(session => session.project_id === selectedProjectIdRef.current)) {
        setArchiveSessions(loaded);
      }
      if (failures) toast.error(`Could not load ${failures} weekly ${failures === 1 ? 'dump' : 'dumps'}`);
    } finally {
      setLoadingArchive(false);
    }
  }, [archiveSessions, sessions]);

  const handleProjectChange = (projectId: string) => {
    if (!projectId || projectId === selectedProjectId) return;
    selectedProjectIdRef.current = projectId;
    setSelectedProjectId(projectId);
    setSelectedSession(null);
    setArchiveSessions([]);
    setShowAllSessions(false);
  };

  // Auto-select most recent session on load
  useEffect(() => {
    if (sessions.length > 0 && !selectedSession) {
      loadSession(sessions[0].id);
    }
  }, [sessions, selectedSession, loadSession]);

  // Refresh only the selected session when its background status changes.
  useEffect(() => {
    if (!selectedSession || showAllSessions) return;
    const summary = sessions.find(session => session.id === selectedSession.id);
    if (!summary || summary.status === selectedSession.status) return;
    let cancelled = false;
    getTriageSession(selectedSession.id).then(session => {
      if (!cancelled) {
        setSelectedSession(current => current?.id === session.id ? session : current);
      }
    }).catch(() => { /* the next poll will retry */ });
    return () => { cancelled = true; };
  }, [sessions, selectedSession, showAllSessions]);

  // Populate separate sessions for missing weeks among the four latest digests.
  const handleFetch = async (projectId: string, projectName: string) => {
    setFetching(true);
    setStreamStatus(`Checking the latest four ${projectName} digests...`);
    try {
      const result = await createTriageSession(projectId, projectName);

      await loadSessions();

      if (result.created_count === 0) {
        setFetching(false);
        setStreamStatus(null);
        const session = await getTriageSession(result.latest_session_id);
        setSelectedSession(session);
        setShowAllSessions(false);
        toast.info('Sessions for the latest four weeks already exist');
        return;
      }

      await loadSession(result.latest_session_id);
      const targetIds = new Set(result.session_ids);
      let failedCount = 0;
      while (true) {
        const currentSessions = await listTriageSessions();
        setAllSessions([...currentSessions].sort((a, b) => b.week_date.localeCompare(a.week_date)));
        const targets = currentSessions.filter(session => targetIds.has(session.id));
        const finished = targets.filter(
          session => session.status === 'completed' || session.status === 'error',
        );
        failedCount = targets.filter(session => session.status === 'error').length;
        setStreamStatus(
          `Scoring ${result.created_count} weekly sessions (${finished.length}/${result.created_count} complete)...`,
        );
        if (finished.length === result.created_count) break;
        await new Promise(resolve => window.setTimeout(resolve, 2000));
      }

      setStreamStatus(null);
      setFetching(false);
      await loadSessions();
      if (failedCount) {
        toast.error(`Created ${result.created_count} sessions; ${failedCount} failed to score`);
      } else {
        toast.success(`Created and scored ${result.created_count} weekly sessions`);
      }
    } catch (err: unknown) {
      setFetching(false);
      setStreamStatus(null);
      const message = err instanceof Error ? err.message : 'Failed to start triage';
      toast.error(message);
    }
  };

  const handleRegenerate = async () => {
    if (!selectedSession) return;
    const sessionId = selectedSession.id;
    if (!window.confirm('Re-fetch and re-score this session? Dismissed papers will be restored to pending.')) return;

    setFetching(true);
    setStreamStatus(`Regenerating ${selectedSession.project_name} articles...`);
    try {
      await regenerateTriageSession(sessionId);
      const es = openTriageStream(sessionId);
      es.onmessage = async (e) => {
        const data = JSON.parse(e.data);
        if (data.status === 'completed' || data.status === 'error') {
          es.close();
          setStreamStatus(null);
          setFetching(false);
          if (data.status === 'completed') {
            const session = await getTriageSession(sessionId);
            setSelectedSession(session);
            await loadSessions();
            toast.success(`Regenerated ${data.article_count} articles and restored dismissed papers`);
          } else {
            toast.error('Triage regeneration failed');
          }
        }
      };
      es.onerror = () => {
        es.close();
        setStreamStatus(null);
        setFetching(false);
        toast.error('Connection error during regeneration');
      };
    } catch (err: unknown) {
      setFetching(false);
      setStreamStatus(null);
      const message = err instanceof Error ? err.message : 'Regeneration failed';
      toast.error(message);
    }
  };

  const handleSubmitAll = async () => {
    if (!selectedSession) return;
    const eligibleCount = selectedSession.articles.filter(
      article => article.decision === 'pending' && Boolean(article.pmcid),
    ).length;
    if (eligibleCount === 0) {
      toast.info('No pending papers with full text are available to submit');
      return;
    }
    if (!window.confirm(`Submit ${eligibleCount} papers to the analysis pipeline?`)) return;

    setSubmittingAll(true);
    try {
      const result = await submitAllTriageArticles(selectedSession.id);
      const session = await getTriageSession(selectedSession.id);
      setSelectedSession(session);
      const skipped = result.skipped_no_full_text + result.skipped_dismissed;
      toast.success(
        `Submitted ${result.submitted} papers${skipped ? `; skipped ${skipped} dismissed or unavailable papers` : ''}`,
      );
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Batch submission failed';
      toast.error(message);
    } finally {
      setSubmittingAll(false);
    }
  };

  // Submit article to pipeline
  const updateArticleState = useCallback((sessionId: string, pmid: string, update: Partial<TriageArticle>) => {
    setSelectedSession(prev => prev?.id === sessionId ? {
      ...prev,
      articles: prev.articles.map(article => article.pmid === pmid ? { ...article, ...update } : article),
    } : prev);
    setArchiveSessions(prev => prev.map(session => session.id === sessionId ? {
      ...session,
      articles: session.articles.map(article => article.pmid === pmid ? { ...article, ...update } : article),
    } : session));
  }, []);

  const handleSubmit = async (sessionId: string, pmid: string) => {
    try {
      const { job_id } = await submitTriageArticle(sessionId, pmid);
      toast.success(`Submitted PMID ${pmid} to pipeline`);
      updateArticleState(sessionId, pmid, { decision: 'submitted', job_id });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Submit failed';
      toast.error(message);
    }
  };

  // Save dismissal or restore the article to pending.
  const handleDecision = async (sessionId: string, pmid: string, decision: 'pending' | 'dismissed') => {
    try {
      await updateTriageArticleDecision(sessionId, pmid, decision);
      updateArticleState(sessionId, pmid, { decision });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : `${decision === 'dismissed' ? 'Dismiss' : 'Restore'} failed`;
      toast.error(message);
    }
  };

  const scopedArticles = useMemo<ScopedArticle[]>(() => {
    if (showAllSessions) {
      return archiveSessions.flatMap(session => session.articles.map(article => ({
        article,
        sessionId: session.id,
        weekDate: session.week_date,
      })));
    }
    if (!selectedSession) return [];
    return selectedSession.articles.map(article => ({
      article,
      sessionId: selectedSession.id,
      weekDate: selectedSession.week_date,
    }));
  }, [archiveSessions, selectedSession, showAllSessions]);

  const articleMatchesFacet = useCallback((article: TriageArticle, facet: FacetFilter) => {
    if (facet === 'variant_annotations' || facet === 'general_pharmacogenomics') {
      return (article.triage_tags ?? []).includes(facet);
    }
    if (PAPER_TYPE_FILTERS.includes(facet as PaperType)) {
      return (article.paper_types ?? []).includes(facet as PaperType);
    }
    return (article.pgx_information_types ?? []).includes(facet as PgxInformationType);
  }, []);

  const articleMatchesStatus = useCallback((article: TriageArticle, status: StatusFilter) => {
    if (status === 'all') return true;
    if (status === 'pending') return article.decision === 'pending';
    return article.triage_label === status;
  }, []);

  const facetCounts = useMemo(() => {
    const counts = new Map<TriageFilter, number>();
    STATUS_FILTERS.forEach(status => {
      counts.set(status, scopedArticles.filter(({ article }) => articleMatchesStatus(article, status)).length);
    });
    [...CURATABLE_FILTERS, ...PAPER_TYPE_FILTERS, ...PGX_INFORMATION_FILTERS].forEach(facet => {
      counts.set(facet, scopedArticles.filter(({ article }) => articleMatchesFacet(article, facet)).length);
    });
    return counts;
  }, [articleMatchesFacet, articleMatchesStatus, scopedArticles]);

  const filteredArticles = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLocaleLowerCase();

    return scopedArticles
      .filter(({ article }) => articleMatchesStatus(article, statusFilter))
      .filter(({ article }) => facetFilters.every(facet => articleMatchesFacet(article, facet)))
      .filter(({ article }) => !normalizedQuery ||
        article.title?.toLocaleLowerCase().includes(normalizedQuery) ||
        article.abstract?.toLocaleLowerCase().includes(normalizedQuery))
      .sort((a, b) => {
        if (showAllSessions && a.weekDate !== b.weekDate) return b.weekDate.localeCompare(a.weekDate);
        return Number(a.article.decision === 'dismissed') - Number(b.article.decision === 'dismissed');
      });
  }, [articleMatchesFacet, articleMatchesStatus, facetFilters, scopedArticles, searchQuery, showAllSessions, statusFilter]);
  const renderedArticles = useMemo(() => filteredArticles.slice(0, articleLimit), [articleLimit, filteredArticles]);

  useEffect(() => {
    setArticleLimit(100);
  }, [facetFilters, searchQuery, selectedSession?.id, showAllSessions, statusFilter]);

  const selectedSessionFetching = !showAllSessions && selectedSession !== null &&
    ['pending', 'scoring'].includes(selectedSession.status);
  const scopeArticleCount = showAllSessions
    ? sessions.reduce((total, session) => total + session.article_count, 0)
    : selectedSession?.articles.length ?? 0;
  const sessionsByMonth = useMemo(() => sessions.reduce<Record<string, TriageSessionListItem[]>>((groups, session) => {
    const month = formatMonth(session.week_date);
    groups[month] = [...(groups[month] ?? []), session];
    return groups;
  }, {}), [sessions]);

  const toggleFacet = (facet: FacetFilter) => {
    setFacetFilters(current => current.includes(facet)
      ? current.filter(item => item !== facet)
      : [...current, facet]);
  };

  const clearFilters = () => {
    setStatusFilter('all');
    setFacetFilters([]);
  };

  useLayoutEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const nextPositions = new Map<string, number>();

    articleElements.current.forEach((element, pmid) => {
      const nextTop = element.getBoundingClientRect().top;
      nextPositions.set(pmid, nextTop);
      const previousTop = articlePositions.current.get(pmid);
      const distance = previousTop === undefined ? 0 : previousTop - nextTop;
      if (!reduceMotion && Math.abs(distance) > 1) {
        element.animate(
          [
            { transform: `translateY(${distance}px)` },
            { transform: 'translateY(0)' },
          ],
          { duration: 320, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
        );
      }
    });

    articlePositions.current = nextPositions;
  }, [renderedArticles]);

  return (
    <div className="min-h-screen bg-gradient-subtle">
      <header className="border-b bg-background/90 backdrop-blur-sm">
        <div className="mx-auto flex h-16 max-w-[1480px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <button onClick={() => navigate('/')} className="text-sm text-muted-foreground hover:text-foreground">
              &larr; Dashboard
            </button>
            <span className="text-muted-foreground">/</span>
            <h1 className="truncate text-lg font-semibold">Literature Triage</h1>
          </div>
          <div className="flex items-center gap-2">
            {streamStatus && <span className="hidden text-sm text-muted-foreground animate-pulse md:inline">{streamStatus}</span>}
            <Button
              size="sm"
              variant="outline"
              disabled={fetching}
              onClick={() => handleFetch(selectedProject.id, selectedProject.name)}
            >
              {fetching ? 'Fetching...' : 'Fetch latest PubTator'}
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1480px] px-4 sm:px-6 lg:px-8">
        <div className="flex min-h-[calc(100vh-65px)] flex-col lg:flex-row">
          <aside className={`${mobileFiltersOpen ? 'block' : 'hidden'} w-full shrink-0 border-b bg-muted/20 py-5 lg:block lg:w-64 lg:border-b-0 lg:border-r lg:pr-5`}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Filter articles</h2>
              {(statusFilter !== 'all' || facetFilters.length > 0) && (
                <button type="button" onClick={clearFilters} className="text-xs font-medium text-primary hover:underline">
                  Reset
                </button>
              )}
            </div>
            <ScrollArea className="lg:h-[calc(100vh-125px)]">
              <div className="space-y-1 pr-3">
                <FacetGroup label="Review status">
                  {STATUS_FILTERS.map(status => (
                    <FacetOption
                      key={status}
                      label={status === 'all' ? 'All articles' : FILTER_LABELS[status]}
                      count={facetCounts.get(status) ?? 0}
                      checked={statusFilter === status}
                      kind="radio"
                      onChange={() => setStatusFilter(status)}
                    />
                  ))}
                </FacetGroup>
                <FacetGroup label="Curatable">
                  {CURATABLE_FILTERS.map(facet => (
                    <FacetOption
                      key={facet}
                      label={FILTER_LABELS[facet]}
                      count={facetCounts.get(facet) ?? 0}
                      checked={facetFilters.includes(facet)}
                      onChange={() => toggleFacet(facet)}
                    />
                  ))}
                </FacetGroup>
                <FacetGroup label="Paper type">
                  {PAPER_TYPE_FILTERS.map(facet => (
                    <FacetOption
                      key={facet}
                      label={FILTER_LABELS[facet]}
                      count={facetCounts.get(facet) ?? 0}
                      checked={facetFilters.includes(facet)}
                      onChange={() => toggleFacet(facet)}
                    />
                  ))}
                </FacetGroup>
                <FacetGroup label="PGx information">
                  {PGX_INFORMATION_FILTERS.map(facet => (
                    <FacetOption
                      key={facet}
                      label={FILTER_LABELS[facet]}
                      count={facetCounts.get(facet) ?? 0}
                      checked={facetFilters.includes(facet)}
                      onChange={() => toggleFacet(facet)}
                    />
                  ))}
                </FacetGroup>
              </div>
            </ScrollArea>
          </aside>

          <section className="flex min-w-0 flex-1 flex-col py-6 lg:pl-8">
            <div className="mb-5 flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
              <div className="min-w-0">
                <h2 className="sr-only">{selectedProject.name}</h2>
                <ToggleGroup
                  type="single"
                  value={selectedProjectId}
                  onValueChange={handleProjectChange}
                  aria-label="Literature project"
                  className="w-fit rounded-lg border bg-muted/60 p-1"
                >
                  {PROJECTS.map(project => (
                    <ToggleGroupItem
                      key={project.id}
                      value={project.id}
                      disabled={fetching}
                      aria-label={`Show ${project.name}`}
                      className="h-8 rounded-md px-3 text-sm text-muted-foreground data-[state=on]:bg-background data-[state=on]:text-foreground data-[state=on]:shadow-sm"
                    >
                      {project.name}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      disabled={sessions.length === 0}
                      className="mt-2 flex max-w-full items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:border-border hover:bg-background disabled:opacity-50"
                    >
                      <CalendarDays className="h-4 w-4 shrink-0 text-primary" />
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-foreground">
                          {showAllSessions ? 'All weekly dumps' : selectedSession ? formatWeekRange(selectedSession.week_date) : 'Select a weekly dump'}
                        </span>
                        <span className="block truncate text-xs">
                          {scopeArticleCount} articles{showAllSessions ? ' · newest dump first' : selectedSession?.status !== 'completed' ? ` · ${selectedSession?.status}` : ''}
                        </span>
                      </span>
                      <ChevronDown className="h-4 w-4 shrink-0" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="max-h-[70vh] w-80 overflow-y-auto p-2">
                    <DropdownMenuLabel>
                      <span className="block text-sm">Weekly dumps</span>
                      <span className="block text-xs font-normal text-muted-foreground">Choose one week or search the full archive.</span>
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => void loadAllSessions()} className="items-start gap-3 py-2.5">
                      <span className="mt-0.5 grid h-4 w-4 place-items-center text-primary">{showAllSessions && <Check className="h-4 w-4" />}</span>
                      <span className="min-w-0 flex-1">
                        <span className="flex justify-between gap-3 font-medium"><span>All articles</span><span>{sessions.reduce((sum, session) => sum + session.article_count, 0)}</span></span>
                        <span className="text-xs text-muted-foreground">Sorted by dump date, newest first</span>
                      </span>
                    </DropdownMenuItem>
                    {Object.entries(sessionsByMonth).flatMap(([month, monthSessions]) => [
                      <DropdownMenuLabel key={`${month}-label`} className="pt-3 text-xs text-muted-foreground">{month}</DropdownMenuLabel>,
                      ...monthSessions.map(session => (
                        <DropdownMenuItem
                          key={session.id}
                          onSelect={() => void loadSession(session.id)}
                          className="items-start gap-3 py-2.5"
                        >
                          <span className="mt-0.5 grid h-4 w-4 place-items-center text-primary">
                            {!showAllSessions && selectedSession?.id === session.id && <Check className="h-4 w-4" />}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex justify-between gap-3 font-medium"><span>{formatWeekRange(session.week_date)}</span><span>{session.article_count}</span></span>
                            <span className={`text-xs ${session.status === 'completed' ? 'text-emerald-700' : 'text-muted-foreground'}`}>
                              {session.status === 'completed' ? 'Complete' : session.status}
                            </span>
                          </span>
                        </DropdownMenuItem>
                      )),
                    ])}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                <Button
                  size="sm"
                  variant="outline"
                  className="lg:hidden"
                  onClick={() => setMobileFiltersOpen(open => !open)}
                  aria-expanded={mobileFiltersOpen}
                >
                  <FilterIcon className="mr-1.5 h-3.5 w-3.5" /> Filters
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={showAllSessions || !selectedSession || fetching || selectedSessionFetching}
                  onClick={handleRegenerate}
                  title={showAllSessions ? 'Select one weekly dump to regenerate it' : undefined}
                >
                  <RotateCcw className="mr-1.5 h-3.5 w-3.5" /> Regenerate
                </Button>
                <Button
                  size="sm"
                  disabled={showAllSessions || !selectedSession || submittingAll || fetching}
                  onClick={handleSubmitAll}
                  title={showAllSessions ? 'Select one weekly dump to submit its pending articles' : undefined}
                >
                  {submittingAll ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1.5 h-3.5 w-3.5" />}
                  Submit all
                </Button>
              </div>
            </div>

            <div className="mb-3 flex flex-col gap-3 border-b pb-3">
              <div className="relative w-full sm:max-w-lg">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                <Input
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search titles and abstracts"
                  aria-label="Search paper titles and abstracts"
                  className="pl-9 pr-9"
                />
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => setSearchQuery('')}
                    className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label="Clear search"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
              <div className="flex min-h-8 flex-col justify-between gap-2 sm:flex-row sm:items-center">
                <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                  <span>Showing <strong className="text-foreground">{renderedArticles.length}</strong> of {filteredArticles.length} matching articles</span>
                  {showAllSessions && <span className="border-l pl-2 text-xs">Across all dumps · newest first</span>}
                </div>
                {(statusFilter !== 'all' || facetFilters.length > 0) && (
                  <div className="flex flex-wrap gap-1.5 sm:justify-end">
                    {statusFilter !== 'all' && (
                      <FilterChip label={FILTER_LABELS[statusFilter]} onRemove={() => setStatusFilter('all')} />
                    )}
                    {facetFilters.map(facet => (
                      <FilterChip key={facet} label={FILTER_LABELS[facet]} onRemove={() => toggleFacet(facet)} />
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="min-h-0 flex-1">
              {(loadingSession || loadingArchive) && (
                <div className="flex h-full flex-col items-center justify-center gap-3" role="status" aria-live="polite">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden="true" />
                  <p className="text-sm text-muted-foreground">{loadingArchive ? 'Loading the weekly archive...' : 'Loading this weekly dump...'}</p>
                </div>
              )}
              {!loadingSession && !loadingArchive && selectedSessionFetching && (
                <div className="flex h-full flex-col items-center justify-center gap-3" role="status" aria-live="polite">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden="true" />
                  <p className="text-muted-foreground">Fetching and scoring this session...</p>
                  <p className="text-sm text-muted-foreground">Articles will appear when triage is complete.</p>
                </div>
              )}
              {!loadingSession && !loadingArchive && !selectedSession && !showAllSessions && (
                <div className="flex h-full items-center justify-center">
                  <p className="text-sm text-muted-foreground">
                    {sessions.length === 0
                      ? `No ${selectedProject.name} weekly dumps yet. Fetch the latest to begin triage.`
                      : 'Select a weekly dump to begin triage.'}
                  </p>
                </div>
              )}
              {!loadingSession && !loadingArchive && !selectedSessionFetching && (selectedSession || showAllSessions) && (
                <ScrollArea className="h-[calc(100vh-220px)] pr-3">
                  <div className="space-y-3 pb-8">
                    {renderedArticles.map(({ article, sessionId, weekDate }) => {
                      const articleKey = `${sessionId}:${article.pmid}`;
                      return (
                        <div
                          key={articleKey}
                          ref={(element) => {
                            if (element) articleElements.current.set(articleKey, element);
                            else articleElements.current.delete(articleKey);
                          }}
                        >
                          <ArticleRow
                            article={article}
                            dumpLabel={showAllSessions ? formatWeekRange(weekDate) : undefined}
                            onSubmit={(pmid) => handleSubmit(sessionId, pmid)}
                            onDecision={(pmid, decision) => handleDecision(sessionId, pmid, decision)}
                            onViewPipeline={(pmid) => navigate(`/viewer/${pmid}`)}
                          />
                        </div>
                      );
                    })}
                    {filteredArticles.length === 0 && (
                      <div className="rounded-lg border border-dashed bg-background py-14 text-center text-sm text-muted-foreground">
                        {searchQuery.trim() ? 'No articles match your search and filters.' : 'No articles match these filters.'}
                      </div>
                    )}
                    {renderedArticles.length < filteredArticles.length && (
                      <div className="flex justify-center py-4">
                        <Button variant="outline" onClick={() => setArticleLimit(limit => limit + 100)}>
                          Load {Math.min(100, filteredArticles.length - renderedArticles.length)} more
                        </Button>
                      </div>
                    )}
                  </div>
                </ScrollArea>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
};

const FacetGroup: React.FC<React.PropsWithChildren<{ label: string }>> = ({ label, children }) => (
  <fieldset className="border-t py-4 first:border-t-0 first:pt-0">
    <legend className="mb-2 text-[11px] font-semibold text-muted-foreground">{label}</legend>
    <div className="space-y-0.5">{children}</div>
  </fieldset>
);

interface FacetOptionProps {
  label: string;
  count: number;
  checked: boolean;
  kind?: 'checkbox' | 'radio';
  onChange: () => void;
}

const FacetOption: React.FC<FacetOptionProps> = ({ label, count, checked, kind = 'checkbox', onChange }) => (
  <button
    type="button"
    role={kind}
    aria-checked={checked}
    onClick={onChange}
    className={`grid min-h-9 w-full grid-cols-[18px_1fr_auto] items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
      checked ? 'bg-primary/10 font-medium text-primary' : 'text-foreground/80 hover:bg-muted'
    }`}
  >
    <span className={`grid h-4 w-4 place-items-center border ${kind === 'radio' ? 'rounded-full' : 'rounded-[4px]'} ${checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/50 bg-background'}`}>
      {checked && (kind === 'radio' ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : <Check className="h-3 w-3" />)}
    </span>
    <span>{label}</span>
    <span className="text-xs tabular-nums text-muted-foreground">{count}</span>
  </button>
);

const FilterChip: React.FC<{ label: string; onRemove: () => void }> = ({ label, onRemove }) => (
  <button
    type="button"
    onClick={onRemove}
    className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary hover:bg-primary/15"
    aria-label={`Remove ${label} filter`}
  >
    {label}<X className="h-3 w-3" />
  </button>
);

interface ArticleRowProps {
  article: TriageArticle;
  dumpLabel?: string;
  onSubmit: (pmid: string) => Promise<void>;
  onDecision: (pmid: string, decision: 'pending' | 'dismissed') => Promise<void>;
  onViewPipeline: (pmid: string) => void;
}

const ArticleRow: React.FC<ArticleRowProps> = ({ article, dumpLabel, onSubmit, onDecision, onViewPipeline }) => {
  const [loading, setLoading] = useState(false);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(
    article.decision === 'submitted' ? 'pending' : null,
  );
  const [jobProgress, setJobProgress] = useState<string>('');
  const [expanded, setExpanded] = useState(false);
  const isDismissed = article.decision === 'dismissed';
  const isCompleted = jobStatus === 'completed';
  const isFailed = jobStatus === 'failed';

  // Open SSE stream for job progress once submitted
  useEffect(() => {
    if (article.decision !== 'submitted' || !article.job_id) return;
    let active = true;
    setJobStatus((current) => current ?? 'pending');
    void getJob(article.job_id).then((job) => {
      if (!active) return;
      setJobStatus(job.status);
      setJobProgress(job.progress ?? '');
    }).catch(() => { /* SSE remains the fallback */ });
    const es = openJobStream(article.job_id);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setJobStatus(data.status as JobStatus);
        setJobProgress(data.progress ?? '');
        if (data.status === 'completed' || data.status === 'failed') es.close();
      } catch { /* ignore parse errors */ }
    };
    es.onerror = () => es.close();
    return () => { active = false; es.close(); };
  }, [article.decision, article.job_id]);

  const handleAction = async (fn: () => Promise<void>) => {
    setLoading(true);
    try { await fn(); } finally { setLoading(false); }
  };

  return (
    <div className={`rounded-xl border bg-card p-4 shadow-sm transition-[border-color,box-shadow,opacity] hover:border-muted-foreground/35 hover:shadow-md ${isDismissed ? 'opacity-40' : ''}`}>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={labelVariant(article.triage_label)}>
            {article.triage_label === 'not_relevant' ? 'Not Relevant' :
             article.triage_label.charAt(0).toUpperCase() + article.triage_label.slice(1)}
          </Badge>
          {(article.triage_tags ?? []).includes('variant_annotations') && (
            <Badge className="border-transparent bg-emerald-600 text-white hover:bg-emerald-600">
              Variant Annotations
            </Badge>
          )}
          {(article.triage_tags ?? []).includes('general_pharmacogenomics') && (
            <Badge variant="secondary">PGx</Badge>
          )}
          {dumpLabel && (
            <Badge variant="outline" className="border-primary/20 bg-primary/5 text-primary hover:bg-primary/5">
              Dump {dumpLabel}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">LitSuggest score: {article.litsuggest_score.toFixed(3)}</span>
          <span className="text-xs text-muted-foreground font-mono">PMID {article.pmid}</span>
          {article.pmcid && (
            <span className="text-xs text-muted-foreground font-mono">{article.pmcid}</span>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          {article.decision === 'submitted' ? (
            <Button size="sm" variant="outline" onClick={() => onViewPipeline(article.pmid)}>
              {isCompleted ? 'View result' : isFailed ? 'View error' : 'View progress'}
            </Button>
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
              <Button
                size="sm"
                variant="outline"
                disabled={loading}
                onClick={(event) => {
                  event.currentTarget.blur();
                  void handleAction(() => onDecision(article.pmid, isDismissed ? 'pending' : 'dismissed'));
                }}
              >
                {isDismissed ? 'Restore' : 'Dismiss'}
              </Button>
            </>
          )}
        </div>
      </div>
      {article.decision === 'submitted' && jobStatus && !isCompleted && (
        <JobProgress
          status={jobStatus}
          progress={jobProgress}
          compact
          className="mb-3"
        />
      )}
      {((article.paper_types ?? []).length > 0 || (article.pgx_information_types ?? []).length > 0) && (
        <div className="mb-3 space-y-1.5">
          {(article.paper_types ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="w-20 text-[11px] font-medium text-muted-foreground">Paper type</span>
              {article.paper_types.map(type => (
                <Badge
                  key={type}
                  variant="outline"
                  className="border-slate-300 bg-slate-50 text-slate-700 hover:bg-slate-50"
                >
                  {FILTER_LABELS[type]}
                </Badge>
              ))}
            </div>
          )}
          {(article.pgx_information_types ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="w-20 text-[11px] font-medium text-muted-foreground">PGx info</span>
              {article.pgx_information_types.map(type => (
                <Badge
                  key={type}
                  variant="outline"
                  className="border-violet-200 bg-violet-50 text-violet-700 hover:bg-violet-50"
                >
                  {FILTER_LABELS[type]}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
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
              <p className="text-xs text-muted-foreground italic line-clamp-2">{article.reasoning}</p>
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
            {article.paper_type_reasoning && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Paper type reasoning</p>
                <p className="text-xs text-foreground">{article.paper_type_reasoning}</p>
              </div>
            )}
            {article.pgx_information_reasoning && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">PGx information reasoning</p>
                <p className="text-xs text-foreground">{article.pgx_information_reasoning}</p>
              </div>
            )}
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
