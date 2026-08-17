"""Fartøjsprofilen og dens byggesten.

Profilen er brugerens input: hvad ved vi om skibet. Motoren læser den, men
ændrer den aldrig. Hver måling bærer sin oprindelse, fordi "skønnet 499 BT" og
"målebrev: 499 BT" ikke bør vægte ens ved en grænse.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date

__all__ = [
    "Tri",
    "ValueSource",
    "Measured",
    "VesselType",
    "OperationType",
    "VesselCategory",
    "VesselProfile",
    "PASSENGER_TYPES",
    "TANKER_TYPES",
    "CARGO_TYPES",
    "FISHING_TYPES",
    "OFFSHORE_TYPES",
    "OFFSHORE_OPERATIONS",
]


class Tri(str, enum.Enum):
    """Kleene-tristand. ``UNKNOWN`` er en værdi, ikke en fejl."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

    def __bool__(self) -> bool:  # pragma: no cover - bevidst spærret
        raise TypeError(
            "Tri må ikke bruges som sandhedsværdi. `if tri is Tri.TRUE` tvinger "
            "kalderen til at tage stilling til UNKNOWN, hvilket er hele pointen."
        )


class ValueSource(str, enum.Enum):
    """Hvor en oplysning kommer fra. Følger med hele vejen ud i svaret."""

    CERTIFICATE = "certificate"
    REGISTRY = "registry"
    DECLARED = "declared"
    DERIVED = "derived"
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class Measured:
    """En talværdi med sin oprindelse."""

    value: float
    source: ValueSource = ValueSource.DECLARED
    note: str | None = None


class VesselType(str, enum.Enum):
    PASSENGER_SHIP = "passenger_ship"
    RO_RO_PASSENGER_SHIP = "ro_ro_passenger_ship"
    HIGH_SPEED_PASSENGER_CRAFT = "high_speed_passenger_craft"
    OIL_TANKER = "oil_tanker"
    CHEMICAL_TANKER = "chemical_tanker"
    GAS_CARRIER = "gas_carrier"
    GENERAL_CARGO_SHIP = "general_cargo_ship"
    CONTAINER_SHIP = "container_ship"
    BULK_CARRIER = "bulk_carrier"
    RO_RO_CARGO_SHIP = "ro_ro_cargo_ship"
    FISHING_VESSEL = "fishing_vessel"
    OFFSHORE_SUPPORT_VESSEL = "offshore_support_vessel"
    ROV_SUPPORT_VESSEL = "rov_support_vessel"
    DIVE_SUPPORT_VESSEL = "dive_support_vessel"
    CABLE_LAYER = "cable_layer"
    TUG = "tug"
    DREDGER = "dredger"
    TRAINING_VESSEL = "training_vessel"
    PLEASURE_CRAFT = "pleasure_craft"
    OTHER = "other"


class OperationType(str, enum.Enum):
    INTERNATIONAL_VOYAGE = "international_voyage"
    DOMESTIC_VOYAGE = "domestic_voyage"
    NEAR_COASTAL = "near_coastal"
    HARBOUR_SERVICE = "harbour_service"
    INLAND_WATERWAY = "inland_waterway"
    FISHING_OPERATION = "fishing_operation"
    OFFSHORE_CONSTRUCTION = "offshore_construction"
    ROV_OPERATION = "rov_operation"
    DIVE_OPERATION = "dive_operation"
    STANDBY_RESCUE = "standby_rescue"
    WIND_FARM_SERVICE = "wind_farm_service"
    TOWAGE = "towage"
    LAID_UP = "laid_up"


class VesselCategory(str, enum.Enum):
    PASSENGER = "passenger"
    TANKER = "tanker"
    CARGO = "cargo"
    FISHING = "fishing"
    OFFSHORE = "offshore"
    SERVICE = "service"
    OTHER = "other"


