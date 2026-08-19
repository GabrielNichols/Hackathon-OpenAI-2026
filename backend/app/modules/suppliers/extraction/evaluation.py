from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .models import ExtractionFieldStatus, ProviderExtractedFieldDTO

DEFAULT_DATASET_PATH = Path(__file__).with_name("datasets") / "synthetic_evaluation.json"


class ExpectedEvaluationField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ExtractionFieldStatus
    normalized_value: Any | None = None


class ExtractionEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    expected_fields: dict[str, ExpectedEvaluationField]
    predicted_fields: list[dict[str, Any]]


class ExtractionEvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    classification: Literal["synthetic_test_only"]
    authorized_for_real_demo: Literal[False]
    disclaimer: str
    cases: list[ExtractionEvaluationCase]


class ExtractionEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_name: str
    classification: Literal["synthetic_test_only"]
    authorized_for_real_demo: Literal[False]
    case_count: int
    schema_valid_rate: float | None
    price_accuracy: float | None
    minimum_people_accuracy: float | None
    not_found_detection_rate: float | None
    critical_hallucination_rate: float | None


def load_evaluation_dataset(path: Path = DEFAULT_DATASET_PATH) -> ExtractionEvaluationDataset:
    return ExtractionEvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def evaluate_dataset(dataset: ExtractionEvaluationDataset) -> ExtractionEvaluationReport:
    schema_valid = 0
    exact_matches = {"base_price_cents": 0, "minimum_people": 0}
    exact_totals = {"base_price_cents": 0, "minimum_people": 0}
    not_found_detected = 0
    not_found_total = 0
    hallucinations = 0

    for case in dataset.cases:
        parsed_by_name: dict[str, ProviderExtractedFieldDTO] = {}
        try:
            parsed_fields = [
                ProviderExtractedFieldDTO.model_validate(field) for field in case.predicted_fields
            ]
            if len({field.field_name for field in parsed_fields}) != len(parsed_fields):
                raise ValueError("duplicate predicted field")
            parsed_by_name = {field.field_name: field for field in parsed_fields}
            schema_valid += 1
        except (TypeError, ValueError):
            parsed_by_name = {}

        raw_by_name = {
            str(field.get("field_name")): field
            for field in case.predicted_fields
            if field.get("field_name") is not None
        }
        for field_name, expected in case.expected_fields.items():
            predicted = parsed_by_name.get(field_name)
            if (
                field_name in exact_totals
                and expected.status is not ExtractionFieldStatus.NOT_FOUND
            ):
                exact_totals[field_name] += 1
                if (
                    predicted is not None
                    and predicted.normalized_value == expected.normalized_value
                ):
                    exact_matches[field_name] += 1
            if expected.status is ExtractionFieldStatus.NOT_FOUND:
                not_found_total += 1
                if predicted is not None and predicted.status is ExtractionFieldStatus.NOT_FOUND:
                    not_found_detected += 1
                raw_prediction = raw_by_name.get(field_name)
                if raw_prediction is not None and raw_prediction.get("value") is not None:
                    hallucinations += 1

    return ExtractionEvaluationReport(
        dataset_name=dataset.name,
        classification=dataset.classification,
        authorized_for_real_demo=dataset.authorized_for_real_demo,
        case_count=len(dataset.cases),
        schema_valid_rate=_ratio(schema_valid, len(dataset.cases)),
        price_accuracy=_ratio(
            exact_matches["base_price_cents"],
            exact_totals["base_price_cents"],
        ),
        minimum_people_accuracy=_ratio(
            exact_matches["minimum_people"],
            exact_totals["minimum_people"],
        ),
        not_found_detection_rate=_ratio(not_found_detected, not_found_total),
        critical_hallucination_rate=_ratio(hallucinations, not_found_total),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate supplier extraction predictions on an explicitly synthetic dataset."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    report = evaluate_dataset(load_evaluation_dataset(arguments.dataset))
    serialized = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
    if arguments.output is None:
        print(serialized)
    else:
        arguments.output.write_text(f"{serialized}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
