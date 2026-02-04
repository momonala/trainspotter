import os
from datetime import datetime
from datetime import timezone

import requests

from src.image_generator import QuadrantData
from src.image_generator import render_image

outfile = "tmp/test.png"

url = "http://localhost:5007/api/esp32/image"
try:
    response = requests.get(url, timeout=3)
    if response.status_code == 200:
        image_data = response.content
        print("Using live server response")
    else:
        raise ConnectionError(f"Status {response.status_code}")
except (requests.RequestException, ConnectionError) as e:
    print(f"Server unavailable ({e}), using mock data")
    quadrants_data = [
        QuadrantData(label="S1/26", arrow="↑", departures=[(8, "S2"), (11, "S1")]),
        QuadrantData(label="S1/26", arrow="↓", departures=[(7, "S2"), (13, "S26")]),
        QuadrantData(label="S8/85", arrow="↑", departures=[(15, "S8"), (24, "S85")]),
        QuadrantData(label="S8/85", arrow="↻", departures=[(13, "S85"), (24, "S8")]),
    ]
    image_data = render_image(quadrants_data, "Bornholmerstr", datetime.now(tz=timezone.utc))

with open(outfile, "wb") as f:
    f.write(image_data)
print(f"Saved image to {outfile}")
os.system(f"open {outfile}")
