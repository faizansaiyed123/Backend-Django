# Spotter Backend Assessment - Fuel Route Optimizer

A Django API that plans a fuel-optimized route between two USA locations. It returns the route geometry, optimal fuel stops (cost-effective), and total fuel cost.

## Vehicle Constraints

- Maximum range: **500 miles**
- Fuel economy: **10 MPG**
- Tank capacity: 50 gallons

## Free External APIs Used (no API key required)

| Purpose | Provider |
|---------|----------|
| Geocoding (start/finish) | [Nominatim](https://nominatim.openstreetmap.org) (OpenStreetMap) |
| Station geocoding (bulk) | [US Census Geocoder](https://geocoding.geo.census.gov/) |
| Station coordinates (fallback) | [US Census Gazetteer](https://www.census.gov/programs-surveys/geography/about/faq/what-gazetteer.html) |
| Route + distance | [OSRM](https://router.project-osrm.org) (Open Source Routing Machine) |

External calls are cached in the database (`GeocodeCache`, `RouteCache`) so repeated requests hit the network at most once per unique query.

## Setup

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install django==5.2 requests

# Load fuel stations from CSV (place the CSV next to manage.py)
python manage.py load_stations

# Geocode stations (Census batch + Gazetteer fallback)
python manage.py geocode_stations
python manage.py geocode_gazetteer

# Run the server
python manage.py runserver
```

## API

### Plan a route

`POST /api/route/`

**Request body:**
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

**Response:**
```json
{
  "start": {"query": "New York, NY", "lat": 40.7127, "lon": -74.0060},
  "finish": {"query": "Los Angeles, CA", "lat": 34.0537, "lon": -118.2428},
  "route": {
    "distance_miles": 2794.01,
    "duration_seconds": 179310,
    "geometry": [[40.71, -74.00], ...]
  },
  "fuel_stops": [
    {
      "opis_id": "1234",
      "name": "Youngstown OH",
      "address": "...",
      "city": "Youngstown",
      "state": "OH",
      "retail_price": 3.059,
      "mile_marker": 400.0,
      "detour_miles": 10.08,
      "gallons": 42.0169,
      "cost": 128.53
    }
  ],
  "total_gallons": 264.4598,
  "total_cost": 835.02,
  "vehicle": {"max_range_miles": 500, "mpg": 10}
}
```

### Error responses

- `400` — Missing or invalid `start`/`finish` fields, or invalid JSON body
- `400` — Location could not be geocoded
- `405` — GET method not allowed (POST only)
- `422` — Route cannot be completed with the 500-mile range (no station within range along the route)
- `502` — External routing service unavailable

## Management Commands

| Command | Purpose |
|---------|---------|
| `python manage.py load_stations [--clear]` | Load fuel stations from CSV, de-duplicating by OPIS ID (keeps lowest price) |
| `python manage.py geocode_stations [--nominatim]` | Geocode stations via US Census batch API; optional Nominatim city fallback |
| `python manage.py geocode_gazetteer` | Geocode remaining stations via US Census Gazetteer place coordinates |

## Data Quality Handling

The CSV (8,151 rows) had these issues, all handled during import:

- **Duplicate OPIS IDs** (678 IDs appear multiple times): kept the lowest-price record per ID
- **Non-US states** (Canadian provinces AB, BC, MB, NB, NS, ON, QC, SK, YT): filtered out
- **Whitespace** in city/state/address fields: trimmed
- **6,626 unique US stations** loaded after cleaning

## Optimization Algorithm

A textbook greedy refueling strategy:

1. Start with a full tank (50 gallons = 500 miles).
2. At the current position, look at stations within range ahead (route miles + detour miles).
3. If a cheaper station is ahead, drive to it (buy just enough fuel at the current station to arrive).
4. If no cheaper station is within range, fill the tank and drive to the farthest reachable station.
5. Pulling off the route costs detour miles (there and back), charged against the tank.
6. Only fuel purchased at stops is billed; the initial full tank is not part of trip cost.

## Tests

```bash
python manage.py test fuel_route
```

10 tests covering: short routes, single/multiple stops, cheapest-station selection, unreachable gaps, range-boundary stations, API validation (missing fields, invalid JSON, wrong method), and response shape.

## Postman Collection

**Collection:** Fuel Route Optimizer  
**Request:** `POST {{base_url}}/api/route/`  
**Body (raw JSON):**
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

Example test requests to include in Postman:
1. `Chicago, IL` → `Indianapolis, IN` (short, no stops)
2. `New York, NY` → `Los Angeles, CA` (long, multiple stops)
3. `Miami, FL` → `Seattle, WA` (cross-country)
4. Missing body → expect 400
5. Invalid location → expect 400
