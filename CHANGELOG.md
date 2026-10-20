# Changelog

## 0.3.0

- Card, **Statistics** tab: the last **2** years are selected at first (was 3).
- Card, **Count** tab: only the **latest year** is selected at first (was every year).
- Card, **Statistics** tab: the year table lists only the selected years, and the "vs. previous" column is now **Comparison**: a green ▲ or red ▼ difference in km against the next older year shown, the oldest one shown being the baseline. The year in progress is compared with the same months, without the asterisk and its note.
- Card, **Statistics** tab: hovering over the chart (or tapping it) shows a guide on the month and a tooltip with each selected year's distance (newest first) and a green ▲ or red ▼ with the difference against the next older year shown (the oldest one shown is the baseline).
- Card, **Statistics** tab: new **Volume** view, beside Cumulative and Monthly, with bar charts of the distance per week (ISO weeks), month and year of the selected years, each bar with its value above it, scrolling sideways when wider than the card (opening at the current week and month), and the same tooltip, opened by clicking a column.
- The card's URL now also carries the file's modification time, so browsers load the new card after an update even when the version did not change. The integration now also sends the distance per ISO week.
- Card, **Statistics** tab: the year table is hidden at first, behind a **Detail** button below the charts (in every view).
- Card, **Count** tab: only ranges with runs in the selected years are shown.
- Card options for the **years selected at first** in each tab (`statistics_years`, `records_years`, `count_years`) and for the **count ranges** (`range_5k`, `range_10k`, `range_21k`, `range_42k`, `range_max`), also in the card editor.
- Card, **Personal records** tab: each record shows a **year tag** in the year's color instead of the date. The tag links to the run, and the date shows on hover.

## 0.2.0

- Setup option **Import the whole run history** (checked by default): fills the long-term statistics of the history sensors (total distance and runs, distance and runs this week/month/year) back to the first run, in the background.
- **Configure** is now a menu: **Change update interval** and **Import history now**.
- New action `ha_smashrun.import_history`.
- **Lovelace card** `custom:ha-smashrun-card`, loaded automatically, with three tabs: **Statistics** (distance year over year, cumulative or monthly, with year filters and a year-over-year table), **Personal records** (5K, 10K, 21K, 42K, filtered by year chips like the Statistics tab) and **Count** (number of runs per distance range, <5K to 42K, plus a total, filtered by year). Every year filter has **All** and **None** buttons.

## 0.1.0

- First version: personal access token, last run sensors, history sensors, runs calendar, external statistics with the whole history, badge count, icon and logo.
