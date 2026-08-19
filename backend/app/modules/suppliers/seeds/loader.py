from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_FIXTURE_DIRECTORY = Path(__file__).with_name("fixtures")


class SyntheticSeedProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_name: str
    categories: list[str]
    service_cities: list[str]
    contact_email: str
    cnpj: None = None


class SyntheticSupplierSeed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    classification: Literal["synthetic_test_only"]
    authorized_for_real_demo: Literal[False]
    disclaimer: str = Field(min_length=20)
    supplier_id: str
    input_format: Literal["pdf", "text", "xlsx"]
    material_reference: str
    profile: SyntheticSeedProfile
    extraction_fixture: dict[str, dict[str, Any]]

    @field_validator("supplier_id")
    @classmethod
    def supplier_id_is_visibly_synthetic(cls, value: str) -> str:
        if not value.startswith("sup_synthetic_"):
            raise ValueError("synthetic seed supplier IDs must be visibly marked")
        return value

    @field_validator("material_reference")
    @classmethod
    def material_reference_cannot_look_real(cls, value: str) -> str:
        if not value.startswith("synthetic-test://"):
            raise ValueError("synthetic seed material must use the synthetic-test scheme")
        return value


def load_synthetic_supplier_seeds(
    directory: Path = DEFAULT_FIXTURE_DIRECTORY,
) -> tuple[SyntheticSupplierSeed, ...]:
    fixture_paths = sorted(directory.glob("*.synthetic.json"))
    if not fixture_paths:
        raise FileNotFoundError(f"no synthetic supplier fixtures found in {directory}")
    seeds = tuple(
        SyntheticSupplierSeed.model_validate_json(path.read_text(encoding="utf-8"))
        for path in fixture_paths
    )
    supplier_ids = [seed.supplier_id for seed in seeds]
    if len(supplier_ids) != len(set(supplier_ids)):
        raise ValueError("synthetic supplier fixture IDs must be unique")
    return seeds
