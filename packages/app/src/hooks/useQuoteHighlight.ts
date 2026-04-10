import { useState } from 'react';

// --- Text collection and position mapping ---

interface TextNodeInfo {
  node: Text;
  startInFull: number;
  endInFull: number;
}

/**
 * Collect all visible text from a container, maintaining a map from
 * character positions in the concatenated string back to DOM Text nodes.
 */
function collectTextNodes(container: Element): { fullText: string; textNodes: TextNodeInfo[] } {
  const textNodes: TextNodeInfo[] = [];
  let fullText = '';

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode: (node: Node) => {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      const tag = parent.tagName.toLowerCase();
      if (tag === 'script' || tag === 'style') return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  let textNode: Text | null;
  while ((textNode = walker.nextNode() as Text)) {
    const text = textNode.textContent || '';
    if (text.length === 0) continue;

    // Add a space between text nodes that would otherwise merge words
    // (e.g., <strong>bold</strong>text → "bold text" not "boldtext")
    if (fullText.length > 0 && !/\s$/.test(fullText) && !/^\s/.test(text)) {
      fullText += ' ';
    }

    textNodes.push({
      node: textNode,
      startInFull: fullText.length,
      endInFull: fullText.length + text.length,
    });
    fullText += text;
  }

  return { fullText, textNodes };
}

// --- Matching strategies ---

/**
 * Build a normalized version of text with a position map back to the original.
 * Normalization: collapse whitespace to single space, lowercase.
 */
function normalizeWithMap(text: string): { normalized: string; toOriginal: number[] } {
  const chars: string[] = [];
  const toOriginal: number[] = [];
  let lastWasSpace = false;

  for (let i = 0; i < text.length; i++) {
    if (/\s/.test(text[i])) {
      if (!lastWasSpace && chars.length > 0) {
        chars.push(' ');
        toOriginal.push(i);
        lastWasSpace = true;
      }
    } else {
      chars.push(text[i].toLowerCase());
      toOriginal.push(i);
      lastWasSpace = false;
    }
  }

  // Trim trailing space
  if (lastWasSpace && chars.length > 0) {
    chars.pop();
    toOriginal.pop();
  }

  return { normalized: chars.join(''), toOriginal };
}

/**
 * Try to find the quote as an exact normalized substring of the document.
 */
function findExactMatch(fullText: string, quote: string): { start: number; end: number } | null {
  const docMap = normalizeWithMap(fullText);
  const quoteNorm = normalizeWithMap(quote).normalized;

  if (quoteNorm.length === 0) return null;

  const idx = docMap.normalized.indexOf(quoteNorm);
  if (idx === -1) return null;

  const origStart = docMap.toOriginal[idx];
  const lastNormIdx = idx + quoteNorm.length - 1;
  const origEnd = lastNormIdx < docMap.toOriginal.length
    ? docMap.toOriginal[lastNormIdx] + 1
    : fullText.length;

  return { start: origStart, end: origEnd };
}

interface WordInfo {
  norm: string;
  origStart: number;
  origEnd: number;
}

function extractWords(text: string): WordInfo[] {
  const words: WordInfo[] = [];
  const regex = /\S+/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    const norm = match[0].toLowerCase().replace(/[,;:""''""'`!?.()[\]{}–—*#]/g, '');
    if (norm.length > 0) {
      words.push({ norm, origStart: match.index, origEnd: match.index + match[0].length });
    }
  }
  return words;
}

/**
 * Word-level sliding window fuzzy match as a fallback.
 */
function findFuzzyMatch(fullText: string, quote: string): { start: number; end: number } | null {
  const docWords = extractWords(fullText);
  const quoteWords = extractWords(quote);

  if (quoteWords.length === 0 || docWords.length === 0) return null;

  const qNorms = quoteWords.map(w => w.norm);

  let bestScore = 0;
  let bestStart = -1;
  let bestEnd = -1;

  const minWin = Math.max(1, Math.floor(qNorms.length * 0.7));
  const maxWin = Math.min(docWords.length, Math.ceil(qNorms.length * 1.3));

  for (let winSize = minWin; winSize <= maxWin; winSize++) {
    // Build initial window word frequency map
    const windowFreq = new Map<string, number>();
    for (let j = 0; j < winSize && j < docWords.length; j++) {
      const w = docWords[j].norm;
      windowFreq.set(w, (windowFreq.get(w) || 0) + 1);
    }

    for (let i = 0; i <= docWords.length - winSize; i++) {
      // Slide window (skip for first position since already initialized)
      if (i > 0) {
        const leaving = docWords[i - 1].norm;
        const lc = windowFreq.get(leaving) || 0;
        if (lc <= 1) windowFreq.delete(leaving);
        else windowFreq.set(leaving, lc - 1);

        const entering = docWords[i + winSize - 1].norm;
        windowFreq.set(entering, (windowFreq.get(entering) || 0) + 1);
      }

      // Count how many quote words are present in this window
      let matches = 0;
      const remaining = new Map(windowFreq);
      for (const qw of qNorms) {
        const count = remaining.get(qw) || 0;
        if (count > 0) {
          matches++;
          remaining.set(qw, count - 1);
        }
      }

      const score = matches / Math.max(qNorms.length, winSize);
      if (score > bestScore) {
        bestScore = score;
        bestStart = i;
        bestEnd = i + winSize - 1;
      }
    }
  }

  // Require at least 50% word match
  if (bestScore < 0.5 || bestStart === -1) return null;

  return {
    start: docWords[bestStart].origStart,
    end: docWords[bestEnd].origEnd,
  };
}

/**
 * Find the best location of the quote in the document text.
 */
function findQuoteInText(fullText: string, quote: string): { start: number; end: number } | null {
  // Strategy 1: Exact normalized substring match (handles most cases)
  const exact = findExactMatch(fullText, quote);
  if (exact) return exact;

  // Strategy 2: Fuzzy word-level sliding window match
  return findFuzzyMatch(fullText, quote);
}

// --- Highlighting ---

/**
 * Remove all existing quote highlights from the container.
 */
function clearHighlights(container: Element): void {
  const highlights = container.querySelectorAll('.quote-highlight');
  highlights.forEach(el => {
    const parent = el.parentNode;
    if (!parent) return;
    const textNode = document.createTextNode(el.textContent || '');
    parent.replaceChild(textNode, el);
    parent.normalize();
  });
}

/**
 * Highlight a character range across potentially multiple text nodes.
 * Returns the first highlight element for scrolling purposes.
 */
function highlightRange(textNodes: TextNodeInfo[], start: number, end: number): HTMLElement | null {
  let firstHighlight: HTMLElement | null = null;

  for (const info of textNodes) {
    // Skip text nodes outside the match range
    if (info.endInFull <= start || info.startInFull >= end) continue;

    // Calculate overlap within this text node
    const nodeStart = Math.max(0, start - info.startInFull);
    const nodeEnd = Math.min(info.node.length, end - info.startInFull);
    if (nodeStart >= nodeEnd) continue;

    // Split the text node to isolate the portion we want to highlight.
    // This avoids surroundContents/extractContents which can break the DOM
    // when ranges cross element boundaries (causing grey overlay artifacts).
    const textNode = info.node;

    // Split off the portion after the highlight
    if (nodeEnd < textNode.length) {
      textNode.splitText(nodeEnd);
    }
    // Split off the portion before the highlight (returns the highlighted part)
    const highlightNode = nodeStart > 0 ? textNode.splitText(nodeStart) : textNode;

    const span = document.createElement('span');
    span.className = 'quote-highlight';
    span.style.backgroundColor = '#fef3c7';
    span.style.borderRadius = '2px';
    span.style.transition = 'background-color 0.3s ease';

    highlightNode.parentNode?.replaceChild(span, highlightNode);
    span.appendChild(highlightNode);

    if (!firstHighlight) firstHighlight = span;
  }

  return firstHighlight;
}

// --- Hook ---

export const useQuoteHighlight = () => {
  const [highlightedText, setHighlightedText] = useState<string | null>(null);

  const handleQuoteClick = (quote: string) => {
    setHighlightedText(quote);

    // Wait for any pending React renders before DOM manipulation
    setTimeout(() => {
      const markdownContainer = document.querySelector('.prose');
      if (!markdownContainer) return;

      // Clear previous highlights and normalize text nodes
      clearHighlights(markdownContainer);

      // Collect all text with position mapping
      const { fullText, textNodes } = collectTextNodes(markdownContainer);
      if (fullText.length === 0) return;

      // Find the quote in the document
      const match = findQuoteInText(fullText, quote);
      if (!match) return;

      // Highlight the matched range and scroll to it
      const firstHighlight = highlightRange(textNodes, match.start, match.end);
      if (firstHighlight) {
        // Use scrollTo on the scroll container instead of scrollIntoView
        // to avoid unwanted horizontal scrolling that shifts the view permanently.
        const scrollContainer = markdownContainer.closest('[data-radix-scroll-area-viewport]')
          || markdownContainer.closest('.overflow-auto, .overflow-y-auto');
        if (scrollContainer) {
          const containerRect = scrollContainer.getBoundingClientRect();
          const highlightRect = firstHighlight.getBoundingClientRect();
          const offsetTop = highlightRect.top - containerRect.top + scrollContainer.scrollTop;
          scrollContainer.scrollTo({
            top: offsetTop - containerRect.height / 2,
            behavior: 'smooth',
          });
        } else {
          // Fallback: use scrollIntoView but prevent horizontal scroll
          const scrollX = window.scrollX;
          firstHighlight.scrollIntoView({ behavior: 'smooth', block: 'center' });
          window.scrollTo({ left: scrollX });
        }
      }
    }, 100);
  };

  return { highlightedText, handleQuoteClick };
};
