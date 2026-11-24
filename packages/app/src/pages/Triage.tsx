import React, { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarDays, Check, ChevronDown, Filter as FilterIcon, Loader2, Pencil, RotateCcw, Search, Send, Upload, X } from 'lucide-react';
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
import UploadPdfDialog, { type StartedPdfUpload } from '@/features/pdf-upload/UploadPdfDialog';
import EditTriageTagsDialog from '@/features/triage/EditTriageTagsDialog';
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
const UPLOADED_PDFS_PROJECT_ID = 'uploaded_pdfs';

const isUploadedPdfSession = (session: { project_id: string } | null | undefined) =>
  session?.project_id === UPLOADED_PDFS_PROJECT_ID;

type TriageFilter = 'all' | 'pending' | 'submitted' | 'dismissed' | 'uploaded_pdf' | 'relevant' | 'borderline' | 'not_relevant' |
  'variant_annotations' | 'general_pharmacogenomics' | PaperType | PgxInformationType;

type StatusFilter = 'all' | 'relevant' | 'borderline' | 'not_relevant';
type WorkflowFilter = 'pending' | 'submitted' | 'dismissed' | 'uploaded_pdf';
type FacetFilter = Exclude<TriageFilter, StatusFilter | WorkflowFilter>;

interface DateRangeFilter {
  from: string;
  to: string;
}

interface ScopedArticle {
  article: TriageArticle;
  sessionId: string;
  weekDate: string;
  isUploadedSession: boolean;
}

type EntityGroup = 'drug' | 'gene' | 'phenotype';
type EntityFilterState = Record<EntityGroup, string[]>;
type EntitySearchState = Record<EntityGroup, string>;

interface EntityTagLike {
  name: string;
  id: string | null;
  source: string | null;
  mentions?: string[];
  genes?: string[];
  drugs?: string[];
}

interface EntityOption {
  key: string;
  label: string;
  searchText: string;
  context?: string;
}

const ENTITY_GROUPS: EntityGroup[] = ['drug', 'gene', 'phenotype'];
const ENTITY_GROUP_LABELS: Record<EntityGroup, string> = {
  drug: 'Drugs',
  gene: 'Genes',
  phenotype: 'Phenotypes',
};

const emptyEntityFilters = (): EntityFilterState => ({ drug: [], gene: [], phenotype: [] });
const emptyEntitySearch = (): EntitySearchState => ({ drug: '', gene: '', phenotype: '' });

const entityKey = (tag: EntityTagLike) => tag.id
  ? `${tag.source ?? 'id'}:${tag.id}`
  : `name:${tag.name.trim().toLocaleLowerCase()}`;

const articleEntityTags = (article: TriageArticle, group: EntityGroup): EntityTagLike[] => {
  if (group === 'drug') return article.drug_tags ?? [];
  if (group === 'gene') return article.gene_tags ?? [];
  return article.phenotype_tags ?? [];
};

