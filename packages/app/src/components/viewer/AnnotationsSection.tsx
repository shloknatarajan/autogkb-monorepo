import React from 'react';

interface AnnotationsSectionProps {
  title: string;
  annotations: any[];
}

export const AnnotationsSection: React.FC<AnnotationsSectionProps> = ({ title, annotations }) => {
  if (!annotations || annotations.length === 0) return null;

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3 text-primary">{title}</h3>
      {annotations.map((annotation: any, index: number) => (
        <div key={index} className="mb-4">
          <p className="text-sm">{JSON.stringify(annotation, null, 2)}</p>
        </div>
      ))}
    </div>
  );
};