"""Pydantic models for VBB payloads — only the fields the app consumes; extras are ignored."""

import logging
from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator

logger = logging.getLogger(__name__)


class _VBBModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Location(_VBBModel):
    latitude: float
    longitude: float


class Products(_VBBModel):
    suburban: bool


class Station(_VBBModel):
    id: str
    name: str
    location: Location
    products: Products
    distance: int = 0

    @field_validator("name")
    @classmethod
    def _strip_berlin_suffix(cls, name: str) -> str:
        return name.replace("(Berlin)", "")


class Line(_VBBModel):
    name: str
    product: str


class Departure(_VBBModel):
    tripId: str
    stop: Station | None = None
    when: datetime
    provenance: str | None = None
    line: Line
    destination: Station | None = None


def parse_stations(stations_data: list[dict]) -> list[Station]:
    """Parse a list of station dicts into Station models."""
    stations = [Station.model_validate(station_dict) for station_dict in stations_data]
    logger.debug("Parsed %d stations", len(stations))
    return stations


def parse_departures(departures_data: dict) -> list[Departure]:
    """Parse a VBB departures response into Departure models.

    Cancelled trips arrive with `when` set to null — they are skipped.
    """
    departures = []
    for departure_dict in departures_data["departures"]:
        if departure_dict.get("when") is None:
            logger.debug("Skipping departure with null 'when' (cancelled trip): %s", departure_dict.get("tripId"))
            continue
        departures.append(Departure.model_validate(departure_dict))
    logger.debug("Parsed %d departures", len(departures))
    return departures
