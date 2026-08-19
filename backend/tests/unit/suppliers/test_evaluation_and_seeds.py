import json

from app.modules.suppliers.extraction.evaluation import (
    evaluate_dataset,
    load_evaluation_dataset,
    main,
)
from app.modules.suppliers.seeds import load_synthetic_supplier_seeds


def test_synthetic_evaluation_dataset_reports_required_metrics_without_demo_claim() -> None:
    dataset = load_evaluation_dataset()
    report = evaluate_dataset(dataset)

    assert dataset.classification == "synthetic_test_only"
    assert dataset.authorized_for_real_demo is False
    assert report.authorized_for_real_demo is False
    assert report.case_count == 3
    assert report.schema_valid_rate == 2 / 3
    assert report.price_accuracy == 1.0
    assert report.not_found_detection_rate == 0.5
    assert report.critical_hallucination_rate == 0.5


def test_evaluation_cli_emits_machine_readable_synthetic_report(capsys: object) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    report = json.loads(output)

    assert report["classification"] == "synthetic_test_only"
    assert report["authorized_for_real_demo"] is False


def test_seed_loader_returns_three_visibly_synthetic_non_demo_fixtures() -> None:
    seeds = load_synthetic_supplier_seeds()

    assert len(seeds) == 3
    assert {seed.input_format for seed in seeds} == {"pdf", "text", "xlsx"}
    assert all(seed.classification == "synthetic_test_only" for seed in seeds)
    assert all(seed.authorized_for_real_demo is False for seed in seeds)
    assert all(seed.supplier_id.startswith("sup_synthetic_") for seed in seeds)
    assert all("NOT AUTHORIZED FOR A REAL DEMO" in seed.disclaimer for seed in seeds)
