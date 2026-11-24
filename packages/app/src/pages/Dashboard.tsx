import React, { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import AddArticleDialog from '@/components/AddArticleDialog';
import UploadPdfDialog from '@/features/pdf-upload/UploadPdfDialog';
import { toast } from 'sonner';
import type { JobResponse } from '@/lib/api';
import { getJob, listArticles, STATUS_LABELS } from '@/lib/api';

interface Study {
  id: string;
  jobId: string | null;
  pmid: string;
  pmcid: string | null;
  source: string;
  status: string;
  progress: string | null;
  title: string;
  description: string;
  numVariants: number | null;
  participants: number | null;
}

const isParsingStatus = (status: string) =>
  status !== 'completed' && status !== 'failed' && status !== 'error';

const Dashboard = () => {
  const navigate = useNavigate();
  const [searchTerm, setSearchTerm] = useState('');
  const [availableStudies, setAvailableStudies] = useState<Study[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [isUploadDialogOpen, setIsUploadDialogOpen] = useState(false);
  const activePollersRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  const startJobPoller = useCallback(
    (jobId: string, pmid: string, onRefresh: () => void) => {
      if (activePollersRef.current.has(jobId)) return;

      const timer = setInterval(async () => {
        try {
          const updated = await getJob(jobId);
          if (updated.status === 'completed') {
            clearInterval(timer);
            activePollersRef.current.delete(jobId);
            onRefresh();
            toast.success(`Analysis complete for PMID ${pmid}!`, {
              position: 'top-right',
              action: {
                label: 'View',
                onClick: () => navigate(`/viewer/${pmid}`, { state: { dynamicData: updated } }),
              },
            });
          } else if (updated.status === 'failed') {
            clearInterval(timer);
            activePollersRef.current.delete(jobId);
            onRefresh();
            toast.error(`Parsing failed for PMID ${pmid}`, {
              description: updated.error ?? 'An unknown error occurred.',
              position: 'top-right',
            });
          } else {
            setAvailableStudies((prev) =>
              prev.map((study) =>
                study.jobId === jobId || study.pmid === pmid
                  ? {
                      ...study,
                      status: updated.status,
                      progress: updated.progress,
                      pmcid: updated.pmcid ?? study.pmcid,
                    }
                  : study,
              ),
            );
          }
        } catch {
          clearInterval(timer);
          activePollersRef.current.delete(jobId);
        }
      }, 3000);

      activePollersRef.current.set(jobId, timer);
    },
    [navigate],
  );

  const fetchAvailableStudies = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setLoading(true);
      const studies: Study[] = [];

      try {
        const entries = await listArticles();
        for (const entry of entries) {
          let summary = entry.summary || '';
          let numVariants: number | null = null;

          if (summary.startsWith('{')) {
            try {
              const parsed = JSON.parse(summary);
              summary = parsed.summary || '';
              numVariants = parsed.num_variants ?? null;
            } catch { /* use raw summary */ }
          }

          studies.push({
            id: entry.pmid,
            jobId: entry.job_id ?? null,
            pmid: entry.pmid,
            pmcid: entry.pmcid,
            source: entry.source,
            status: entry.status ?? 'completed',
            progress: entry.progress ?? null,
            title: entry.title || entry.pmcid || `PMID ${entry.pmid}`,
            description: summary,
            numVariants,
            participants: null,
          });
        }
      } catch {
        // API unavailable
      }

      setAvailableStudies(studies);
      if (showSpinner) setLoading(false);

      for (const study of studies) {
        if (study.jobId && isParsingStatus(study.status)) {
          startJobPoller(study.jobId, study.pmid, () => {
            void fetchAvailableStudies(false);
          });
        }
      }
    },
    [startJobPoller],
  );

  useEffect(() => {
    void fetchAvailableStudies(true);
  }, [fetchAvailableStudies]);

  useEffect(() => {
    const pollers = activePollersRef.current;
    return () => {
      pollers.forEach((timer) => clearInterval(timer));
      pollers.clear();
    };
  }, []);

  const filteredStudies = useMemo(() => {
    const base = searchTerm.trim()
      ? availableStudies.filter((study) => {
          const term = searchTerm.toLowerCase();
          return (
            study.pmid.toLowerCase().includes(term) ||
            (study.pmcid?.toLowerCase().includes(term) ?? false) ||
            study.title.toLowerCase().includes(term)
          );
        })
      : availableStudies;

    return [...base].sort((a, b) => {
      const aParsing = isParsingStatus(a.status) ? 0 : 1;
      const bParsing = isParsingStatus(b.status) ? 0 : 1;
      return aParsing - bParsing;
    });
  }, [searchTerm, availableStudies]);

  const handleStudyClick = (study: Study) => {
    if (isParsingStatus(study.status)) return;
    navigate(`/viewer/${study.pmid}`);
  };

  const handleArticleAdded = (pmid: string, jobData: JobResponse) => {
    toast.success(`Analysis complete for PMID ${pmid}!`);
    navigate(`/viewer/${pmid}`, { state: { dynamicData: jobData } });
  };

  const handlePdfUploaded = (
    pmid: string,
    jobData: JobResponse,
    title?: string | null,
  ) => {
    if (jobData.status === 'completed') {
      toast.success(`Analysis complete for PMID ${pmid}!`, {
        position: 'top-right',
        action: {
          label: 'View',
          onClick: () => navigate(`/viewer/${pmid}`, { state: { dynamicData: jobData } }),
        },
      });
      void fetchAvailableStudies(false);
      return;
    }

    // Optimistically show the parsing article at the top of the dashboard immediately
    const optimisticStudy: Study = {
      id: pmid,
      jobId: jobData.job_id,
      pmid,
      pmcid: jobData.pmcid,
      source: jobData.source,
      status: jobData.status,
      progress: jobData.progress,
      title: title || jobData.pmcid || `PMID ${pmid}`,
      description: '',
      numVariants: null,
      participants: null,
    };
    setAvailableStudies((prev) => [
      optimisticStudy,
      ...prev.filter((existing) => existing.pmid !== pmid),
    ]);

    startJobPoller(jobData.job_id, pmid, () => {
      void fetchAvailableStudies(false);
    });
  };

  return (
    <div className="min-h-screen bg-gradient-subtle">
      <header className="bg-transparent">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 rounded-lg flex items-center justify-center">
                <img src="/favicon.ico" alt="PMC Icon" className="w-8 h-8 rounded-lg" />
              </div>
              <h1 className="text-xl font-bold text-foreground"></h1>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8 text-center">
          <h2 className="text-5xl font-bold text-foreground mb-4">AutoGKB</h2>
          <p className="text-xl text-muted-foreground mb-8">
            Search all available studies
          </p>
          <div className="max-w-2xl mx-auto space-y-4">
            <Input
              type="text"
              placeholder="Search by PMID, PMCID, or title..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full text-lg py-3 px-6"
            />
            <div className="flex gap-3 justify-center">
              <Button
                onClick={() => setIsAddDialogOpen(true)}
                size="lg"
              >
                Add PMC Article
              </Button>
              <Button
                onClick={() => setIsUploadDialogOpen(true)}
                size="lg"
                variant="outline"
              >
                Upload PDF
              </Button>
              <Button variant="outline" size="lg" onClick={() => navigate('/triage')}>
                Literature Triage
              </Button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredStudies.map((study) => {
            const parsing = isParsingStatus(study.status);
            const statusText =
              study.progress || STATUS_LABELS[study.status] || 'Parsing article...';

            return (
              <Card
                key={study.id}
                className={
                  parsing
                    ? 'bg-card border-amber-300/80 dark:border-amber-700/70 shadow-sm cursor-default'
                    : 'cursor-pointer hover:shadow-medium transition-bounce bg-card border-border hover:border-primary/20'
                }
                onClick={() => handleStudyClick(study)}
              >
                <CardHeader>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <div className="px-3 py-1.5 bg-primary/10 text-primary text-xs font-medium rounded-full">
                        {study.pmcid || `PMID: ${study.pmid}`}
                      </div>
                      {study.source === 'pdf_upload' && (
                        <div className="px-2 py-1 bg-orange-100 text-orange-700 text-xs font-medium rounded-full">
                          PDF
                        </div>
                      )}
                      {parsing && (
                        <div className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200 text-xs font-medium rounded-full">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          Parsing
                        </div>
                      )}
                    </div>
                    {!parsing && study.numVariants != null && (
                      <div className="px-3 py-1.5 bg-accent text-accent-foreground text-xs font-medium rounded-full truncate">
                        {study.numVariants} Variant{study.numVariants !== 1 ? 's' : ''}
                      </div>
                    )}
                  </div>
                  <CardTitle className="text-lg leading-tight line-clamp-2">
                    {study.title}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {parsing ? (
                    <CardDescription className="mb-4 text-amber-800/90 dark:text-amber-300/90">
                      {statusText}
                    </CardDescription>
                  ) : (
                    <CardDescription className="mb-4 line-clamp-3">
                      {study.description}
                    </CardDescription>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>

        {filteredStudies.length === 0 && searchTerm && (
          <div className="text-center py-12">
            <div className="w-16 h-16 bg-muted rounded-full mx-auto mb-4 flex items-center justify-center">
              <span className="text-muted-foreground text-xl">🔍</span>
            </div>
            <h3 className="text-lg font-medium text-foreground mb-2">No studies found</h3>
            <p className="text-muted-foreground">
              Try adjusting your search terms
            </p>
          </div>
        )}

        {loading && (
          <div className="text-center py-12">
            <div className="w-16 h-16 bg-muted rounded-full mx-auto mb-4 flex items-center justify-center animate-pulse">
              <span className="text-muted-foreground text-xl">📄</span>
            </div>
            <h3 className="text-lg font-medium text-foreground mb-2">Loading studies...</h3>
            <p className="text-muted-foreground">
              Checking for available markdown and JSON files
            </p>
          </div>
        )}

        {!loading && availableStudies.length === 0 && (
          <div className="text-center py-12">
            <div className="w-16 h-16 bg-muted rounded-full mx-auto mb-4 flex items-center justify-center">
              <span className="text-muted-foreground text-xl">📄</span>
            </div>
            <h3 className="text-lg font-medium text-foreground mb-2">No studies available</h3>
            <p className="text-muted-foreground">
              Add corresponding .md and .json files to data/markdown/ and data/annotations/ folders
            </p>
          </div>
        )}
      </main>

      <AddArticleDialog
        open={isAddDialogOpen}
        onOpenChange={setIsAddDialogOpen}
        onSuccess={handleArticleAdded}
      />

      <UploadPdfDialog
        open={isUploadDialogOpen}
        onOpenChange={setIsUploadDialogOpen}
        onSuccess={handlePdfUploaded}
      />
    </div>
  );
};

export default Dashboard;
