# UK Rail Timetable Generator

Generate railway timetable and route CSV files from official UK rail data — no programming knowledge required to run it.

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
7. [Understanding the Outputs](#understanding-the-outputs)
8. [Stopping the App](#stopping-the-app)
9. [Troubleshooting](#troubleshooting)
10. [Data Sources](#data-sources)

---

## What This App Does

You type in a **station name**, an **operator code** (e.g. `VT` for Avanti West Coast), and a **date or date range**. The app looks up official Network Rail data and produces two downloadable spreadsheet files:

- **Timetable CSV** — every departure at that station on those dates, with train class and number of coaches
- **Route CSV** — the ordered list of stations the train calls at, with distances and journey times between each pair

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
Create a free account and log in.

### Step 2 — Download CORPUS (station data)
- In Data Feeds, find **CORPUS Extract**
- Download the JSON file
- Rename it to `CORPUSExtract.json`
- Place it in: `backend/data/corpus/CORPUSExtract.json`

### Step 3 — Download CIF Timetable data
- In Data Feeds, find **Full TTIS data** (timetable)
- Download the `.MCA` file
- Place it in the folder: `backend/data/cif/`
  (put the file inside that folder — the app will find it automatically)

### Step 4 — Download Mileage data
- Register at: https://raildata.org.uk/ (Rail Data Marketplace)
- Find the **NESA mileage** dataset
- Convert or download as JSON in the format shown in `backend/data/sample/`
- Name it `mileage.json` and place it at: `backend/data/mileage/mileage.json`

### Step 5 — (Optional) Darwin API token for extra detail
Darwin provides the number of coaches and train class. Without it, those two fields will be blank.
- Register at: https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/
- You will receive a token by email
- You will enter this token in the configuration step below

> **Note:** The app works fine without a Darwin token — you just won't get coach/class data.

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
Now open the file `backend\.env` in Notepad and fill in your Darwin token if you have one (find the line `DARWIN_API_TOKEN=` and paste your token after the `=`). Save and close.

**2. Install backend (Python) packages:**
```
pip install -r backend\requirements.txt
```
This downloads the required Python packages. It may take a minute or two.

**3. Install frontend (website) packages:**
```
npm install
```
This may also take a minute or two.

**4. Start the backend** (leave this window open):
```
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
You should see a message: `Application startup complete.`

**5. Open a second Command Prompt window**, navigate to the same folder again, then start the frontend:
```
cd C:\Users\YourName\Downloads\Electric-Train-Web-App
npm run dev
```
You should see: `Local: http://localhost:3000`

**6. Open your web browser** and go to: http://localhost:3000

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
Open `backend/.env` in TextEdit to add your Darwin token if you have one. Find the line `DARWIN_API_TOKEN=` and paste your token after the `=`. Save the file.

**2. Install backend (Python) packages:**
```
pip3 install -r backend/requirements.txt
```

**3. Install frontend (website) packages:**
```
npm install
```

**4. Start the backend** (leave this Terminal window open):
```
python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
You should see: `Application startup complete.`

**5. Open a second Terminal window** (`Cmd + T` for a new tab), navigate to the same folder, then start the frontend:
```
cd ~/Downloads/Electric-Train-Web-App
npm run dev
```
You should see: `Local: http://localhost:3000`

**6. Open Safari or Chrome** and go to: http://localhost:3000

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
Open `backend/.env` in a text editor (e.g. `nano backend/.env`) to add your Darwin token if you have one. Find `DARWIN_API_TOKEN=` and paste your token after the `=`. Press `Ctrl+X`, then `Y`, then Enter to save.

**2. Install backend (Python) packages:**
```
pip3 install -r backend/requirements.txt
```

**3. Install frontend (website) packages:**
```
npm install
```

**4. Start the backend** (leave this terminal window open):
```
python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
You should see: `Application startup complete.`

**5. Open a second terminal window/tab** and navigate to the same folder, then start the frontend:
```
cd ~/Downloads/Electric-Train-Web-App
npm run dev
```
You should see: `Local: http://localhost:3000`

**6. Open your web browser** and go to: http://localhost:3000

---

## Using the App

Once the app is open in your browser at http://localhost:3000:

| Field | What to enter | Example |
|-------|--------------|---------|
| **Station Name** | The 3-letter station code (CRS), or the full station name | `KGX` or `London Kings Cross` |
| **Operator Code** | 2-letter code for the train company | `VT` (Avanti), `GW` (GWR), `SW` (South Western) |
| **Date Start** | The date you want timetables for | `2026-03-15` |
| **Date End** | Leave blank for a single day, or enter an end date (max 31 days) | `2026-03-21` |
| **Train Route** | A name you choose for your own reference | `London-Birmingham Main` |
| **Route Variant** | A label for the stopping pattern | `Stopping` or `Express` |

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

**Steps:**
1. Fill in all the fields
2. Click **Validate Inputs** — this checks everything looks correct before running
3. Click **Generate CSV** — the app processes the data (may take a few seconds)
4. Once results appear, click **Download Timetable CSV** and/or **Download Route CSV**
5. Open the downloaded `.csv` files in Excel, Google Sheets, or LibreOffice Calc

---

## Understanding the Outputs

### Timetable CSV

| Column | Meaning |
|--------|---------|
| `date` | The date of the service (YYYY-MM-DD) |
| `departure_time` | Time the train departs the station you searched (HH:MM:SS) |
| `train_route` | The route name you typed in |
| `train_class` | First / Standard (from Darwin — blank if not available) |
| `number_of_coaches` | How many coaches the train has (from Darwin — blank if not available) |

### Route CSV

| Column | Meaning |
|--------|---------|
| `route_variant` | The variant name you typed in |
| `seq` | Stop number along the route (1 = first stop, 2 = second, etc.) |
| `from_station` | 3-letter code of the station the train departs from |
| `to_station` | 3-letter code of the next station |
| `distance_miles` | Rail distance between those two stations (blank if data unavailable) |
| `run_min` | Minutes the train takes to travel between those two stations |
| `wait_min` | Minutes the train waits at `from_station` before departing |

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
- Check your CIF data file is in the `backend/data/cif/` folder
- Make sure the operator code is correct for the station and date you chose
- Try a date within the validity range of your CIF file (usually covers ~3 months)

**"Station not found in CORPUS"**
- Try using the 3-letter CRS code instead of the station name (e.g. `KGX` not `Kings Cross`)
- Make sure your `CORPUSExtract.json` file is in `backend/data/corpus/`

**Page won't load at http://localhost:3000**
- Make sure both terminal windows are still running (you should see no errors in them)
- Try http://127.0.0.1:3000 instead

**train_class and number_of_coaches are always blank**
- This is normal if you haven't set a Darwin API token in `backend/.env`
- Register at https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/ to get a free token

---

## Data Sources

All data used by this app comes exclusively from official Network Rail sources:

| Source | What it provides | Where to get it |
|--------|-----------------|-----------------|
| **Network Rail CIF** | Train schedules, times, calling points | https://datafeeds.networkrail.co.uk/ |
| **Network Rail CORPUS** | Station names, CRS codes, TIPLOC codes | https://datafeeds.networkrail.co.uk/ |
| **Network Rail NESA** | Official rail distances in miles | https://raildata.org.uk/ |
| **Darwin OpenLDBWS** | Train class and coach numbers | https://realtime.nationalrail.co.uk/ |

---

## Project Structure (for developers)

```
Electric-Train-Web-App/
├── backend/              # Python FastAPI API server
│   ├── app/              # Application code
│   ├── tests/            # 133 tests (unit + integration)
│   ├── data/sample/      # Sample CSV outputs
│   ├── requirements.txt  # Python dependencies
│   └── .env.example      # Configuration template
├── src/                  # Next.js frontend (website)
├── package.json          # Node.js dependencies
└── pyproject.toml        # Python test configuration
```

Developer docs: [Architecture](backend/ARCHITECTURE.md) · [Source Mapping](backend/SOURCE_MAPPING.md) · [CIF-Darwin Matching](backend/CIF_DARWIN_MATCHING.md) · [Security Report](backend/SECURITY_REPORT.md)
