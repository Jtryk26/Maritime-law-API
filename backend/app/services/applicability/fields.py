"""Feltregister.

Ét sted defineres hvert felt, en regel må sammenligne på: datatype, hvilken
port det hører til, etiket og hvordan man skaffer værdien. At tilføje et felt
er én post her plus én gren i :func:`resolve_field` — ikke ændringer spredt i
motoren.

Feltnavnene er den vokabular, regeldata, API og brugerflade deler. De ændres
derfor ikke uden en migration.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .derive import DerivedFacts
from .profile import Tri, ValueSource, VesselProfile

__all__ = [
    "Gate",
    "DataType",
    "FieldSpec",
    "FieldValue",
    "FIELD_REGISTRY",
    "get_field_spec",
    "resolve_field",
    "known_field_names",
]


class Gate(str, enum.Enum):
    """Portene i beslutningsvejen, i den rækkefølge de køres."""

    TEMPORAL_STATUS = "temporal_status"
    JURISDICTION = "jurisdiction"
    STRUCTURED_METADATA = "structured_metadata"
    THRESHOLDS = "thresholds"
    EXCLUSIONS = "exclusions"
    COVERAGE = "coverage"


class DataType(str, enum.Enum):
    ENUM = "enum"
    ENUM_SET = "enum_set"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    STRING = "string"
    STRING_SET = "string_set"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    data_type: DataType
    gate: Gate
    label_da: str
    label_en: str
    unit: str | None = None
    input_hint_da: str | None = None


@dataclass(frozen=True, slots=True)
class FieldValue:
    present: bool
    value: object = None
    source: ValueSource | None = None


ABSENT = FieldValue(present=False)


def _spec(*args, **kwargs) -> FieldSpec:
    spec = FieldSpec(*args, **kwargs)
    return spec


FIELD_REGISTRY: dict[str, FieldSpec] = {
    spec.name: spec
    for spec in (
        _spec("vessel.type", DataType.ENUM, Gate.STRUCTURED_METADATA, "Skibstype", "Vessel type"),
        _spec(
            "vessel.all_types",
            DataType.ENUM_SET,
            Gate.STRUCTURED_METADATA,
            "Skibstyper (inkl. sekundære)",
            "Vessel types (incl. secondary)",
        ),
        _spec(
            "operation.types",
            DataType.ENUM_SET,
            Gate.STRUCTURED_METADATA,
            "Fartsområde / operationstype",
            "Operation type",
        ),
        _spec(
            "dim.length_overall_m",
            DataType.NUMBER,
            Gate.THRESHOLDS,
            "Længde overalt (LOA)",
            "Length overall",
            unit="m",
            input_hint_da="Fremgår af målebrevet.",
        ),
        _spec(
            "dim.length_rule_m",
            DataType.NUMBER,
            Gate.THRESHOLDS,
            "Længde (regeldefineret)",
            "Length (rule definition)",
            unit="m",
        ),
        _spec("dim.breadth_m", DataType.NUMBER, Gate.THRESHOLDS, "Bredde", "Breadth", unit="m"),
        _spec("dim.depth_m", DataType.NUMBER, Gate.THRESHOLDS, "Dybde", "Depth", unit="m"),
        _spec(
            "dim.dimensionstal",
            DataType.NUMBER,
            Gate.THRESHOLDS,
            "Dimensionstal (L × B × D)",
            "Dimension number",
            input_hint_da="Udledes automatisk, hvis længde, bredde og dybde er oplyst.",
        ),
        _spec(
            "dim.gross_tonnage",
            DataType.NUMBER,
            Gate.THRESHOLDS,
            "Bruttotonnage",
            "Gross tonnage",
            unit="BT",
            input_hint_da="Fremgår af målebrevet.",
        ),
        _spec(
            "dim.deadweight_tonnes",
            DataType.NUMBER,
            Gate.THRESHOLDS,
            "Dødvægt",
            "Deadweight",
            unit="t",
        ),
        _spec(
            "persons.passenger_count",
            DataType.NUMBER,
            Gate.THRESHOLDS,
            "Antal passagerer",
            "Passenger count",
            input_hint_da="Højeste tilladte antal ifølge fartstilladelsen.",
        ),
        _spec(
            "persons.industrial_personnel",
            DataType.NUMBER,
            Gate.THRESHOLDS,
            "Antal industripersonel",
            "Industrial personnel",
        ),
        _spec(
            "persons.crew_count", DataType.NUMBER, Gate.THRESHOLDS, "Besætningsstørrelse", "Crew size"
        ),
        _spec(
            "jurisdiction.flag_state",
            DataType.STRING,
            Gate.JURISDICTION,
            "Flagstat",
            "Flag state",
        ),
        _spec(
            "jurisdiction.operating_areas",
            DataType.STRING_SET,
            Gate.JURISDICTION,
            "Farvandsområder",
            "Operating areas",
        ),
        _spec(
            "cargo.types", DataType.STRING_SET, Gate.STRUCTURED_METADATA, "Lasttyper", "Cargo types"
        ),
        _spec(
            "cargo.dangerous_goods",
            DataType.BOOLEAN,
            Gate.STRUCTURED_METADATA,
            "Farligt gods",
            "Dangerous goods",
        ),
        _spec(
            "lifecycle.keel_laid_date",
            DataType.DATE,
            Gate.THRESHOLDS,
            "Kølstrækningsdato",
            "Keel laying date",
        ),
        _spec(
            "lifecycle.delivery_date",
            DataType.DATE,
            Gate.THRESHOLDS,
            "Leveringsdato",
            "Delivery date",
        ),
        _spec(
            "derived.vessel_category",
            DataType.ENUM,
            Gate.STRUCTURED_METADATA,
            "Skibskategori (udledt)",
            "Vessel category (derived)",
        ),
        _spec(
            "derived.is_passenger_ship",
            DataType.BOOLEAN,
            Gate.STRUCTURED_METADATA,
            "Er passagerskib",
            "Is passenger ship",
            input_hint_da="Afgøres af passagerantallet. Oplys det for at lukke spørgsmålet.",
        ),
        _spec(
            "derived.is_tanker",
            DataType.BOOLEAN,
            Gate.STRUCTURED_METADATA,
            "Er tankskib",
            "Is tanker",
        ),
        _spec(
            "derived.is_cargo_ship",
            DataType.BOOLEAN,
            Gate.STRUCTURED_METADATA,
            "Er lastskib",
            "Is cargo ship",
        ),
        _spec(
            "derived.is_fishing_vessel",
            DataType.BOOLEAN,
            Gate.STRUCTURED_METADATA,
            "Er fiskeskib",
            "Is fishing vessel",
        ),
        _spec(
            "derived.is_offshore_unit",
            DataType.BOOLEAN,
            Gate.STRUCTURED_METADATA,
            "Er offshore-/ROV-fartøj",
            "Is offshore/ROV unit",
            input_hint_da="Afgøres af operationstypen.",
        ),
    )
}


def known_field_names() -> tuple[str, ...]:
    return tuple(FIELD_REGISTRY)


def get_field_spec(name: str) -> FieldSpec:
    """Slår et felt op. ``attr.*`` er tilladt uden registrering."""
    spec = FIELD_REGISTRY.get(name)
    if spec is not None:
        return spec
    if name.startswith("attr."):
        label = name[len("attr.") :]
        return FieldSpec(
            name=name,
            data_type=DataType.STRING,
            gate=Gate.STRUCTURED_METADATA,
            label_da=f"Attribut: {label}",
            label_en=f"Attribute: {label}",
        )
    raise KeyError(
        f"Ukendt felt {name!r}. Tilføj det i FIELD_REGISTRY, eller brug attr.* til ad hoc-data."
    )


def _measured(value) -> FieldValue:
    if value is None:
        return ABSENT
    return FieldValue(True, value.value, value.source)


def _tri(value: Tri) -> FieldValue:
    """Et tre-værdi-faktum. ``UNKNOWN`` bliver til "ikke oplyst"."""
    if value is Tri.UNKNOWN:
        return ABSENT
    return FieldValue(True, value is Tri.TRUE, ValueSource.DERIVED)


def resolve_field(profile: VesselProfile, derived: DerivedFacts, name: str) -> FieldValue:
    """Henter feltets værdi fra profilen eller de udledte fakta."""
    match name:
        case "vessel.type":
            return FieldValue(True, profile.vessel_type.value, ValueSource.DECLARED)
        case "vessel.all_types":
            return FieldValue(
                True, [t.value for t in derived.all_vessel_types], ValueSource.DECLARED
            )
        case "operation.types":
            if not profile.operation_types:
                return ABSENT
            return FieldValue(
                True, [o.value for o in profile.operation_types], ValueSource.DECLARED
            )
        case "dim.length_overall_m":
            return _measured(profile.dimensions.length_overall_m)
        case "dim.length_rule_m":
            return _measured(profile.dimensions.length_rule_m or derived.length_rule_m)
        case "dim.breadth_m":
            return _measured(profile.dimensions.breadth_m)
        case "dim.depth_m":
            return _measured(profile.dimensions.depth_m)
        case "dim.dimensionstal":
            return _measured(profile.dimensions.dimensionstal or derived.dimensionstal)
        case "dim.gross_tonnage":
            return _measured(profile.dimensions.gross_tonnage)
        case "dim.deadweight_tonnes":
            return _measured(profile.dimensions.deadweight_tonnes)
        case "persons.passenger_count":
            return _measured(profile.persons.passenger_count)
        case "persons.industrial_personnel":
            return _measured(profile.persons.industrial_personnel)
        case "persons.crew_count":
            return _measured(profile.persons.crew_count)
        case "jurisdiction.flag_state":
            if not profile.jurisdiction.flag_state:
                return ABSENT
            return FieldValue(True, profile.jurisdiction.flag_state, ValueSource.REGISTRY)
        case "jurisdiction.operating_areas":
            if not profile.jurisdiction.operating_areas:
                return ABSENT
            return FieldValue(True, list(profile.jurisdiction.operating_areas), ValueSource.DECLARED)
        case "cargo.types":
            if not profile.cargo.cargo_types:
                return ABSENT
            return FieldValue(True, list(profile.cargo.cargo_types), ValueSource.DECLARED)
        case "cargo.dangerous_goods":
            if profile.cargo.carries_dangerous_goods is None:
                return ABSENT
            return FieldValue(True, profile.cargo.carries_dangerous_goods, ValueSource.DECLARED)
        case "lifecycle.keel_laid_date":
            if profile.lifecycle.keel_laid_date is None:
                return ABSENT
            return FieldValue(True, profile.lifecycle.keel_laid_date, ValueSource.REGISTRY)
        case "lifecycle.delivery_date":
            if profile.lifecycle.delivery_date is None:
                return ABSENT
            return FieldValue(True, profile.lifecycle.delivery_date, ValueSource.REGISTRY)
        case "derived.vessel_category":
            return FieldValue(True, derived.vessel_category.value, ValueSource.DERIVED)
        case "derived.is_passenger_ship":
            return _tri(derived.is_passenger_ship)
        case "derived.is_tanker":
            return _tri(derived.is_tanker)
        case "derived.is_cargo_ship":
            return _tri(derived.is_cargo_ship)
        case "derived.is_fishing_vessel":
            return _tri(derived.is_fishing_vessel)
        case "derived.is_offshore_unit":
            return _tri(derived.is_offshore_unit)

    if name.startswith("attr."):
        raw = profile.attributes.get(name[len("attr.") :])
        if raw is None:
            return ABSENT
        return FieldValue(True, raw, ValueSource.DECLARED)

    return ABSENT
