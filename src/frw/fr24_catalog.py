"""Reviewed Flightradar24 API catalog for optional on-demand lookups."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ApiEndpoint:
    key: str
    title: str
    path: str
    fields: tuple[tuple[str, str, bool], ...]
    credits: str
    description: str


ENDPOINTS = (
    ApiEndpoint("live_full", "Live positions · full", "/live/flight-positions/full",
                (("bounds", "Bounds (N,S,W,E)", True), ("limit", "Maximum results (1–5000)", False)),
                "8 credits per returned flight; empty result: 1 credit",
                "All live fields available to your API subscription."),
    ApiEndpoint("live_light", "Live positions · light", "/live/flight-positions/light",
                (("bounds", "Bounds (N,S,W,E)", True), ("limit", "Maximum results (1–5000)", False)),
                "6 credits per returned flight; empty result: 1 credit", "Core live position fields."),
    ApiEndpoint("live_count", "Live positions · count", "/live/flight-positions/count",
                (("bounds", "Bounds (N,S,W,E)", True),), "15% of corresponding full query, rounded up",
                "Returns a count, not aircraft records."),
    ApiEndpoint("historic_full", "Historic positions · full", "/historic/flight-positions/full",
                (("timestamp", "Unix timestamp (UTC)", True), ("bounds", "Bounds (N,S,W,E)", True),
                 ("limit", "Maximum results (1–5000)", False)),
                "8 credits per returned flight; empty result: 1 credit", "Positions at one historic timestamp."),
    ApiEndpoint("historic_light", "Historic positions · light", "/historic/flight-positions/light",
                (("timestamp", "Unix timestamp (UTC)", True), ("bounds", "Bounds (N,S,W,E)", True),
                 ("limit", "Maximum results (1–5000)", False)),
                "6 credits per returned flight; empty result: 1 credit", "Positions at one historic timestamp."),
    ApiEndpoint("historic_count", "Historic positions · count", "/historic/flight-positions/count",
                (("timestamp", "Unix timestamp (UTC)", True), ("bounds", "Bounds (N,S,W,E)", True)),
                "15% of corresponding full query, rounded up", "Count at one historic timestamp."),
    ApiEndpoint("events_full", "Historic events · full", "/historic/flight-events/full",
                (("flight_ids", "FR24 flight IDs (comma-separated, max 15)", True),
                 ("event_types", "Event types (comma-separated or all)", True),
                 ),
                "3–4 credits per returned event; empty result: 1 credit", "Query one to 15 FR24 flight IDs; the provider supports no result-limit parameter."),
    ApiEndpoint("events_light", "Historic events · light", "/historic/flight-events/light",
                (("flight_ids", "FR24 flight IDs (comma-separated, max 15)", True),
                 ("event_types", "Event types (comma-separated or all)", True),
                 ),
                "2–3 credits per returned event; empty result: 1 credit", "Query one to 15 FR24 flight IDs; the provider supports no result-limit parameter."),
    ApiEndpoint("summary_full", "Flight summary · full", "/flight-summary/full",
                (("flight_ids", "FR24 flight IDs (max 15)", False),
                 ("flight_datetime_from", "From (UTC ISO date/time)", False),
                 ("flight_datetime_to", "To (UTC ISO date/time)", False),
                 ("painted_as", "Painted-as airline ICAO codes", False),
                 ("operating_as", "Operating airline ICAO codes", False),
                 ("flights", "Flight numbers (optional filter)", False),
                 ("callsigns", "Callsigns (optional filter)", False),
                 ("registrations", "Registrations (optional filter)", False),
                 ("airports", "Airport or route filter", False),
                 ("aircraft", "Aircraft ICAO type codes", False),
                 ("routes", "Route codes", False),
                 ("limit", "Maximum results (1–5000)", False)),
                "2 credits live; 3–6 historic per returned flight; empty: 1", "Detailed flight summary; subscription access varies."),
    ApiEndpoint("summary_light", "Flight summary · light", "/flight-summary/light",
                (("flight_ids", "FR24 flight IDs (max 15)", False),
                 ("flight_datetime_from", "From (UTC ISO date/time)", False),
                 ("flight_datetime_to", "To (UTC ISO date/time)", False),
                 ("painted_as", "Painted-as airline ICAO codes", False),
                 ("operating_as", "Operating airline ICAO codes", False),
                 ("flights", "Flight numbers (optional filter)", False),
                 ("callsigns", "Callsigns (optional filter)", False),
                 ("registrations", "Registrations (optional filter)", False),
                 ("airports", "Airport or route filter", False),
                 ("aircraft", "Aircraft ICAO type codes", False),
                 ("routes", "Route codes", False),
                 ("limit", "Maximum results (1–5000)", False)),
                "1 credit live; 2–3 historic per returned flight; empty: 1", "Core flight summary; subscription access varies."),
    ApiEndpoint("summary_count", "Flight summary · count", "/flight-summary/count",
                (("flight_ids", "FR24 flight IDs (max 15)", False),
                 ("flight_datetime_from", "From (UTC ISO date/time)", False),
                 ("flight_datetime_to", "To (UTC ISO date/time)", False),
                 ("painted_as", "Painted-as airline ICAO codes", False),
                 ("operating_as", "Operating airline ICAO codes", False),
                 ("flights", "Flight numbers (optional filter)", False),
                 ("callsigns", "Callsigns (optional filter)", False),
                 ("registrations", "Registrations (optional filter)", False),
                 ("airports", "Airport or route filter", False),
                 ("aircraft", "Aircraft ICAO type codes", False),
                 ("routes", "Route codes", False)),
                "15% of corresponding full query, rounded up", "Flight summary count."),
    ApiEndpoint("tracks", "Flight tracks", "/flight-tracks",
                (("flight_id", "FR24 flight ID", True),), "40 credits per returned flight",
                "Track by FR24 flight ID, not callsign or Mode-S hex."),
    ApiEndpoint("airports_light", "Airport details · light", "/static/airports/{code}/light",
                (("code", "Airport ICAO/IATA code", True),), "1 credit per query", "Airport reference fields."),
    ApiEndpoint("airports_full", "Airport details · full", "/static/airports/{code}/full",
                (("code", "Airport ICAO/IATA code", True),), "50 credits per query", "Expanded airport and runway fields."),
    ApiEndpoint("airlines_light", "Airline details · light", "/static/airlines/{code}/light",
                (("code", "Airline ICAO code", True),), "1 credit per query", "Airline reference fields."),
    ApiEndpoint("usage", "API usage", "/usage", (("period", "Usage period", False),),
                "Usage endpoint: at most once per minute", "Check credits used in your account."),
)

BY_KEY = {endpoint.key: endpoint for endpoint in ENDPOINTS}
MAX_RESPONSE_CHARS = 8 * 1024 * 1024


def make_query(endpoint_key, values):
    """Build a bounded query using only the selected reviewed endpoint fields."""
    endpoint = BY_KEY.get(endpoint_key)
    if endpoint is None:
        raise ValueError("Choose a supported API endpoint.")
    allowed = {key for key, _label, _required in endpoint.fields}
    params = {}
    for key, value in values.items():
        if key not in allowed:
            raise ValueError("This query contains an unsupported field.")
        text = str(value).strip()
        if text:
            if len(text) > 200:
                raise ValueError("Keep each query field to 200 characters or fewer.")
            params[key] = text
    if "period" in params and params["period"] not in {"24h", "7d", "30d", "1y"}:
        raise ValueError("Choose a supported usage period: 24h, 7d, 30d or 1y.")
    for key, label, required in endpoint.fields:
        if required and not params.get(key):
            raise ValueError(f"Enter {label.lower()}.")
    if endpoint.key in {"historic_full", "historic_light", "historic_count"}:
        if not params.get("timestamp", "").isdigit():
            raise ValueError("Timestamp must be a Unix time in seconds.")
    if "bounds" in params:
        try:
            north, south, west, east = (float(value.strip()) for value in params["bounds"].split(","))
        except (TypeError, ValueError) as exc:
            raise ValueError("Bounds must be four numbers: north,south,west,east.") from exc
        if not (-90 <= south < north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
            raise ValueError("Bounds must use valid coordinates with north above south.")
    if endpoint.key in {"events_full", "events_light", "summary_full", "summary_light", "summary_count"}:
        ids = bool(params.get("flight_ids"))
        start = bool(params.get("flight_datetime_from"))
        end = bool(params.get("flight_datetime_to"))
        if endpoint.key.startswith("events_"):
            if len(params["flight_ids"].split(",")) > 15:
                raise ValueError("Enter at most 15 FR24 flight IDs.")
        elif ids == (start or end) or start != end:
            raise ValueError("Choose FR24 flight IDs or both UTC date/time bounds and an airport filter.")
        if ids and len(params["flight_ids"].split(",")) > (15 if endpoint.key.startswith(("summary_", "events_")) else 10):
            raise ValueError("Enter at most 15 FR24 flight IDs.")
        if endpoint.key.startswith("events_"):
            event_types = {"gate_departure", "takeoff", "cruising", "airspace_transition",
                           "descent", "landed", "diverting", "diverted", "gate_arrival"}
            requested = {item.strip().lower() for item in params["event_types"].split(",")}
            if requested != {"all"} and (not requested or not requested <= event_types):
                raise ValueError("Use documented event types or 'all'.")
        elif not ids and start and end:
            start_at = _parse_utc(params["flight_datetime_from"])
            end_at = _parse_utc(params["flight_datetime_to"])
            if end_at <= start_at:
                raise ValueError("Use an increasing UTC date/time range.")
            if (end_at - start_at).total_seconds() > 14 * 86400:
                raise ValueError("Flight summary date ranges cannot exceed 14 days per query.")
            if not any(params.get(key) for key in ("flights", "registrations", "callsigns", "painted_as", "operating_as", "airports", "aircraft", "routes")):
                raise ValueError("Date ranges need at least one flight, operator, aircraft or route filter.")
        for key in ("flight_ids", "flights", "callsigns", "registrations", "painted_as", "operating_as", "airports", "aircraft", "routes"):
            if key in params and len(params[key].split(",")) > 15:
                raise ValueError(f"Enter no more than 15 values for {key}.")
    if endpoint.key.startswith("airports_"):
        code = params.pop("code").upper()
        if not code.isalnum() or len(code) not in (3, 4):
            raise ValueError("Enter a three-character IATA or four-character ICAO airport code.")
        path = endpoint.path.replace("{code}", code)
        return path, params
    if endpoint.key == "airlines_light":
        code = params.pop("code").upper()
        if not code.isalnum() or len(code) != 3:
            raise ValueError("Enter a three-character airline ICAO code.")
        return endpoint.path.replace("{code}", code), params
    for key in ("limit",):
        if key in params:
            try:
                limit = int(params[key])
            except ValueError as exc:
                raise ValueError("Result limit must be a whole number.") from exc
            if not 1 <= limit <= 5000:
                raise ValueError("Result limit must be between 1 and 5000.")
            params[key] = str(limit)
    return endpoint.path, params


def _parse_utc(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("Use UTC date/time in the form YYYY-MM-DDTHH:MM:SSZ.") from exc
