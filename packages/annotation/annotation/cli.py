"""Command-line entry point for the annotation experiment."""

import argparse
from pathlib import Path

from annotation.extractor import DEFAULT_MODEL, annotate_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("article", type=Path, help="Article text or Markdown file")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = annotate_text(args.article.read_text(encoding="utf-8"), model=args.model)
    content = result.model_dump_json(indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)


if __name__ == "__main__":
    main()
