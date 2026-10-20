# Changelog

## 0.2.0

- Setup option **Import the whole run history** (checked by default): fills the long-term statistics of the history sensors (total distance and runs, distance and runs this week/month/year) back to the first run, in the background.
- **Configure** is now a menu: **Change update interval** and **Import history now**.
- New action `ha_smashrun.import_history`.
- **Lovelace card** `custom:ha-smashrun-card`, loaded automatically, with three tabs: **Statistics** (distance year over year, cumulative or monthly, with year filters and a year-over-year table), **Personal records** (5K, 10K, 21K, 42K, filtered by year chips like the Statistics tab) and **Count** (number of runs per distance range, <5K to 42K, plus a total, filtered by year). Every year filter has **All** and **None** buttons.

## 0.1.0

- First version: personal access token, last run sensors, history sensors, runs calendar, external statistics with the whole history, badge count, icon and logo.
