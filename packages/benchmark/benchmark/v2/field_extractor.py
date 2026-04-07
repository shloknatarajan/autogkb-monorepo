"""
Given a proposed annotation, extract the fields that are relevant to the benchmark.
Should be able to take an argument of variant, drug, or phenotype and get all the values from those fields
across annotations from the json file
function: (proposed_annotation: str | Path to json file (ex. data/proposed_annotations/PMC384715.json), dict: {pmcid [str]: all the fields list[str]})
"""

import json
from enum import Enum
from pydantic import BaseModel


class FieldTypes(Enum):
    """An enum representing the three types of fields we care about: drug, variant, or phenotype."""

    DRUG = "drug"
    VARIANT = "variant"
    PHENOTYPE = "phenotype"


class SingleArticleFields(BaseModel):
    pmcid: str
    type: FieldTypes
    fields: list[str]


def null_to_empty(value: str | None) -> str:
    """Converts null string values to empty strings"""
    if value is None or value == "null" or value == "":
        return ""
    return value


def fields_from_file(file_path: str, field_type: str) -> SingleArticleFields:
    """Gets all the (unique) mentioned drugs in a file"""
    if field_type not in [ft.value for ft in FieldTypes]:
        raise ValueError(f"Invalid field type: {field_type}")

    # from json file, extract all the drugs from variant/drug annotations
    with open(file_path, "r") as f:
        data = json.load(f)
    fields: list[str] = []
    pmcid = data["pmcid"]

    field_map = {
        "drug": "Drug(s)",
        "variant": "Variant/Haplotypes",
        "phenotype": "Phenotype",
    }

    field_key = field_map[field_type]
    for item in data.get("var_drug_ann", []) or []:
        value = null_to_empty(item.get(field_key))
        if value:
            fields.append(value)
    for item in data.get("var_pheno_ann", []) or []:
        value = null_to_empty(item.get(field_key))
        if value:
            fields.append(value)
    for item in data.get("var_fa_ann", []) or []:
        value = null_to_empty(item.get(field_key))
        if value:
            fields.append(value)

    return SingleArticleFields(pmcid=pmcid, type=FieldTypes(field_type), fields=fields)


if __name__ == "__main__":
    print(fields_from_file("data/proposed_annotations/PMC384715.json", "drug"))
    print(fields_from_file("data/proposed_annotations/PMC384715.json", "variant"))
    print(fields_from_file("data/proposed_annotations/PMC384715.json", "phenotype"))
