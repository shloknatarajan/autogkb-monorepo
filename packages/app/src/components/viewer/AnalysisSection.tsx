import React from 'react';
import { CheckCircle, XCircle, AlertCircle, TrendingUp } from 'lucide-react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ChevronRight } from 'lucide-react';

interface AnalysisSectionProps {
  analysisData: any;
}

const ScoreBadge: React.FC<{ score: number }> = ({ score }) => {
  const percentage = Math.round(score * 100);
  let bgColor = 'bg-red-100 text-red-800';
  if (percentage >= 80) bgColor = 'bg-green-100 text-green-800';
  else if (percentage >= 50) bgColor = 'bg-yellow-100 text-yellow-800';
  
  return (
    <span className={`px-2 py-1 rounded text-sm font-medium ${bgColor}`}>
      {percentage}%
    </span>
  );
};

const FieldComparison: React.FC<{ field: string; comparison: any }> = ({ field, comparison }) => {
  const isMatch = comparison.match_status === 'match';
  const isPartial = comparison.match_status === 'partial_match';
  
  return (
    <div className={`p-2 rounded text-sm ${isMatch ? 'bg-green-50' : isPartial ? 'bg-yellow-50' : 'bg-red-50'}`}>
      <div className="flex items-center gap-2 mb-1">
        {isMatch ? (
          <CheckCircle className="h-4 w-4 text-green-600" />
        ) : isPartial ? (
          <AlertCircle className="h-4 w-4 text-yellow-600" />
        ) : (
          <XCircle className="h-4 w-4 text-red-600" />
        )}
        <span className="font-medium">{field}</span>
        <span className="text-muted-foreground ml-auto">{Math.round(comparison.score * 100)}%</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <span className="text-muted-foreground">Ground Truth:</span>
          <div className="font-mono">{String(comparison.ground_truth ?? 'null')}</div>
        </div>
        <div>
          <span className="text-muted-foreground">Prediction:</span>
          <div className="font-mono">{String(comparison.prediction ?? 'null')}</div>
        </div>
      </div>
    </div>
  );
};

