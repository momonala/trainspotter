import logging
from datetime import datetime
from datetime import timezone

from tabulate import tabulate

from .datamodels import Departure
from .utils import cleanse_transport_type
from .utils import get_platform_group
from .utils import get_thresholds
from .utils import get_walk_time
from .vbb_api import get_inbound_trains
from .vbb_api import get_nearby_stations

logger = logging.getLogger(__name__)


def get_platform_number(platform: str) -> float:
    """Platform number for sorting; unknown platforms sort last."""
    try:
        return int(platform)
    except ValueError:
        return float("inf")


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
        walk_time = get_walk_time(station)
        print(f"🚉 {station.name} ({station.distance}m away)")
        if walk_time is not None:
            print(f"   {walk_time} minute walk")
        print("=" * 100)

        departures = get_inbound_trains(station)
        if not departures:
            print("No departures found")
            continue

        departures_by_type: dict[str, dict[str, list[Departure]]] = {}
        for departure in departures:
            transport_type = cleanse_transport_type(departure)
            if transport_type == "other":
                continue

            platform = departure.platform or "?"
            platform_group = get_platform_group(station.name, platform, transport_type)
            departures_by_type.setdefault(transport_type, {}).setdefault(platform_group, []).append(departure)

        for transport_type, platforms in sorted(departures_by_type.items()):
            print(f"\n{transport_type.upper()}")
            print("-" * 100)

            for platform in sorted(platforms.keys(), key=get_platform_number):
                print(f"\nPlatform {platform}")

                now = datetime.now(timezone.utc)
                rows = []
                for departure in sorted(platforms[platform], key=lambda x: x.when):
                    minutes_away = int((departure.when - now).total_seconds() / 60)
                    color = get_time_color(minutes_away, walk_time)
                    time_str = f"{departure.when.strftime('%H:%M')} ({minutes_away}m)"
                    rows.append([f"{color}{time_str}\033[0m", departure.line.name, departure.provenance])

                print(tabulate(rows, headers=["Time", "Line", "To"], tablefmt="simple"))


if __name__ == "__main__":
    main()
