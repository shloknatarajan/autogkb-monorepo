
import React, { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { HyperlinkedText } from '@/components/ui/hyperlinked-text';
import { StudyParametersSection } from './StudyParametersSection';
import { DrugAnnotationsSection } from './DrugAnnotationsSection';
import { FunctionalAnnotationsSection } from './FunctionalAnnotationsSection';
import { PhenotypeAnnotationsSection } from './PhenotypeAnnotationsSection';
import { CollapsibleCitations } from './CollapsibleCitations';
import { AnalysisSection } from './AnalysisSection';

interface AnnotationsPanelProps {
  jsonData: any;
  benchmarkJsonData: any | null;
  analysisJsonData: any | null;
  onQuoteClick: (quote: string) => void;
}

// Helper to detect if data is in the new annotation_sentences format
const isAnnotationSentencesFormat = (data: any): boolean => {
  return data?.result?.associations !== undefined || data?.metadata?.pipeline_config !== undefined;
};

// Newer API responses nest the result as {pmcid, summary, variants, num_variants, associations}
// inside data.result.summary (i.e. data.result.summary is the whole result object).
// Older ones have data.result.summary as a plain string.
const unwrapResult = (data: any): { summary: string | null; associations: any[]; variants: string[] } => {
  if (isAnnotationSentencesFormat(data)) {
    const result = data?.result;
    const rawSummary = result?.summary;

    // Newer shape: result.summary is an object containing the real fields
    if (rawSummary && typeof rawSummary === 'object' && 'summary' in rawSummary) {
      return {
        summary: rawSummary.summary ?? null,
        associations: rawSummary.associations ?? result?.associations ?? [],
        variants: rawSummary.variants ?? result?.variants ?? [],
      };
    }

    // Older shape: result.summary is already a string
    return {
      summary: typeof rawSummary === 'string' ? rawSummary : null,
      associations: result?.associations ?? [],
      variants: result?.variants ?? [],
    };
  }

  // Legacy format
  return {
    summary: typeof data?.summary === 'string' ? data.summary : null,
    associations: [],
    variants: [],
  };
};

export const AnnotationsPanel: React.FC<AnnotationsPanelProps> = ({ jsonData, benchmarkJsonData, analysisJsonData, onQuoteClick }) => {
  const [expandedAssociations, setExpandedAssociations] = useState<Set<number>>(new Set());

  const isNewFormat = isAnnotationSentencesFormat(jsonData);
  const { summary, associations, variants } = unwrapResult(jsonData);

  return (
    <Card className="h-full rounded-none border-0 shadow-none flex flex-col">
      <CardHeader className="bg-gradient-secondary border-b flex-shrink-0">
        <CardTitle className="text-lg">Structured Data</CardTitle>
      </CardHeader>
      <CardContent className="p-0 flex-1 flex flex-col min-h-0">
        <Tabs defaultValue="formatted" className="w-full flex flex-col h-full">
          <div className="border-b px-6 pt-4 flex-shrink-0 bg-background">
            <TabsList className="grid w-full grid-cols-1">
              <TabsTrigger value="formatted">Formatted View</TabsTrigger>
              {/* Curator and Analysis tabs hidden but code preserved below */}
            </TabsList>
          </div>
          
          <TabsContent value="formatted" className="mt-0 flex-1 min-h-0">
            <ScrollArea className="h-[calc(100vh-12rem)]">
              <div className="p-6 space-y-4">
                {/* Summary Section */}
                {summary && (
                  <div className="mb-6">
                    <h3 className="text-2xl font-semibold text-black mb-3">Summary</h3>
                    <div className="text-sm text-foreground prose prose-sm max-w-none prose-headings:text-lg prose-headings:font-semibold prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-ul:my-2 prose-li:my-0">
                      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
                        {summary}
                      </ReactMarkdown>
                    </div>
                  </div>
                )}

                {/* New Format: Variant Associations */}
                {isNewFormat && associations.length > 0 && (
                  <div id="found-associations-section">
                    <div className="flex justify-between items-center mb-3">
                      <h3 className="text-2xl font-semibold text-black">Variant Associations</h3>
                      {variants.length > 0 && (
                        <span className="text-sm text-muted-foreground">
                          {variants.length} variant{variants.length !== 1 ? 's' : ''} identified
                        </span>
                      )}
                    </div>

                    {/* Summary Table for new format with integrated expandable details */}
                    <div className="mb-6 overflow-x-auto">
                      <table className="w-full border-collapse border border-border rounded-lg">
                        <thead>
                          <tr className="bg-muted/50">
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium w-8"></th>
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium">Variant</th>
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium">Association</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...associations]
                            .map((assoc: any, index: number) => ({ assoc, originalIndex: index }))
                            .sort((a, b) => {
                              const aIsMentioned = /mentioned.*not studied/i.test(a.assoc.sentence || '');
                              const bIsMentioned = /mentioned.*not studied/i.test(b.assoc.sentence || '');
                              if (aIsMentioned === bIsMentioned) return 0;
                              return aIsMentioned ? 1 : -1;
                            })
                            .map(({ assoc, originalIndex: index }) => {
                            const isExpanded = expandedAssociations.has(index);
                            const hasDetails = assoc.explanation || (assoc.citations && assoc.citations.length > 0);

                            return (
                              <React.Fragment key={index}>
                                <tr
                                  className={`${hasDetails ? 'cursor-pointer' : ''} hover:bg-muted/25 transition-colors ${isExpanded ? 'bg-muted/30' : ''}`}
                                  onClick={() => {
                                    if (!hasDetails) return;
                                    const newExpanded = new Set(expandedAssociations);
                                    if (isExpanded) {
                                      newExpanded.delete(index);
                                    } else {
                                      newExpanded.add(index);
                                    }
                                    setExpandedAssociations(newExpanded);
                                  }}
                                >
                                  <td className="border border-border px-2 py-2 text-sm text-center">
                                    {hasDetails && (
                                      <ChevronRight
                                        className={`h-4 w-4 text-muted-foreground transition-transform duration-200 inline-block ${isExpanded ? 'rotate-90' : ''}`}
                                      />
                                    )}
                                  </td>
                                  <td className="border border-border px-3 py-2 text-sm font-medium">
                                    {assoc.variant_id}
                                  </td>
                                  <td className="border border-border px-3 py-2 text-sm">
                                    {assoc.sentence}
                                  </td>
                                </tr>
                                {isExpanded && hasDetails && (
                                  <tr className="bg-muted/15">
                                    <td colSpan={3} className="border border-border px-4 py-3">
                                      <div className="space-y-2 pl-4 border-l-2 border-primary/30">
                                        {assoc.explanation && (
                                          <div>
                                            <p className="text-sm text-black">
                                              <strong>Explanation:</strong> {assoc.explanation}
                                            </p>
                                          </div>
                                        )}
                                        {assoc.citations && assoc.citations.length > 0 && (
                                          <CollapsibleCitations
                                            citations={assoc.citations}
                                            onQuoteClick={onQuoteClick}
                                          />
                                        )}
                                      </div>
                                    </td>
                                  </tr>
                                )}
                              </React.Fragment>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Old Format: Header with Study Parameters and Found Associations button */}
                {!isNewFormat && (
                  <>
                    <div className="flex justify-between items-center mb-3">
                      <h3 className="text-2xl font-semibold text-black">Study Parameters</h3>
                      {jsonData.annotations?.relationships && (
                        <button
                          onClick={() => {
                            const foundAssociationsElement = document.getElementById('found-associations-section');
                            if (foundAssociationsElement) {
                              foundAssociationsElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
                            }
                          }}
                          className="px-3 py-1 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 transition-colors"
                        >
                          Found Associations
                        </button>
                      )}
                    </div>

                    {/* Study Parameters Content */}
                    <div>
                      <StudyParametersSection
                        studyParameters={jsonData.study_parameters}
                      />
                    </div>
                  </>
                )}

                {/* Old Format: Annotations */}
                {!isNewFormat && jsonData.annotations?.relationships && (
                  <div id="found-associations-section">
                    <h3 className="text-2xl font-semibold mb-2 text-black">Found Associations</h3>
                    
                    {/* Summary Table with integrated expandable details */}
                    <div className="mb-6 overflow-x-auto">
                      <table className="w-full border-collapse border border-border rounded-lg">
                        <thead>
                          <tr className="bg-muted/50">
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium w-8"></th>
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium">Gene</th>
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium">Polymorphism</th>
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium">Drug</th>
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium">Effect</th>
                            <th className="border border-border px-3 py-2 text-left text-sm font-medium">P-value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {jsonData.annotations.relationships.map((relationship: any, index: number) => {
                            const isExpanded = expandedAssociations.has(index);
                            const hasDetails = (relationship.citations && relationship.citations.length > 0) ||
                              (relationship.p_value_citations && relationship.p_value_citations.length > 0) ||
                              relationship.relationship_effect;

                            return (
                              <React.Fragment key={index}>
                                <tr
                                  className={`${hasDetails ? 'cursor-pointer' : ''} hover:bg-muted/25 transition-colors ${isExpanded ? 'bg-muted/30' : ''}`}
                                  onClick={() => {
                                    if (!hasDetails) return;
                                    const newExpanded = new Set(expandedAssociations);
                                    if (isExpanded) {
                                      newExpanded.delete(index);
                                    } else {
                                      newExpanded.add(index);
                                    }
                                    setExpandedAssociations(newExpanded);
                                  }}
                                >
                                  <td className="border border-border px-2 py-2 text-sm text-center">
                                    {hasDetails && (
                                      <ChevronRight
                                        className={`h-4 w-4 text-muted-foreground transition-transform duration-200 inline-block ${isExpanded ? 'rotate-90' : ''}`}
                                      />
                                    )}
                                  </td>
                                  <td className="border border-border px-3 py-2 text-sm font-medium">
                                    {relationship.gene}
                                  </td>
                                  <td className="border border-border px-3 py-2 text-sm">
                                    <HyperlinkedText item={relationship.polymorphism} />
                                  </td>
                                  <td className="border border-border px-3 py-2 text-sm">
                                    {relationship.drug ? <HyperlinkedText item={relationship.drug} /> : 'N/A'}
                                  </td>
                                  <td className="border border-border px-3 py-2 text-sm">
                                    {relationship.relationship_effect}
                                  </td>
                                  <td className="border border-border px-3 py-2 text-sm">
                                    {relationship.p_value || 'N/A'}
                                  </td>
                                </tr>
                                {isExpanded && hasDetails && (
                                  <tr className="bg-muted/15">
                                    <td colSpan={6} className="border border-border px-4 py-3">
                                      <div className="space-y-2 pl-4 border-l-2 border-primary/30">
                                        <p className="text-sm text-black">
                                          <strong>Effect:</strong> {relationship.relationship_effect}
                                        </p>
                                        {relationship.p_value && (
                                          <p className="text-sm text-black">
                                            <strong>P-value:</strong> {relationship.p_value}
                                            {relationship.p_value_citations && relationship.p_value_citations.length > 0 && (
                                              <span className="ml-2">
                                                <CollapsibleCitations
                                                  citations={relationship.p_value_citations}
                                                  onQuoteClick={onQuoteClick}
                                                  inline={true}
                                                />
                                              </span>
                                            )}
                                          </p>
                                        )}
                                        {relationship.citations && relationship.citations.length > 0 && (
                                          <CollapsibleCitations
                                            citations={relationship.citations}
                                            onQuoteClick={onQuoteClick}
                                          />
                                        )}
                                      </div>
                                    </td>
                                  </tr>
                                )}
                              </React.Fragment>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Old Format: Drug Annotations */}
                {!isNewFormat && jsonData.var_drug_ann && (
                  <DrugAnnotationsSection
                    drugAnnotations={jsonData.var_drug_ann}
                    onQuoteClick={onQuoteClick}
                  />
                )}

                {/* Old Format: Phenotype Annotations */}
                {!isNewFormat && jsonData.var_pheno_ann && (
                  <PhenotypeAnnotationsSection
                    phenotypeAnnotations={jsonData.var_pheno_ann}
                    onQuoteClick={onQuoteClick}
                  />
                )}

                {/* Old Format: Functional Annotations */}
                {!isNewFormat && jsonData.var_fa_ann && (
                  <FunctionalAnnotationsSection
                    functionalAnnotations={jsonData.var_fa_ann}
                    onQuoteClick={onQuoteClick}
                  />
                )}
              </div>
            </ScrollArea>
          </TabsContent>
          
          <TabsContent value="curator" className="mt-0 flex-1 min-h-0">
            <ScrollArea className="h-[calc(100vh-12rem)]">
              <div className="p-6 space-y-4">
                {benchmarkJsonData ? (
                  <>
                    {/* Summary Section */}
                    {benchmarkJsonData.summary && (
                      <div className="mb-6">
                        <h3 className="text-2xl font-semibold text-black mb-3">Summary</h3>
                        <p className="text-sm text-foreground">{benchmarkJsonData.summary}</p>
                      </div>
                    )}
                    
                    {/* Study Parameters */}
                    <div className="flex justify-between items-center mb-3">
                      <h3 className="text-2xl font-semibold text-black">Study Parameters</h3>
                    </div>
                    
                    <div>
                      <StudyParametersSection 
                        studyParameters={benchmarkJsonData.study_parameters} 
                      />
                    </div>

                    {/* Drug Annotations */}
                    {benchmarkJsonData.var_drug_ann && benchmarkJsonData.var_drug_ann.length > 0 && (
                      <DrugAnnotationsSection 
                        drugAnnotations={benchmarkJsonData.var_drug_ann} 
                        onQuoteClick={onQuoteClick}
                      />
                    )}

                    {/* Phenotype Annotations */}
                    {benchmarkJsonData.var_pheno_ann && benchmarkJsonData.var_pheno_ann.length > 0 && (
                      <PhenotypeAnnotationsSection 
                        phenotypeAnnotations={benchmarkJsonData.var_pheno_ann} 
                        onQuoteClick={onQuoteClick}
                      />
                    )}

                    {/* Functional Annotations */}
                    {benchmarkJsonData.var_fa_ann && benchmarkJsonData.var_fa_ann.length > 0 && (
                      <FunctionalAnnotationsSection 
                        functionalAnnotations={benchmarkJsonData.var_fa_ann} 
                        onQuoteClick={onQuoteClick}
                      />
                    )}
                  </>
                ) : (
                  <div className="text-center text-muted-foreground py-12">
                    <p>No curator annotations available for this study.</p>
                  </div>
                )}
              </div>
            </ScrollArea>
          </TabsContent>
          
          {/* Analysis tab content hidden but preserved for future use
          <TabsContent value="analysis" className="mt-0 flex-1 min-h-0">
            <ScrollArea className="h-[calc(100vh-12rem)]">
              <div className="p-6">
                <AnalysisSection analysisData={analysisJsonData} />
              </div>
            </ScrollArea>
          </TabsContent>
          */}
        </Tabs>
      </CardContent>
    </Card>
  );
};
