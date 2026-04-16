# Indoor Localization Simulator

Desktop application built with Python and PySide6 for indoor localization experiments. It lets you create building layouts, define trajectories, generate synthetic RSS/ToF measurements, run localization algorithms, and analyze their error.

Tutorials:

- English: [`docs/tutorial.md`](docs/tutorial.md)
- Spanish: [`docs/tutorial_es.md`](docs/tutorial_es.md)

---

## Requirements

- Python **>= 3.11**
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
cd indoor-loc-sim
uv sync
```

## Run

```bash
uv run indoor-loc-sim
```

---

## Current application status

The application currently has 5 main tabs:

1. **Planimetry**
2. **Trajectories**
3. **Signals**
4. **Estimation**
5. **Error Analysis**

It also includes:

- **File → Open Building / Save Building / Save Building As** (`.json`)
- **File → Open Project / Save Project / Save Project As** (`.ilsim`, with legacy `.ilsproj` read compatibility)
- **File → Settings** for global RSS model parameters
- window title showing the current file name and a `*` marker when the building has unsaved changes
- status bar messages for building, trajectory, signal, estimation, and analysis updates

---

## Recommended workflow

1. **Create the planimetry**: levels, dimensions, background image, walls, doors, and beacons.
2. **Define waypoints** and generate the ground-truth trajectory.
3. **Generate RSS or ToF signals** along that trajectory.
4. **Run localization** with the desired algorithm.
5. **Analyze errors** and compare multiple runs.
6. **Save the project** if you want to preserve building, trajectory, signals, and results.

---

## 1. Planimetry

### Features

- **Multi-level** support.
- Level dimensions defined in meters.
- Optional **floor plan image** as background.
- **Show/hide floor plan** toggle.
- **px/m scale** calibration for background images.
- Interactive drawing of:
  - beacons
  - walls
  - doors
  - rectangular rooms
- Single and rectangle selection.
- Direct selection of walls, doors, and beacons in `Select` mode.
- Delete selected items with `Delete` or `Backspace`.
- Pan, zoom, and fit-to-view.
- Temporary panning with the **middle mouse button**.
- Configurable snap-to-grid.
- Wall color editing.
- Undo.
- Planimetry changes propagate to the rest of the application.

### Parameters

| Parameter | Description |
|---|---|
| **Width (m), Height (m)** | Real level size. |
| **Floor height (m)** | Vertical spacing between floors; affects `z` on upper levels. |
| **Load Floor Plan** | Loads a background image for the level. |
| **Show floor plan** | Shows or hides the loaded image without removing it. If hidden, a white metric background/grid is shown instead. |
| **Scale (px/m)** | Pixel-to-meter ratio used to map the image to real dimensions. |
| **Label** | Beacon label. |
| **Tx Power (dBm)** | Stored transmit power for the beacon. Currently informational; the active RSS model uses global `A` as the reference power term. |
| **Snap spacing** | Snap step in meters. |

### Notes

- The planimetry tab does **not** display trajectories or estimation overlays when you switch back to it.
- If beacon positions, walls, or doors change, other tabs are refreshed when needed.
- Hidden tabs now refresh lazily after planimetry changes to keep editing responsive in large buildings.

---

## 2. Trajectories

### Features

- Define waypoints by clicking on the map.
- Generate a 2D/3D ground-truth trajectory with constant speed along path segments.
- Show the trajectory both in the trajectory tab and on the shared planimetry canvas.

### Parameters

| Parameter | Description |
|---|---|
| **Walking speed (m/s)** | Motion speed used to assign timestamps and velocities. |
| **Sampling frequency (Hz)** | Final trajectory frequency and signal generation frequency. |

### Trajectory model

Ground-truth generation works like this:

1. Start from the user-defined waypoints.
2. Spatially resample each segment using an internal step computed as:

   `dx = walking_speed / frequency`

3. Convert cumulative path length `s` into time using:

   `t = s / walking_speed`

4. Estimate velocities by finite differences.
5. Resample the full path on a uniform time grid:

   `t_k = 0, 1/f, 2/f, ..., T`

### Practical consequence

- `Sampling frequency` controls the final number of time samples.
- `Walking speed` controls the spatial distance covered between samples.

---

## 3. Signals

### Signal types available in the UI

- **RSS**
- **ToF**


### Features

- Generate synthetic beacon measurements for every trajectory point.
- Average multiple samples per trajectory point.
- Plot time-series signals with per-beacon visibility control.
- Generate **RSS heatmaps** per beacon or as a global average.
- Simulate NLoS conditions for ToF.

### Generation parameters

| Parameter | Description |
|---|---|
| **Signal type** | Signal type to generate: RSS or ToF. |
| **Samples per point** | Number of independent measurements averaged at each trajectory point. |
| **RSS σ** | RSS noise standard deviation in dB. Supports `0.0`. |
| **ToF σ (ns)** | ToF noise standard deviation in nanoseconds. |
| **A (RSSI at d₀)** | Reference RSSI at distance `d₀`. |
| **d₀ (ref. distance)** | Reference distance for the RSS model. |
| **Wall attenuation (dB)** | Extra attenuation per wall crossing. |
| **NLoS mode (ToF)** | `None`, `Increase error`, or `Skip measurement`. |
| **NLoS error multiplier** | ToF sigma multiplier for NLoS when `Increase error` is used. |
| **Path loss exponent** | `n` in the RSS log-distance model. |
| **Heatmap resolution (m)** | Spatial resolution of the RSS heatmap. |

### Generation models

#### RSS

The RSS model is a log-distance path-loss model with Gaussian noise:

```text
RSS = A - 10 * n * log10(d / d0) + N(0, sigma_rss)
```

Where:

- `A` = RSSI at reference distance `d0`
- `n` = path loss exponent
- `d` = 3D Euclidean distance between position and beacon
- `sigma_rss` = RSS noise standard deviation

Distance is clamped internally to a minimum of `0.1 m` to avoid singularities.

If walls are enabled and `Wall attenuation > 0`, the effective RSS becomes:

```text
RSS_eff = RSS - N_walls * wall_attenuation
```

#### ToF

```text
ToF = d / c + N(0, sigma_tof)
```

Where:

- `d` = 3D Euclidean distance
- `c = 3e8 m/s`
- `sigma_tof` is entered in the UI in **ns** and converted internally to seconds

#### NLoS handling for ToF

If there is no line of sight between the point and the beacon:

- **None**: no special treatment
- **Increase error**: multiply ToF sigma by `NLoS error multiplier`
- **Skip measurement**: store the measurement as `NaN`

### RSS heatmap

The heatmap uses the same RSS model as signal generation, evaluated on a 2D grid with configurable resolution.

---

## 4. Estimation

### Available algorithms

Internal names and UI labels are:

- `pos2D_EKF_RSS` → **EKF + RSS**
- `pos2D_EKF_ToF` → **EKF + ToF**
- `pos2D_EKF_RSS_Accel` → **EKF + RSS + Accel**
- `pos2D_UKF_RSS` → **UKF + RSS**
- `pos2D_Tri_ToF` → **Trilateration + ToF**
- `pos2D_Tri_RSS` → **Trilateration + RSS**
- `pos2D_Fingerprint_RSS` → **Fingerprint + RSS**

The UI automatically enables only the parameters that affect the selected algorithm.

### Estimation parameters

#### General

| Parameter | Applies to |
|---|---|
| **Process noise σ** | EKF RSS, EKF ToF, EKF RSS+Accel, UKF RSS |
| **Measurement noise σ (dB)** | EKF RSS, EKF RSS+Accel, UKF RSS |
| **Measurement noise σ (ns)** | EKF ToF |
| **Accelerometer noise variance** | EKF RSS+Accel only |

#### Fingerprinting

| Parameter | Description |
|---|---|
| **Grid spacing (m)** | Radio map grid spacing. |
| **k (neighbors)** | Base number of nearest neighbors. |
| **Auto-scale k with grid density** | Automatically scales `k` for finer grids. |
| **Samples per point** | Number of RSS samples averaged per radio map point. |
| **Distance metric** | `Euclidean`, `Manhattan`, `Cosine`, `Correlation`. |

### Models and equations

#### EKF + RSS (`pos2D_EKF_RSS`)

State vector:

```text
x = [x, y, z, vx, vy]
```

Constant-velocity prediction:

```text
x_next = [x + dt*vx, y + dt*vy, z, vx, vy]
```

Observation model:

```text
z_k = h(x_k) = predicted RSS from all beacons
```

Process noise `Q` follows a white-acceleration model and scales with `dt`.

#### EKF + ToF (`pos2D_EKF_ToF`)

Uses the same 5-state vector as EKF + RSS.

Internally it filters **ranges in meters**, not raw times:

```text
range_i = c * ToF_i
```

Observation model:

```text
h_i(x) = sqrt((x-x_i)^2 + (y-y_i)^2 + (z-z_i)^2)
```

This is a **2D** estimator with known/fixed `z`.

#### EKF + RSS + Accel (`pos2D_EKF_RSS_Accel`)

Extended state:

```text
x = [x, y, z, vx, vy, ax, ay]
```

Constant-acceleration prediction:

```text
x_next = x + dt*vx + 0.5*ax*dt^2
vx_next = vx + ax*dt
```

and similarly for `y`.

The accelerometer is simulated from the ground-truth trajectory by differentiating `vx` and `vy`, then adding Gaussian noise.

The fused measurement contains both RSS and acceleration:

```text
z_k = [RSS_1, ..., RSS_N, ax, ay]
```

#### UKF + RSS (`pos2D_UKF_RSS`)

Uses the same state as EKF + RSS, but with an Unscented Kalman Filter.

Fixed UKF parameters:

- `alpha = 1e-3`
- `kappa = 0`
- `beta = 2`

#### Trilateration + ToF (`pos2D_Tri_ToF`)

Convert ToF to distance:

```text
d_i = c * ToF_i
```

Then remove the vertical component using known `z`:

```text
d_xy_i^2 = d_i^2 - (z_i - z)^2
```

Finally solve the 2D linearized least-squares trilateration system.

Requires **at least 3 valid beacons**.

If fewer are available, the previous estimate is reused.

#### Trilateration + RSS (`pos2D_Tri_RSS`)

Convert effective RSS to distance with:

```text
d = d0 * 10^((A - RSS_eff) / (10*n))
```

At each time step it:

- always selects the **3 strongest RSS beacons**
- if fewer than 3 valid measurements exist, it reuses the previous estimate

This is the most noise-sensitive method in the application.

#### Fingerprint + RSS (`pos2D_Fingerprint_RSS`)

##### Radio map construction

Build a uniform 2D grid with spacing `Grid spacing`, then compute the average RSS vector for all beacons at each grid point using `Samples per point` samples.

##### k-NN estimation

For each observed RSS measurement, compute its distance to all radio map fingerprints:

```text
d_i = cdist(RSS_obs, RSS_i)
```

Select the `k` nearest neighbors and estimate position by weighted averaging:

```text
w_i = 1 / (d_i + 1e-10)
p_hat = sum(w_i * p_i) / sum(w_i)
```

If `Auto-scale k` is enabled, the effective neighbor count becomes:

```text
k_eff = max(k, round((2.0 / grid_spacing)^2 * k))
```

This keeps the averaged spatial support roughly stable for finer grids.

### Overlays and results

- The radio map can be displayed over the floor plan.
- Hovering over estimated points highlights the radio map neighbors used in that estimate.

---

## 5. Error Analysis

### Features

- Compare multiple simulation runs at once.
- Show/hide individual runs.
- Summary metrics:
  - mean error
  - P50
  - P90
  - maximum error
- CSV export.

### Available plots

- **CDF of Errors**
- **Error over Time**
- **X Error over Time**
- **Y Error over Time**

All error values are reported in **meters**.

---

## Project persistence

### Current format

Projects are saved as **`.ilsim`** ZIP archives.

Persisted content:

- `building.json`
- `waypoints.json`
- `ground_truth.json`
- `beacon_signals.json`
- `simulation_runs.json`
- floor plan images inside `images/`

When a project is loaded, the application restores:

- building and levels
- background images
- waypoints
- ground-truth trajectory
- generated signals
- previous simulation runs, including analysis data and fingerprint overlays

Legacy `.ilsproj` files can also be read.

### Save behavior in the UI

- If you open a building with **Open Building...**, that `.json` becomes the current file:
  - **Save Building** overwrites the same file
  - **Save Building As...** writes a copy to another path
- If you open a project with **Open Project...**, that `.ilsim` becomes the current file:
  - **Save Project** overwrites the same file
  - **Save Project As...** writes a copy to another path
- If the building has unsaved changes, the window title shows a `*`
- When closing the application with unsaved changes, the app offers:
  - **Save**
  - **Cancel**
  - **Exit without saving**

---

## Global settings

The **Settings** dialog lets you edit the global RSS model parameters:

| Parameter | Meaning |
|---|---|
| **A (RSSI at d₀)** | RSSI at the reference distance. |
| **d₀ (ref. distance)** | Reference distance for the log-distance model. |
| **n (path-loss exp.)** | RSS path-loss exponent. |
| **σ (shadowing noise)** | RSS noise standard deviation. |
| **Wall attenuation** | Per-wall attenuation. |

These values are used by signal generation, and the estimation tab reads the relevant RSS model parameters from there. In particular, the **path-loss exponent is no longer configured separately in estimation**.

---

## Important notes

- All `pos2D_*` algorithms currently estimate in **2D**; `z` is treated as known/fixed.
- `Tri RSS` is the most fragile method because RSS-to-distance conversion is highly sensitive to noise and model mismatch.
- `EKF ToF` uses distances in meters internally even though the ToF noise is entered in nanoseconds in the UI.
- `EKF RSS + Accel` does not use a real IMU; it simulates acceleration from the ground-truth trajectory with Gaussian noise.
- Changing the planimetry after generating signals or simulations can make those old signals/results inconsistent with the new layout.
- Trajectories, Signals, and Estimation tabs now refresh lazily after planimetry edits to keep large-scene editing responsive.

---

## Project structure

```text
src/indoor_loc_sim/
├── core/
│   ├── models.py
│   ├── project_io.py
│   └── trajectory.py
├── engine/
│   ├── analysis.py
│   ├── fingerprint.py
│   ├── geometry.py
│   ├── signals.py
│   └── tracking.py
├── gui/
│   ├── main_window.py
│   ├── state.py
│   ├── tabs/
│   │   ├── analysis_tab.py
│   │   ├── estimation_tab.py
│   │   ├── planimetry_tab.py
│   │   ├── signal_tab.py
│   │   └── trajectory_tab.py
│   └── widgets/
│       ├── floor_plan_canvas.py
│       └── settings_dialog.py
└── main.py
```

---

## Main dependencies

- **PySide6** — GUI
- **NumPy** — numerical computation
- **SciPy** — interpolation and fingerprinting distances
- **Matplotlib** — plots and visualization

---

## Platforms

The application is intended to run on:

- Linux
- Windows
- macOS

---

## Additional resources

- English tutorial: `docs/tutorial.md`
- Spanish tutorial: `docs/Tutorial_es.md`
