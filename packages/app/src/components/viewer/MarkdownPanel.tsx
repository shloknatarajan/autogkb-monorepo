import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';

interface MarkdownPanelProps {
  markdown: string;
  isFullWidth?: boolean;
}

export const MarkdownPanel: React.FC<MarkdownPanelProps> = ({ markdown, isFullWidth = false }) => {
  return (
    <div className="h-full border-r">
      <Card className="h-full rounded-none border-0 shadow-none">
        <CardHeader className="bg-gradient-secondary border-b">
          <CardTitle className="text-lg">Paper Content</CardTitle>
        </CardHeader>
        <CardContent className="p-0 h-full">
          <ScrollArea className="h-[calc(100vh-8rem)]">
            <div className={`${isFullWidth ? 'max-w-3xl mx-auto' : ''} p-6 prose prose-lg prose-slate prose-h1:text-black prose-headings:text-primary prose-p:text-foreground prose-p:font-libertinus prose-strong:text-foreground prose-strong:font-libertinus prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg prose-h4:text-base prose-h5:text-sm prose-h6:text-xs prose-h2:border-b prose-h2:border-border prose-h2:pb-2 prose-p:leading-relaxed prose-li:font-libertinus prose-table:border-collapse prose-table:border-2 prose-table:border-border prose-table:shadow-soft prose-table:rounded-lg prose-table:overflow-hidden prose-th:border prose-th:border-border prose-th:bg-secondary prose-th:px-6 prose-th:py-4 prose-th:text-left prose-th:font-bold prose-th:text-foreground prose-td:border prose-td:border-border prose-td:px-6 prose-td:py-3 prose-td:text-sm prose-td:align-top prose-td:font-libertinus prose-table:w-full prose-table:my-8 prose-tr:even:bg-muted/30 ${isFullWidth ? '' : 'max-w-none'}`}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  table: ({node, ...props}) => <div className="bg-table-bg p-4 rounded-lg my-8"><table className="w-full border-collapse border-2 border-border shadow-soft rounded-lg overflow-hidden bg-background" {...props} /></div>,
                  thead: ({node, ...props}) => <thead {...props} />,
                  tbody: ({node, ...props}) => <tbody {...props} />,
                  tr: ({node, ...props}) => <tr className="even:bg-muted/30" {...props} />,
                  th: ({node, ...props}) => <th className="border border-border bg-secondary px-6 py-4 text-left font-bold text-foreground" {...props} />,
                  td: ({node, ...props}) => <td className="border border-border px-6 py-3 text-sm align-top" {...props} />,
                  img: ({node, ...props}) => <span className="block bg-figure-bg p-4 rounded-lg my-6"><img {...props} /></span>,
                  p: ({node, ...props}) => <p className="whitespace-pre-line" {...props} />
                }}
              >
                {markdown.replace(/\[([^\]]+)\]\(#[^)]+\)\1/g, '[$1](#)')}
              </ReactMarkdown>
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
};