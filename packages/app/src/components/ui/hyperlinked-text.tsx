import React from 'react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

interface HyperlinkedTextProps {
  item: string | { value: string; link?: string };
  className?: string;
}

export const HyperlinkedText: React.FC<HyperlinkedTextProps> = ({ item, className = "" }) => {
  // Handle legacy string format
  if (typeof item === 'string') {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className={`${className} cursor-help text-muted-foreground`}>
              {item}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <p>No external link available</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  // Handle new object format with optional link
  if (item && typeof item === 'object' && 'value' in item) {
    if (item.link) {
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <a
                href={item.link}
                target="_blank"
                rel="noopener noreferrer"
                className={`${className} text-primary hover:text-primary/80 underline underline-offset-2`}
              >
                {item.value}
              </a>
            </TooltipTrigger>
            <TooltipContent>
              <p>Click to open external link</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    } else {
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={`${className} cursor-help text-muted-foreground`}>
                {item.value}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <p>No external link available</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    }
  }

  // Fallback
  return <span className={className}>N/A</span>;
};