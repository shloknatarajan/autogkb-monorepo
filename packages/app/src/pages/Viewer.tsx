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

const AVAILABLE_PMCS = [
  'PMC12035587', 'PMC11430164', 'PMC11971672', 'PMC10275785', 'PMC12038368', 
  'PMC2859392', 'PMC11603346', 'PMC12036300', 'PMC10399933', 'PMC10786722',
  'PMC10880264', 'PMC10946077', 'PMC10993165', 'PMC11062152', 'PMC12260932',
  'PMC12319246', 'PMC12331468', 'PMC3113609', 'PMC3387531', 'PMC3548984',
  'PMC3584248', 'PMC3839910', 'PMC384715', 'PMC4706412', 'PMC4916189',
  'PMC5508045', 'PMC554812', 'PMC5561238', 'PMC6435416', 'PMC6465603',
  'PMC6714829', 'PMC8790808', 'PMC8973308'
];

const Viewer = () => {
  const { pmcid } = useParams<{ pmcid: string }>();
  const navigate = useNavigate();
  const { data, loading, error } = useViewerData(pmcid);
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

  if (error || !data) {
    return (
      <div className="min-h-screen bg-gradient-subtle flex items-center justify-center">
        <Card className="max-w-md mx-auto">
          <CardHeader>
            <CardTitle>Study Not Found</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4">
              {error || `The requested PMCID "${pmcid}" could not be found.`}
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
      <ViewerHeader pmcid={pmcid || ''} />
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