const FILTER_LABELS: Record<TriageFilter, string> = {
  all: 'All',
  pending: 'Unsubmitted',
  submitted: 'Submitted',
  dismissed: 'Dismissed',
  uploaded_pdf: 'Uploaded PDF',
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

const STATUS_FILTERS: StatusFilter[] = ['all', 'relevant', 'borderline', 'not_relevant'];
const CURATABLE_FILTERS: FacetFilter[] = ['variant_annotations', 'general_pharmacogenomics'];
const WORKFLOW_FILTERS: WorkflowFilter[] = ['submitted', 'pending', 'dismissed', 'uploaded_pdf'];

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

const formatUploadedPdfLabel = (date: string) => `Uploaded PDFs - ${formatMonth(date)}`;

const formatFilterDate = (date: string) => parseWeekDate(date).toLocaleDateString('en-US', {
  month: 'short',
  day: 'numeric',
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
  const [statusFilters, setStatusFilters] = useState<StatusFilter[]>([]);
  const [workflowFilters, setWorkflowFilters] = useState<WorkflowFilter[]>([]);
  const [facetFilters, setFacetFilters] = useState<FacetFilter[]>([]);
  const [entityFilters, setEntityFilters] = useState<EntityFilterState>(emptyEntityFilters);
  const [entitySearch, setEntitySearch] = useState<EntitySearchState>(emptyEntitySearch);
  const [dateRange, setDateRange] = useState<DateRangeFilter>({ from: '', to: '' });
  const [searchQuery, setSearchQuery] = useState('');
  const [articleLimit, setArticleLimit] = useState(100);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [isUploadDialogOpen, setIsUploadDialogOpen] = useState(false);
  const [editingTagsTarget, setEditingTagsTarget] = useState<{
    sessionId: string;
    article: TriageArticle;
  } | null>(null);
  const [streamStatus, setStreamStatus] = useState<string | null>(null);
  const selectedProjectIdRef = useRef(selectedProjectId);
  const articleElements = useRef(new Map<string, HTMLDivElement>());
  const articlePositions = useRef(new Map<string, number>());

  const selectedProject = PROJECTS.find(project => project.id === selectedProjectId) ?? PROJECTS[0];
  const sessions = useMemo(
    () => allSessions.filter(session => session.project_id === selectedProjectId),
    [allSessions, selectedProjectId],
  );
  const uploadedPdfSessions = useMemo(
    () => allSessions.filter(session => isUploadedPdfSession(session)),
    [allSessions],
  );

  // Load sessions list on mount
  const loadSessions = useCallback(async () => {
    const s = await listTriageSessions();
    const sorted = [...s].sort((a, b) => b.week_date.localeCompare(a.week_date));
    setAllSessions(sorted);
    return sorted;
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // Load a session's full data
  const loadSession = useCallback(async (sessionId: string) => {
    setLoadingSession(true);
    setShowAllSessions(false);
    try {
      const session = await getTriageSession(sessionId);
      if (session.project_id === selectedProjectIdRef.current || isUploadedPdfSession(session)) {
        setSelectedSession(session);
      }
    } catch {
      toast.error('Failed to load session');
    } finally {
      setLoadingSession(false);
    }
  }, []);

  const loadAllSessions = useCallback(async () => {
    setLoadingArchive(true);
    try {
      const availableIds = new Set(sessions.map(session => session.id));
      const loaded = archiveSessions.filter(session => availableIds.has(session.id));
      if (selectedSession && availableIds.has(selectedSession.id) && !loaded.some(session => session.id === selectedSession.id)) {
        loaded.push(selectedSession);
      }
      const loadedIds = new Set(loaded.map(session => session.id));
      const queue = sessions.filter(session => !loadedIds.has(session.id));
      loaded.push(...await Promise.all(queue.map(summary => getTriageSession(summary.id))));
      loaded.sort((a, b) => b.week_date.localeCompare(a.week_date));

      if (loaded.length !== sessions.length || !loaded.every(session => session.project_id === selectedProjectIdRef.current)) {
        throw new Error('The weekly archive changed while it was loading');
      }
      setArchiveSessions([...loaded]);
      setShowAllSessions(true);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load the weekly archive';
      toast.error(message);
    } finally {
      setLoadingArchive(false);
    }
  }, [archiveSessions, selectedSession, sessions]);

  const handleProjectChange = (projectId: string) => {
    if (!projectId || projectId === selectedProjectId) {
      if (isUploadedPdfSession(selectedSession)) {
        setSelectedSession(null);
        setShowAllSessions(false);
      }
      return;
    }
    selectedProjectIdRef.current = projectId;
    setSelectedProjectId(projectId);
    setSelectedSession(null);
    setArchiveSessions([]);
    setShowAllSessions(false);
  };

  // Auto-select most recent session on load
  useEffect(() => {
    if (selectedSession) return;
    if (sessions.length > 0) {
      void loadSession(sessions[0].id);
    } else if (uploadedPdfSessions.length > 0) {
      void loadSession(uploadedPdfSessions[0].id);
    }
  }, [sessions, uploadedPdfSessions, selectedSession, loadSession]);

  // Refresh only the selected session when its background status changes.
  useEffect(() => {
    if (!selectedSession || showAllSessions) return;
    const summary = allSessions.find(session => session.id === selectedSession.id);
    if (!summary || summary.status === selectedSession.status) return;
    let cancelled = false;
    getTriageSession(selectedSession.id).then(session => {
      if (!cancelled) {
        setSelectedSession(current => current?.id === session.id ? session : current);
      }
    }).catch(() => { /* the next poll will retry */ });
    return () => { cancelled = true; };
  }, [allSessions, selectedSession, showAllSessions]);

  // Poll uploaded PDFs session while background triage scoring is still running
  const selectedSessionId = selectedSession?.id ?? null;
  const hasScoringArticles = Boolean(
    selectedSession &&
      !showAllSessions &&
      selectedSession.articles.some(article => article.triage_label === 'scoring'),
  );
  useEffect(() => {
    if (!selectedSessionId || !hasScoringArticles) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      getTriageSession(selectedSessionId)
        .then(updated => {
          if (!cancelled) {
            setSelectedSession(current => current?.id === selectedSessionId ? updated : current);
          }
        })
        .catch(() => { /* retry on next tick */ });
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedSessionId, hasScoringArticles]);

  const handlePdfUploaded = useCallback(async (uploads: StartedPdfUpload[]) => {
    setArchiveSessions([]);
    const refreshed = await loadSessions();
    const targetSessionId =
      uploads.find(upload => upload.job.triage_session_id)?.job.triage_session_id ??
      refreshed.find(session => isUploadedPdfSession(session))?.id;
    if (targetSessionId) {
      await loadSession(targetSessionId);
    } else if (selectedSession) {
      await loadSession(selectedSession.id);
    }
  }, [loadSession, loadSessions, selectedSession]);

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
      // If the paper is already being processed (e.g. via PDF upload), refresh the
      // session so this row updates to Submitted / View progress immediately.
      if (message.toLowerCase().includes('already being processed')) {
        try {
          const refreshed = await getTriageSession(sessionId);
          setSelectedSession((prev) => (prev?.id === sessionId ? refreshed : prev));
          setArchiveSessions((prev) =>
            prev.map((session) => (session.id === sessionId ? refreshed : session)),
          );
        } catch {
          // ignore refresh errors
        }
      }
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
        isUploadedSession: isUploadedPdfSession(session),
      })));
    }
    if (!selectedSession) return [];
    const uploadedSession = isUploadedPdfSession(selectedSession);
    return selectedSession.articles.map(article => ({
      article,
      sessionId: selectedSession.id,
      weekDate: selectedSession.week_date,
      isUploadedSession: uploadedSession,
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
    return article.triage_label === status;
  }, []);

  const articleMatchesWorkflow = useCallback((article: TriageArticle, workflow: WorkflowFilter, isUploadedSession = false) => {
    if (workflow === 'uploaded_pdf') {
      return isUploadedSession || article.job_source === 'pdf_upload';
    }
    return article.decision === workflow;
  }, []);

  const articleMatchesDateRange = useCallback((publicationDate: string | null) => {
    if (!dateRange.from && !dateRange.to) return true;
    if (!publicationDate) return false;
    return (!dateRange.from || publicationDate >= dateRange.from) &&
      (!dateRange.to || publicationDate <= dateRange.to);
  }, [dateRange]);

  const dateFilteredArticles = useMemo(
    () => scopedArticles.filter(({ article }) => articleMatchesDateRange(article.publication_date)),
    [articleMatchesDateRange, scopedArticles],
  );

  const entityOptions = useMemo<Record<EntityGroup, EntityOption[]>>(() => {
    const optionMaps: Record<EntityGroup, Map<string, EntityOption>> = {
      drug: new Map(),
      gene: new Map(),
      phenotype: new Map(),
    };

    scopedArticles.forEach(({ article }) => {
      ENTITY_GROUPS.forEach(group => {
        articleEntityTags(article, group).forEach(tag => {
          const key = entityKey(tag);
          const existing = optionMaps[group].get(key);
          const contextValues = group === 'phenotype'
            ? [...(tag.genes ?? []), ...(tag.drugs ?? [])]
            : [];
          const searchValues = [tag.name, ...(tag.mentions ?? []), tag.id ?? '', tag.source ?? '', ...contextValues];
          if (existing) {
            existing.searchText = `${existing.searchText} ${searchValues.join(' ')}`.toLocaleLowerCase();
          } else {
            optionMaps[group].set(key, {
              key,
              label: tag.name,
              searchText: searchValues.join(' ').toLocaleLowerCase(),
              context: contextValues.length > 0 ? [...new Set(contextValues)].join(', ') : undefined,
            });
          }
        });
      });
    });

    return {
      drug: [...optionMaps.drug.values()].sort((a, b) => a.label.localeCompare(b.label)),
      gene: [...optionMaps.gene.values()].sort((a, b) => a.label.localeCompare(b.label)),
      phenotype: [...optionMaps.phenotype.values()].sort((a, b) => a.label.localeCompare(b.label)),
    };
  }, [scopedArticles]);

  useEffect(() => {
    setEntityFilters(current => {
      let changed = false;
      const next = emptyEntityFilters();
      ENTITY_GROUPS.forEach(group => {
        const availableKeys = new Set(entityOptions[group].map(option => option.key));
        next[group] = current[group].filter(key => availableKeys.has(key));
        if (next[group].length !== current[group].length) changed = true;
      });
      return changed ? next : current;
    });
  }, [entityOptions]);

  const articleMatchesEntityFilters = useCallback((article: TriageArticle, excludedGroup?: EntityGroup) => (
    ENTITY_GROUPS.every(group => {
      if (group === excludedGroup || entityFilters[group].length === 0) return true;
      const articleKeys = new Set(articleEntityTags(article, group).map(entityKey));
      return entityFilters[group].some(key => articleKeys.has(key));
    })
  ), [entityFilters]);

  const facetCounts = useMemo(() => {
    const counts = new Map<TriageFilter, number>();
    STATUS_FILTERS.forEach(status => {
      counts.set(status, dateFilteredArticles.filter(({ article }) => articleMatchesStatus(article, status)).length);
    });
    [...CURATABLE_FILTERS, ...PAPER_TYPE_FILTERS, ...PGX_INFORMATION_FILTERS].forEach(facet => {
      counts.set(facet, dateFilteredArticles.filter(({ article }) => articleMatchesFacet(article, facet)).length);
    });
    WORKFLOW_FILTERS.forEach(workflow => {
      counts.set(
        workflow,
        dateFilteredArticles.filter(({ article, isUploadedSession }) =>
          articleMatchesWorkflow(article, workflow, isUploadedSession),
        ).length,
      );
    });
    return counts;
  }, [articleMatchesFacet, articleMatchesStatus, articleMatchesWorkflow, dateFilteredArticles]);

  const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
  const entityBaseArticles = useMemo(() => dateFilteredArticles
    .filter(({ article }) => statusFilters.length === 0 || statusFilters.some(status => articleMatchesStatus(article, status)))
    .filter(({ article, isUploadedSession }) => workflowFilters.length === 0 || workflowFilters.some(workflow => articleMatchesWorkflow(article, workflow, isUploadedSession)))
    .filter(({ article }) => facetFilters.every(facet => articleMatchesFacet(article, facet)))
    .filter(({ article }) => !normalizedQuery ||
      article.title?.toLocaleLowerCase().includes(normalizedQuery) ||
      article.abstract?.toLocaleLowerCase().includes(normalizedQuery)), [
    articleMatchesFacet,
    articleMatchesStatus,
    articleMatchesWorkflow,
    dateFilteredArticles,
    facetFilters,
    normalizedQuery,
    statusFilters,
    workflowFilters,
  ]);

  const entityFacetCounts = useMemo<Record<EntityGroup, Map<string, number>>>(() => {
    const counts: Record<EntityGroup, Map<string, number>> = {
      drug: new Map(),
      gene: new Map(),
      phenotype: new Map(),
    };
    ENTITY_GROUPS.forEach(group => {
      entityOptions[group].forEach(option => {
        counts[group].set(option.key, entityBaseArticles.filter(({ article }) => (
          articleMatchesEntityFilters(article, group) &&
          articleEntityTags(article, group).some(tag => entityKey(tag) === option.key)
        )).length);
      });
    });
    return counts;
  }, [articleMatchesEntityFilters, entityBaseArticles, entityOptions]);

  const filteredArticles = useMemo(() => {
    return entityBaseArticles
      .filter(({ article }) => articleMatchesEntityFilters(article))
      .sort((a, b) => {
        const dismissedOrder = Number(a.article.decision === 'dismissed') - Number(b.article.decision === 'dismissed');
        if (dismissedOrder !== 0) return dismissedOrder;
        if (showAllSessions && a.weekDate !== b.weekDate) return b.weekDate.localeCompare(a.weekDate);
        return 0;
      });
  }, [articleMatchesEntityFilters, entityBaseArticles, showAllSessions]);
  const renderedArticles = useMemo(() => filteredArticles.slice(0, articleLimit), [articleLimit, filteredArticles]);

  useEffect(() => {
    setArticleLimit(100);
  }, [dateRange, entityFilters, facetFilters, searchQuery, selectedSession?.id, showAllSessions, statusFilters, workflowFilters]);

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
  const dateBounds = useMemo(() => {
    const dates = scopedArticles
      .map(({ article }) => article.publication_date)
      .filter((value): value is string => Boolean(value))
      .sort();
    if (dates.length === 0) return { min: undefined, max: undefined };
    return { min: dates[0], max: dates[dates.length - 1] };
  }, [scopedArticles]);
  const hasActiveEntityFilters = ENTITY_GROUPS.some(group => entityFilters[group].length > 0);
  const hasActiveFilters = statusFilters.length > 0 || workflowFilters.length > 0 || facetFilters.length > 0 || hasActiveEntityFilters || Boolean(dateRange.from || dateRange.to);

  const toggleStatus = (status: StatusFilter) => {
    if (status === 'all') {
      setStatusFilters([]);
      return;
    }
    setStatusFilters(current => current.includes(status)
      ? current.filter(item => item !== status)
      : [...current, status]);
  };

  const toggleFacet = (facet: FacetFilter) => {
    setFacetFilters(current => current.includes(facet)
      ? current.filter(item => item !== facet)
      : [...current, facet]);
  };

  const toggleWorkflow = (workflow: WorkflowFilter) => {
    setWorkflowFilters(current => current.includes(workflow)
      ? current.filter(item => item !== workflow)
      : [...current, workflow]);
  };

  const toggleEntityFilter = (group: EntityGroup, key: string) => {
    setEntityFilters(current => ({
      ...current,
      [group]: current[group].includes(key)
        ? current[group].filter(item => item !== key)
        : [...current[group], key],
    }));
  };

  const clearFilters = () => {
    setStatusFilters([]);
    setWorkflowFilters([]);
    setFacetFilters([]);
    setEntityFilters(emptyEntityFilters());
    setEntitySearch(emptyEntitySearch());
    setDateRange({ from: '', to: '' });
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
              onClick={() => setIsUploadDialogOpen(true)}
            >
              <Upload className="mr-1.5 h-3.5 w-3.5" />
              Upload PDF
            </Button>
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
              {hasActiveFilters && (
                <button type="button" onClick={clearFilters} className="text-xs font-medium text-primary hover:underline">
                  Reset
                </button>
              )}
            </div>
            <ScrollArea className="lg:h-[calc(100vh-125px)]">
              <div className="space-y-1 pr-3">
                <FacetGroup label="Publication date">
                  <div className="space-y-2 px-2 pb-1">
                    <div className="space-y-1">
                      <label htmlFor="triage-date-from" className="text-xs text-muted-foreground">From</label>
                      <Input
                        id="triage-date-from"
                        type="date"
                        value={dateRange.from}
                        min={dateBounds.min}
                        max={dateRange.to || dateBounds.max}
                        onChange={(event) => {
                          const from = event.target.value;
                          setDateRange(current => ({
                            from,
                            to: from && current.to && from > current.to ? from : current.to,
                          }));
                        }}
                        className="h-9 text-xs"
                      />
                    </div>
                    <div className="space-y-1">
                      <label htmlFor="triage-date-to" className="text-xs text-muted-foreground">To</label>
                      <Input
                        id="triage-date-to"
                        type="date"
                        value={dateRange.to}
                        min={dateRange.from || dateBounds.min}
                        max={dateBounds.max}
                        onChange={(event) => {
                          const to = event.target.value;
                          setDateRange(current => ({
                            from: to && current.from && to < current.from ? to : current.from,
                            to,
                          }));
                        }}
                        className="h-9 text-xs"
                      />
                    </div>
                    <p className="text-[11px] leading-4 text-muted-foreground">
                      Inclusive; based on PubMed metadata.
                    </p>
                  </div>
                </FacetGroup>
                <FacetGroup label="Review status">
                  {STATUS_FILTERS.map(status => (
                    <FacetOption
                      key={status}
                      label={status === 'all' ? 'All articles' : FILTER_LABELS[status]}
                      count={facetCounts.get(status) ?? 0}
                      checked={status === 'all' ? statusFilters.length === 0 : statusFilters.includes(status)}
                      onChange={() => toggleStatus(status)}
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
                <fieldset className="border-t py-4">
                  <legend className="mb-1 text-[11px] font-semibold text-muted-foreground">Entity tags</legend>
                  <p className="mb-3 px-1 text-[11px] leading-4 text-muted-foreground">
                    Match any tag within a group and all selected groups.
                  </p>
                  <div className="space-y-2">
                    {ENTITY_GROUPS.map(group => (
                      <EntityFacetGroup
                        key={group}
                        group={group}
                        label={ENTITY_GROUP_LABELS[group]}
                        options={entityOptions[group]}
                        counts={entityFacetCounts[group]}
                        selectedKeys={entityFilters[group]}
                        query={entitySearch[group]}
                        onQueryChange={(query) => setEntitySearch(current => ({ ...current, [group]: query }))}
                        onToggle={(key) => toggleEntityFilter(group, key)}
                      />
                    ))}
                  </div>
                </fieldset>
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
                <FacetGroup label="Workflow">
                  {WORKFLOW_FILTERS.map(workflow => (
                    <FacetOption
                      key={workflow}
                      label={FILTER_LABELS[workflow]}
                      count={facetCounts.get(workflow) ?? 0}
                      checked={workflowFilters.includes(workflow)}
                      onChange={() => toggleWorkflow(workflow)}
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
                      disabled={sessions.length === 0 && uploadedPdfSessions.length === 0}
                      className="mt-2 flex max-w-full items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:border-border hover:bg-background disabled:opacity-50"
                    >
                      <CalendarDays className="h-4 w-4 shrink-0 text-primary" />
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-foreground">
                          {showAllSessions
                            ? 'All weekly dumps'
                            : selectedSession
                              ? isUploadedPdfSession(selectedSession)
                                ? formatUploadedPdfLabel(selectedSession.week_date)
                                : formatWeekRange(selectedSession.week_date)
                              : 'Select a weekly dump'}
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
                      <span className="block text-xs font-normal text-muted-foreground">Choose one week, uploaded PDFs, or search the full archive.</span>
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {sessions.length > 0 && (
                      <DropdownMenuItem onSelect={() => void loadAllSessions()} className="items-start gap-3 py-2.5">
                        <span className="mt-0.5 grid h-4 w-4 place-items-center text-primary">{showAllSessions && <Check className="h-4 w-4" />}</span>
                        <span className="min-w-0 flex-1">
                          <span className="flex justify-between gap-3 font-medium"><span>All articles</span><span>{sessions.reduce((sum, session) => sum + session.article_count, 0)}</span></span>
                          <span className="text-xs text-muted-foreground">Sorted by dump date, newest first</span>
                        </span>
                      </DropdownMenuItem>
                    )}
                    {uploadedPdfSessions.length > 0 && (
                      <>
                        <DropdownMenuLabel className="pt-3 text-xs text-muted-foreground">
                          Uploaded PDFs
                        </DropdownMenuLabel>
                        {uploadedPdfSessions.map(session => (
                          <DropdownMenuItem
                            key={session.id}
                            onSelect={() => void loadSession(session.id)}
                            className="items-start gap-3 py-2.5"
                          >
                            <span className="mt-0.5 grid h-4 w-4 place-items-center text-primary">
                              {!showAllSessions && selectedSession?.id === session.id && <Check className="h-4 w-4" />}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="flex justify-between gap-3 font-medium">
                                <span>{formatUploadedPdfLabel(session.week_date)}</span>
                                <span>{session.article_count}</span>
                              </span>
                              <span className={`text-xs ${session.status === 'completed' ? 'text-emerald-700' : 'text-muted-foreground'}`}>
                                {session.status === 'completed' ? 'Complete' : session.status}
                              </span>
                            </span>
                          </DropdownMenuItem>
                        ))}
                      </>
                    )}
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
                  disabled={showAllSessions || !selectedSession || isUploadedPdfSession(selectedSession) || submittingAll || fetching}
                  onClick={handleSubmitAll}
                  title={
                    showAllSessions
                      ? 'Select one weekly dump to submit its pending articles'
                      : isUploadedPdfSession(selectedSession)
                        ? 'Uploaded PDFs are submitted automatically on upload'
                        : undefined
                  }
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
                {hasActiveFilters && (
                  <div className="flex flex-wrap gap-1.5 sm:justify-end">
                    {(dateRange.from || dateRange.to) && (
                      <FilterChip
                        label={dateRange.from && dateRange.to
                          ? `${formatFilterDate(dateRange.from)} – ${formatFilterDate(dateRange.to)}`
                          : dateRange.from
                            ? `From ${formatFilterDate(dateRange.from)}`
                            : `Through ${formatFilterDate(dateRange.to)}`}
                        onRemove={() => setDateRange({ from: '', to: '' })}
                      />
                    )}
                    {statusFilters.map(status => (
                      <FilterChip key={status} label={FILTER_LABELS[status]} onRemove={() => toggleStatus(status)} />
                    ))}
                    {workflowFilters.map(workflow => (
                      <FilterChip key={workflow} label={FILTER_LABELS[workflow]} onRemove={() => toggleWorkflow(workflow)} />
                    ))}
                    {facetFilters.map(facet => (
                      <FilterChip key={facet} label={FILTER_LABELS[facet]} onRemove={() => toggleFacet(facet)} />
                    ))}
                    {ENTITY_GROUPS.flatMap(group => entityFilters[group].map(key => {
                      const option = entityOptions[group].find(item => item.key === key);
                      if (!option) return null;
                      return (
                        <FilterChip
                          key={`${group}:${key}`}
                          label={`${ENTITY_GROUP_LABELS[group].slice(0, -1)}: ${option.label}`}
                          onRemove={() => toggleEntityFilter(group, key)}
                        />
                      );
                    }))}
                  </div>
                )}
              </div>
            </div>

            <div className="min-h-0 flex-1">
              {(loadingSession || (loadingArchive && archiveSessions.length === 0)) && (
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
              {!loadingSession && (!loadingArchive || archiveSessions.length > 0) && !selectedSessionFetching && (selectedSession || showAllSessions) && (
                <ScrollArea className="h-[calc(100vh-220px)] pr-3">
                  <div className="space-y-3 pb-8">
                    {renderedArticles.map(({ article, sessionId, weekDate, isUploadedSession }) => {
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
                            isUploadedSession={isUploadedSession}
                            onSubmit={(pmid) => handleSubmit(sessionId, pmid)}
                            onDecision={(pmid, decision) => handleDecision(sessionId, pmid, decision)}
                            onEditTags={() => setEditingTagsTarget({ sessionId, article })}
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

      <UploadPdfDialog
        open={isUploadDialogOpen}
        onOpenChange={setIsUploadDialogOpen}
        onSuccess={(uploads) => {
          void handlePdfUploaded(uploads);
        }}
      />

      <EditTriageTagsDialog
        open={editingTagsTarget !== null}
        sessionId={editingTagsTarget?.sessionId ?? null}
        article={editingTagsTarget?.article ?? null}
        onOpenChange={(isOpen) => {
          if (!isOpen) setEditingTagsTarget(null);
        }}
        onSaved={(sessionId, updated) => {
          updateArticleState(sessionId, updated.pmid, updated);
          setEditingTagsTarget((prev) =>
            prev && prev.sessionId === sessionId && prev.article.pmid === updated.pmid
              ? { sessionId, article: updated }
              : prev,
          );
        }}
      />
    </div>
  );
};

const ENTITY_GROUP_DOT_CLASSES: Record<EntityGroup, string> = {
  drug: 'bg-sky-600',
  gene: 'bg-emerald-600',
  phenotype: 'bg-amber-600',
};

interface EntityFacetGroupProps {
  group: EntityGroup;
  label: string;
  options: EntityOption[];
  counts: Map<string, number>;
  selectedKeys: string[];
  query: string;
  onQueryChange: (query: string) => void;
  onToggle: (key: string) => void;
}

const EntityFacetGroup: React.FC<EntityFacetGroupProps> = ({
  group,
  label,
  options,
  counts,
  selectedKeys,
  query,
  onQueryChange,
  onToggle,
}) => {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleOptions = options
    .filter(option => !normalizedQuery || option.searchText.includes(normalizedQuery))
    .sort((a, b) => {
      const aAvailable = (counts.get(a.key) ?? 0) > 0 || selectedKeys.includes(a.key);
      const bAvailable = (counts.get(b.key) ?? 0) > 0 || selectedKeys.includes(b.key);
      if (aAvailable !== bAvailable) return Number(bAvailable) - Number(aAvailable);
      return (counts.get(b.key) ?? 0) - (counts.get(a.key) ?? 0) || a.label.localeCompare(b.label);
    });
  const availableCount = options.filter(option => (counts.get(option.key) ?? 0) > 0).length;

  return (
    <Collapsible defaultOpen={false} className="overflow-hidden rounded-lg border bg-background">
      <CollapsibleTrigger className="flex min-h-10 w-full items-center gap-2 px-2.5 text-left hover:bg-muted/60 [&[data-state=open]>svg]:rotate-180">
        <span className={`h-2 w-2 shrink-0 rounded-full ${ENTITY_GROUP_DOT_CLASSES[group]}`} aria-hidden="true" />
        <span className="flex-1 text-xs font-medium">{label}</span>
        <span className="text-[10px] tabular-nums text-muted-foreground">
          {selectedKeys.length > 0 ? `${selectedKeys.length} selected · ` : ''}{availableCount} available
        </span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform" aria-hidden="true" />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t px-2 pb-2">
        <div className="relative my-2">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={`Search ${label.toLocaleLowerCase()}`}
            aria-label={`Search ${label.toLocaleLowerCase()}`}
            className="h-8 pl-8 pr-7 text-xs"
          />
          {query && (
            <button
              type="button"
              onClick={() => onQueryChange('')}
              className="absolute right-1 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={`Clear ${label.toLocaleLowerCase()} search`}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <div className="max-h-48 space-y-0.5 overflow-y-auto pr-1">
          {visibleOptions.map(option => {
            const count = counts.get(option.key) ?? 0;
            const checked = selectedKeys.includes(option.key);
            return (
              <FacetOption
                key={option.key}
                label={option.label}
                secondaryLabel={option.context}
                count={count}
                checked={checked}
                disabled={!checked && count === 0}
                onChange={() => onToggle(option.key)}
              />
            );
          })}
          {visibleOptions.length === 0 && (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">No matching {label.toLocaleLowerCase()}.</p>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
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
  secondaryLabel?: string;
  count: number;
  checked: boolean;
  kind?: 'checkbox' | 'radio';
  disabled?: boolean;
  onChange: () => void;
}

const FacetOption: React.FC<FacetOptionProps> = ({ label, secondaryLabel, count, checked, kind = 'checkbox', disabled = false, onChange }) => (
  <button
    type="button"
    role={kind}
    aria-checked={checked}
    disabled={disabled}
    onClick={onChange}
    className={`grid min-h-9 w-full grid-cols-[18px_1fr_auto] items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
      checked ? 'bg-primary/10 font-medium text-primary' : 'text-foreground/80 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent'
    }`}
  >
    <span className={`grid h-4 w-4 place-items-center border ${kind === 'radio' ? 'rounded-full' : 'rounded-[4px]'} ${checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/50 bg-background'}`}>
      {checked && (kind === 'radio' ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : <Check className="h-3 w-3" />)}
    </span>
    <span className="min-w-0">
      <span className="block truncate">{label}</span>
      {secondaryLabel && <span className="block truncate text-[10px] font-normal text-muted-foreground">{secondaryLabel}</span>}
    </span>
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
  isUploadedSession?: boolean;
  onSubmit: (pmid: string) => Promise<void>;
  onDecision: (pmid: string, decision: 'pending' | 'dismissed') => Promise<void>;
  onEditTags: () => void;
  onViewPipeline: (pmid: string) => void;
}

const ArticleRow: React.FC<ArticleRowProps> = ({ article, dumpLabel, isUploadedSession, onSubmit, onDecision, onEditTags, onViewPipeline }) => {
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
          {article.triage_label === 'scoring' ? (
            <Badge variant="outline" className="gap-1 text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Scoring...
            </Badge>
          ) : (
            <Badge variant={labelVariant(article.triage_label)}>
              {article.triage_label === 'not_relevant' ? 'Not Relevant' :
               article.triage_label.charAt(0).toUpperCase() + article.triage_label.slice(1)}
            </Badge>
          )}
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
          {(isUploadedSession || article.job_source === 'pdf_upload') && (
            <Badge
              variant="outline"
              className="border-orange-200 bg-orange-100 text-orange-800 hover:bg-orange-100 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200"
            >
              Uploaded PDF
            </Badge>
          )}
          {!isUploadedSession && (
            <span className="text-xs text-muted-foreground">LitSuggest score: {article.litsuggest_score.toFixed(3)}</span>
          )}
          {article.publication_date && (
            <span className="text-xs text-muted-foreground">Published {formatFilterDate(article.publication_date)}</span>
          )}
          <span className="text-xs text-muted-foreground font-mono">PMID {article.pmid}</span>
          {article.pmcid && (
            <span className="text-xs text-muted-foreground font-mono">{article.pmcid}</span>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          {article.triage_label === 'scoring' ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex cursor-not-allowed">
                  <Button
                    size="icon"
                    variant="outline"
                    disabled
                    className="pointer-events-none h-9 w-9"
                    aria-label="Edit tags"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>Tags can be edited once scoring completes</TooltipContent>
            </Tooltip>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon"
                  variant="outline"
                  className="h-9 w-9"
                  onClick={onEditTags}
                  aria-label="Edit tags"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Edit tags</TooltipContent>
            </Tooltip>
          )}
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
      {((article.paper_types ?? []).length > 0 ||
        (article.pgx_information_types ?? []).length > 0 ||
        (article.drug_tags ?? []).length > 0 ||
        (article.gene_tags ?? []).length > 0 ||
        (article.phenotype_tags ?? []).length > 0) && (
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
          {(article.drug_tags ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="w-20 text-[11px] font-medium text-muted-foreground">Drugs</span>
              {article.drug_tags.map(drug => {
                const badge = (
                  <Badge
                    variant="outline"
                    className="border-sky-200 bg-sky-50 text-sky-700 hover:bg-sky-50"
                  >
                    {drug.name}
                  </Badge>
                );
                return (
                  <Tooltip key={`${drug.source ?? 'unresolved'}:${drug.id ?? drug.name}`}>
                    <TooltipTrigger asChild>
                      {drug.url ? (
                        <a href={drug.url} target="_blank" rel="noopener noreferrer">
                          {badge}
                        </a>
                      ) : (
                        <span>{badge}</span>
                      )}
                    </TooltipTrigger>
                    <TooltipContent>
                      {drug.resolved
                        ? `${drug.source === 'rxnorm' ? 'RxNorm' : 'PharmGKB'} ${drug.id}`
                        : 'Unresolved drug mention'}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
          )}
          {(article.gene_tags ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="w-20 text-[11px] font-medium text-muted-foreground">Genes</span>
              {article.gene_tags.map(gene => {
                const badge = (
                  <Badge
                    variant="outline"
                    className="border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-50"
                  >
                    {gene.name}
                  </Badge>
                );
                return (
                  <Tooltip key={`${gene.source ?? 'unresolved'}:${gene.id ?? gene.name}`}>
                    <TooltipTrigger asChild>
                      {gene.url ? (
                        <a href={gene.url} target="_blank" rel="noopener noreferrer">
                          {badge}
                        </a>
                      ) : (
                        <span>{badge}</span>
                      )}
                    </TooltipTrigger>
                    <TooltipContent>
                      {gene.resolved
                        ? `ClinPGx ${gene.id}`
                        : 'Unresolved gene mention'}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
          )}
          {(article.phenotype_tags ?? []).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="w-20 text-[11px] font-medium text-muted-foreground">Phenotypes</span>
              {article.phenotype_tags.map(phenotype => {
                const badge = (
                  <Badge
                    variant="outline"
                    className="border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-50"
                  >
                    {phenotype.name}
                  </Badge>
                );
                const context = [...phenotype.genes, ...phenotype.drugs];
                const sourceLabel = phenotype.source === 'cpic'
                  ? 'CPIC standardized phenotype'
                  : phenotype.id
                    ? `ClinPGx ${phenotype.id}`
                    : 'Unresolved phenotype mention';
                return (
                  <Tooltip key={`${phenotype.source ?? 'unresolved'}:${phenotype.id ?? phenotype.name}:${phenotype.genes.join(',')}`}>
                    <TooltipTrigger asChild>
                      {phenotype.url ? (
                        <a href={phenotype.url} target="_blank" rel="noopener noreferrer">
                          {badge}
                        </a>
                      ) : (
                        <span>{badge}</span>
                      )}
                    </TooltipTrigger>
                    <TooltipContent>
                      {context.length > 0 ? `${sourceLabel} · ${context.join(', ')}` : sourceLabel}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
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
