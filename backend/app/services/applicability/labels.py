"""Danske visningsnavne for profilens kodeværdier.

Motoren arbejder med stabile koder (`ro_ro_passenger_ship`, `DK_TERRITORIAL`);
forklaringen til mennesker skal bruge ord. Ukendte koder vises uændret, så en
ny værdi aldrig forsvinder fra forklaringen — den ser blot teknisk ud, indtil
den får et navn her.
"""

from __future__ import annotations

__all__ = ["VALUE_LABELS_DA", "value_label_da", "join_labels_da"]

VALUE_LABELS_DA: dict[str, str] = {
    # Skibstyper (profile.VesselType)
    "passenger_ship": "passagerskib",
    "ro_ro_passenger_ship": "ro-ro-passagerskib",
    "high_speed_passenger_craft": "hurtiggående passagerfartøj",
    "oil_tanker": "olietankskib",
    "chemical_tanker": "kemikalietankskib",
    "gas_carrier": "gastankskib",
    "general_cargo_ship": "stykgodsskib",
    "container_ship": "containerskib",
    "bulk_carrier": "bulkskib",
    "ro_ro_cargo_ship": "ro-ro-lastskib",
    "fishing_vessel": "fiskefartøj",
    "offshore_support_vessel": "offshoreforsyningsfartøj",
    "rov_support_vessel": "ROV-supportskib",
    "dive_support_vessel": "dykkerstøtteskib",
    "cable_layer": "kabelskib",
    "tug": "bugserbåd",
    "dredger": "uddybningsfartøj",
    "training_vessel": "skoleskib",
    "pleasure_craft": "fritidsfartøj",
    # Operationstyper (profile.OperationType)
    "international_voyage": "international fart",
    "domestic_voyage": "indenrigsfart",
    "near_coastal": "nærfart",
    "harbour_service": "havnetjeneste",
    "inland_waterway": "indre vandveje",
    "fishing_operation": "fiskeri",
    "offshore_construction": "offshore anlægsarbejde",
    "rov_operation": "ROV-operation",
    "dive_operation": "dykkeroperation",
    "standby_rescue": "standby/redningsberedskab",
    "wind_farm_service": "vindmølleservice",
    "towage": "bugsering",
    "laid_up": "oplagt",
    # Datakilder (profile.ValueSource)
    "certificate": "målebrev/certifikat",
    "registry": "skibsregister",
    "declared": "erklæret",
    "derived": "udledt",
    "estimated": "skønnet",
    # Flag, registre og farvande
    "DK": "dansk flag",
    "FO": "færøsk flag",
    "EU": "EU/EØS",
    "OTHER": "tredjeland",
    "DAS": "DAS",
    "DIS": "DIS",
    "FAS": "FAS",
    "FOREIGN": "udenlandsk register",
    "DK_TERRITORIAL": "danske farvande",
    "INTERNATIONAL": "internationalt farvand",
    "NORTH_SEA": "Nordsøen",
    "BALTIC_SEA": "Østersøen",
    "WORLDWIDE": "hele verden",
    "*": "alle",
}


def value_label_da(value: object) -> str:
    text = str(value)
    return VALUE_LABELS_DA.get(text, text)


def join_labels_da(values) -> str:
    return ", ".join(value_label_da(v) for v in values)
