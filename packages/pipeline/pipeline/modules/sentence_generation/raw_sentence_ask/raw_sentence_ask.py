"""
Raw Sentence Ask Experiment - LLM-based sentence generation.

Given an article and a specific variant, ask LLMs to generate sentences
describing the pharmacogenetic association, similar to ground truth sentences.

Improvements:
- Uses combined article + supplement text via BioC integration
- Provides variant context via SNP expander (alternative notations)
"""

import json
import re
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from shared.utils import call_llm, get_markdown_text
from generation.modules.variant_finding.regex_variants.extract_variants_v5 import (
    get_combined_text,
)
from shared.term_normalization.snp_expansion import SNPExpander

# Paths
SENTENCE_BENCH_PATH = (
    Path(__file__).resolve().parents[4]
    / "data"
    / "benchmark_v2"
    / "sentence_bench.jsonl"
)
PROMPTS_FILE = Path(__file__).parent / "prompts.yaml"
RESULTS_DIR = Path(__file__).parent / "results"

# Global SNP expander instance
_snp_expander: SNPExpander | None = None


def get_snp_expander() -> SNPExpander:
    """Get or initialize the SNP expander singleton."""
    global _snp_expander
    if _snp_expander is None:
        _snp_expander = SNPExpander()
        _snp_expander.load_or_build()
    return _snp_expander


def load_prompts() -> dict:
    """Load prompts from yaml file."""
    with open(PROMPTS_FILE) as f:
        return yaml.safe_load(f)


def load_sentence_bench() -> dict[str, dict[str, list[str]]]:
    """
    Load sentence benchmark data.

    Returns:
        dict mapping pmcid -> {variant: [sentences]}
    """
    data: dict[str, dict[str, list[str]]] = {}

    with open(SENTENCE_BENCH_PATH) as f:
        for line in f:
            entry = json.loads(line)
            pmcid = entry["pmcid"]
            variant = entry["variant"]
            sentences = entry["sentences"]

            if pmcid not in data:
                data[pmcid] = {}
            data[pmcid][variant] = sentences

    return data


def get_variant_context(variant: str) -> str:
    """
    Get additional context for a variant using SNP expander.

    For rsID variants, looks up alternative notations (gene + position).
    For star alleles and HLA alleles, returns empty string.

    Returns:
        Context string like "Also known as: CYP2B6 516G>T" or empty string.
    """
    expander = get_snp_expander()

    # Check if this is an rsID
    if variant.lower().startswith("rs"):
        # Search through all mappings for this rsID
        alt_notations = []
        for (gene, notation), mapping in expander._mappings.items():
            if mapping.rsid.lower() == variant.lower():
                alt_notations.append(f"{gene} {notation}")
                if mapping.star_allele:
                    alt_notations.append(mapping.star_allele)

        # Also check curated mappings
        for (gene, notation), mapping in expander._curated_mappings.items():
            if mapping.rsid.lower() == variant.lower():
                alt_notations.append(f"{gene} {notation}")

        if alt_notations:
            unique_notations = list(set(alt_notations))
            return f"Also known as: {', '.join(unique_notations)}"

    return ""


