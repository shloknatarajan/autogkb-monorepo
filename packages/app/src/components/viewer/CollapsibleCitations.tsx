import React, { useState } from 'react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { QuoteButtons } from './QuoteButton';

interface CollapsibleCitationsProps {
  citations: string[];
  onQuoteClick: (quote: string) => void;
  inline?: boolean;
  label?: string;
}

export const CollapsibleCitations: React.FC<CollapsibleCitationsProps> = ({ 
  citations, 
  onQuoteClick, 
  inline = false,
  label = "citations"
}) => {
  const [isOpen, setIsOpen] = useState(false);

  if (!citations || citations.length === 0) return null;

  const citationCount = citations.length;

  if (inline) {
    return (
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger asChild>
          <button className="text-primary hover:text-primary/80 text-xs font-medium ml-1">
            [{citationCount} {label}]
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-1">
            <QuoteButtons quotes={citations} onQuoteClick={onQuoteClick} />
          </div>
        </CollapsibleContent>
      </Collapsible>
    );
  }

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger asChild>
        <button className="text-primary hover:text-primary/80 text-sm font-medium">
          [{citationCount} {label}]
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1">
          <QuoteButtons quotes={citations} onQuoteClick={onQuoteClick} />
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
};