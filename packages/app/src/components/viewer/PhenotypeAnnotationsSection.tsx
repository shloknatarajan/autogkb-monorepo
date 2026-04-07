import React from 'react';
import { CollapsibleCitations } from './CollapsibleCitations';

interface PhenotypeAnnotation {
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
  "Metabolizer types": string | null;
  "isPlural": string;
  "Is/Is Not associated": string;
  "Direction of effect": string | null;
  "Side effect/efficacy/other": string;
  "Phenotype": string;
  "Multiple phenotypes And/or": string | null;
  "When treated with/exposed to/when assayed with": string | null;
  "Multiple drugs And/or": string | null;
  "Population types": string | null;
  "Population Phenotypes or diseases": string | null;
  "Multiple phenotypes or diseases And/or": string | null;
  "Comparison Allele(s) or Genotype(s)": string | null;
  "Comparison Metabolizer types": string | null;
  "PMID_norm": string;
  "Variant Annotation ID_norm": string;
  "Citations"?: string[];
}

interface PhenotypeAnnotationsSectionProps {
  phenotypeAnnotations: PhenotypeAnnotation[];
  onQuoteClick: (quote: string) => void;
}

export const PhenotypeAnnotationsSection: React.FC<PhenotypeAnnotationsSectionProps> = ({ phenotypeAnnotations, onQuoteClick }) => {
  if (!phenotypeAnnotations || phenotypeAnnotations.length === 0) return null;

  return (
    <div>
      <h3 className="text-2xl font-semibold mb-3 text-black">Phenotype Annotations</h3>
      {phenotypeAnnotations.map((annotation, index) => (
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
            {annotation.Phenotype && (
              <p><span className="font-medium">Phenotype:</span> {annotation.Phenotype}</p>
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
            {annotation["Side effect/efficacy/other"] && (
              <p><span className="font-medium">Effect Type:</span> {annotation["Side effect/efficacy/other"]}</p>
            )}
            {annotation["Metabolizer types"] && (
              <p><span className="font-medium">Metabolizer Types:</span> {annotation["Metabolizer types"]}</p>
            )}
            {annotation["Specialty Population"] && (
              <p><span className="font-medium">Specialty Population:</span> {annotation["Specialty Population"]}</p>
            )}
            {annotation["Population types"] && annotation["Population Phenotypes or diseases"] && (
              <p><span className="font-medium">Population:</span> {annotation["Population types"]} {annotation["Population Phenotypes or diseases"]}</p>
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
