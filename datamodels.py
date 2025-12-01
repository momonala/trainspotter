import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Location:
    """Represents a geographic location."""

    type: str
    id: str
    latitude: float
    longitude: float


@dataclass
class Products:
    """Represents available transport products at a station."""

    suburban: bool
    subway: bool
    tram: bool
    bus: bool
    ferry: bool
    express: bool
    regional: bool


@dataclass
class Station:
    """Represents a public transport station."""

    type: str
    id: str
    name: str
    location: Location
    products: Products
    stationDHID: str
    distance: int


@dataclass
class Color:
    """Represents line color information."""

    fg: str
    bg: str


@dataclass
class Operator:
    """Represents a transport operator."""

    type: str
    id: str
    name: str


@dataclass
class Line:
    """Represents a transport line."""

    type: str
    id: str
    fahrtNr: str
    name: str
    public: bool
    adminCode: str
    productName: str
    mode: str
    product: str
    operator: Operator
    color: Color | None


@dataclass
class Departure:
    """Represents an departure at a station."""

    tripId: str
    stop: Station
    when: datetime
    plannedWhen: datetime
    delay: int | None
    platform: str | None
    plannedPlatform: str | None
    prognosisType: str | None
    direction: str | None
    provenance: str
    line: Line
    remarks: list[str]
    origin: Station
    destination: Station | None
    currentTripPosition: Location | None


def _parse_station(station_dict: dict) -> Station:
    """Helper function to parse a station dictionary into a Station object."""
    return Station(
        type=station_dict["type"],
        id=station_dict["id"],
        name=station_dict["name"].replace("(Berlin)", ""),
        location=Location(**station_dict["location"]),
        products=Products(**station_dict["products"]),
        stationDHID=station_dict.get("stationDHID", ""),
        distance=station_dict.get("distance", 0),
    )


def _parse_location(location_dict: dict | None) -> Location | None:
    """Helper function to parse a location dictionary into a Location object."""
    if not location_dict:
        return None
    return Location(
        type=location_dict["type"],
        id=location_dict.get("id", ""),
        latitude=location_dict["latitude"],
        longitude=location_dict["longitude"],
    )


def parse_stations(stations_data: list[dict]) -> list[Station]:
    """Parses a list of station dicts into Station dataclasses."""
    stations = [_parse_station(station_dict) for station_dict in stations_data]
    logger.debug(f"Parsed {len(stations)} stations")
    return stations


def parse_departures(departures_data: dict) -> list[Departure]:
    """Parses departures data into Departure dataclasses."""
    departures: list[Departure] = []
    for departure_dict in departures_data["departures"]:
        # Parse nested objects
        stop = _parse_station(departure_dict["stop"]) if departure_dict["stop"] else None
        origin = _parse_station(departure_dict["origin"]) if departure_dict["origin"] else None
        destination = _parse_station(departure_dict["destination"]) if departure_dict["destination"] else None
        current_trip_position = _parse_location(departure_dict.get("currentTripPosition"))

        # Parse line and its nested objects
        line_dict = departure_dict["line"]
        operator = Operator(**line_dict["operator"]) if "operator" in line_dict else None
        color = Color(**line_dict["color"]) if "color" in line_dict else None

        line = Line(
            **{k: v for k, v in line_dict.items() if k not in ["operator", "color"]},
            operator=operator,
            color=color,
        )

        # Defensive: check 'when' and 'plannedWhen' are strings
        when_str = departure_dict["when"]
        planned_when_str = departure_dict["plannedWhen"]
        if not isinstance(when_str, str) or not isinstance(planned_when_str, str):
            logger.debug(f"Skipping departure with invalid 'when': {when_str} or 'plannedWhen': {planned_when_str}")
            continue
        when = datetime.fromisoformat(when_str)
        planned_when = datetime.fromisoformat(planned_when_str)

        # Create departure object
        departure = Departure(
            tripId=departure_dict["tripId"],
            stop=stop,
            when=when,
            plannedWhen=planned_when,
            delay=departure_dict["delay"],
            platform=departure_dict["platform"],
            plannedPlatform=departure_dict["plannedPlatform"],
            prognosisType=departure_dict["prognosisType"],
            direction=departure_dict["direction"],
            provenance=departure_dict["provenance"],
            line=line,
            remarks=departure_dict["remarks"],
            origin=origin,
            destination=destination,
            currentTripPosition=current_trip_position,
        )
        departures.append(departure)

    logger.debug(f"Parsed {len(departures)} departures")
    return departures