def normalize_for_comparison(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    # Lowercase
    text = text.lower()
    # Remove extra whitespace
    text = " ".join(text.split())
    # Remove punctuation except key characters
    text = re.sub(r"[^\w\s\*\:\+\-/>]", "", text)
    return text


def compute_similarity(generated: str, ground_truth: str) -> float:
    """
    Compute similarity between generated and ground truth sentence.

    Uses word overlap (Jaccard similarity) as a simple metric.
    """
    gen_words = set(normalize_for_comparison(generated).split())
    gt_words = set(normalize_for_comparison(ground_truth).split())

    if not gt_words:
        return 1.0 if not gen_words else 0.0

    intersection = gen_words & gt_words
    union = gen_words | gt_words

    return len(intersection) / len(union) if union else 0.0


def score_generated_sentence(generated: str, ground_truth_sentences: list[str]) -> dict:
    """
    Score a generated sentence against ground truth.

    Returns best match similarity and which ground truth it matched best.
    """
    if not ground_truth_sentences:
        return {"best_similarity": 0.0, "best_match_idx": -1, "best_match": None}

    similarities = [compute_similarity(generated, gt) for gt in ground_truth_sentences]

    best_idx = similarities.index(max(similarities))

    return {
        "best_similarity": similarities[best_idx],
        "best_match_idx": best_idx,
        "best_match": ground_truth_sentences[best_idx],
        "all_similarities": similarities,
    }


def run_experiment(
    model: str,
    prompt_version: str = "v1",
    pmcids: list[str] | None = None,
    max_variants_per_article: int | None = None,
    use_supplements: bool = True,
    use_variant_context: bool = True,
) -> dict:
    """
    Run sentence generation experiment.

    Args:
        model: LLM model to use
        prompt_version: Which prompt version from prompts.yaml
        pmcids: List of PMCIDs to process (None = all in sentence bench)
        max_variants_per_article: Limit variants per article (for testing)
        use_supplements: Whether to include supplementary material text
        use_variant_context: Whether to include variant alternative notations
    """
    prompts = load_prompts()
    prompt_config = prompts[prompt_version]
    sentence_bench = load_sentence_bench()

    # Initialize SNP expander if using variant context
    if use_variant_context:
        print("Initializing SNP expander...")
        expander = get_snp_expander()
        stats = expander.stats()
        print(f"  Loaded {stats['total_mappings']} SNP notation mappings\n")

    # Filter to specified PMCIDs
    if pmcids:
        sentence_bench = {k: v for k, v in sentence_bench.items() if k in pmcids}

    print("Running sentence generation experiment")
    print(f"Model: {model}")
    print(f"Prompt: {prompt_version} ({prompt_config['name']})")
    print(f"Articles: {len(sentence_bench)}")
    print(f"Use supplements: {use_supplements}")
    print(f"Use variant context: {use_variant_context}")
    print("=" * 60)

    results = {
        "model": model,
        "prompt_version": prompt_version,
        "prompt_name": prompt_config["name"],
        "timestamp": datetime.now().isoformat(),
        "use_supplements": use_supplements,
        "use_variant_context": use_variant_context,
        "per_article_results": [],
    }

    total_similarity = 0
    total_variants = 0
    articles_with_supplements = 0

    for pmcid, variant_sentences in sentence_bench.items():
        print(f"\n{pmcid} ({len(variant_sentences)} variants)")
        print("-" * 40)

        # Get article text (with or without supplements)
        if use_supplements:
            combined_text, supplement_text = get_combined_text(pmcid)
            article_text = combined_text
            has_supplement = supplement_text is not None
            if has_supplement:
                articles_with_supplements += 1
                print("  [+supplement]")
        else:
            article_text = get_markdown_text(pmcid)
            has_supplement = False

        if not article_text:
            print("  No article text found, skipping")
            continue

        article_results = {
            "pmcid": pmcid,
            "has_supplement": has_supplement,
            "variants": [],
        }

        variants_to_process = list(variant_sentences.keys())
        if max_variants_per_article:
            variants_to_process = variants_to_process[:max_variants_per_article]

        for variant in variants_to_process:
            ground_truth = variant_sentences[variant]

            # Get variant context if enabled
            variant_context = ""
            if use_variant_context:
                variant_context = get_variant_context(variant)

            # Format prompt with variant context if available
            if variant_context and "{variant_context}" in prompt_config["user"]:
                user_prompt = prompt_config["user"].format(
                    variant=variant,
                    variant_context=variant_context,
                    article_text=article_text,
                )
            else:
                # Fallback for prompts without variant_context placeholder
                user_prompt = prompt_config["user"].format(
                    variant=variant,
                    article_text=article_text,
                )
                # Append context if available but no placeholder
                if variant_context:
                    user_prompt = user_prompt.replace(
                        f'variant "{variant}"',
                        f'variant "{variant}" ({variant_context})',
                    )

            system_prompt = prompt_config["system"]

            # Call LLM
            try:
                generated = call_llm(model, system_prompt, user_prompt)
            except Exception as e:
                logger.error(f"Error generating for {pmcid}/{variant}: {e}")
                generated = ""

            # Score result
            score = score_generated_sentence(generated, ground_truth)

            variant_result = {
                "variant": variant,
                "variant_context": variant_context,
                "generated": generated,
                "ground_truth": ground_truth,
                **score,
            }
            article_results["variants"].append(variant_result)

            total_similarity += score["best_similarity"]
            total_variants += 1

            # Print result
            sim_pct = score["best_similarity"] * 100
            status = "✓" if sim_pct >= 70 else ("○" if sim_pct >= 40 else "✗")
            context_note = f" [{variant_context}]" if variant_context else ""
            print(f"  {status} {variant}: {sim_pct:.0f}% similarity{context_note}")
            print(f"      Generated: {generated[:100]}...")
            print(f"      Ground truth: {ground_truth[0][:100]}...")

        # Calculate article average
        if article_results["variants"]:
            article_results["avg_similarity"] = sum(
                v["best_similarity"] for v in article_results["variants"]
            ) / len(article_results["variants"])
        else:
            article_results["avg_similarity"] = 0

        results["per_article_results"].append(article_results)

    # Calculate overall metrics
    avg_similarity = total_similarity / total_variants if total_variants else 0
    results["avg_similarity"] = avg_similarity
    results["total_variants"] = total_variants
    results["articles_with_supplements"] = articles_with_supplements

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total variants processed: {total_variants}")
    print(f"Average similarity: {avg_similarity:.1%}")
    if use_supplements:
        print(
            f"Articles with supplements: {articles_with_supplements}/{len(sentence_bench)}"
        )

    # Count by similarity threshold
    all_sims = [
        v["best_similarity"]
        for a in results["per_article_results"]
        for v in a["variants"]
    ]
    high_sim = sum(1 for s in all_sims if s >= 0.7)
    med_sim = sum(1 for s in all_sims if 0.4 <= s < 0.7)
    low_sim = sum(1 for s in all_sims if s < 0.4)
    print(f"High similarity (>=70%): {high_sim}")
    print(f"Medium similarity (40-70%): {med_sim}")
    print(f"Low similarity (<40%): {low_sim}")

    # Save results
    RESULTS_DIR.mkdir(exist_ok=True)
    safe_model_name = model.replace("/", "_").replace(":", "_")
    suffix = "_enhanced" if (use_supplements or use_variant_context) else ""
    output_path = RESULTS_DIR / f"{safe_model_name}_{prompt_version}{suffix}.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run sentence generation experiment")
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument("--prompt", default="v1", help="Prompt version (v1, v2)")
    parser.add_argument(
        "--pmcids",
        nargs="+",
        default=None,
        help="Specific PMCIDs to process",
    )
    parser.add_argument(
        "--max-variants",
        type=int,
        default=None,
        help="Max variants per article",
    )
    parser.add_argument(
        "--no-supplements",
        action="store_true",
        help="Disable supplement text integration",
    )
    parser.add_argument(
        "--no-variant-context",
        action="store_true",
        help="Disable variant context/normalization",
    )

    args = parser.parse_args()
    run_experiment(
        args.model,
        args.prompt,
        args.pmcids,
        args.max_variants,
        use_supplements=not args.no_supplements,
        use_variant_context=not args.no_variant_context,
    )