PASSENGER_TYPES: frozenset[VesselType] = frozenset(
    {
        VesselType.PASSENGER_SHIP,
        VesselType.RO_RO_PASSENGER_SHIP,
        VesselType.HIGH_SPEED_PASSENGER_CRAFT,
    }
)
TANKER_TYPES: frozenset[VesselType] = frozenset(
    {VesselType.OIL_TANKER, VesselType.CHEMICAL_TANKER, VesselType.GAS_CARRIER}
)
CARGO_TYPES: frozenset[VesselType] = frozenset(
    {
        VesselType.GENERAL_CARGO_SHIP,
        VesselType.CONTAINER_SHIP,
        VesselType.BULK_CARRIER,
        VesselType.RO_RO_CARGO_SHIP,
    }
)
FISHING_TYPES: frozenset[VesselType] = frozenset({VesselType.FISHING_VESSEL})
OFFSHORE_TYPES: frozenset[VesselType] = frozenset(
    {
        VesselType.OFFSHORE_SUPPORT_VESSEL,
        VesselType.ROV_SUPPORT_VESSEL,
        VesselType.DIVE_SUPPORT_VESSEL,
        VesselType.CABLE_LAYER,
    }
)
SERVICE_TYPES: frozenset[VesselType] = frozenset(
    {VesselType.TUG, VesselType.DREDGER, VesselType.TRAINING_VESSEL}
)
OFFSHORE_OPERATIONS: frozenset[OperationType] = frozenset(
    {
        OperationType.OFFSHORE_CONSTRUCTION,
        OperationType.ROV_OPERATION,
        OperationType.DIVE_OPERATION,
        OperationType.WIND_FARM_SERVICE,
    }
)


@dataclass(slots=True)
class Dimensions:
    length_overall_m: Measured | None = None
    #: "Længde" som defineret i det pågældende regelsæt.
    length_rule_m: Measured | None = None
    breadth_m: Measured | None = None
    depth_m: Measured | None = None
    #: Dimensionstal = længde × bredde × dybde. Udledes hvis ikke oplyst.
    dimensionstal: Measured | None = None
    gross_tonnage: Measured | None = None
    deadweight_tonnes: Measured | None = None


@dataclass(slots=True)
class Persons:
    passenger_count: Measured | None = None
    industrial_personnel: Measured | None = None
    crew_count: Measured | None = None


@dataclass(slots=True)
class Jurisdiction:
    #: ISO 3166-1 alpha-2, f.eks. "DK".
    flag_state: str | None = None
    #: F.eks. ["DK_TERRITORIAL", "EU", "INTERNATIONAL"]. "*" i reglen matcher alt.
    operating_areas: list[str] = field(default_factory=list)
    port_states: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Lifecycle:
    keel_laid_date: date | None = None
    delivery_date: date | None = None
    major_conversion_date: date | None = None


@dataclass(slots=True)
class Cargo:
    cargo_types: list[str] = field(default_factory=list)
    carries_dangerous_goods: bool | None = None


@dataclass(slots=True)
class VesselProfile:
    """Alt motoren ved om skibet.

    Felter må gerne mangle. Motoren gætter ikke — den svarer
    ``NEEDS_MANUAL_REVIEW`` og oplyser hvilket felt der afgør sagen.
    """

    profile_id: str
    vessel_type: VesselType
    operation_types: list[OperationType] = field(default_factory=list)
    vessel_name: str | None = None
    assessment_date: date | None = None
    additional_vessel_types: list[VesselType] = field(default_factory=list)
    dimensions: Dimensions = field(default_factory=Dimensions)
    persons: Persons = field(default_factory=Persons)
    jurisdiction: Jurisdiction = field(default_factory=Jurisdiction)
    lifecycle: Lifecycle = field(default_factory=Lifecycle)
    cargo: Cargo = field(default_factory=Cargo)
    #: Udvidelsespunkt: en regel kan sammenligne på ``attr.<navn>`` uden kodeændring.
    attributes: dict[str, object] = field(default_factory=dict)

    @property
    def all_vessel_types(self) -> list[VesselType]:
        seen: list[VesselType] = [self.vessel_type]
        for extra in self.additional_vessel_types:
            if extra not in seen:
                seen.append(extra)
        return seen
