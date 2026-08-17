"""Udledte fakta om fartøjet — tre-værdi og strengt.

Reglen, hele filen hviler på
============================

**Et udledt faktum må kun blive FALSE, når de data, der definerer det, er
oplyst.** Mangler de, er svaret ``UNKNOWN``, og motoren sender sagen til manuel
gennemgang med navnet på det felt, der afgør den.

Hvorfor det er vigtigt nok til at koste manuelle sager: "passagerskib" er i
lovgivningen defineret ved *antallet af passagerer*, ikke ved hvad fartøjet er
registreret som. Udleder man ``is_passenger_ship = False``, blot fordi
passagerantallet mangler, kan én fejlklassificeret fartøjstype i basen give et
skråsikkert "gælder ikke" på en sikkerhedsbestemmelse. Det er den forkerte fejl
at begå i denne retning.

Fakta, der er defineret ved *skibstypen* — tankskib, lastskib, fiskeskib — må
gerne blive FALSE, fordi skibstypen altid er oplyst. Der er positiv
dokumentation for svaret. Grænsen går præcis dér: er faktummet defineret ved en
oplysning, profilen kan mangle, arves den usikkerhed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import (
    CARGO_TYPES,
    FISHING_TYPES,
    OFFSHORE_OPERATIONS,
    OFFSHORE_TYPES,
    PASSENGER_TYPES,
    SERVICE_TYPES,
    TANKER_TYPES,
    Measured,
    Tri,
    ValueSource,
    VesselCategory,
    VesselProfile,
    VesselType,
)

__all__ = [
    "DerivationNote",
    "ProfileConflict",
    "DerivedFacts",
    "derive_facts",
    "DERIVATION_CONFIG",
]

#: Grænser og valg, der er tænkt til at blive redigeret i hånden.
DERIVATION_CONFIG: dict[str, object] = {
    # Over dette antal passagerer er skibet et passagerskib.
    "passenger_ship_threshold": 12,
    # Bruges LOA som regeldefineret længde, når den sidste mangler?
    "use_loa_as_rule_length": True,
    # Tolereret afvigelse mellem oplyst og beregnet dimensionstal.
    "dimensionstal_mismatch_fraction": 0.10,
}


@dataclass(frozen=True, slots=True)
class DerivationNote:
    """En antagelse motoren har truffet, skrevet så den kan anfægtes."""

    key: str
    text_da: str
    text_en: str
    basis: str = "ENGINE_DERIVATION"


@dataclass(frozen=True, slots=True)
class ProfileConflict:
    """Profildata, der modsiger hinanden."""

    code: str
    detail_da: str
    detail_en: str
    fields: tuple[str, ...] = ()


@dataclass(slots=True)
class DerivedFacts:
    all_vessel_types: list[VesselType]
    vessel_category: VesselCategory
    is_passenger_ship: Tri
    is_tanker: Tri
    is_cargo_ship: Tri
    is_fishing_vessel: Tri
    is_offshore_unit: Tri
    dimensionstal: Measured | None = None
    length_rule_m: Measured | None = None
    derivations: list[DerivationNote] = field(default_factory=list)
    conflicts: list[ProfileConflict] = field(default_factory=list)


def _categorize(types: list[VesselType]) -> VesselCategory:
    if any(t in PASSENGER_TYPES for t in types):
        return VesselCategory.PASSENGER
    if any(t in TANKER_TYPES for t in types):
        return VesselCategory.TANKER
    if any(t in FISHING_TYPES for t in types):
        return VesselCategory.FISHING
    if any(t in OFFSHORE_TYPES for t in types):
        return VesselCategory.OFFSHORE
    if any(t in CARGO_TYPES for t in types):
        return VesselCategory.CARGO
    if any(t in SERVICE_TYPES for t in types):
        return VesselCategory.SERVICE
    return VesselCategory.OTHER


def _tri(value: bool) -> Tri:
    return Tri.TRUE if value else Tri.FALSE


def derive_facts(profile: VesselProfile) -> DerivedFacts:
    """Beregner de udledte fakta og noterer hver antagelse."""
    derivations: list[DerivationNote] = []
    conflicts: list[ProfileConflict] = []

    types = profile.all_vessel_types
    category = _categorize(types)
    threshold = int(DERIVATION_CONFIG["passenger_ship_threshold"])  # type: ignore[arg-type]

    # --- Passagerskib: strengt ------------------------------------------
    pax = profile.persons.passenger_count
    declared_carries = profile.attributes.get("carries_passengers")

    if any(t in PASSENGER_TYPES for t in types):
        is_passenger_ship = Tri.TRUE
        derivations.append(
            DerivationNote(
                "is_passenger_ship",
                "Skibstypen er i sig selv en passagerskibstype.",
                "The vessel type is itself a passenger ship type.",
            )
        )
    elif pax is not None:
        is_passenger_ship = _tri(pax.value > threshold)
        derivations.append(
            DerivationNote(
                "is_passenger_ship",
                f"{pax.value:g} passagerer sammenholdt med grænsen på {threshold}.",
                f"{pax.value:g} passengers compared with the threshold of {threshold}.",
            )
        )
    elif declared_carries is False:
        is_passenger_ship = Tri.FALSE
        derivations.append(
            DerivationNote(
                "is_passenger_ship",
                "Profilen oplyser udtrykkeligt, at skibet ikke medfører passagerer.",
                "The profile explicitly states that the vessel carries no passengers.",
            )
        )
    else:
        # Her ligger den strenge beslutning. Skibstypen alene er IKKE
        # dokumentation for, at fartøjet ikke er et passagerskib.
        is_passenger_ship = Tri.UNKNOWN
        derivations.append(
            DerivationNote(
                "is_passenger_ship",
                (
                    "Passagerantal er ikke oplyst. Skibstypen alene afgør ikke, om "
                    "fartøjet er et passagerskib i lovens forstand, så spørgsmålet "
                    "står åbent."
                ),
                (
                    "Passenger count not supplied. Vessel type alone does not settle "
                    "whether this is a passenger ship in law, so the question stays open."
                ),
                basis="ENGINE_DERIVATION_STRICT",
            )
        )

    if any(t in PASSENGER_TYPES for t in types) and pax is not None and pax.value <= threshold:
        conflicts.append(
            ProfileConflict(
                "passenger_count_inconsistent",
                (
                    f"Skibet er angivet som passagerskib, men har kun {pax.value:g} "
                    "passagerer. Kontrollér fartstilladelsen."
                ),
                f"Vessel declared as passenger ship but carries only {pax.value:g} passengers.",
                ("vessel.type", "persons.passenger_count"),
            )
        )

    # --- Fakta defineret ved skibstypen ---------------------------------
    # Skibstypen er altid oplyst, så der er positiv dokumentation for både
    # ja og nej. Disse må gerne blive FALSE.
    is_tanker = _tri(any(t in TANKER_TYPES for t in types))
    is_cargo_ship = _tri(any(t in CARGO_TYPES for t in types) or is_tanker is Tri.TRUE)
    is_fishing_vessel = _tri(any(t in FISHING_TYPES for t in types))

    offshore_by_type = any(t in OFFSHORE_TYPES for t in types)
    offshore_by_operation = any(o in OFFSHORE_OPERATIONS for o in profile.operation_types)
    if offshore_by_type or offshore_by_operation:
        is_offshore_unit = Tri.TRUE
    elif profile.operation_types:
        is_offshore_unit = Tri.FALSE
    else:
        # Uden oplyst operationstype kan et ellers almindeligt fartøj sagtens
        # være i offshore-arbejde. Det er ikke et nej, det er et ubesvaret spørgsmål.
        is_offshore_unit = Tri.UNKNOWN
        derivations.append(
            DerivationNote(
                "is_offshore_unit",
                "Operationstype er ikke oplyst, og skibstypen alene afgør det ikke.",
                "Operation type not supplied and the vessel type alone does not settle it.",
                basis="ENGINE_DERIVATION_STRICT",
            )
        )

    if is_fishing_vessel is Tri.TRUE and profile.operation_types and not any(
        o.value == "fishing_operation" for o in profile.operation_types
    ):
        conflicts.append(
            ProfileConflict(
                "fishing_operation_missing",
                "Fartøjet er et fiskeskib, men fiskeri er ikke angivet som operationstype.",
                "Vessel is a fishing vessel but fishing is not listed as an operation type.",
                ("vessel.type", "operation.types"),
            )
        )

    # --- Mål -------------------------------------------------------------
    dims = profile.dimensions
    length_rule = dims.length_rule_m
    if length_rule is None and dims.length_overall_m is not None and DERIVATION_CONFIG[
        "use_loa_as_rule_length"
    ]:
        length_rule = Measured(
            dims.length_overall_m.value,
            ValueSource.DERIVED,
            "LOA brugt som regellængde",
        )
        derivations.append(
            DerivationNote(
                "length_rule_m",
                "Regeldefineret længde er ikke oplyst; længde overalt (LOA) er brugt i stedet.",
                "Rule length not supplied; length overall used instead.",
            )
        )

    dimensionstal = dims.dimensionstal
    if length_rule is not None and dims.breadth_m is not None and dims.depth_m is not None:
        computed = round(length_rule.value * dims.breadth_m.value * dims.depth_m.value, 2)
        if dimensionstal is None:
            dimensionstal = Measured(computed, ValueSource.DERIVED, "L × B × D")
            derivations.append(
                DerivationNote(
                    "dimensionstal",
                    (
                        f"Dimensionstal beregnet til {computed:g} som "
                        f"{length_rule.value:g} × {dims.breadth_m.value:g} × {dims.depth_m.value:g}."
                    ),
                    (
                        f"Dimension number computed as {length_rule.value:g} × "
                        f"{dims.breadth_m.value:g} × {dims.depth_m.value:g} = {computed:g}."
                    ),
                )
            )
        else:
            allowed = computed * float(DERIVATION_CONFIG["dimensionstal_mismatch_fraction"])  # type: ignore[arg-type]
            if abs(dimensionstal.value - computed) > allowed:
                conflicts.append(
                    ProfileConflict(
                        "dimensionstal_mismatch",
                        (
                            f"Oplyst dimensionstal {dimensionstal.value:g} afviger fra "
                            f"beregnet {computed:g} (L × B × D)."
                        ),
                        (
                            f"Declared dimension number {dimensionstal.value:g} differs from "
                            f"computed {computed:g}."
                        ),
                        (
                            "dim.dimensionstal",
                            "dim.length_rule_m",
                            "dim.breadth_m",
                            "dim.depth_m",
                        ),
                    )
                )

    return DerivedFacts(
        all_vessel_types=types,
        vessel_category=category,
        is_passenger_ship=is_passenger_ship,
        is_tanker=is_tanker,
        is_cargo_ship=is_cargo_ship,
        is_fishing_vessel=is_fishing_vessel,
        is_offshore_unit=is_offshore_unit,
        dimensionstal=dimensionstal,
        length_rule_m=length_rule,
        derivations=derivations,
        conflicts=conflicts,
    )
