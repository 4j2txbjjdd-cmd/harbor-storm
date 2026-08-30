# Live weather

Harbor reads external weather through one boundary, `WeatherProvider`, and there
are exactly two implementations of it:

| lane | provider | `name` | used by |
|---|---|---|---|
| deterministic | `MockWeatherProvider` | `seeded-fixture` | tests, `app.demo`, `app.gate`, replay |
| live | `GoogleWeatherProvider` | `google-weather-live` | `app.live_gate` |

The live lane is an **addition**. The seeded lane is what the demos and the six
hard gates run on — five mechanically checked and one manual demo-legibility
gate — and it must stay offline: a gate that depends on today's weather is not a
gate.

## Which lane, and who says so

The lane is chosen in exactly one place — `app.config.Settings` reading
`WEATHER_PROVIDER` — and asserted in another. `app.live_gate --provider live`
does not *select* live; it states what the caller believes configuration
produced, and refuses to run if the two disagree. Two independent statements
that must agree is what keeps a failed live run from being reported as a
passing seeded one.

There is deliberately no `auto`. `Settings` already refuses `WEATHER_PROVIDER=google`
with no key, `GoogleWeatherProvider` raises on HTTP failure, on an unknown site
and on an empty forecast, and nothing anywhere catches `WeatherProviderError` to
carry on with a fixture. An empty forecast reads downstream as "no severe
weather", which would approve exactly the plan this system exists to reject, so
absence fails at the boundary rather than becoming fair weather.

## Run the live gate

```bash
WEATHER_PROVIDER=google GOOGLE_WEATHER_API_KEY=... \
  .venv/bin/python -m app.live_gate --provider live
```

`--scenario harborwindow|stormslot` narrows it; `--json` and `--out FILE` give
the full evidence. Exit 0 = every check passed, 1 = a check failed, 2 = the run
was blocked and **nothing was proven**.

## What the gate checks

Not the weather. Live weather changes, so asserting values would make the gate
fail on a calm day and pass for the wrong reason on a rough one. It checks that
the decision and the observations agree:

1. **provider lane is live** — the object handing out observations is the live
   adapter, not a fixture wearing a flag.
2. **request reached the API** — provenance shows real HTTP 200s, real
   timestamps, real point counts, for every site the scenario needed.
3. **normalised** — those responses became `WeatherPoint`s keyed by clock hour.
4. **scenario consumed them** — the severe hours *on the trace* equal the severe
   hours recomputed from the live series. Not that a provider existed: that its
   output drove the record. The evidence also carries the observed maxima next
   to the configured limits, because on a calm day `severe_hours=[]` equals
   `[]` whatever produced it, and the numbers are what make that informative.
5. **outcome is consistent** — a committed plan's exposed hours are covered by
   the live forecast and none of them is severe; an uncommitted run has a
   recorded reason for every candidate. Both are legitimate outcomes.

## The two scenarios read weather differently, and that is preserved

- **StormSlot** asks one question of one site: is rain at or above
  `storm_threshold_rain_mm` on the road, over `transit_hours`.
- **HarborWindow** asks two questions of two sites: is wind over `max_wind_kph`
  or rain over `max_rain_mm` at *either* end, over `crossing_hours`.

`app/live_gate.py` keeps a separate lens per scenario that calls the scenario's
own code. A single shared "severe hours" helper would make the gate agree with
itself rather than with the scenarios.

## Provenance, and what it is not

Every successful fetch appends a `FetchRecord`: site, coordinates, endpoint,
request timestamp, HTTP status, hours requested, points returned, the response's
time zone, and the first and last forecast interval. `provider.provenance()`
returns them; an empty list means no request left the process.

It is **not** an event. Weather is an external fact source, so where a fact came
from is provenance about an input, not a transition in the operation — putting
HTTP status codes into the authoritative vocabulary would make the trace argue
about its own plumbing. The gate reads provenance from the provider and reports
it alongside the run. *(Consequence worth knowing: a Firestore trace alone does
not say which lane produced it. Recording the lane on the measurement event is
a reasonable future change and a deliberate non-goal here.)*

The API key is a query parameter, so the request URL is a secret. It never
appears in a `FetchRecord`, an exception message or a log line; failures name
the coordinates, which are not secret. The `FetchRecord` half of that is
asserted mechanically:
`tests/test_weather_provider.py::test_provenance_never_carries_the_key` fetches
under a known key value and fails if either that value or a `key=` fragment
survives into serialised provenance.

## The clock-hour horizon

`WeatherPoint` is keyed by clock hour, so 24 hours is the entire domain and
`GoogleWeatherProvider` refuses a horizon outside 1..24 — a longer one cannot
produce more points, it would be truncated in `translate()` and the run would
describe less weather than it claimed.

A rolling 24-hour forecast covers every clock hour exactly once, which is why
scenario schedule hours resolve against live data at all. The consequence is
that a live run binds a scenario hour to the *next* occurrence of that clock
hour — at 19:00 local, "12:00" is tomorrow. That is a property of the point
model, not of this integration, and it is the thing to revisit if the schedule
ever needs real dates.

## Live routing is not in scope

StormSlot's live run uses live weather and the **seeded** route provider.
`GoogleRoutesProvider` is unimplemented by design: HarborWindow is the flagship
scenario and does not use routing at all, and StormSlot is carried as transfer
evidence — proof the substrate is not specific to one workflow — rather than as a
second live integration. The gate reports the seeded route lane in its own output
rather than leaving it to be assumed.
