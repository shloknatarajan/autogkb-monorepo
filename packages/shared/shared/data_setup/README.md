# Data Setup
This should setup all the files coming from ClinPGx / PubMed. All steps triggered via 
`pixi run setup-data` or `python -m src.data_setup.main`

## Term Lookup Download Links
https://api.clinpgx.org/v1/download/file/data/genes.zip
https://api.clinpgx.org/v1/download/file/data/variants.zip
https://api.clinpgx.org/v1/download/file/data/phenotypes.zip
https://api.clinpgx.org/v1/download/file/data/drugs.zip
https://api.clinpgx.org/v1/download/file/data/variantAnnotations.zip
https://api.clinpgx.org/v1/download/file/data/chemicals.zip

`prepare_term_lookup_data()` materializes reduced local `genes.tsv`,
`drugs.tsv`, and `variants.tsv` tables containing the fields used at runtime.
