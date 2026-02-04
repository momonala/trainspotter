import os

import requests

# Call the localhost endpoint
url = "http://localhost:5007/api/esp32/image"
response = requests.get(url)
outfile = "tmp/test.png"

if response.status_code == 200:
    # Save the image
    with open(outfile, "wb") as f:
        f.write(response.content)
    print(f"Saved image to {outfile}")
    # Open the image
    os.system(f"open {outfile}")
else:
    print(f"Error: {response.status_code}")
