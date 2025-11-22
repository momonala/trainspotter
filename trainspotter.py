import logging
from datetime import datetime, timezone

from tabulate import tabulate

from datamodels import Departure
from utils import cleanse_transport_type, get_direction, get_platform_group, get_thresholds, get_walk_time
from vbb_api import get_inbound_trains, get_nearby_stations

logger = logging.getLogger(__name__)


def get_platform_number(platform: str) -> int:
    """Get the platform number for sorting."""
    try:
        return int(platform)
    except ValueError:
        return float("inf")  # Put unknown platforms at the end


def get_time_color(minutes_until: int, walk_time: int | None) -> str:
    """Get ANSI color code based on time thresholds."""
    if walk_time is None:
        return "\033[0m"  # Reset color if no walk time

    red_threshold, yellow_threshold = get_thresholds(walk_time)
    if minutes_until < red_threshold:
        return "\033[91m"  # Red
    elif minutes_until < yellow_threshold:
        return "\033[93m"  # Yellow
    else:
        return "\033[92m"  # Green


def main() -> None:
    """Main function to display departures for all nearby stations."""
    stations = get_nearby_stations()
    if not stations:
        print("No S-Bahn stations found nearby!")
        return

    for station in stations:
        print("\n" + "=" * 100)
        walk_time = get_walk_time(station.name)
        print(f"🚉 {station.name} ({station.distance}m away)")
        if walk_time is not None:
            print(f"   {walk_time} minute walk")
        print("=" * 100)

        departures = get_inbound_trains(station)
        if not departures:
            print("No departures found")
            continue

        # Group departures by transport type and platform
        departures_by_type: dict[str, dict[str, list[Departure]]] = {}
        for departure in departures:
            transport_type = cleanse_transport_type(departure)
            if transport_type == "other":  # Skip ferry, express, regional
                continue

            platform = departure.platform or "?"
            platform_group = get_platform_group(station.name, platform, transport_type)

            # Initialize nested dictionaries if needed
            if transport_type not in departures_by_type:
                departures_by_type[transport_type] = {}
            if platform_group not in departures_by_type[transport_type]:
                departures_by_type[transport_type][platform_group] = []

            departures_by_type[transport_type][platform_group].append(departure)

        # Print tables for each transport type and platform
        for transport_type, platforms in sorted(departures_by_type.items()):
            print(f"\n{transport_type.upper()}")
            print("-" * 100)

            # Sort platforms numerically
            for platform in sorted(platforms.keys(), key=get_platform_number):
                direction_symbol = get_direction(station.name, platform, transport_type)
                platform_header = f"Platform {platform}"
                if direction_symbol:
                    platform_header = f"{direction_symbol} {platform_header}"
                print(f"\n{platform_header}")

                # Sort departures by time
                platform_departures = sorted(platforms[platform], key=lambda x: x.when)

                # Create and print the table
                headers = ["Time", "Line", "To"]
                rows = []
                for departure in platform_departures:
                    now = datetime.now(timezone.utc)
                    minutes_away = int((departure.when - now).total_seconds() / 60)
                    color = get_time_color(minutes_away, walk_time)
                    time_str = f"{departure.when.strftime('%H:%M')} ({minutes_away}m)"

                    rows.append([f"{color}{time_str}\033[0m", departure.line.name, departure.provenance])

                print(tabulate(rows, headers=headers, tablefmt="simple"))


if __name__ == "__main__":
    main()
