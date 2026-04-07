import React from 'react';

interface StudyParameter {
  "Study Parameters ID": number;
  "Variant Annotation ID": number;
  "Study Type": string | null;
  "Study Cases": number | null;
  "Study Controls": number | null;
  "Characteristics": string;
  "Characteristics Type": string;
  "Frequency In Cases": number | null;
  "Allele Of Frequency In Cases": string | null;
  "Frequency In Controls": number | null;
  "Allele Of Frequency In Controls": string | null;
  "P Value": string | null;
  "Ratio Stat Type": string | null;
  "Ratio Stat": number | null;
  "Confidence Interval Start": number | null;
  "Confidence Interval Stop": number | null;
  "Biogeographical Groups": string;
  "Variant Annotation ID_norm": string;
}

interface StudyParametersSectionProps {
  studyParameters: StudyParameter[];
}

export const StudyParametersSection: React.FC<StudyParametersSectionProps> = ({ studyParameters }) => {
  if (!studyParameters || studyParameters.length === 0) return null;

  return (
    <div>
      {studyParameters.map((param, index) => (
        <div key={index} className="mb-6 p-4 border border-border rounded-lg bg-muted/30">
          <h4 className="font-medium text-base mb-3 text-primary border-b pb-1">Parameter Set {index + 1}</h4>
          <div className="space-y-2 text-sm">
            {param["Study Type"] && (
              <p><span className="font-medium">Study Type:</span> {param["Study Type"]}</p>
            )}
            {param["Study Cases"] !== null && (
              <p><span className="font-medium">Study Cases:</span> {param["Study Cases"]}</p>
            )}
            {param["Study Controls"] !== null && (
              <p><span className="font-medium">Study Controls:</span> {param["Study Controls"]}</p>
            )}
            {param.Characteristics && (
              <p><span className="font-medium">Characteristics:</span> {param.Characteristics}</p>
            )}
            {param["Characteristics Type"] && (
              <p><span className="font-medium">Characteristics Type:</span> {param["Characteristics Type"]}</p>
            )}
            {param["Frequency In Cases"] !== null && (
              <p><span className="font-medium">Frequency In Cases:</span> {param["Frequency In Cases"]} {param["Allele Of Frequency In Cases"] && `(${param["Allele Of Frequency In Cases"]})`}</p>
            )}
            {param["Frequency In Controls"] !== null && (
              <p><span className="font-medium">Frequency In Controls:</span> {param["Frequency In Controls"]} {param["Allele Of Frequency In Controls"] && `(${param["Allele Of Frequency In Controls"]})`}</p>
            )}
            {param["P Value"] && (
              <p><span className="font-medium">P Value:</span> {param["P Value"]}</p>
            )}
            {param["Ratio Stat Type"] && param["Ratio Stat"] !== null && (
              <p><span className="font-medium">{param["Ratio Stat Type"]}:</span> {param["Ratio Stat"]} {param["Confidence Interval Start"] !== null && param["Confidence Interval Stop"] !== null && `(95% CI: ${param["Confidence Interval Start"]}-${param["Confidence Interval Stop"]})`}</p>
            )}
            {param["Biogeographical Groups"] && (
              <p><span className="font-medium">Biogeographical Groups:</span> {param["Biogeographical Groups"]}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
