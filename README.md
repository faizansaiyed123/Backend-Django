# Spotter Backend Assessment — Fuel Route Optimizer

A Django API that plans a fuel-cost-efficient driving route between two US locations.

The API returns:

- Route geometry for map rendering.
- Fuel stops that satisfy the 500-mile vehicle range.
- Fuel prices from the supplied OPIS/Truckstop CSV.
- Fuel purchased at each stop.
- Total gallons purchased during the trip.
- Total fuel cost.

## Assessment constraints

| Constraint | Value |
|---|---:|
| Maximum vehicle range | 500 miles |
| Fuel economy | 10 MPG |
| Tank capacity | 50 gallons |
| Route API calls | One per uncached trip |
| Input geography | US locations only |

### Starting-fuel assumption

The vehicle starts the trip with a full 50-gallon tank.

The initial tank is not included in `total_cost` because it is assumed to be available before departure. Only fuel purchased during the planned trip is billed.

## Architecture

The request path is intentionally small:

```
POST /api/route/
        |
        +-- US geocoding (cached)
        |
        +-- OSRM route request (cached)
        |
        +-- route geometry processing
        |
        +-- database candidate filtering
        |
        +-- station-to-route projection
        |
        +-- greedy fuel optimization
        |
        +-- JSON response
```

The large fuel-price CSV is processed offline into the SQLite database. The API does not parse or geocode the entire CSV on every request.

## External services

| Purpose | Provider |
|---|---|
| Start/finish geocoding | [Nominatim](https://nominatim.openstreetmap.org) |
| Bulk station geocoding | [US Census Geocoder](https://geocoding.geo.census.gov/) |
| Station city fallback | [US Census Gazetteer](https://www.census.gov/programs-surveys/geography/about/faq/what-gazetteer.html) |
| Route + distance + geometry | [OSRM](https://router.project-osrm.org) |

No API key is required for the providers used by this assessment implementation.

Trip geocoding is explicitly restricted to US results. Nominatim requests are rate-limited and cached.

## Setup

Use Python 3.12+.

```bash
python -m venv .venv

# Windows PowerShell
.venv\\Scripts\\Activate.ps1

# macOS/Linux
# source .venv/bin/activate

python -m pip install -r requirements.txt
```

Place the supplied assessment CSV next to `manage.py` as:

```
fuel-prices-for-be-assessment.csv
```

The supplied CSV should remain local unless Spotter explicitly authorizes redistribution.

Create the database:

```bash
python manage.py migrate
```

Load and preprocess the stations:

```bash
python manage.py load_stations
python manage.py geocode_stations
python manage.py geocode_gazetteer
```

Start the API:

```bash
python manage.py runserver
```

## API

### Plan a route

```
POST /api/route/
Content-Type: application/json
```

Request:

```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

Response shape:

```json
{
  "start": {
    "query": "New York, NY",
    "lat": 40.7127,
    "lon": -74.0060
  },
  "finish": {
    "query": "Los Angeles, CA",
    "lat": 34.0537,
    "lon": -118.2428
  },
  "route": {
    "distance_miles": 2794.01,
    "duration_seconds": 179310,
    "geometry": [[40.71, -74.00], "..."]
  },
  "fuel_stops": [
    {
      "opis_id": "1234",
      "name": "Example Truck Stop",
      "address": "...",
      "city": "Example City",
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
  "vehicle": {
    "max_range_miles": 500,
    "mpg": 10
  }
}
```

### Errors

- `400` — Invalid JSON or missing/invalid `start` or `finish`.
- `400` — Location could not be resolved as a US location.
- `405` — Only `POST` is supported.
- `422` — The route cannot be completed within the 500-mile range.
- `502` — Routing provider unavailable or returned no usable route.

## Management commands

| Command | Purpose |
|---|---|
| `python manage.py load_stations [--clear]` | Load fuel stations from CSV, trimming data, filtering non-US rows, and de-duplicating OPIS IDs by lowest supplied price. |
| `python manage.py geocode_stations [--nominatim]` | Geocode stations with the US Census batch API and optionally use a rate-limited Nominatim city/state fallback. |
| `python manage.py geocode_gazetteer` | Fill unresolved stations using US Census Gazetteer place coordinates. |

## Fuel-station data processing

The supplied dataset is cleaned before request-time logic:

- Whitespace is normalized.
- Non-US state codes are removed.
- Invalid prices or missing OPIS IDs are skipped.
- Duplicate OPIS IDs are reduced deterministically to the lowest supplied price.
- Station coordinates are precomputed offline.
- Runtime lookup first applies a latitude/longitude bounding box around the route.

Station-to-route matching uses two stages:

1. Coarse candidate discovery using route samples.
2. Precise projection onto nearby route segments to calculate a more accurate route-mile position and detour distance.

This avoids scanning and precisely projecting every station against every route segment.

## Fuel optimization

The optimizer is a deterministic greedy refueling algorithm.

At each fuel stop:

1. Consider stations reachable within one full tank, including the round-trip detour.
2. If a cheaper station is reachable, use the first cheaper station along the route and buy only enough to reach it.
3. If no cheaper station is reachable, fill the current tank and skip to the farthest reachable station.
4. If the destination is reachable within the current fuel, buy only what is needed to finish.
5. If the trip cannot be completed within the 500-mile range and available stations, return `422` rather than inventing a route.

Fuel consumed for a station detour is included in the range calculation.

## Performance

The implementation is designed around the assessment requirement that routing should not be called excessively:

- Start/finish geocoding is cached.
- Route responses are cached for seven days.
- Only one OSRM request is needed for a new trip.
- The fuel CSV is preprocessed offline.
- Runtime station lookup uses database bounding-box filtering.
- Route-mile projection is performed only for candidate stations.
- Route-mile lookup uses binary search over cumulative route distances.

## Data-quality assumptions

Some truck-stop records use highway exits/intersections rather than precise street addresses. The preprocessing pipeline first attempts bulk US Census geocoding and can fall back to city-level coordinates for unresolved stations.

City-level fallback coordinates are approximate and are not presented as GPS-accurate truck-stop addresses.

## Tests

Run:

```bash
python manage.py test fuel_route
```

The suite covers:

- Short routes.
- Unreachable station gaps.
- 500-mile boundary behavior.
- Nearest-cheaper greedy selection.
- Farthest reachable selection when no cheaper option exists.
- Cheaper stations that require additional fuel at the current stop.
- Final-stop fuel minimization.
- Detour fuel consumption.
- Route geometry distance normalization.
- API validation and response shape.
- US-only geocoding.
- Geocoding cache behavior.
- Route-cache behavior.

## Postman / Loom demonstration

Use:

```
POST http://127.0.0.1:8000/api/route/
```

Recommended demonstration requests:

1. `Chicago, IL` → `Indianapolis, IN` — short route, no fuel stop needed.
2. `New York, NY` → `Los Angeles, CA` — long route with multiple fuel stops.
3. `Miami, FL` → `Seattle, WA` — long cross-country route.
4. Invalid JSON — demonstrate `400`.
5. Non-US location — demonstrate US-only validation.

The required Loom should show the API working in Postman and briefly explain the routing, preprocessing, caching, and optimization decisions.
