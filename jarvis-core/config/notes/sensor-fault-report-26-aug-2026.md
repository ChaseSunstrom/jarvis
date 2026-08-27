---
title: Sensor Fault Report — 26 Aug 2026
tags:
- sensors
- diagnostics
- fault
created: '2026-08-27T04:51:43+00:00'
updated: '2026-08-27T04:51:43+00:00'
---

# Sensor Fault Report

**Date:** Wednesday 26 August 2026
**Scope:** All 11 `sensor.*` entities in the house. Read-only analysis of current state and 24 h history.

## Confirmed faults

| Entity | Fault type | Evidence |
|--------|-----------|----------|
| `sensor.outside_temperature` | Zero variance / stuck | 151 samples, min = max = 15.6 °C, 0 changes in 24 h. Real outdoor temperature does not hold one value for a full day. |
| `sensor.outside_humidity` | Zero variance / stuck | 151 samples, min = max = 54 %, 0 changes in 24 h. Frozen on a cached reading. |
| `sensor.power_consumption` | Zero variance / stuck | 151 samples, min = max = 412 W, 0 changes in 24 h. A single kitchen circuit *could* be flat, but exactly 412 W with no movement for a day is a frozen reading, not a live one. |
| `sensor.feels_like_outside` | No data / offline | 0 samples in 24 h, current value `unknown`. Not publishing at all. |
| `sensor.house_power` | No data / offline | 0 samples in 24 h, current value `unknown`. Not publishing at all. |
| `sensor.ollama_loaded_model` | Degraded / unavailable | 1 sample, value "unavailable" across the whole window. Source not reporting. |
| `sensor.jarvis_uptime` | No history | 0 samples in the window. Uptime is a counter so flatness is expected, but the absence of any stored history means it is not being recorded. |

## Suspect (borderline)

| Entity | Note |
|--------|------|
| `sensor.garage_temperature` | Flat once reporting (min = max = 12.5 °C) after starting from `unknown`. A garage can sit near-constant, so flagged as suspect rather than confirmed faulty. |

## Healthy / not faulty

- `sensor.model_server_models` — "house", near-constant; a model name is not expected to vary.
- `sensor.disk_free` — 95 changes, range 3.4–13.1 GB; genuinely live.
- `sensor.load_average` — 346 changes, range 0.11–11.87; genuinely live.

## Nature of the errors

- **No impossible ranges surfaced.** Every anomaly is either *zero-variance* (a frozen reading) or *missing data* (no samples / `unknown` / "unavailable"). There are no out-of-bounds values.
- The three frozen sensors (outside_temperature, outside_humidity, power_consumption) share the same signature: a large sample count with min = max and zero changes — the classic "last value, never updated" failure.
- The three no-data sensors (feels_like_outside, house_power, jarvis_uptime) return zero samples in the window, indicating they are offline or not publishing rather than merely flat.

## Full inventory (11 sensors)

power_consumption, disk_free, feels_like_outside, garage_temperature, house_power, jarvis_uptime, load_average, model_server_models, ollama_loaded_model, outside_humidity, outside_temperature.
