# 🚉 Trainspotter

A real-time Berlin public transport dashboard showing upcoming departures closest to my apartment.

## Features

- 🚆 Real-time departure information for S-Bahn, U-Bahn, Bus, and Tram
- 🚶‍♂️ Walking time indicators (default: 15 min to Gesundbrunnen, 5 min to Bornholmer)
- 🎨 Official BVG/S-Bahn Berlin transport logos and line colors
- 🎯 Time-based highlighting:
  - 🔴 Red: Not enough time to catch train
  - 🟡 Yellow: Tight timing but possible
  - 🟢 Green: Comfortable timing

### Time Thresholds

Ex.
#### Bornholmer Straße (5 min walk)
- Red: < 3 minutes until departure
- Yellow: 3-7 minutes
- Green: > 7 minutes

#### Gesundbrunnen (15 min walk)
- Red: < 13 minutes until departure
- Yellow: 13-17 minutes
- Green: > 17 minutes

## Setup

1. Install Poetry (if not already installed):
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

2. Install dependencies:
```bash
poetry install
```

3. Run the Flask application:
```bash
python app.py
```

4. Open in your browser:
```
http://localhost:5007
```

### Terminal View

You can also view departures directly in your terminal:
```bash
python trainspotter.py
```

This provides a compact, color-coded view with:
- 🔴 Red: Not enough time to catch train
- 🟡 Yellow: Tight timing but possible
- 🟢 Green: Comfortable timing based on your walk time

## Configuration

The application is configured through `config.json`. You'll need to update this file with your specific location and stations:

```json
{
    "stations": {
        "station_name": {
            "walk_time": 15,  # Minutes to walk to this station
            "hidden_platforms": {  # Optional: platforms to hide
                "S-Bahn": ["1", "4"]
            }
        }
    },
    "location": {
        "latitude": 52.552045,  # Your location coordinates
        "longitude": 13.399863
    },
    "update_interval_min": 30  # How often to fetch new data
}
```


## Technical Details

### Backend
- Flask web server
- Real-time data fetching from VBB API

### Frontend
- Auto-updates every 30 seconds
- Official transport logos from wikipedia
- Uses official VBB colors for all transport lines [S-Bahn](https://en.wikipedia.org/wiki/Module:Adjacent_stations/Berlin_S-Bahn), [U-Bahn](https://en.wikipedia.org/wiki/Module:Adjacent_stations/Berlin_U-Bahn)

## License

MIT License - feel free to use and modify as needed!
