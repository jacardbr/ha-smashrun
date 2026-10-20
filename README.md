# Smashrun for Home Assistant

Custom integration (HACS) that brings your **Smashrun** runs into Home Assistant: your last run (distance, duration, pace, heart rate, cadence, elevation), your whole run history (totals, weekly/monthly/yearly distance, a calendar of every run and long-term statistics back to your first run) and your badges.

It uses the official [Smashrun API](https://api.smashrun.com/) with a **personal access token**, read-only (`read_activity` scope). No API key or password is needed.

## How it works

Every 30 minutes (configurable) Home Assistant asks Smashrun for your latest run. Only when a new run shows up (and once a day, to pick up edits and deletions) does it read the full run list and your badges again. A run appears in Home Assistant after it has synced to Smashrun.

## Getting a personal access token

1. Sign in to <https://smashrun.com>.
2. Open this address: <https://secure.smashrun.com/oauth2/authenticate?client_id=client&scope=read_activity&response_type=token&redirect_uri=urn:ietf:wg:oauth:2.0:oob> (the setup form has the same link).
3. Allow access. Smashrun shows the access token, or puts it in the address bar after `access_token=`.
4. Copy it. You can paste the token alone or the whole address: the integration extracts the token.

Personal tokens **expire after about 60 days** and cannot be renewed automatically (Smashrun only allows that for registered apps). When the token expires, Home Assistant shows a **Reauthenticate** notice for the integration: repeat the steps above and paste the new token. To swap the token earlier, use **Settings → Devices & services → Smashrun → ⋮ → Reconfigure**. The **Token expires** sensor tells you when the current one stops working, so you can set up a reminder. Personal tokens are also limited to 250 API requests per hour, far above what the integration uses.

## Installation (HACS)

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jacardbr&repository=ha-smashrun&category=integration)

1. Click the button above, or add it by hand: **HACS → ⋮ → Custom repositories**, paste `https://github.com/jacardbr/ha-smashrun`, category **Integration**, then **Add**.
2. Search for **Smashrun** in HACS and download it.
3. **Restart Home Assistant.**
4. Go to **Settings → Devices & services → Add integration → Smashrun** and paste your access token.

Manual installation (without HACS): copy `custom_components/ha_smashrun` to `<your /config>/custom_components/ha_smashrun` and restart.

## Options

**Configure** on the integration sets the update interval (5 to 1440 minutes, default 30).

## Entities

### Last run

| Entity | Unit | Notes |
|---|---|---|
| Last run | date/time | Start time. Attributes: `run_id`, `url`, `duration` (h:mm:ss), `pace_per_km`, `pace_per_mile`, `end`, and `raw` (every field the API returned) |
| Last run distance | km | |
| Last run duration | min | |
| Last run pace | min/km or min/mi | Follows your Smashrun distance unit. The `pace` attribute has it as m:ss |
| Last run speed | km/h | Disabled by default |
| Last run average heart rate | bpm | Only if the run has heart rate data |
| Last run max heart rate | bpm | |
| Last run cadence | spm | |
| Last run elevation gain | m | |

Distances and speeds are converted to miles automatically when Home Assistant uses US customary units.

### History

| Entity | Unit | Notes |
|---|---|---|
| Total distance | km | All runs ever |
| Total runs | runs | |
| Distance this week / month / year | km | The week starts on Monday |
| Runs this week / month / year | runs | Week and month disabled by default |
| **Runs** (calendar) | | One event per run, from start to finish, with distance, time, pace and a link to the run on Smashrun |

The calendar shows every run you ever logged, so you can browse your history in the Home Assistant calendar or use it in automations.

### Badges

| Entity | Notes |
|---|---|
| Badges | Number of badges earned. The `recent_badges` attribute lists the last 10 |
| Latest badge | Name of the most recent badge, with its image as the entity picture |

The badges endpoint is listed by Smashrun but its response format is not documented, so these two sensors read it defensively. If the endpoint is not available for your token, they show as unavailable and the rest of the integration keeps working.

### Token

| Entity | Notes |
|---|---|
| Token expires | When the personal access token stops working (diagnostic). Checked once a day |

Example reminder a week before:

```yaml
automation:
  - alias: Smashrun token expiring
    triggers:
      - trigger: template
        value_template: >
          {{ states('sensor.smashrun_token_expires') not in ['unknown', 'unavailable']
             and as_datetime(states('sensor.smashrun_token_expires')) - now() < timedelta(days=7) }}
    actions:
      - action: persistent_notification.create
        data:
          title: Smashrun
          message: The Smashrun token expires in less than a week. Get a new one and use Reconfigure on the integration.
```

(Adjust the `entity_id` to the one shown in your Home Assistant.)

## Long-term statistics (full history)

The integration writes two statistics with your **whole** history, from your first run:

- `ha_smashrun:distance_<your Smashrun id>`: distance (km)
- `ha_smashrun:runs_<your Smashrun id>`: number of runs

They are rebuilt whenever the run list changes. Use them with a **Statistics graph** card, for example the distance per week:

```yaml
type: statistics-graph
title: Distance per week
chart_type: bar
period: week
stat_types:
  - change
entities:
  - ha_smashrun:distance_123456
days_to_show: 365
```

Use `period: month` or `period: year` for other views. To find your id, open the card editor and search for "Smashrun", or look at **Developer tools → Statistics**.

## Diagnostics

**Settings → Devices & services → Smashrun → ⋮ → Download diagnostics** gives the raw API payloads of the last run, the first runs and the first badges (the token and names are redacted). Include it when reporting a problem.

## Development

The API client and the run helpers have tests that run with the standard library only:

```sh
python3 -m unittest discover -s tests -v
```
