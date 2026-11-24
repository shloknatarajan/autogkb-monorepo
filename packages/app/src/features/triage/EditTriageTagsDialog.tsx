import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, Plus, RotateCcw, X } from 'lucide-react';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  updateTriageArticleTags,
  regenerateTriageArticleTags,
  type DrugTag,
  type GeneTag,
  type PaperType,
  type PgxInformationType,
  type PhenotypeTag,
  type TriageArticle,
  type UpdateTriageArticleTagsPayload,
} from '@/lib/api';

const PAPER_TYPE_OPTIONS: { value: PaperType; label: string }[] = [
  { value: 'methods', label: 'Methods' },
  { value: 'population_frequency', label: 'Population Frequency' },
  { value: 'allele_function', label: 'Allele Function' },
  { value: 'implementation', label: 'Implementation' },
  { value: 'review', label: 'Review' },
  { value: 'phenoconversion', label: 'Phenoconversion' },
];

const PGX_INFO_OPTIONS: { value: PgxInformationType; label: string }[] = [
  { value: 'pd', label: 'PD' },
  { value: 'dosage', label: 'Dosage' },
  { value: 'efficacy', label: 'Efficacy' },
  { value: 'toxicity', label: 'Toxicity' },
  { value: 'other', label: 'Other' },
  { value: 'metabolism_pk', label: 'Metabolism/PK' },
];

interface DraftEntityTag {
  name: string;
  id?: string | null;
  source?: string | null;
  url?: string | null;
  resolved?: boolean;
  mentions?: string[];
  ncbi_gene_id?: string | null;
  hgnc_id?: string | null;
  categories?: string[];
  directions?: string[];
  drugs?: string[];
  genes?: string[];
}

interface EditTriageTagsDialogProps {
  open: boolean;
  sessionId: string | null;
  article: TriageArticle | null;
  onOpenChange: (open: boolean) => void;
  onSaved: (sessionId: string, updated: TriageArticle) => void;
}

function appendDraftTerms(
  current: DraftEntityTag[],
  rawInput: string,
): DraftEntityTag[] {
  const parts = rawInput
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) return current;

  const existingKeys = new Set(
    current.map((tag) => tag.name.trim().toLocaleLowerCase()),
  );
  const next = [...current];
  for (const term of parts) {
    const key = term.toLocaleLowerCase();
    if (existingKeys.has(key)) continue;
    existingKeys.add(key);
    next.push({ name: term, resolved: false, mentions: [term] });
  }
  return next;
}

