## Overview
Create a similar setup to the sentence generation ask, but we want to save the generated sentences to a file on their own.
This will be a json file with the following structure:
```json
{
    "<PMCID>": {
        "<variant>": [
            "sentence 1",
            "sentence 2"
        ]
    }
}
```

## Process
1. Run a prompt from prompts.yaml (name of the prompt setup should be a param)
2. Save the generated sentences to a timestamped file on their own that uses model name, prompt name, and timestamp as part of the filename.
This file will follow the structure shown above.
3. For this, we just want to test against one pmcid and its variants. We can get the first pmcid and its variants from the variant_bench.jsonl file.

## Notes
- use load_dotenv() to load the environment variables
- use datetime to get the current timestamp
- have the outputs saved to a directory called "outputs"

You should just be creating one python file to do this in this directory

