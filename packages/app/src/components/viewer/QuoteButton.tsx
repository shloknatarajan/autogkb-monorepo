import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Quote } from 'lucide-react';

interface QuoteButtonProps {
  quote: string;
  index: number;
  onClick: (quote: string) => void;
}

export const QuoteButton: React.FC<QuoteButtonProps> = ({ quote, index, onClick }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  
  const shouldTruncate = quote.length > 120; // Roughly 2 lines worth
  const displayQuote = !isExpanded && shouldTruncate 
    ? quote.substring(0, 120) 
    : quote;
  
  const handleQuoteClick = () => {
    onClick(quote);
  };
  
  const handleExpandClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsExpanded(!isExpanded);
  };
  
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={handleQuoteClick}
      className="h-auto px-2 py-1 text-xs hover:bg-primary/10 justify-start text-left whitespace-normal"
    >
      <div className="flex items-start gap-1 w-full">
        <span className="font-medium text-primary flex-shrink-0">[{index + 1}]</span>
        <div className="italic text-muted-foreground min-w-0 flex-1">
          <span className="leading-relaxed block">
            {displayQuote}
            {!isExpanded && shouldTruncate && (
              <span
                onClick={handleExpandClick}
                className="text-primary hover:text-primary/80 ml-1 underline cursor-pointer"
              >
                ...
              </span>
            )}
            {isExpanded && shouldTruncate && (
              <span
                onClick={handleExpandClick}
                className="text-primary hover:text-primary/80 ml-1 underline cursor-pointer"
              >
                {' '}Show less
              </span>
            )}
          </span>
        </div>
      </div>
    </Button>
  );
};

interface QuoteButtonsProps {
  quotes?: string[];
  onQuoteClick: (quote: string) => void;
}

export const QuoteButtons: React.FC<QuoteButtonsProps> = ({ quotes, onQuoteClick }) => {
  if (!quotes || quotes.length === 0) return null;
  
  return (
    <div className="space-y-1 mt-2">
      {quotes.map((quote, index) => (
        <QuoteButton
          key={index}
          quote={quote}
          index={index}
          onClick={onQuoteClick}
        />
      ))}
    </div>
  );
};