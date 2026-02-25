"""
Helper script to automatically install GeneralUser-GS.sf2 soundfont to
'assets' folder (used in creation of incipit mp3 from MIDI).
"""

import warnings

from pathlib import Path
from urllib.request import Request, urlopen

def download_soundfont():
    """Download GeneralUser-GS.sf2 from GitHub with validation."""

    project_root = Path(__file__).parent
    folder = project_root / "assets"
    filename = "GeneralUser-GS.sf2"
    path = folder / filename

    url = ("https://github.com/mrbumpy409"
           "/GeneralUser-GS/raw/main/GeneralUser-GS.sf2")

    # check 'assets' directory exists
    folder.mkdir(parents=True, exist_ok=True)
    # Check if file exists and is not empty (e.g. > 1MB)
    if path.exists() and path.stat().st_size > 1_000_000:
        print(f"{filename} already exists.")
        return

    print(f"Downloading {filename}...")
    # Add User-Agent to prevent GitHub bot-detection from blocking urllib
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})

    try:
        with urlopen(req) as response:
            with open(path, 'wb') as out_file:
                out_file.write(response.read())
        print("Download complete.")
    except Exception as e:
        if path.exists():
            # if download fails while in progress
            # delete any partially downloaded/corrupted file
            path.unlink()
        warnings.warn(f"Failed to download SoundFont: {e}", RuntimeWarning)

if __name__ == "__main__":
    download_soundfont()
