# UK Rail Timetable Generator

Generate railway timetable and route CSV files from official UK rail data — no programming knowledge required to run it.

Beyond basic timetable generation, the app includes an **Electric Train Energy Pipeline** and a **Solar Pipeline** for analysing the energy demand of electric train services and modelling solar supply.

---

## Table of Contents

1. [What This App Does](#what-this-app-does)
2. [Before You Start — Install These First](#before-you-start--install-these-first)
3. [Download the App](#download-the-app)
4. [Get Your Data Files](#get-your-data-files)
5. [Step-by-Step Setup](#step-by-step-setup)
   - [Windows](#windows)
   - [Mac](#mac)
   - [Linux](#linux)
6. [Using the App](#using-the-app)
   - [Step 1 — Upload CIF (once)](#step-1--upload-cif-once)
   - [Step 2 — Generate Timetables](#step-2--generate-timetables)
   - [Step 3 — Edit CSVs in Browser](#step-3--edit-csvs-in-browser)
   - [Step 4 — Results History](#step-4--results-history)
   - [Step 5 — Electric Energy Pipeline](#step-5--electric-energy-pipeline)
   - [Step 6 — Solar Analysis](#step-6--solar-analysis)
7. [Understanding the Outputs](#understanding-the-outputs)
8. [Stopping the App](#stopping-the-app)
9. [Troubleshooting](#troubleshooting)
10. [Data Sources](#data-sources)
11. [Project Structure (for developers)](#project-structure-for-developers)

---

## What This App Does

You type in a **station name**, an **operator code** (e.g. `VT` for Avanti West Coast), and a **date or date range**. The app looks up official Network Rail data and produces two downloadable spreadsheet files:

- **Timetable CSV** — every departure at that station on those dates, with train class and number of coaches
- **Route CSV** — the ordered list of stations the train calls at, with distances and journey times between each pair

Once you have those outputs, the app can also:

- **Calculate electric energy demand** per traction substation (TSS) for each half-hour of the day, using a built-in energy model with rolling-stock parameters
- **Model solar generation** against that demand using PVGIS irradiance data, computing solar share %, utilisation %, and seasonal breakdowns

All data comes from official Network Rail sources only (CIF timetables, CORPUS station data, NESA mileage, Darwin).

---

## Before You Start — Install These First

You need to install two free programs. This only needs to be done once.

### 1. Python (version 3.11 or newer)

Python runs the backend part of the app.

**Windows:**
1. Go to https://www.python.org/downloads/
2. Click the big yellow **Download Python** button
3. Run the installer
4. **Important:** On the first screen of the installer, tick the box that says **"Add Python to PATH"** before clicking Install
5. Click **Install Now**

**Mac:**
1. Go to https://www.python.org/downloads/
2. Click the big yellow **Download Python** button
3. Open the downloaded `.pkg` file and follow the steps

**Linux (Ubuntu/Debian):**

Open the Terminal app and type:
```
sudo apt update && sudo apt install python3 python3-pip -y
```

To check Python installed correctly, open a terminal/command prompt and type:
```
python --version
```
You should see something like `Python 3.11.x`. On Mac/Linux you may need to type `python3 --version`.

---

### 2. Node.js (version 18 or newer)

Node.js runs the website part of the app.

**Windows & Mac:**
1. Go to https://nodejs.org/
2. Click the **LTS** (recommended) download button
3. Run the installer and follow the steps

**Linux (Ubuntu/Debian):**
```
sudo apt install nodejs npm -y
```

To check Node.js installed correctly:
```
node --version
```
You should see something like `v18.x.x` or higher.

---

## Download the App

### Option A — If you have Git installed
Open a terminal / command prompt and run:
```
git clone https://github.com/Aditya-Dev598/Electric-Train-Web-App.git
cd Electric-Train-Web-App
```

### Option B — Download as a ZIP
1. Go to the repository page on GitHub
2. Click the green **Code** button
3. Click **Download ZIP**
4. Unzip the downloaded file
5. Open a terminal / command prompt and navigate into the folder:

**Windows:** Open the unzipped folder, click the address bar at the top, type `cmd`, press Enter

**Mac/Linux:** Open Terminal, type `cd ` (with a space), then drag the unzipped folder into the terminal window, press Enter

---

## Get Your Data Files

The app needs three data files from Network Rail. These are free but require registration.

### Step 1 — Register for Network Rail Data Feeds

Go to: https://datafeeds.networkrail.co.uk/
Create a free account and log in. You will use this to download the CORPUS and CIF files.

### Step 2 — Download CORPUS (station data)

- In Data Feeds, find **CORPUS Extract** (you can search for it)
- Click **Download** — the file will be called something like `CORPUSExtract.json`
- Place it in: `backend/data/corpus/CORPUSExtract.json`
  - If the file has a different name, rename it to exactly `CORPUSExtract.json`

### Step 3 — Prepare your CIF timetable file

- In Data Feeds, find **Full TTIS data** (timetable)
- Download the `.MCA` or `.CIF` file (it will be a large file, typically 500 MB – 1 GB)
- You can either:
  - Place it in `backend/data/cif/` before starting the app (it will load automatically on startup), **or**
  - Upload it through the app's **Upload CIF to Server** button after the app is running (recommended — a progress bar will show while it loads)

### Step 4 — Mileage data (optional — app auto-estimates if not provided)

The mileage data tells the app the distance in miles between each pair of stations.

**Without any action on your part**, the app will automatically estimate distances using free OpenStreetMap station coordinates (no registration, no download). The first time a generation runs without `mileage.json`, the backend:
1. Downloads UK rail station coordinates from OpenStreetMap (one-time, ~500 KB)
2. Downloads station elevations from OpenTopoData SRTM 30m (one-time)
3. Caches both to `backend/data/mileage/.coord_cache.json` — subsequent runs are instant

Estimated distances are accurate to within ~5–10% (straight-line × 1.15 rail factor). The route CSV will also include an `avg_elevation_m` column (average metres above sea level between each station pair) automatically.

**For official distances** (required for legal/regulatory use), get the NESA dataset from Rail Data Marketplace:

1. Go to: https://raildata.org.uk/ and create a free account
2. Search for **NESA** or **mileage** in the dataset catalogue
3. Download the dataset and convert it to this JSON format:
   ```json
   {
     "segments": [
       {
         "from_tiploc": "WATRLMN",
         "to_tiploc": "CLPHMJN",
         "elr": "WAT1",
         "miles": 3,
         "chains": 45
       }
     ]
   }
   ```
   - `from_tiploc` / `to_tiploc` — TIPLOC codes (e.g. `WATRLMN` for Waterloo)
   - `elr` — Engineer's Line Reference
   - Either `miles` + `chains` (1 mile = 80 chains), or a pre-computed `distance_miles` float
4. Save as `backend/data/mileage/mileage.json`

When `mileage.json` is present, official distances are used and the coordinate estimate is only used as a fallback for any pairs not found in the file.

### Step 5 — (Optional) Darwin API token for better train class and coach data

The app fills in `train_class` and `number_of_coaches` automatically using two sources:

**Source 1 — CIF data (always active, no setup needed)**
Train class (Standard / 1st & Standard) and approximate coach count are read directly from the Network Rail CIF file you already uploaded. This works for all services with no extra configuration.

**Source 2 — Darwin live API (optional, more accurate for today/near-future dates)**
Darwin is National Rail's live train system. It provides real-time formation data and is more accurate than CIF for services running in the next 7 days. Darwin only covers live/upcoming dates — it cannot enrich historical data.

To enable Darwin:
1. Go to: https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/
2. Fill in the registration form — you will receive a token like `a1b2c3d4-e5f6-7890-abcd-ef1234567890`
3. Open `backend/.env` in a text editor, find `DARWIN_API_TOKEN=` and paste your token after the `=`:
   ```
   DARWIN_API_TOKEN=a1b2c3d4-e5f6-7890-abcd-ef1234567890
   ```
4. Save the file and restart the backend (`Ctrl+C`, then re-run the uvicorn command)

> **Do not change `DARWIN_API_URL`** — it is already set correctly in `.env.example`.

> The app works fine without Darwin — CIF-based class and coach data will still be populated automatically.

---

## Step-by-Step Setup

### Windows

Open **Command Prompt** (press `Windows key`, type `cmd`, press Enter).

Navigate to the app folder (replace the path with where you unzipped/cloned the app):
```
cd C:\Users\YourName\Downloads\Electric-Train-Web-App
```

**1. Set up the backend configuration:**
```
copy backend\.env.example backend\.env
```
Now open the file `backend\.env` in Notepad. If you have a Darwin token, find the line `DARWIN_API_TOKEN=` and paste your token after the `=`. Save and close. (You can leave it as-is and add the token later.)

**2. Create a virtual environment for the backend** (keeps Python packages tidy):
```
python -m venv venv
venv\Scripts\activate
```
You should see `(venv)` appear at the start of your prompt. This means the virtual environment is active.

**3. Install backend (Python) packages:**
```
pip install -r backend\requirements.txt
```
This downloads the required Python packages. It may take a minute or two.

**4. Install frontend (website) packages:**
```
npm install
```
This may also take a minute or two.

**5. Start the backend** (leave this window open):
```
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
You should see a message: `Application startup complete.`

> Next time you start the app, you need to activate the virtual environment again (`venv\Scripts\activate`) before running the uvicorn command.

**7. Open a second Command Prompt window**, navigate to the same folder again, then start the frontend:
```
cd C:\Users\YourName\Downloads\Electric-Train-Web-App
npm run dev
```
You should see: `Local: http://localhost:3000`

**8. Open your web browser** and go to: http://localhost:3000

---

### Mac

Open **Terminal** (press `Cmd + Space`, type `Terminal`, press Enter).

Navigate to the app folder:
```
cd ~/Downloads/Electric-Train-Web-App
```
(Adjust the path if you saved it somewhere else.)

**1. Set up the backend configuration:**
```
cp backend/.env.example backend/.env
```
Open `backend/.env` in TextEdit. If you have a Darwin token, find the line `DARWIN_API_TOKEN=` and paste your token after the `=`. Save the file. (You can leave it blank and add the token later.)

**2. Create a virtual environment for the backend:**
```
python3 -m venv venv
source venv/bin/activate
```
You should see `(venv)` appear at the start of your prompt.

**3. Install backend (Python) packages:**
```
pip install -r backend/requirements.txt
```

**4. Install frontend (website) packages:**
```
npm install
```

**5. Start the backend** (leave this Terminal window open):
```
python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
You should see: `Application startup complete.`

> Next time you start the app, activate the virtual environment first: `source venv/bin/activate`

**6. Open a second Terminal window** (`Cmd + T` for a new tab), navigate to the same folder, then start the frontend:
```
cd ~/Downloads/Electric-Train-Web-App
npm run dev
```
You should see: `Local: http://localhost:3000`

**7. Open Safari or Chrome** and go to: http://localhost:3000

---

### Linux

Open a **Terminal** window.

Navigate to the app folder:
```
cd ~/Downloads/Electric-Train-Web-App
```

**1. Set up the backend configuration:**
```
cp backend/.env.example backend/.env
```
Open `backend/.env` in a text editor (e.g. `nano backend/.env`). If you have a Darwin token, find `DARWIN_API_TOKEN=` and paste your token after the `=`. Press `Ctrl+X`, then `Y`, then Enter to save. (You can leave it blank and add the token later.)

**2. Create a virtual environment for the backend:**
```
python3 -m venv venv
source venv/bin/activate
```
You should see `(venv)` appear at the start of your prompt.

**3. Install backend (Python) packages:**
```
pip install -r backend/requirements.txt
```

**4. Install frontend (website) packages:**
```
npm install
```

**5. Start the backend** (leave this terminal window open):
```
python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
You should see: `Application startup complete.`

> Next time you start the app, activate the virtual environment first: `source venv/bin/activate`

**6. Open a second terminal window/tab** and navigate to the same folder, then start the frontend:
```
cd ~/Downloads/Electric-Train-Web-App
npm run dev
```
You should see: `Local: http://localhost:3000`

**7. Open your web browser** and go to: http://localhost:3000

---

## Using the App

Once the app is open in your browser at http://localhost:3000, the workflow has six stages.

---

### Step 1 — Upload CIF (once)

At the top of the page you will see a **CIF Status** bar:

- **Green** (loaded): The backend already has a CIF file loaded — you can generate timetables immediately. The filename and schedule count are shown.
- **Amber with spinner**: The CIF file is currently being parsed — wait until it turns green (large files can take 1–3 minutes).
- **Amber** (not loaded): Click **Upload CIF to Server** and select your `.MCA` or `.CIF` file. The file is saved on the server and reused for all future queries without re-uploading. A spinner will appear while it parses.

> Once uploaded, the CIF stays loaded even after you close the browser tab. You only need to upload it again if you want to use a newer CIF file.
> The server accepts CIF files up to **2 GB** — the full Network Rail `toc-full.CIF` is typically around 1 GB.

---

### Step 2 — Generate Timetables

Fill in the form:

| Field | What to enter | Example |
|-------|--------------|---------|
| **Station Name** | The 3-letter station code (CRS), or the full station name | `KGX` or `London Kings Cross` |
| **Operator Code** | 2-letter code for the train company | `VT` (Avanti), `GW` (GWR), `SW` (South Western) |
| **Date Start** | The date you want timetables for | `2026-03-15` |
| **Date End** | Leave blank for a single day, or enter an end date (max 31 days) | `2026-03-21` |

**Common operator codes:**

| Code | Operator |
|------|----------|
| `VT` | Avanti West Coast |
| `GW` | Great Western Railway |
| `SW` | South Western Railway |
| `EM` | East Midlands Railway |
| `GR` | LNER |
| `TP` | TransPennine Express |
| `XC` | CrossCountry |
| `NT` | Northern Trains |
| `TL` | Thameslink |
| `SN` | Southern |
| `GX` | Gatwick Express |

**Steps:**
1. Fill in all the fields
2. Click **Validate Inputs** — this checks everything looks correct before running
3. Click **Generate CSV** — the app processes the data (may take a few seconds)
4. Once results appear, click **Download Timetable CSV** and/or **Download Route CSV**
5. Open the downloaded `.csv` files in Excel, Google Sheets, or LibreOffice Calc

---

### Step 3 — Edit CSVs in Browser

After generating, an **Excel-like editor** appears below the results for both the Timetable and Route CSVs.

- **Double-click any cell** to edit its value; press Enter or Tab to confirm
- **Select rows** using the checkbox column on the left
- Click **Delete Selected** to remove chosen rows
- Click **Add Row** to append a blank row at the bottom
- Click **Save Changes** to write your edits back to the server (the downloaded CSV will reflect your changes)

**Route editor only:**
- Click **Renumber Seq** after deleting stops to close any gaps in the `seq` column — it renumbers each route variant independently starting from 1
- Click **Recalc Distances** to re-estimate `distance_miles` and `avg_elevation_m` for all remaining rows after edits or deletions — uses the coordinate fallback (OSM + OpenTopoData)

> This is particularly useful for filling in the `train_type` and `cars` columns, and for shortening routes by deleting unwanted stops before running the Electric Pipeline (see Step 5).

---

### Step 4 — Results History

All generated results are saved automatically and persist across server restarts. The **Results History** table shows all past runs with:

- Station, operator, date range
- Row counts for timetable and route
- Buttons to **open the editor**, **download CSVs**, or **delete** the result

**Merging results:** Select two or more results using the checkboxes and click **Merge Selected (N)**. This combines their timetable and route data into a single new entry in Results History:
- Timetable rows are concatenated; exact duplicate service rows are removed automatically
- Route deduplication is whole-route based — if Fleet "Stopping" is A→B→C and Camberley "Stopping" is also A→B→C, only one copy is kept; but if they have different stops they are both kept unchanged
- The merged result can be edited (fill `train_type`/`cars`) and then used as input to the Electric Pipeline

---

### Step 5 — Electric Energy Pipeline

The Electric Pipeline calculates the half-hourly energy demand at each traction substation (TSS) served by the selected timetable results.

**Before running, you need:**

1. **Rolling Stock CSV** — energy parameters per train type. Required columns:

   | Column | Meaning | Example |
   |--------|---------|---------|
   | `train_type` | Must match the value in your timetable's `train_type` column | `CAF_URBOS_3` |
   | `kwh_per_km_per_car` | Electrical energy per km per car (kWh) | `0.7` |
   | `aux_kw_per_car` | Auxiliary power per car (heating, lighting, AC) in kW | `6` |
   | `drive_eff` | Drivetrain efficiency 0–1 (default 0.9) | `0.9` |
   | `regen_eff` | Regenerative braking recovery 0–1 (default 0) | `0.25` |
   | `line_losses_pct` | Transmission losses as a fraction (e.g. 0.05 = 5%) | `0.05` |

   Example row:
   ```
   train_type,kwh_per_km_per_car,aux_kw_per_car,drive_eff,regen_eff,line_losses_pct
   CAF_URBOS_3,0.7,6,0.9,0.25,0.05
   ```

   > **Regen note:** `regen_eff` uses a stop-based model — kinetic energy is recovered at every station stop regardless of gradient. A value of 0.25 reduces traction draw by ~18% on a typical metro/tram route.

2. **Station Points CSV** — maps each station name to a TSS. Required columns:

   | Column | Meaning | Example |
   |--------|---------|---------|
   | `Station` | Station name (must match the `from_station`/`to_station` values in your route CSV, case-insensitive) | `WOLVERHAMPTON_STATION` |
   | `TSS` | Name of the traction substation serving that station | `BILSTON_ROAD_(TSS_2)` |

**Steps:**
1. Upload Rolling Stock and Station Points CSVs using the upload buttons in the **Electric Pipeline** panel
2. Choose your data source — you have two options:
   - **From Results History:** tick the checkboxes for the results you want (one or more), or use **Merge Selected** first to combine them into one editable result
   - **Direct upload:** upload a Timetable CSV and a Route CSV directly using the **Timetable CSV** and **Route CSV** upload slots in the Electric panel (useful for externally prepared files)
3. Before running: use the in-browser editor to fill in `train_type` and `cars` columns in your timetable CSV — these are not populated automatically by the timetable generator
4. Click **Run Electric Pipeline** (the button label shows which mode is active). A progress spinner appears — the pipeline runs in the background and typically finishes in 30–120 seconds. The page stays responsive throughout.
5. The results appear as one CSV file per TSS. Each file has:
   - `date` and `dep_time` columns
   - 48 half-hour energy columns (`0:30`, `1:00`, … `0:00`) in kWh
   - A `Total Units` column summing all bins
6. Click each TSS name to download its CSV

---

### Step 6 — Solar Analysis

The Solar Pipeline models how much of the electric demand from Step 5 could be met by a rooftop or trackside solar installation.

**You need two files:**

1. **Half-hour Demand CSV** — one of the TSS output files from Step 5 (columns: `date`, `dep_time`, then 48 half-hour bin columns)

2. **PVGIS Solar CSV** — hourly solar irradiance data downloaded from the EU PVGIS tool:
   - Go to: https://re.jrc.ec.europa.eu/pvg_tools/en/
   - Enter the location of the TSS or solar installation
   - Select **Hourly Data**, choose a year, and download as CSV

**Steps:**
1. Upload your Demand CSV and PVGIS CSV in the **Solar Analysis** panel
2. Click **Run Solar Analysis**
3. Results appear:
   - **Inline chart** — average daily demand vs. solar supply vs. used solar (24-hour profile)
   - **Solar share %** — percentage of annual demand met by solar
   - **Utilisation %** — percentage of available solar energy that was actually used (not spilled)
4. Download links are provided for all 5 output files:

| File | Contents |
|------|----------|
| `demand_hourly.xlsx` | Hourly demand in wide format (one column per hour) |
| `pvgis_supply.xlsx` | Hourly solar supply matched to demand dates |
| `avg_profile.xlsx` | Average 24-hour demand, supply, and used-solar profile |
| `avg_profile.png` | Chart of the 24-hour average profile |
| `solar_metrics.xlsx` | Annual and seasonal summary: solar share %, utilisation %, spillage % |

---

## Understanding the Outputs

### Timetable CSV

| Column | Meaning |
|--------|---------|
| `date` | The date of the service (YYYY-MM-DD) |
| `departure_time` | Time the train departs the station you searched (HH:MM:SS) |
| `route_variant` | Identifies the stopping pattern of this service |
| `train_uid` | CIF train UID — uniquely identifies the schedule (e.g. `W12345`) |
| `origin_departure` | Departure time from the service's origin station (used for merge deduplication) |
| `stop_type` | `stop` = train calls here; `pass` = train passes through without stopping |
| `train_class` | First / Standard (from CIF or Darwin — blank if neither has the data) |
| `number_of_coaches` | How many coaches the train has (from CIF or Darwin — blank if unavailable) |

### Route CSV

| Column | Meaning |
|--------|---------|
| `route_variant` | Identifies which stopping pattern this row belongs to |
| `seq` | Stop number along the route (1 = first stop, 2 = second, etc.) |
| `from_station` | Name of the station the train departs from |
| `to_station` | Name of the next station |
| `stop_type` | `stop` or `pass` for the `from_station` |
| `distance_miles` | Rail distance in miles between those two stations (official if mileage.json present, coordinate estimate otherwise) |
| `avg_elevation_m` | Average elevation in metres above sea level between the two stations (from OpenTopoData) |
| `run_min` | Minutes the train takes to travel between those two stations |
| `wait_min` | Minutes the train waits at `from_station` before departing |

> All numeric columns (`seq`, `distance_miles`, `avg_elevation_m`, `run_min`, `wait_min`) are written as plain numbers — not quoted strings — so they sort and calculate correctly in Excel and Google Sheets.

### Electric TSS CSV (per substation)

One file per Traction Sub-Station (TSS), one row per day.

| Column | Meaning |
|--------|---------|
| `Date` | Date in DD/MM/YYYY format |
| `Day` | Day of week (e.g. `Saturday`) |
| `Total Units` | Total kWh demand at this TSS for the day |
| `0:30` … `0:00` | kWh consumed in each 30-minute window (48 columns covering a full 24-hour day) |

The last bin is labelled `0:00` (midnight wrap, i.e. 23:30–00:00).

---

## Stopping the App

When you are finished, go back to each terminal/command prompt window and press **Ctrl + C** to stop the backend and frontend servers.

---

## Troubleshooting

**"Python is not recognized" or "python: command not found"**
- Windows: Re-run the Python installer and make sure to tick **"Add Python to PATH"**
- Mac/Linux: Try typing `python3` instead of `python`

**"pip is not recognized"**
- Try `pip3` instead of `pip`
- Windows: Try `python -m pip install -r backend\requirements.txt`

**"npm is not recognized"**
- Node.js did not install correctly. Re-download and re-run the Node.js installer from https://nodejs.org/

**"No services found" or empty CSV**
- Check your CIF data file is uploaded (green CIF status bar at the top of the page)
- Make sure the operator code is correct for the station and date you chose
- Try a date within the validity range of your CIF file (usually covers ~3 months)

**"Station not found in CORPUS"**
- Try using the 3-letter CRS code instead of the station name (e.g. `KGX` not `Kings Cross`)
- Make sure your `CORPUSExtract.json` file is in `backend/data/corpus/`

**Page won't load at http://localhost:3000**
- Make sure both terminal windows are still running (you should see no errors in them)
- Try http://127.0.0.1:3000 instead

**`train_class` and `number_of_coaches` are always blank**
- These are populated automatically from the CIF file (no setup needed) for most services
- If they are still blank, download the debug CSV from Results History and check the `timing_load` and `seating_class` columns — if those are also blank, the CIF file may not have formation data for this operator
- For live/near-future dates, a Darwin API token (see Step 5 of Get Your Data Files) provides more accurate data

**Electric Pipeline returns no TSS files**
- Make sure Rolling Stock and Station Points CSVs are uploaded (green ticks in the Electric panel)
- Make sure `train_type` and `cars` are filled in the timetable editor (they are blank by default — use the in-browser editor or fill them before uploading)
- Check that station names in your Station Points CSV (`Station` column) match the `from_station`/`to_station` values in your route CSV — the app reports unmatched stations in a debug CSV

**Solar pipeline chart is blank or metrics show 0%**
- Make sure the PVGIS CSV was downloaded from the PVGIS hourly data tool (not monthly averages)
- Ensure the date range in the PVGIS file overlaps with dates in your demand CSV

---

## Data Sources

All data used by this app comes exclusively from official Network Rail sources:

| Source | What it provides | Where to get it |
|--------|-----------------|-----------------|
| **Network Rail CIF** | Train schedules, times, calling points, class & coach data | https://datafeeds.networkrail.co.uk/ |
| **Network Rail CORPUS** | Station names, CRS codes, TIPLOC codes | https://datafeeds.networkrail.co.uk/ |
| **Network Rail NESA** | Official rail distances in miles (optional) | https://raildata.org.uk/ |
| **OpenStreetMap / Overpass** | Station coordinates for distance estimation (auto, free) | https://overpass-api.de/ |
| **OpenTopoData SRTM 30m** | Station elevations for avg_elevation_m column (auto, free) | https://api.opentopodata.org/ |
| **Darwin OpenLDBWS** | Live train class and coach numbers (optional) | https://realtime.nationalrail.co.uk/ |
| **EU PVGIS** | Solar irradiance data (Solar pipeline only) | https://re.jrc.ec.europa.eu/pvg_tools/en/ |

---

## Project Structure (for developers)

```
Electric-Train-Web-App/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI application factory
│   │   ├── config.py                  # Settings (env vars, data paths)
│   │   ├── routers/
│   │   │   ├── cif.py                 # POST /api/cif/upload, GET /api/cif/status
│   │   │   ├── timetable.py           # Generate, download, list, delete, edit, merge CSVs
│   │   │   ├── electric.py            # Upload reference files, run energy pipeline
│   │   │   ├── solar.py               # Run solar analysis
│   │   │   └── health.py              # GET /api/health
│   │   ├── services/
│   │   │   ├── orchestrator.py        # Main timetable generation logic
│   │   │   ├── cif_parser.py          # Network Rail CIF/MCA parser (supports .gz)
│   │   │   ├── corpus_mapper.py       # CORPUS station data mapper
│   │   │   ├── mileage_resolver.py    # NESA mileage lookup + OSM coordinate fallback + elevation
│   │   │   ├── cif_formation.py       # CIF BS record → train_class and coach count lookup
│   │   │   ├── darwin_enricher.py     # Darwin API enrichment (headcode+time matching)
│   │   │   ├── result_store.py        # Persistent result storage
│   │   │   ├── electric_pipeline.py   # Half-hourly TSS energy model (stop-based regen)
│   │   │   └── solar_pipeline.py      # Solar supply vs. demand model
│   │   └── security/
│   │       └── middleware.py          # Rate limiting, size limits, security headers
│   ├── tests/
│   │   ├── unit/                      # Unit tests for individual services
│   │   ├── integration/               # Integration tests
│   │   └── e2e/
│   │       └── test_full_pipeline.py  # End-to-end tests (177 tests, no network)
│   ├── data/
│   │   ├── cif/                       # CIF timetable files (uploaded or pre-placed; .gz supported)
│   │   ├── corpus/                    # CORPUSExtract.json
│   │   ├── mileage/                   # mileage.json (optional official distances)
│   │   ├── electric/                  # rolling_stock.csv, station_points.csv, route.csv, timetable.csv
│   │   ├── results/                   # Persistent generated results (auto-created, gitignored)
│   │   └── sample/                    # Sample timetable and route CSVs for testing
│   ├── requirements.txt               # Python dependencies
│   └── .env.example                   # Configuration template
├── src/
│   ├── app/
│   │   └── page.tsx                   # Main UI (all panels)
│   ├── components/
│   │   └── CsvEditor.tsx              # In-browser Excel-like CSV editor
│   └── lib/
│       └── api.ts                     # TypeScript API client
├── validate_pipeline.py               # Electric pipeline parameter sensitivity tests (developer tool)
├── package.json                       # Node.js dependencies
└── pyproject.toml                     # Python test configuration
```

### Running the tests

```bash
# All tests (unit + integration + e2e) — requires no network or external files
python -m pytest backend/tests/ -v

# End-to-end tests only
python -m pytest backend/tests/e2e/ -v
```

Developer docs: [Architecture](backend/ARCHITECTURE.md) · [Source Mapping](backend/SOURCE_MAPPING.md) · [CIF-Darwin Matching](backend/CIF_DARWIN_MATCHING.md) · [Security Report](backend/SECURITY_REPORT.md)
