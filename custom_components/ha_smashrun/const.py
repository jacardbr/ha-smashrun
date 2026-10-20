"""Constants for the Smashrun integration."""

DOMAIN = "ha_smashrun"

# Personal (user-level) access token: Smashrun's implicit OAuth flow with the
# public client id "client". The token lasts about 60 days and cannot be
# refreshed, so the user pastes a new one when it expires.
TOKEN_URL = (
    "https://secure.smashrun.com/oauth2/authenticate"
    "?client_id=client&scope=read_activity&response_type=token"
    "&redirect_uri=urn:ietf:wg:oauth:2.0:oob"
)

CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 30  # minutes
MIN_SCAN_INTERVAL = 5  # minutes
MAX_SCAN_INTERVAL = 1440  # minutes

# Full re-read of the run list and badges even if the latest run did not
# change, to pick up edits and deletions of older runs.
FULL_REFRESH_HOURS = 24

# Badges listed in the attributes of the badge count sensor.
RECENT_BADGES = 10