const EditTriageTagsDialog: React.FC<EditTriageTagsDialogProps> = ({
  open,
  sessionId,
  article,
  onOpenChange,
  onSaved,
}) => {
  const [paperTypes, setPaperTypes] = useState<PaperType[]>([]);
  const [pgxInfoTypes, setPgxInfoTypes] = useState<PgxInformationType[]>([]);
  const [drugTags, setDrugTags] = useState<DraftEntityTag[]>([]);
  const [geneTags, setGeneTags] = useState<DraftEntityTag[]>([]);
  const [phenotypeTags, setPhenotypeTags] = useState<DraftEntityTag[]>([]);

  const [drugInput, setDrugInput] = useState('');
  const [geneInput, setGeneInput] = useState('');
  const [phenotypeInput, setPhenotypeInput] = useState('');

  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const hydrateFromArticle = useCallback((source: TriageArticle) => {
    setPaperTypes([...(source.paper_types ?? [])]);
    setPgxInfoTypes([...(source.pgx_information_types ?? [])]);
    setDrugTags([...(source.drug_tags ?? [])]);
    setGeneTags([...(source.gene_tags ?? [])]);
    setPhenotypeTags([...(source.phenotype_tags ?? [])]);
    setDrugInput('');
    setGeneInput('');
    setPhenotypeInput('');
  }, []);

  useEffect(() => {
    if (open && article) {
      hydrateFromArticle(article);
    }
  }, [open, article, hydrateFromArticle]);

  const busy = saving || regenerating;

  const toggleItem = <T extends string>(
    list: T[],
    setList: React.Dispatch<React.SetStateAction<T[]>>,
    value: T,
  ) => {
    setList((prev) =>
      prev.includes(value) ? prev.filter((item) => item !== value) : [...prev, value],
    );
  };

  const handleSave = async () => {
    if (!sessionId || !article || busy) return;

    const finalDrugs = appendDraftTerms(drugTags, drugInput);
    const finalGenes = appendDraftTerms(geneTags, geneInput);
    const finalPhenotypes = appendDraftTerms(phenotypeTags, phenotypeInput);

    const payload: UpdateTriageArticleTagsPayload = {
      paper_types: paperTypes,
      pgx_information_types: pgxInfoTypes,
      drug_tags: finalDrugs as Partial<DrugTag>[],
      gene_tags: finalGenes as Partial<GeneTag>[],
      phenotype_tags: finalPhenotypes as Partial<PhenotypeTag>[],
    };

    setSaving(true);
    try {
      const updated = await updateTriageArticleTags(sessionId, article.pmid, payload);
      onSaved(sessionId, updated);
      toast.success('Tags updated');
      onOpenChange(false);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to save tags';
      toast.error(message);
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerateOriginal = async () => {
    if (!sessionId || !article || busy) return;

    setRegenerating(true);
    try {
      const regenerated = await regenerateTriageArticleTags(sessionId, article.pmid);
      hydrateFromArticle(regenerated);
      onSaved(sessionId, regenerated);
      toast.success('Regenerated original tags');
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to regenerate original tags';
      toast.error(message);
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!busy) onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="sm:max-w-[620px]">
        <DialogHeader>
          <DialogTitle>Edit Tags</DialogTitle>
          <DialogDescription className="line-clamp-2">
            {article ? (
              <>
                <span className="font-mono text-xs">PMID {article.pmid}</span>
                {article.title ? ` · ${article.title}` : ''}
              </>
            ) : null}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[65vh] space-y-5 overflow-y-auto py-2 pr-1">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Paper type
            </p>
            <div className="flex flex-wrap gap-1.5">
              {PAPER_TYPE_OPTIONS.map((option) => {
                const active = paperTypes.includes(option.value);
                return (
                  <button
                    key={option.value}
                    type="button"
                    disabled={busy}
                    onClick={() => toggleItem(paperTypes, setPaperTypes, option.value)}
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                      active
                        ? 'border-slate-400 bg-slate-200 text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100'
                        : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              PGx information
            </p>
            <div className="flex flex-wrap gap-1.5">
              {PGX_INFO_OPTIONS.map((option) => {
                const active = pgxInfoTypes.includes(option.value);
                return (
                  <button
                    key={option.value}
                    type="button"
                    disabled={busy}
                    onClick={() => toggleItem(pgxInfoTypes, setPgxInfoTypes, option.value)}
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                      active
                        ? 'border-violet-400 bg-violet-100 text-violet-900 dark:border-violet-700 dark:bg-violet-950 dark:text-violet-100'
                        : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>

          <EntityTagEditor
            label="Drugs"
            placeholder="Add drug (e.g., warfarin)"
            tags={drugTags}
            inputValue={drugInput}
            disabled={busy}
            badgeClassName="border-sky-200 bg-sky-50 text-sky-800 hover:bg-sky-50 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-200"
            onInputChange={setDrugInput}
            onAdd={() => {
              setDrugTags((prev) => appendDraftTerms(prev, drugInput));
              setDrugInput('');
            }}
            onRemove={(index) =>
              setDrugTags((prev) => prev.filter((_, idx) => idx !== index))
            }
          />

          <EntityTagEditor
            label="Genes"
            placeholder="Add gene (e.g., CYP2D6)"
            tags={geneTags}
            inputValue={geneInput}
            disabled={busy}
            badgeClassName="border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
            onInputChange={setGeneInput}
            onAdd={() => {
              setGeneTags((prev) => appendDraftTerms(prev, geneInput));
              setGeneInput('');
            }}
            onRemove={(index) =>
              setGeneTags((prev) => prev.filter((_, idx) => idx !== index))
            }
          />

          <EntityTagEditor
            label="Phenotypes"
            placeholder="Add phenotype (e.g., Poor Metabolizer)"
            tags={phenotypeTags}
            inputValue={phenotypeInput}
            disabled={busy}
            badgeClassName="border-amber-200 bg-amber-50 text-amber-900 hover:bg-amber-50 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
            onInputChange={setPhenotypeInput}
            onAdd={() => {
              setPhenotypeTags((prev) => appendDraftTerms(prev, phenotypeInput));
              setPhenotypeInput('');
            }}
            onRemove={(index) =>
              setPhenotypeTags((prev) => prev.filter((_, idx) => idx !== index))
            }
          />

          <p className="text-[11px] text-muted-foreground">
            New drug, gene, and phenotype entries are automatically matched to ClinPGx/CPIC identifiers when saved.
          </p>
        </div>

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void handleRegenerateOriginal()}
          >
            {regenerating ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            )}
            {regenerating ? 'Regenerating...' : 'Regenerate original'}
          </Button>
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={busy}
              onClick={() => void handleSave()}
            >
              {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              {saving ? 'Saving...' : 'Save changes'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

interface EntityTagEditorProps {
  label: string;
  placeholder: string;
  tags: DraftEntityTag[];
  inputValue: string;
  disabled: boolean;
  badgeClassName: string;
  onInputChange: (value: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
}

const EntityTagEditor: React.FC<EntityTagEditorProps> = ({
  label,
  placeholder,
  tags,
  inputValue,
  disabled,
  badgeClassName,
  onInputChange,
  onAdd,
  onRemove,
}) => (
  <div className="space-y-2">
    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {label}
    </p>
    {tags.length > 0 && (
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tag, idx) => (
          <Badge
            key={`${tag.source ?? 'draft'}:${tag.id ?? tag.name}:${idx}`}
            variant="outline"
            className={`gap-1 pr-1 ${badgeClassName}`}
            title={tag.id ? `${tag.source ?? 'ClinPGx'} ${tag.id}` : undefined}
          >
            <span>{tag.name}</span>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onRemove(idx)}
              className="rounded-full p-0.5 hover:bg-black/10 dark:hover:bg-white/10"
              aria-label={`Remove ${tag.name}`}
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
      </div>
    )}
    <div className="flex gap-2">
      <Input
        value={inputValue}
        disabled={disabled}
        placeholder={placeholder}
        className="h-8 text-xs"
        onChange={(e) => onInputChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onAdd();
          }
        }}
      />
      <Button
        type="button"
        size="sm"
        variant="secondary"
        disabled={disabled || !inputValue.trim()}
        onClick={onAdd}
        className="h-8 shrink-0 px-2.5 text-xs"
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add
      </Button>
    </div>
  </div>
);

export default EditTriageTagsDialog;
