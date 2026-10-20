"""Fartøjsprofilen og dens taksonomier for Vessel Applicability 2.0.

Definitionerne bor i domænelaget (`app.services.vessel_applicability.models`),
fordi motoren afgør regler ud fra dem, og domænet ikke må afhænge af
API-skemaerne. Dette modul gør dem tilgængelige fra skemapakken.
"""

from app.services.vessel_applicability.models import (
    OperatingArea,
    PrimaryVesselType,
    PropulsionType,
    VesselProfile,
)

__all__ = ["OperatingArea", "PrimaryVesselType", "PropulsionType", "VesselProfile"]
