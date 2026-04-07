import React from 'react';
import { CollapsibleCitations } from './CollapsibleCitations';

interface FunctionalAnnotation {
  "Variant Annotation ID": number;
  "Variant/Haplotypes": string;
  "Gene": string;
  "Drug(s)": string;
  "PMID": number;
  "Phenotype Category": string;
  "Significance": string;
  "Notes": string;
  "Sentence": string;
  "Alleles": string;
  "Specialty Population": string | null;
  "Assay type": string | null;
  "Metabolizer types": string | null;
  "isPlural": string;
  "Is/Is Not associated": string;
  "Direction of effect": string | null;
  "Functional terms": string;
  "Gene/gene product": string | null;
  "When treated with/exposed to/when assayed with": string | null;
  "Multiple drugs And/or": string | null;
  "Cell type": string | null;
  "Comparison Allele(s) or Genotype(s)": string | null;
  "Comparison Metabolizer types": string | null;
  "PMID_norm": string;
  "Variant Annotation ID_norm": string;
  "Citations"?: string[];
}

interface FunctionalAnnotationsSectionProps {
  functionalAnnotations: FunctionalAnnotation[];
  onQuoteClick: (quote: string) => void;
}

export const FunctionalAnnotationsSection: React.FC<FunctionalAnnotationsSectionProps> = ({ functionalAnnotations, onQuoteClick }) => {
  if (!functionalAnnotations || functionalAnnotations.length === 0) return null;

  return (
    <div>
      <h3 className="text-2xl font-semibold mb-3 text-black">Functional Annotations</h3>
      {functionalAnnotations.map((annotation, index) => (
        <div key={index} className="mb-6 p-4 border border-border rounded-lg bg-muted/30">
          <h4 className="font-medium text-base mb-3 text-primary border-b pb-1">
            {annotation.Gene} {annotation["Variant/Haplotypes"]}
          </h4>
          <div className="space-y-2 text-sm">
            {annotation.Sentence && (
              <p className="mb-2 italic text-foreground/90">{annotation.Sentence}</p>
            )}
            {annotation["Variant/Haplotypes"] && (
              <p><span className="font-medium">Variant/Haplotypes:</span> {annotation["Variant/Haplotypes"]}</p>
            )}
            {annotation.Gene && (
              <p><span className="font-medium">Gene:</span> {annotation.Gene}</p>
            )}
            {annotation["Drug(s)"] && (
              <p><span className="font-medium">Drug(s):</span> {annotation["Drug(s)"]}</p>
            )}
            {annotation.Alleles && (
              <p><span className="font-medium">Alleles:</span> {annotation.Alleles}</p>
            )}
            {annotation["Phenotype Category"] && (
              <p><span className="font-medium">Phenotype Category:</span> {annotation["Phenotype Category"]}</p>
            )}
            {annotation.Significance && (
              <p><span className="font-medium">Significance:</span> {annotation.Significance}</p>
            )}
            {annotation["Direction of effect"] && (
              <p><span className="font-medium">Direction of Effect:</span> {annotation["Direction of effect"]}</p>
            )}
            {annotation["Functional terms"] && (
              <p><span className="font-medium">Functional Terms:</span> {annotation["Functional terms"]}</p>
            )}
            {annotation["Assay type"] && (
              <p><span className="font-medium">Assay Type:</span> {annotation["Assay type"]}</p>
            )}
            {annotation["Cell type"] && (
              <p><span className="font-medium">Cell Type:</span> {annotation["Cell type"]}</p>
            )}
            {annotation["Gene/gene product"] && (
              <p><span className="font-medium">Gene/Gene Product:</span> {annotation["Gene/gene product"]}</p>
            )}
            {annotation["Metabolizer types"] && (
              <p><span className="font-medium">Metabolizer Types:</span> {annotation["Metabolizer types"]}</p>
            )}
            {annotation["Specialty Population"] && (
              <p><span className="font-medium">Specialty Population:</span> {annotation["Specialty Population"]}</p>
            )}
            {annotation["Comparison Allele(s) or Genotype(s)"] && (
              <p><span className="font-medium">Comparison:</span> {annotation["Comparison Allele(s) or Genotype(s)"]} {annotation["Comparison Metabolizer types"] && `(${annotation["Comparison Metabolizer types"]})`}</p>
            )}
            {annotation.Notes && (
              <p className="mt-2 pt-2 border-t border-border"><span className="font-medium">Notes:</span> {annotation.Notes}</p>
            )}
            {annotation.Citations && annotation.Citations.length > 0 && (
              <div className="mt-3 pt-2 border-t border-border">
                <span className="font-medium">Citations: </span>
                <CollapsibleCitations 
                  citations={annotation.Citations} 
                  onQuoteClick={onQuoteClick}
                  inline={true}
                />
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
