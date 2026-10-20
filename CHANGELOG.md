# Changelog

## 0.5.0

- Card, **Personal Records → Performance**: clicking a tile shows the run behind the record below the tiles (date and time, location, distance, time, pace and its route drawn from the GPS track over Esri's Light Gray or Dark Gray Canvas map, following the theme, which the new `route_map` card option turns off); clicking it again hides it. The records now also carry the run's total time. The location comes from the new `ha_smashrun/run` WebSocket command, which reads the run from Smashrun once (`GET /my/activities/{id}`) and caches its location and its GPS track thinned to 400 points.
- Card, **Personal Records → Performance**: a record is now the run's real time and pace, as raced (a 42.7 km marathon in 3:53:46 is a 3:53:46 42K), instead of its pace scaled to the exact distance.
- Card, **Personal Records → Performance**: a record now only comes from runs of that distance's range (the Count tab's ranges, configurable in the card) that are at least the distance (1% GPS margin). A half marathon no longer counts as a 10K record.
- **Sign in with email and password**: the setup now offers it beside pasting a token. Home Assistant gets the personal access token itself and a new one 7 days before it expires (or when Smashrun rejects it), so tokens no longer need pasting. A failed login opens a reauthentication asking for the password. **Reconfigure** switches between the two ways. Uses the login service behind Smashrun's authorization page (not part of the documented API). Diagnostics redact the email and password. A token from the login is taken to last at most 60 days (Smashrun's personal tokens), even when the login answers with a longer lifetime, so it is renewed at most 53 days after it was obtained; the **Token expires** sensor shows that date (the daily token check is ignored when it answers zero or less), and an early renewal happens at most once every 12 hours.

## 0.4.0

- Card, **Personal Records** tab: two views, **Performance** (the fastest 5K, 10K, 21K and 42K, as before) and the new **Volume**: the most kilometres in one day, week, month and year of the selected years, each with its year tag. The integration now also sends the biggest day of each year.
- Card, **Personal Records** tab: the four tiles sit on one line (scrolling sideways on very narrow cards).
- Card: the explanatory notes under the **Count** and **Personal Records** tiles were removed (the README keeps the explanations).
- Card: the **Personal records** tab is now **Personal Records**.
- Card, **Count** tab: the tiles no longer show the km range, and each bar is the range's share of the total (the Total bar is full).
- Card: the year filter sits lower and stays on one line, scrolling sideways when it does not fit. **All** and **None** are now one button: it reads All, and None once every year is selected. Selected years (and the All/None button) have their text and border in Home Assistant's blue on a light theme, and in the color of the tabs not selected on a dark theme, instead of the year's color.

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