const AnnotationItem: React.FC<{ item: any; index: number }> = ({ item, index }) => {
  const [isOpen, setIsOpen] = React.useState(false);
  const annotation = item.annotation || item;
  const isMatched = item.matched;
  
  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger asChild>
        <div className={`flex items-center gap-2 p-3 rounded cursor-pointer hover:bg-muted/50 border ${isMatched ? 'border-green-200 bg-green-50/50' : 'border-red-200 bg-red-50/50'}`}>
          <ChevronRight className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
          {isMatched ? (
            <CheckCircle className="h-4 w-4 text-green-600" />
          ) : (
            <XCircle className="h-4 w-4 text-red-600" />
          )}
          <span className="font-medium text-sm">
            {annotation.Gene || annotation.gene || 'Unknown'} - {annotation['Variant/Haplotypes'] || annotation.variant || 'N/A'}
          </span>
          {item.overall_match_score !== undefined && (
            <span className="ml-auto">
              <ScoreBadge score={item.overall_match_score} />
            </span>
          )}
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="pl-6 pr-2 py-2 space-y-2">
          {item.field_comparison && (
            <div className="space-y-1">
              {Object.entries(item.field_comparison).map(([field, comparison]) => (
                <FieldComparison key={field} field={field} comparison={comparison} />
              ))}
            </div>
          )}
          {!item.field_comparison && (
            <div className="text-sm space-y-1">
              {Object.entries(annotation).map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <span className="text-muted-foreground">{key}:</span>
                  <span>{String(value ?? 'null')}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
};

const BenchmarkSection: React.FC<{ title: string; data: any }> = ({ title, data }) => {
  const [isOpen, setIsOpen] = React.useState(true);
  
  if (!data) return null;
  
  const groundTruth = data.ground_truth_annotations;
  const extraPredictions = data.extra_predictions;
  const missingAnnotations = data.missing_annotations;
  
  const extraCount = extraPredictions?.count ?? extraPredictions?.length ?? 0;
  const extraItems = extraPredictions?.items ?? (Array.isArray(extraPredictions) ? extraPredictions : []);
  const missingCount = missingAnnotations?.count ?? missingAnnotations?.length ?? 0;
  const missingItems = missingAnnotations?.items ?? (Array.isArray(missingAnnotations) ? missingAnnotations : []);
  
  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger asChild>
        <div className="flex items-center gap-2 p-3 bg-secondary/30 rounded cursor-pointer hover:bg-secondary/50">
          <ChevronRight className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
          <h4 className="font-semibold flex-1">{title}</h4>
          <ScoreBadge score={data.score} />
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="p-3 space-y-4">
          {/* Stats */}
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="p-2 bg-muted/50 rounded">
              <div className="text-lg font-bold">{groundTruth?.matched_count ?? 0}</div>
              <div className="text-xs text-muted-foreground">Matched</div>
            </div>
            <div className="p-2 bg-red-50 rounded">
              <div className="text-lg font-bold text-red-600">{extraCount}</div>
              <div className="text-xs text-muted-foreground">Extra Predictions</div>
            </div>
            <div className="p-2 bg-yellow-50 rounded">
              <div className="text-lg font-bold text-yellow-600">{missingCount}</div>
              <div className="text-xs text-muted-foreground">Missing</div>
            </div>
          </div>

          {/* Matched Annotations */}
          {groundTruth?.items && groundTruth.items.length > 0 && (
            <div>
              <h5 className="text-sm font-medium mb-2 flex items-center gap-2">
                <CheckCircle className="h-4 w-4 text-green-600" />
                Ground Truth Annotations ({groundTruth.count})
              </h5>
              <div className="space-y-2">
                {groundTruth.items.map((item: any, idx: number) => (
                  <AnnotationItem key={idx} item={item} index={idx} />
                ))}
              </div>
            </div>
          )}

          {/* Extra Predictions */}
          {extraItems.length > 0 && (
            <div>
              <h5 className="text-sm font-medium mb-2 flex items-center gap-2">
                <AlertCircle className="h-4 w-4 text-orange-600" />
                Extra Predictions (False Positives)
              </h5>
              <div className="space-y-2">
                {extraItems.map((item: any, idx: number) => (
                  <AnnotationItem key={idx} item={{ annotation: item.annotation || item, matched: false }} index={idx} />
                ))}
              </div>
            </div>
          )}

          {/* Missing Annotations */}
          {missingItems.length > 0 && (
            <div>
              <h5 className="text-sm font-medium mb-2 flex items-center gap-2">
                <XCircle className="h-4 w-4 text-red-600" />
                Missing Annotations (False Negatives)
              </h5>
              <div className="space-y-2">
                {missingItems.map((item: any, idx: number) => (
                  <AnnotationItem key={idx} item={{ annotation: item.annotation || item, matched: false }} index={idx} />
                ))}
              </div>
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
};

export const AnalysisSection: React.FC<AnalysisSectionProps> = ({ analysisData }) => {
  if (!analysisData) {
    return (
      <div className="text-center text-muted-foreground py-12">
        <p>No analysis data available for this study.</p>
      </div>
    );
  }

  const benchmarks = analysisData.benchmarks || {};

  return (
    <div className="space-y-6">
      {/* Header with Overall Score */}
      <div className="bg-gradient-to-r from-primary/10 to-primary/5 p-4 rounded-lg">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xl font-semibold text-black">Analysis Results</h3>
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-primary" />
            <span className="text-2xl font-bold">
              {Math.round(analysisData.overall_score * 100)}%
            </span>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          {analysisData.title}
        </p>
        <div className="mt-2 text-xs text-muted-foreground">
          PMCID: {analysisData.pmcid} | PMID: {analysisData.pmid} | Benchmarks: {analysisData.num_benchmarks}
        </div>
      </div>

      {/* Benchmark Sections */}
      <div className="space-y-3">
        {benchmarks.drug_annotations && (
          <BenchmarkSection title="Drug Annotations" data={benchmarks.drug_annotations} />
        )}
        {benchmarks.phenotype_annotations && (
          <BenchmarkSection title="Phenotype Annotations" data={benchmarks.phenotype_annotations} />
        )}
        {benchmarks.functional_analysis && (
          <BenchmarkSection title="Functional Analysis" data={benchmarks.functional_analysis} />
        )}
      </div>
    </div>
  );
};
