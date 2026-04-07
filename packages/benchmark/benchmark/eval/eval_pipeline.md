# Evaluation Pipeline
This should run and create a comprehensive evaluation of the pipeline outputs. Draw on what we've done for the benchmark_v2 and the experiment evaluation scripts.
Judging models and prompts should be configurable via a config file. We should have reports for whether the variants were correctly identified and whether the sentences were correctly generated similar to what we've done in the benchmark_v2 and experiment evaluation scripts.

This should take in a pipeline output (ex. generation_pipeline/outputs/base_config.json) and evaluate it against the ground truth information as we've done in the benchmark_v2 and experiment evaluation scripts for each stage of the pipeline. We are just ignoring citation evaluations for now.

The evaluation results should be saved to a json file with information on what config/output was evaluated and the judging models/prompts used (if the prompts were configurable).