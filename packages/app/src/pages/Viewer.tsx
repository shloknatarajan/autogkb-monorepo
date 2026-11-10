import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useViewerData } from '@/hooks/useViewerData';
import { useQuoteHighlight } from '@/hooks/useQuoteHighlight';
import { ViewerHeader } from '@/components/viewer/ViewerHeader';
import { MarkdownPanel } from '@/components/viewer/MarkdownPanel';
import { AnnotationsPanel } from '@/components/viewer/AnnotationsPanel';
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable';
import { ImperativePanelHandle } from 'react-resizable-panels';
import { Menu } from 'lucide-react';
import { JobProgress } from '@/components/JobProgress';

const Viewer = () => {
  const { pmid } = useParams<{ pmid: string }>();
  const navigate = useNavigate();
  const { data, job, loading, error } = useViewerData(pmid);
  const { handleQuoteClick } = useQuoteHighlight();
  const [isRightPanelCollapsed, setIsRightPanelCollapsed] = useState(false);
  const rightPanelRef = useRef<ImperativePanelHandle>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Check for Ctrl+E (Windows/Linux) or Cmd+E (Mac)
      if ((e.ctrlKey || e.metaKey) && e.key === 'e') {
        e.preventDefault();
        
        if (rightPanelRef.current) {
          if (isRightPanelCollapsed) {
            rightPanelRef.current.expand();
          } else {
            rightPanelRef.current.collapse();
          }
          setIsRightPanelCollapsed(!isRightPanelCollapsed);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isRightPanelCollapsed]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-subtle flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 bg-gradient-primary rounded-full mx-auto mb-4 animate-pulse"></div>
          <p className="text-muted-foreground">Loading study data...</p>
        </div>
      </div>
    );
  }

  if (job && job.status !== 'completed') {
    const failed = job.status === 'failed';
    return (
      <div className="min-h-screen bg-gradient-subtle">
        <ViewerHeader pmid={pmid || ''} />
        <main className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-3xl items-center px-6 py-12">
          <Card className="w-full overflow-hidden border-primary/15 shadow-strong">
            <div className={failed ? 'h-1 bg-destructive' : 'h-1 bg-primary'} />
            <CardHeader className="pb-3">
              <CardTitle>{failed ? 'Curation could not finish' : 'Curation in progress'}</CardTitle>
              <p className="text-sm text-muted-foreground">
                PMID {pmid} {failed
                  ? 'encountered an error during processing.'
                  : 'is moving through the evidence extraction pipeline. This page updates automatically.'}
              </p>
            </CardHeader>
            <CardContent className="space-y-5">
              <JobProgress status={job.status} progress={failed ? job.error : job.progress} />
              <div className="flex items-center justify-between border-t pt-4 text-xs text-muted-foreground">
                <span>Job {job.job_id.slice(0, 8)}</span>
                {!failed && <span>You can leave this page; processing will continue.</span>}
              </div>
              {failed && (
                <Button variant="outline" onClick={() => navigate('/triage')}>Back to triage</Button>
              )}
            </CardContent>
          </Card>
        </main>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-gradient-subtle flex items-center justify-center">
        <Card className="max-w-md mx-auto">
          <CardHeader>
            <CardTitle>Study Not Found</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4">
              {error || `The requested PMID "${pmid}" could not be found.`}
            </p>
            <Button onClick={() => navigate('/dashboard')}>
              Back to Dashboard
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-subtle">
      <ViewerHeader pmid={pmid || ''} />
      <div className="relative h-[calc(100vh-4rem)]">
        {isRightPanelCollapsed && (
          <Button
            onClick={() => {
              rightPanelRef.current?.expand();
              setIsRightPanelCollapsed(false);
            }}
            className="fixed top-20 right-4 z-50 shadow-strong"
            size="icon"
            variant="default"
          >
            <Menu className="h-4 w-4" />
          </Button>
        )}
        <ResizablePanelGroup direction="horizontal" className="h-full">
          <ResizablePanel defaultSize={50} minSize={30}>
            <MarkdownPanel markdown={data.markdown} isFullWidth={isRightPanelCollapsed} />
          </ResizablePanel>
          {!isRightPanelCollapsed && <ResizableHandle withHandle />}
          <ResizablePanel 
            ref={rightPanelRef}
            defaultSize={50} 
            minSize={0}
            collapsible
            collapsedSize={0}
            onCollapse={() => setIsRightPanelCollapsed(true)}
            onExpand={() => setIsRightPanelCollapsed(false)}
          >
            <AnnotationsPanel 
              jsonData={data.json} 
              benchmarkJsonData={data.benchmarkJson}
              analysisJsonData={data.analysisJson}
              onQuoteClick={handleQuoteClick} 
            />
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </div>
  );
};

export default Viewer;
