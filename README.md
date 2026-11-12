# Downlink Simulation

## 1. Environment Setup

Use Python 3.12. Install the required dependencies:

```bash
python -m pip install -r requirements.txt
```

## 2. Input Data

Prepare the following UTF-8 JSON files in the `input/` directory:

| File | Description |
|---|---|
| `tle.json` | Satellite TLE records containing `EPOCH`, `TLE_LINE1`, and `TLE_LINE2` |
| `weather.json` | Daily meteorological data for the Beijing area, including temperature, pressure, relative humidity, precipitation, and wind speed |

Weather data must cover January 1, 2025, through January 1, 2026, inclusive, indexed by Beijing local dates (`YYYYMMDD`).

TLE records can be retrieved from Space-Track. Set the `SPACETRACK_USER` and `SPACETRACK_PASS` environment variables, then run:

```bash
python download_tle.py --format json --output input/tle.json
```

## 3. Running the Simulation

### Full-Year Simulation

```bash
python simulate.py --archive input/tle.json --weather input/weather.json --output-dir results
```

### Single-Day Simulation

```bash
python simulate.py --archive input/tle.json --weather input/weather.json --date 20250101
```

Single-day mode evaluates the specified UTC date and displays the results without writing output files.

## 4. Output Files

Full-year simulation results are saved in the `results/` directory:

| Output Path | Description |
|---|---|
| `daily/` | Daily simulation records in JSON format |
| `daily.csv` | Consolidated daily simulation results |
| `summary.json` | Annual summary statistics |

Data volumes are reported in MiB.

## 5. Model Configuration

Link parameters are defined in `link_model.py` and can be adjusted as needed.

For additional command-line options:

```bash
python simulate.py --help
python download_tle.py --help
```
