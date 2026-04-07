import { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { type JobResponse, getJobByPmid } from '@/lib/api';

interface ViewerData {
  markdown: string;
  json: any;
  benchmarkJson: any | null;
  analysisJson: any | null;
  annotationSource: 'annotation_sentences' | 'annotations';
}

export const useViewerData = (pmid: string | undefined) => {
  const [data, setData] = useState<ViewerData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const location = useLocation();

  useEffect(() => {
    const loadData = async () => {
      if (!pmid) {
        setError('No PMID provided');
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setError(null);

        // Priority 1: Router state (just-analyzed article)
        const dynamicData = location.state?.dynamicData as JobResponse | undefined;
        if (dynamicData?.annotation_data != null && dynamicData?.markdown_content != null) {
          setData({
            markdown: dynamicData.markdown_content ?? '',
            json: dynamicData.annotation_data,
            benchmarkJson: null,
            analysisJson: null,
            annotationSource: 'annotation_sentences',
          });
          setLoading(false);
          return;
        }

        // Priority 2: API fallback (previously analyzed article)
        let jobData = null;
        try {
          jobData = await getJobByPmid(pmid);
        } catch {
          // API unavailable — fall through to static files
        }
        if (jobData?.annotation_data != null && jobData?.markdown_content != null) {
          setData({
            markdown: jobData.markdown_content ?? '',
            json: jobData.annotation_data,
            benchmarkJson: null,
            analysisJson: null,
            annotationSource: 'annotation_sentences',
          });
          setLoading(false);
          return;
        }

        // Priority 3: Static files (existing behavior)
        // Load markdown first
        const markdownResponse = await fetch(`/data/markdown/${pmid}.md`);
        if (!markdownResponse.ok) {
          throw new Error(`Markdown file not found for PMCID: ${pmid}`);
        }
        const markdownText = await markdownResponse.text();

        // Try to load from annotation_sentences first (preferred), then fallback to annotations
        let jsonData: any = null;
        let annotationSource: 'annotation_sentences' | 'annotations' = 'annotations';

        // Try annotation_sentences first
        try {
          const sentencesResponse = await fetch(`/data/annotation_sentences/${pmid}.json`);
          if (sentencesResponse.ok) {
            jsonData = await sentencesResponse.json();
            annotationSource = 'annotation_sentences';
          }
        } catch (e) {
          // Silently continue to fallback
        }

        // Fallback to annotations if annotation_sentences not found
        if (!jsonData) {
          const annotationsResponse = await fetch(`/data/annotations/${pmid}.json`);
          if (!annotationsResponse.ok) {
            throw new Error(`No annotation files found for PMCID: ${pmid}`);
          }
          jsonData = await annotationsResponse.json();
          annotationSource = 'annotations';
        }

        // Try to load benchmark annotations and analysis, but don't fail if they don't exist
        let benchmarkData = null;
        let analysisData = null;
        
        try {
          const benchmarkResponse = await fetch(`/data/benchmark_annotations/${pmid}.json`);
          if (benchmarkResponse.ok) {
            benchmarkData = await benchmarkResponse.json();
          }
        } catch (e) {
          console.log('No benchmark annotations available for', pmid);
        }

        try {
          const analysisResponse = await fetch(`/data/analysis/${pmid}.json`);
          if (analysisResponse.ok) {
            analysisData = await analysisResponse.json();
          }
        } catch (e) {
          console.log('No analysis available for', pmid);
        }

        setData({
          markdown: markdownText,
          json: jsonData,
          benchmarkJson: benchmarkData,
          analysisJson: analysisData,
          annotationSource
        });
      } catch (error) {
        console.error('Error loading data:', error);
        setError(`Failed to load data for PMID: ${pmid}. Please ensure the article has been analyzed.`);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [pmid, location.state]);

  return { data, loading, error };
};