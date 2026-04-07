import React, { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import AddArticleDialog from '@/components/AddArticleDialog';
import { toast } from 'sonner';
import type { JobResponse } from '@/lib/api';
import { listPmcids } from '@/lib/api';

interface Study {
  id: string;
  title: string;
  description: string;
  numVariants: number | null;
  participants: number | null;
}

const Dashboard = () => {
  const navigate = useNavigate();
  const [searchTerm, setSearchTerm] = useState('');
  const [availableStudies, setAvailableStudies] = useState<Study[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);

  useEffect(() => {
    const discoverAvailableStudies = async () => {
      setLoading(true);
      const studies: Study[] = [];

      try {
        // Primary: load completed PMCIDs from the API
        const entries = await listPmcids();
        for (const entry of entries) {
          let summary = entry.summary || '';
          let numVariants: number | null = null;

          // Some entries store summary as a JSON string with num_variants
          if (summary.startsWith('{')) {
            try {
              const parsed = JSON.parse(summary);
              summary = parsed.summary || '';
              numVariants = parsed.num_variants ?? null;
            } catch { /* use raw summary */ }
          }

          studies.push({
            id: entry.pmcid,
            title: entry.title || entry.pmcid,
            description: summary,
            numVariants,
            participants: null,
          });
        }
      } catch {
        // API unavailable — fall back to static file discovery
        try {
          const manifestResponse = await fetch('/data/manifest.json').catch(() => null);
          let pmcIds: string[] = [];
          if (manifestResponse?.ok) {
            const manifest = await manifestResponse.json().catch(() => null);
            pmcIds = manifest?.studies || [];
          }

          for (const pmcid of pmcIds) {
            try {
              const sentencesResponse = await fetch(`/data/annotation_sentences/${pmcid}.json`).catch(() => null);
              const annotationsResponse = sentencesResponse?.ok
                ? null
                : await fetch(`/data/annotations/${pmcid}.json`).catch(() => null);
              const jsonData = sentencesResponse?.ok
                ? await sentencesResponse.json().catch(() => null)
                : annotationsResponse?.ok
                  ? await annotationsResponse.json().catch(() => null)
                  : null;

              if (jsonData) {
                const variants = jsonData.result?.variants;
                studies.push({
                  id: pmcid,
                  title: jsonData.title || jsonData.result?.pmcid || pmcid,
                  description: jsonData.result?.associations?.[0]?.sentence || '',
                  numVariants: Array.isArray(variants) ? variants.length : null,
                  participants: null,
                });
              }
            } catch { /* skip */ }
          }
        } catch { /* nothing */ }
      }

      setAvailableStudies(studies);
      setLoading(false);
    };

    discoverAvailableStudies();
  }, []);

  const filteredStudies = useMemo(() => {
    if (!searchTerm.trim()) return availableStudies;
    
    const term = searchTerm.toLowerCase();
    return availableStudies.filter(study => 
      study.id.toLowerCase().includes(term) ||
      study.title.toLowerCase().includes(term)
    );
  }, [searchTerm, availableStudies]);

  const handlePMCIDClick = (pmcid: string) => {
    navigate(`/viewer/${pmcid}`);
  };

  const handleArticleAdded = (pmcid: string, jobData: JobResponse) => {
    toast.success(`Analysis complete for ${pmcid}!`);
    navigate(`/viewer/${pmcid}`, { state: { dynamicData: jobData } });
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
              placeholder="Search by PMCID or title..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full text-lg py-3 px-6"
            />
            <Button
              onClick={() => setIsAddDialogOpen(true)}
              size="lg"
              className="w-full sm:w-auto"
            >
              Add New Article
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredStudies.map((study) => (
            <Card 
              key={study.id}
              className="cursor-pointer hover:shadow-medium transition-bounce bg-card border-border hover:border-primary/20"
              onClick={() => handlePMCIDClick(study.id)}
            >
              <CardHeader>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="px-3 py-1.5 bg-primary/10 text-primary text-xs font-medium rounded-full">
                    {study.id}
                  </div>
                  {study.numVariants != null && (
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
                <CardDescription className="mb-4 line-clamp-3">
                  {study.description}
                </CardDescription>
                {study.participants && (
                  <div className="flex items-center text-sm text-muted-foreground">
                    <span className="font-medium">Participants:</span>
                    <span className="ml-1">{study.participants}</span>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
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
    </div>
  );
};

export default Dashboard;