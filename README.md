[![Русский](https://img.shields.io/badge/язык-Русский-blue)](README.ru.md)
## ARFFS Response Time Analysis (QGIS Plugin)

<p align="center">
  <img src="docs/images/hero_fire.svg" alt="ARFFS Response Time — Hero" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/Moroz-Froze/arrivel-time-calculator" target="_blank">
    <img src="docs/images/btn_download.svg" alt="Download" />
  </a>
  &nbsp;
  <a href="#usage">
    <img src="docs/images/btn_docs.svg" alt="Docs" />
  </a>
  &nbsp;
  <a href="https://github.com/Moroz-Froze/arrivel-time-calculator/issues" target="_blank">
    <img src="docs/images/btn_issues.svg" alt="Issues" />
  </a>
</p>

A QGIS 3.x plugin for **Aerodrome Fire and Rescue Service (ARFFS)** response time analysis per **ICAO Annex 14** requirements. Features physics-based vehicle dynamics modeling (acceleration, deceleration, curve speed limits), color-coded isochrone road maps, and configurable vehicle presets for common ARFFS vehicles.

<p align="center">
  <img src="docs/images/feature_cards.svg" alt="Key Features" width="100%" />
</p>

### Features

- **ARFFS Response Time Isochrones (`arffs_isochrone`)**:
  - Generates color-coded buffered road/taxiway polygons showing response time zones from ARFFS stations.
  - **Green**: < 2 minutes (ICAO first vehicle requirement).
  - **Orange**: 2-3 minutes (ICAO all vehicles requirement).
  - **Red**: > 3 minutes (exceeds ICAO response time).
  - Splits edges at threshold boundaries for precise zone delineation.

- **Nearest Fire Station (`nearest_fire_station`)**:
  - Identifies the nearest ARFFS station based on shortest physics-based travel time.
  - Output attributes: `nearest_station`, `distance_km`, `response_time_min`, `station_x`, `station_y` + original object attributes.

- **Response Time Routes (`response_time_routes`)**:
  - Generates route geometries between incident locations and ARFFS stations.
  - Modes: nearest station only; all stations; all stations within a time threshold.
  - Output attributes: `object_id`, `station_name`, `distance_km`, `response_time_min`, `object_type`, `route_type`.

- **All Stations Response Analysis (`all_stations_response`)**:
  - Evaluates response times across all fire ranks simultaneously:
    - Rank 1: 1 unit, Rank 1-bis: 2 units, Rank 2: 3 units, Rank 3: 4 units, Rank 4: 5 units, Rank 5: 6 units.
  - Output attributes per rank: `rank_N_min`, `rank_N_max`, `rank_N_avg`.
  - Overall statistics: `arrival_time_min`, `arrival_time_max`, `arrival_time_mean`.
  - ICAO-based `evaluation`: "satisfactory" (first unit <= 2 min and mean <= 3 min), "marginal", or "unsatisfactory".

- **Arrival Time Matrix (`arrival_time_matrix`)**:
  - Computes arrival times from all fire units to all target locations (buildings/points of interest).
  - Output: target layer with a column per unit showing arrival time in minutes.

- **First Arrival Unit (`first_arrival_unit`)**:
  - Processes the arrival time matrix to identify the first arriving unit at each location.
  - Output: `first_unit` (station name) and `first_time` (minutes).

### Physics-Based Travel Time Model

All algorithms use a **trapezoidal velocity profile** that models realistic vehicle dynamics:

1. **Activation time** — configurable delay from alarm to vehicle departure (default: 1.0 min).
2. **Acceleration phase** — vehicle accelerates from entry speed to cruising speed.
3. **Cruising phase** — vehicle travels at the road's maximum speed (capped by vehicle max speed).
4. **Deceleration phase** — vehicle slows to exit speed for turns or stops.

Turn angles at intersections are computed from road geometry, and curve speed limits are applied:

| Deflection Angle | Speed Factor | Description |
|---|---|---|
| 0-15° | 100% | Straight road |
| 15-45° | 80% | Gentle curve |
| 45-90° | 50% | Moderate turn |
| 90-135° | 30% | Sharp turn |
| 135-180° | 15% | U-turn / near-stop |

### Vehicle Presets

| Vehicle | Max Speed (km/h) | Acceleration (m/s²) | Deceleration (m/s²) |
|---|---|---|---|
| Rosenbauer Panther 6x6 | 105 | 2.0 | 3.5 |
| Oshkosh Striker 3000 | 113 | 1.8 | 3.5 |
| Oshkosh Striker 6x6 | 113 | 1.6 | 3.0 |
| E-One Titan HPR | 100 | 1.7 | 3.2 |
| Custom | User-defined | User-defined | User-defined |

### Vehicle Parameters

All algorithms accept the following physics parameters:

| Parameter | Range | Default | Description |
|---|---|---|---|
| Activation Time | 0-5 min | 1.0 | Alarm-to-departure delay |
| Max Speed | 10-200 km/h | 105 | Vehicle top speed |
| Acceleration | 0.5-5.0 m/s² | 2.0 | Vehicle acceleration rate |
| Deceleration | 1.0-8.0 m/s² | 3.5 | Vehicle braking rate |

<p align="center">
  <img src="docs/images/overview.svg" alt="Plugin Workflow Overview" width="860" />
  <br/>
  <sub>Input layers → Road graph → Physics model → Algorithms → Output layers</sub>
</p>

### ARFFS Default Road Speeds (km/h)

These default speeds apply when using custom aerodrome road layers:

| Road Type | Speed (km/h) |
|---|---|
| Runway | 80 |
| Perimeter road | 70 |
| Taxiway | 60 |
| Access road | 50 |
| Service | 40 |
| Apron | 30 |
| Other | 30 |

When using OSM-based road networks, the following speed mappings are also available:

| OSM `highway` tags | Speed (km/h) |
|---|---|
| trunk, motorway(+_link), primary(+_link) | 49 |
| secondary(+_link), unclassified | 37 |
| tertiary(+_link), residential, living_street | 26 |
| road, service, track | 16 |
| footway, path, pedestrian, steps, cycleway, bridleway, corridor | 5 |

### Requirements
- QGIS: 3.16 - 3.99
- Internet access (required to fetch OSM road graph via OSMnx, unless using a custom road layer)
- Python dependencies in the QGIS environment: `osmnx`, `networkx` (usually `shapely` is already included with QGIS)

### Installing Dependencies (Windows, OSGeo4W/QGIS)
1. Open the **OSGeo4W Shell** (or **QGIS Python Console**).
2. Install the packages (specify versions compatible with your QGIS/Python build if needed):

```bash
python -m pip install --upgrade pip
python -m pip install "osmnx>=1.4,<2.0" "networkx>=2.6,<3.0"
# If compatibility issues arise, try pinning versions:
# python -m pip install osmnx==1.6.0 networkx==2.8.8
```

If you're behind a proxy or on a corporate network, add `--proxy` flags or configure environment variables beforehand.

### Plugin Installation
- Copy the plugin folder into your QGIS profile plugins directory.
  Example (Windows):
  - `C:\Users\<USER>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\fire_analysis_plugin`
- Restart QGIS and enable the plugin via **Plugins -> Manage and Install Plugins**.

### Usage
1. Load into your project:
   - An **ARFFS station layer** (points) — fire/rescue station locations.
   - A **road network layer** (lines) — aerodrome roads, taxiways, or OSM-based network.
   - Optionally, an **incident/target layer** (points or polygons) for non-isochrone algorithms.
2. Open the **Processing Toolbox** -> group **ARFFS Response Time Analysis**.
3. Run the desired algorithm and configure parameters:
   - Select a **vehicle preset** or set custom physics parameters.
   - Set the **activation time** (alarm-to-departure delay).
   - For routes: choose a mode and, if applicable, set a time threshold.
4. Output layers:
   - **Isochrones**: Colored polygon layer (green/orange/red zones).
   - **Nearest Station**: Point/polygon layer with response time attributes.
   - **Routes**: Line layer with route geometries and time/distance attributes.
   - **All Stations Response**: Layer with per-rank metrics and ICAO evaluation.
   - **Arrival Time Matrix**: Target layer with per-unit arrival time columns.
   - **First Arrival Unit**: Layer with first unit name and arrival time.

<p align="center">
  <img src="docs/images/steps_pipeline.svg" alt="Execution Steps" width="100%" />
</p>

#### Route Generation Modes
<p>
  <img src="docs/images/routes_modes.svg" alt="Route Generation Modes" width="820" />
</p>

### ICAO Annex 14 Evaluation Criteria

The All Stations Response Analysis algorithm evaluates coverage using ICAO Annex 14 thresholds:

- **Satisfactory**: First unit arrives within 2 minutes AND mean arrival time <= 3 minutes.
- **Marginal**: First unit arrives within 3 minutes but does not meet "satisfactory" criteria.
- **Unsatisfactory**: First unit arrival exceeds 3 minutes.

### Tips and Limitations
- **Custom Road Layers**: For best results on aerodromes, use a dedicated road network layer with road type attributes rather than relying on OSM data.
- **OSM Completeness**: When using OSM networks, route accuracy depends on the completeness of OpenStreetMap data in your area.
- **Graph Loading**: For large extents, increase the buffer cautiously — it affects graph size and computation time.
- **CRS**: Algorithms handle coordinate transformations automatically, but input layers must have valid CRS definitions.
- **Bidirectional Roads**: Aerodrome road edges are treated as bidirectional by default.

### Example Outputs
- **Isochrones**: Color-coded polygon layer showing green (<2 min), orange (2-3 min), and red (>3 min) response zones around ARFFS stations.
- **Nearest Station**: Point/polygon layer with `nearest_station`, `response_time_min`, and distance attributes.
- **Routes**: Line layer with route geometries and response time per object-station pair.
- **All Stations by Rank**: Layer with `rank_1_min/max/avg` through `rank_5_min/max/avg`, `arrival_time_min/max/mean`, and `evaluation`.

<p>
  <img src="docs/images/nearest_flow.svg" alt="Nearest Station Algorithm" width="820" />
</p>

#### Coverage Assessment
<p>
  <img src="docs/images/coverage_ranks.svg" alt="Response Time Coverage by Fire Rank" width="820" />
</p>

### Feedback and Source Code
- Repository: [GitHub](https://github.com/Moroz-Froze/arrivel-time-calculator)
- Bug reports / feature requests: [Issues](https://github.com/Moroz-Froze/arrivel-time-calculator/issues)

### Changelog
- **v2.0.0 — ARFFS Response Analysis**:
  - Added ARFFS isochrone algorithm with color-coded road segment output.
  - Added physics-based travel time model (trapezoidal velocity profile).
  - Added acceleration and deceleration modeling.
  - Added curve speed limits based on turn sharpness.
  - Added activation time parameter (alarm to departure).
  - Added vehicle presets (Rosenbauer Panther 6x6, Oshkosh Striker 3000, Oshkosh Striker 6x6, E-One Titan HPR).
  - Added ARFFS road types (runway, taxiway, apron, perimeter_road).
  - Added bidirectional edge support for aerodrome roads.
  - Updated evaluation thresholds to ICAO Annex 14 (2 min / 3 min).
  - Retrofitted all existing algorithms with physics parameters.
- **v1.1.0 — Fire Response 1.0**:
  - Added arrival time matrix calculation algorithm.
  - Added first arrival unit response time algorithm.
  - Added OSM routing and coverage analysis; 3 algorithms, OSM graph module, automatic station name field detection.

### License
See the `LICENSE` file (if present) or the license information in the plugin metadata.
