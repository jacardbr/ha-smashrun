"""Constants for the Smashrun integration."""

DOMAIN = "ha_smashrun"

# Personal (user-level) access token: Smashrun's implicit OAuth flow with the
# public client id "client". The token lasts about 60 days and cannot be
# refreshed: the user pastes a new one when it expires, or, when the email and
# password are saved, the integration gets a new one itself.
TOKEN_URL = (
    "https://secure.smashrun.com/oauth2/authenticate"
    "?client_id=client&scope=read_activity&response_type=token"
    "&redirect_uri=urn:ietf:wg:oauth:2.0:oob"
)

# With the email and password saved, a new token is fetched this long before
# the current one expires (and whenever Smashrun rejects it).
TOKEN_RENEW_BEFORE_DAYS = 7
# Personal tokens last 60 days: a token from the login is taken to expire 60
# days after it was obtained, or earlier if Smashrun says so (the login has
# answered with longer lifetimes, which are not trusted).
TOKEN_MAX_DAYS = 60
# Never renew more often than this (guards against a wrong expiry date).
TOKEN_RENEW_MIN_HOURS = 12
# Saved in the entry with the token: when it expires (ISO, from the login's
# expires_in) and when the integration last got a new one.
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
CONF_TOKEN_RENEWED_AT = "token_renewed_at"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_IMPORT_HISTORY = "import_history"  # one-shot flag set by the setup form
# Per entity: first hour owned by the recorder (the import writes before it).
CONF_STATS_CUTOFFS = "statistics_cutoffs"

DEFAULT_SCAN_INTERVAL = 30  # minutes
MIN_SCAN_INTERVAL = 5  # minutes
MAX_SCAN_INTERVAL = 1440  # minutes

# Full re-read of the run list and badges even if the latest run did not
# change, to pick up edits and deletions of older runs.
FULL_REFRESH_HOURS = 24

# Badges listed in the attributes of the badge count sensor.
RECENT_BADGES = 10
