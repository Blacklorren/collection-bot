import urllib.request
import os

base_url = "https://raw.githubusercontent.com/google/fonts/main/ofl/opensans/"
files = ["OpenSans-Bold.ttf", "OpenSans-Regular.ttf"]
target_dir = "utils"

if not os.path.exists(target_dir):
    os.makedirs(target_dir)

for f in files:
    url = base_url + f
    dest = os.path.join(target_dir, f)
    print(f"Downloading {url} to {dest}...")
    try:
        urllib.request.urlretrieve(url, dest)
        print(f"Success: {dest}")
    except Exception as e:
        print(f"Error downloading {f}: {e}")
