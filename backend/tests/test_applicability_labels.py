"""Forklaringer skal bruge ord, ikke motorens interne koder."""

from app.services.applicability.explain import _format_value
from app.services.applicability.labels import VALUE_LABELS_DA, join_labels_da, value_label_da
from app.services.applicability.profile import OperationType, ValueSource, VesselType


def test_alle_skibstyper_og_operationstyper_har_et_dansk_navn():
    for enum_cls in (VesselType, OperationType, ValueSource):
        for member in enum_cls:
            if member.value == "other":
                continue
            assert member.value in VALUE_LABELS_DA, member.value


def test_lister_vises_med_danske_navne():
    assert _format_value(["ro_ro_passenger_ship", "bulk_carrier"]) == "ro-ro-passagerskib, bulkskib"
    assert _format_value("international_voyage") == "international fart"


def test_ukendt_kode_vises_uaendret():
    assert value_label_da("helt_ny_type") == "helt_ny_type"
    assert join_labels_da(["*"]) == "alle"


def test_tal_og_sandhedsvaerdier_er_uberoert():
    assert _format_value(500.0) == "500"
    assert _format_value(True) == "ja"
