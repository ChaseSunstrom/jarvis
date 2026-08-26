"""Sky — the next ISS pass for the house, what is overhead, the moon, the planets.

Shaped like ``sun``: location from the top-level ``jarvis:`` block, entities
recomputed on a timer, and read-only tools the model can call. The arithmetic
is skyfield's. The only things ever downloaded are orbital elements (a few KB,
refreshed when they are older than ``refresh_hours``) and the planetary
ephemeris (``de421.bsp``, 17 MB, once), and both are cached under the config
directory — so every question below is answered offline for as long as the
elements are worth anything, and every answer says how old they are.

::

    jarvis:
      latitude: 51.5072
      longitude: -0.1276
      elevation: 11
      time_zone: Europe/London

    sky:
      tle_cache: sky/tle          # under the config dir; drop your own .csv/.tle here
      ephemeris: sky/de421.bsp    # under the config dir; downloaded once when absent
      refresh_hours: 24           # re-fetch a source older than this; never under 2
      min_altitude: 10            # degrees; a pass begins and ends here
      update_interval: 300        # seconds between entity recomputes
      satellites:                 # tracked: one `sky.<short name>_next_pass` entity each
        - ISS (ZARYA)
      sources:                    # CelesTrak groups as OMM CSV (TLE is read too)
        - https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=csv
      download: true              # false: never touch the network (air-gapped, tests)

Entities:

* ``sky.iss_next_pass`` — state: the ISO time (house zone) the next pass rises
  above ``min_altitude``, ``none`` when there is none in 48 hours, ``unknown``
  without elements; attributes ``max_alt``, ``direction``, ``visible``,
  ``rise_direction``, ``culmination``, ``set``, ``set_direction``,
  ``tle_age_hours``, ``elements_age_days``, ``satellite``.
* ``sky.moon`` — state: the phase name; attributes ``illumination`` (percent),
  ``phase_angle``, ``next_full``, ``next_new``.

Tools (tier 1, read-only — they keep running after a turn has read a hostile
page, because nothing here can change anything): ``next_pass``,
``overhead_now``, ``moon_phase``, ``planets_tonight``. Each returns a short
dict and a ``spoken`` sentence in the house register — 24-hour times,
compass words, "bright" — and the age of the elements it was computed from.

"Visible" is three things at once, the way Heavens-Above means it: the
satellite above ``min_altitude``, lit by the sun (``is_sunlit``), while the
sun at the house is below −6°. "Bright" is a visible pass that also climbs
past 40°. Without the ephemeris the first is still computed and the other
two are ``null`` — the satellite tools need only the WGS84 observer; the
moon and the planets need the file, and say so when it is missing.

What this does not do. It never refuses for stale elements: a month-old set
can be minutes wrong and, after an ISS reboost, simply wrong, and the reply
carries the age so a person can weigh it. It does not probe for the network;
the fetch is the probe, and a failed one keeps the cache and logs once. It
does not track Starlink — 11,000 objects is a job for a thread at dusk, not a
question — and the ``visual``, ``starlink`` and ``last-30-days`` groups are
opt-in through ``sources:``. Aircraft (readsb, profile ``radio``) are a
separate integration.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ...entity import Entity, EntityPlatform

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "sky"

DEFAULT_TLE_CACHE = "sky/tle"
DEFAULT_EPHEMERIS = "sky/de421.bsp"
#: NAIF moved de421 out of the current directory when de440 became the
#: default; skyfield's own loader still points at ssd.jpl.nasa.gov, which
#: serves the same 16,790,528-byte file. One URL here, so the error message
#: names the one that failed.
DEFAULT_EPHEMERIS_URL = (
    "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/a_old_versions/de421.bsp"
)
#: OMM rows (CSV), not TLEs. The catalogue passed 100,000 objects on
#: 2026-07-11 and a six-digit number cannot be written into a TLE, so a TLE
#: feed silently lacks anything launched after that; CelesTrak's own default
#: is CSV now. The TLE reader stays for a file somebody typed in by hand.
DEFAULT_SOURCES = (
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=csv",
)
DEFAULT_SATELLITES = ("ISS (ZARYA)",)
DEFAULT_REFRESH_HOURS = 24.0
#: CelesTrak regenerates every two hours and asks not to be polled faster; a
#: box that does gets its address blocked, which is a worse outcome than a
#: two-hour-old element set.
MIN_REFRESH_HOURS = 2.0
DEFAULT_MIN_ALTITUDE = 10.0
DEFAULT_UPDATE_INTERVAL = 300.0
#: How far ahead the entities and the default tool call look.
DEFAULT_WINDOW_HOURS = 48
MAX_WINDOW_HOURS = 24 * 14
#: A retry sooner than this after a failed fetch is a retry against a server
#: that told us to stop (403) or a network that is not there.
REFRESH_CHECK_SECONDS = 3600.0
FETCH_TIMEOUT = 20.0
#: The observer counts as in the dark once the sun is below civil twilight.
#: Heavens-Above uses the same threshold; a pass in a lighter sky is real but
#: is not one anyone will see.
DARK_SUN_ALTITUDE = -6.0
#: "Bright" is a high pass: the station is nearest and the geometry is best.
BRIGHT_ALTITUDE = 40.0

STATE_NONE = "none"
STATE_UNKNOWN = "unknown"

#: Catalogue names people never use. Both directions: what the user says →
#: the catalogue name, and the catalogue name → what Jarvis says back.
SATELLITE_ALIASES: dict[str, str] = {
    "iss": "ISS (ZARYA)",
    "iss (zarya)": "ISS (ZARYA)",
    "zarya": "ISS (ZARYA)",
    "space station": "ISS (ZARYA)",
    "the space station": "ISS (ZARYA)",
    "international space station": "ISS (ZARYA)",
    "station": "ISS (ZARYA)",
    "tiangong": "CSS (TIANHE)",
    "css": "CSS (TIANHE)",
    "css (tianhe)": "CSS (TIANHE)",
    "tianhe": "CSS (TIANHE)",
    "chinese space station": "CSS (TIANHE)",
    "hubble": "HST",
    "hst": "HST",
}
SPOKEN_NAMES: dict[str, str] = {
    "ISS (ZARYA)": "the space station",
    "CSS (TIANHE)": "Tiangong",
    "HST": "Hubble",
}
SHORT_NAMES: dict[str, str] = {
    "ISS (ZARYA)": "ISS",
    "CSS (TIANHE)": "Tiangong",
    "HST": "Hubble",
}

#: The five naked-eye planets, by the name de421 knows them under. Mars,
#: Jupiter and Saturn are barycentres in that file; for a direction from
#: Earth the difference is nothing.
PLANETS: tuple[tuple[str, str], ...] = (
    ("Mercury", "mercury"),
    ("Venus", "venus"),
    ("Mars", "mars barycenter"),
    ("Jupiter", "jupiter barycenter"),
    ("Saturn", "saturn barycenter"),
)

COMPASS = (
    "north", "north-east", "east", "south-east",
    "south", "south-west", "west", "north-west",
)

#: Half-width, in degrees of phase angle, of the band that gets a cardinal
#: name. Ten degrees is under a day either side: the moon is "full" on the
#: night people would call it full, and "waxing gibbous, 98 percent" the
#: night before, which is what it looks like.
PHASE_BAND = 10.0


class SkyUnavailable(RuntimeError):
    """Something the question needs is not on this box — with the reason."""


# --- skyfield, lazily ------------------------------------------------------
# The import is deferred so that `jarvis` starts without numpy on a box that
# has no `sky:` block, and so that a missing wheel is one clear message from
# the tool rather than an ImportError at boot.
_SKYFIELD: dict[str, Any] = {}


def _sf() -> dict[str, Any]:
    if _SKYFIELD:
        return _SKYFIELD
    try:
        from skyfield import almanac
        from skyfield.api import EarthSatellite, load, load_file, wgs84
    except ImportError as err:  # pragma: no cover - the wheel is in requirements.txt
        raise SkyUnavailable(
            "skyfield is not installed (pip install skyfield); the sky integration "
            "cannot compute anything without it"
        ) from err
    _SKYFIELD.update(
        almanac=almanac,
        EarthSatellite=EarthSatellite,
        load_file=load_file,
        wgs84=wgs84,
        # builtin=True reads the leap-second and ΔT tables shipped in the
        # wheel: no download at start-up, ever.
        ts=load.timescale(builtin=True),
    )
    return _SKYFIELD


# --- helpers --------------------------------------------------------------
def compass(azimuth: float) -> str:
    """An eight-point compass word for an azimuth in degrees (0 = north)."""
    index = int(((float(azimuth) % 360.0) + 22.5) // 45.0) % 8
    return COMPASS[index]


def moon_phase_name(angle: float) -> str:
    """The phase name for skyfield's phase angle (0 new, 90 first quarter, 180 full)."""
    angle = float(angle) % 360.0
    for centre, name in ((0.0, "new moon"), (90.0, "first quarter"),
                         (180.0, "full moon"), (270.0, "last quarter")):
        if min(abs(angle - centre), abs(angle - centre - 360.0), abs(angle - centre + 360.0)) \
                <= PHASE_BAND:
            return name
    if angle < 90.0:
        return "waxing crescent"
    if angle < 180.0:
        return "waxing gibbous"
    if angle < 270.0:
        return "waning gibbous"
    return "waning crescent"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _hhmm(value: datetime) -> str:
    return value.strftime("%H:%M")


def _when(value: datetime, now: datetime) -> str:
    """'tonight at 21:47', 'tomorrow morning at 05:12', 'on Thursday at 04:49', 'on 11 September at 04:26'.

    A weekday name only inside the week: the next full and the next new moon
    were both "on Friday" once, a fortnight apart, and nothing in the sentence
    said which Friday.
    """
    days = (value.date() - now.date()).days
    clock = _hhmm(value)
    if days == 0:
        if value.hour < 12:
            return f"this morning at {clock}"
        if value.hour < 18:
            return f"this afternoon at {clock}"
        return f"tonight at {clock}"
    if days == 1:
        if value.hour < 12:
            return f"tomorrow morning at {clock}"
        return f"tomorrow at {clock}"
    if days < 7:
        return f"on {value.strftime('%A')} at {clock}"
    return f"on {value.day} {value.strftime('%B')} at {clock}"


def _age_phrase(days: float | None, hours: float | None = None) -> str:
    """'from orbital elements 9 days old' — the caveat every satellite answer carries.

    The epoch age (`days`) is the one that means something: it is how far the
    elements have been propagated. The cache age (`hours`, when the elements
    were last fetched) only stands in when there is no satellite to read an
    epoch from.
    """
    if days is None and hours is None:
        return "with no orbital elements at all"
    total_hours = days * 24.0 if days is not None else float(hours or 0.0)
    if total_hours >= 24.0:
        n = int(round(total_hours / 24.0))
        return f"from orbital elements {n} day{'s' if n != 1 else ''} old"
    n = max(1, int(round(total_hours)))
    return f"from orbital elements {n} hour{'s' if n != 1 else ''} old"


def _join(names: list[str]) -> str:
    """'Mercury', 'Mercury and Venus', 'Mercury, Venus and Mars'."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _source_name(url: str) -> str:
    """A file stem for a source URL: `GROUP=stations` → `stations`."""
    match = re.search(r"[?&](GROUP|CATNR|NAME|INTDES)=([^&]+)", url, re.I)
    if match:
        key, value = match.group(1).lower(), match.group(2)
        stem = value if key == "group" else f"{key}-{value}"
    else:
        stem = re.sub(r"^https?://", "", url)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "elements"
    return stem[:80]


def _source_is_csv(url: str) -> bool:
    return bool(re.search(r"FORMAT=(csv|json)", url, re.I))


def _spoken_name(name: str) -> str:
    return SPOKEN_NAMES.get(name, name)


def _short_name(name: str) -> str:
    return SHORT_NAMES.get(name, re.sub(r"\s*\(.*\)$", "", name))


# --- the element cache -----------------------------------------------------
@dataclass
class ElementSet:
    """One satellite's elements and where they came from."""

    name: str
    #: Either two TLE lines or one OMM row; `build` knows which.
    lines: tuple[str, str] | None = None
    omm: dict[str, str] | None = None
    source: str = ""
    fetched_at: datetime | None = None

    def build(self, ts: Any) -> Any:
        sf = _sf()
        if self.omm is not None:
            return sf["EarthSatellite"].from_omm(ts, self.omm)
        assert self.lines is not None
        return sf["EarthSatellite"](self.lines[0], self.lines[1], self.name, ts)


def parse_elements(text: str, source: str = "", fetched_at: datetime | None = None) -> list[ElementSet]:
    """Every satellite in a TLE file or a CelesTrak OMM CSV. Tolerant of CRLF and blank lines.

    Detects the format from the content, not the file name: a source that is
    changed from `FORMAT=tle` to `FORMAT=csv` keeps working with the file it
    already cached.
    """
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in body.split("\n")]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return []
    out: list[ElementSet] = []
    if lines[0].upper().startswith("OBJECT_NAME,"):
        for row in csv.DictReader(io.StringIO("\n".join(lines))):
            name = (row.get("OBJECT_NAME") or "").strip()
            if name and row.get("EPOCH"):
                out.append(ElementSet(name=name, omm=dict(row), source=source, fetched_at=fetched_at))
        return out
    previous = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            name = previous.strip() or f"NORAD {line[2:7].strip()}"
            out.append(ElementSet(name=name, lines=(line, lines[i + 1]),
                                  source=source, fetched_at=fetched_at))
            previous = ""
            i += 2
            continue
        previous = line
        i += 1
    return out


FetchText = Callable[[str], "asyncio.Future[str] | Any"]


class TleCache:
    """Element sets on disk, with the time each was fetched, refreshed when stale.

    Files: ``<name>.tle`` or ``<name>.csv`` beside ``<name>.meta.json``
    (``{"url", "fetched_at"}``). A file with no meta — one an operator dropped
    in by hand — is dated by its mtime. A failed fetch keeps the file it could
    not replace and logs once; a fetch whose body parses to no satellites is
    treated the same, so a 200 carrying an HTML error page cannot clobber a
    good cache.
    """

    def __init__(
        self,
        directory: Path,
        sources: list[str],
        refresh_hours: float = DEFAULT_REFRESH_HOURS,
        download: bool = True,
        fetch: FetchText | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.sources = [str(s) for s in sources if str(s).strip()]
        self.refresh_hours = max(float(refresh_hours), MIN_REFRESH_HOURS)
        self.download = bool(download)
        self._fetch = fetch or _fetch_text
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._warned: set[str] = set()
        self.last_refresh: dict[str, Any] = {}

    # --- reading -----------------------------------------------------------
    def _path_for(self, url: str) -> Path:
        return self.directory / (_source_name(url) + (".csv" if _source_is_csv(url) else ".tle"))

    def _meta_for(self, path: Path) -> Path:
        return path.with_name(path.stem + ".meta.json")

    def fetched_at(self, path: Path) -> datetime | None:
        meta = self._meta_for(path)
        if meta.exists():
            try:
                raw = json.loads(meta.read_text(encoding="utf-8")).get("fetched_at")
                if raw:
                    stamp = datetime.fromisoformat(str(raw))
                    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
            except (OSError, ValueError, AttributeError):
                pass
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return None

    def files(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(p for p in self.directory.iterdir()
                      if p.suffix.lower() in (".tle", ".txt", ".csv") and p.is_file())

    def load(self) -> dict[str, ElementSet]:
        """Every satellite in every cached file; the newest file wins a name clash."""
        out: dict[str, ElementSet] = {}
        entries: list[tuple[datetime, Path]] = []
        for path in self.files():
            stamp = self.fetched_at(path) or datetime.fromtimestamp(0, tz=timezone.utc)
            entries.append((stamp, path))
        for stamp, path in sorted(entries):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for element in parse_elements(text, source=path.name, fetched_at=stamp):
                out[element.name] = element
        return out

    def age_hours(self, element: ElementSet | None = None) -> float | None:
        """Hours since the set `element` came from was fetched — or the newest file's."""
        if element is not None and element.fetched_at is not None:
            return max(0.0, (self._now() - element.fetched_at).total_seconds() / 3600.0)
        stamps = [self.fetched_at(p) for p in self.files()]
        stamps = [s for s in stamps if s is not None]
        if not stamps:
            return None
        return max(0.0, (self._now() - max(stamps)).total_seconds() / 3600.0)

    def stale_sources(self) -> list[str]:
        out: list[str] = []
        for url in self.sources:
            path = self._path_for(url)
            if not path.exists():
                out.append(url)
                continue
            stamp = self.fetched_at(path)
            if stamp is None or (self._now() - stamp).total_seconds() > self.refresh_hours * 3600.0:
                out.append(url)
        return out

    # --- writing -----------------------------------------------------------
    async def async_refresh(self, force: bool = False) -> dict[str, Any]:
        """Fetch every stale source. Never raises; the report says what happened.

        Not a probe for the network: the fetch is the probe. Offline, every
        source fails, the caches stay, and the age they report is the truth.
        """
        report: dict[str, Any] = {"fetched": [], "failed": [], "kept": [], "disabled": not self.download}
        if not self.download or not self.sources:
            self.last_refresh = report
            return report
        wanted = self.sources if force else self.stale_sources()
        for url in self.sources:
            if url not in wanted:
                report["kept"].append(url)
                continue
            path = self._path_for(url)
            try:
                text = await self._fetch(url)
                elements = parse_elements(text)
                if not elements:
                    raise ValueError("the body parsed to no satellites")
            except Exception as err:  # noqa: BLE001 - a fetch that fails must keep the cache
                report["failed"].append({"url": url, "error": f"{type(err).__name__}: {err}"})
                if url not in self._warned:
                    self._warned.add(url)
                    age = self.age_hours()
                    _LOGGER.warning(
                        "sky: could not refresh %s (%s); %s",
                        url, err,
                        f"keeping the cached set, {age:.0f} h old" if age is not None
                        else "and there is no cached set to fall back on",
                    )
                continue
            self._warned.discard(url)
            stamp = self._now()
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".part")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(path)
                self._meta_for(path).write_text(
                    json.dumps({"url": url, "fetched_at": stamp.isoformat(), "satellites": len(elements)}),
                    encoding="utf-8",
                )
            except OSError as err:
                report["failed"].append({"url": url, "error": f"write: {err}"})
                continue
            report["fetched"].append({"url": url, "satellites": len(elements), "file": path.name})
            _LOGGER.info("sky: refreshed %s — %d satellites", path.name, len(elements))
        self.last_refresh = report
        return report

    async def async_refresh_loop(self, first_after: float = REFRESH_CHECK_SECONDS) -> None:
        """Hourly: refresh what is stale. Runs as a task; cancelled on shutdown.

        Sleeps first: the warm-up at setup does the first refresh, and a loop
        that also refreshed on its first turn fetched the same elements twice
        in the same second (both saw an empty cache before either had written).
        """
        await asyncio.sleep(first_after)
        while True:
            try:
                await self.async_refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive one bad hour
                _LOGGER.exception("sky: element refresh failed")
            await asyncio.sleep(REFRESH_CHECK_SECONDS)


async def _fetch_text(url: str) -> str:
    """One GET, no redirects (a 301 from CelesTrak means the URL is wrong, not moved)."""
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(FETCH_TIMEOUT), follow_redirects=False) as client:
        response = await client.get(url, headers={"User-Agent": "jarvis-sky/1"})
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.text


# --- the ephemeris ----------------------------------------------------------
class Ephemeris:
    """de421 on disk, loaded once, downloaded once in the background if allowed."""

    def __init__(self, path: Path, url: str = DEFAULT_EPHEMERIS_URL, download: bool = True) -> None:
        self.path = Path(path)
        self.url = url
        self.download = bool(download)
        self._kernel: Any = None
        self._downloading = False
        self.last_error: str = ""

    @property
    def available(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size > 0

    def kernel(self) -> Any:
        """The loaded SPK, or SkyUnavailable naming what is missing."""
        if self._kernel is not None:
            return self._kernel
        if not self.available:
            why = (
                f"the planetary ephemeris is not downloaded yet ({self.path.name}"
                f"{'; ' + self.last_error if self.last_error else ''})"
                if self.download
                else f"the planetary ephemeris {self.path.name} is not present and downloads are off"
            )
            raise SkyUnavailable(why)
        self._kernel = _sf()["load_file"](str(self.path))
        return self._kernel

    async def async_ensure(self) -> bool:
        """Download the file if it is absent and downloads are on. Never raises."""
        if self.available or not self.download or self._downloading:
            return self.available
        self._downloading = True
        try:
            import httpx

            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".part")
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
                async with client.stream("GET", self.url) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"HTTP {response.status_code}")
                    with tmp.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            handle.write(chunk)
            if tmp.stat().st_size < 1_000_000:
                tmp.unlink(missing_ok=True)
                raise RuntimeError("the download was too small to be an ephemeris")
            tmp.replace(self.path)
            self.last_error = ""
            _LOGGER.info("sky: downloaded %s (%d bytes)", self.path.name, self.path.stat().st_size)
        except Exception as err:  # noqa: BLE001 - an absent ephemeris is a degraded answer, not a crash
            self.last_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning(
                "sky: could not download the ephemeris from %s (%s); the moon and the "
                "planets are unavailable and passes cannot say whether they are lit",
                self.url, err,
            )
        finally:
            self._downloading = False
        return self.available


# --- the sky itself ---------------------------------------------------------
@dataclass
class Pass:
    rise: datetime
    rise_azimuth: float
    culmination: datetime
    max_altitude: float
    culmination_azimuth: float
    set: datetime
    set_azimuth: float
    sunlit: bool | None = None
    dark_at_house: bool | None = None

    @property
    def visible(self) -> bool | None:
        if self.sunlit is None or self.dark_at_house is None:
            return None
        return bool(self.sunlit and self.dark_at_house)

    @property
    def duration_s(self) -> int:
        return int(round((self.set - self.rise).total_seconds()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "rise": _iso(self.rise),
            "rise_direction": compass(self.rise_azimuth),
            "culmination": _iso(self.culmination),
            "max_altitude": int(round(self.max_altitude)),
            "direction": compass(self.culmination_azimuth),
            "set": _iso(self.set),
            "set_direction": compass(self.set_azimuth),
            "duration_s": self.duration_s,
            "sunlit": self.sunlit,
            "dark_at_house": self.dark_at_house,
            "visible": self.visible,
            "bright": bool(self.visible) and self.max_altitude >= BRIGHT_ALTITUDE,
        }


class SkyData:
    """Location-bound wrapper: every answer, from the cache, at the house's clock.

    Stored at ``jarvis.data["sky"]``. Every method here is synchronous and
    CPU-bound (milliseconds for one satellite; the callers run them in a
    thread). None raises for a missing ephemeris — the dict says so — but a
    missing element set for the satellite asked about is an error dict with
    the names that ARE cached.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        elevation: float,
        cache: TleCache,
        ephemeris: Ephemeris,
        now: Callable[[], datetime],
        satellites: list[str] | None = None,
        min_altitude: float = DEFAULT_MIN_ALTITUDE,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.elevation = float(elevation)
        self.cache = cache
        self.ephemeris = ephemeris
        self._now = now
        self.satellites = list(satellites or DEFAULT_SATELLITES)
        self.min_altitude = float(min_altitude)
        self._house: Any = None

    # --- plumbing ----------------------------------------------------------
    def now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @property
    def zone(self) -> Any:
        return self.now().tzinfo

    def _local(self, t: Any) -> datetime:
        """A skyfield Time as a datetime in the house's zone, to the second."""
        value = t.utc_datetime().astimezone(self.zone)
        return value.replace(microsecond=0)

    def house(self) -> Any:
        if self._house is None:
            self._house = _sf()["wgs84"].latlon(
                self.latitude, self.longitude, elevation_m=self.elevation
            )
        return self._house

    def _observer(self) -> Any:
        return self.ephemeris.kernel()["earth"] + self.house()

    def _eph_or_none(self) -> Any:
        try:
            return self.ephemeris.kernel()
        except SkyUnavailable:
            return None

    def elements(self) -> dict[str, ElementSet]:
        return self.cache.load()

    def resolve(self, query: str | None, elements: dict[str, ElementSet] | None = None) -> ElementSet:
        """The element set for what the user called it, or SkyUnavailable naming what is cached."""
        elements = elements if elements is not None else self.elements()
        wanted = (query or "").strip()
        if not wanted:
            wanted = self.satellites[0] if self.satellites else "ISS (ZARYA)"
        key = wanted.lower()
        resolved = SATELLITE_ALIASES.get(key, wanted)
        for name, element in elements.items():
            if name.lower() == resolved.lower():
                return element
        # Substring, then word overlap: "noaa 20" against "NOAA 20 (JPSS-1)".
        for name, element in elements.items():
            if key in name.lower():
                return element
        words = {w for w in re.split(r"[^a-z0-9]+", key) if w}
        for name, element in elements.items():
            if words and words <= {w for w in re.split(r"[^a-z0-9]+", name.lower()) if w}:
                return element
        cached = ", ".join(sorted(elements)[:12]) or "nothing"
        asked = f" (asked as {wanted!r})" if resolved != wanted else ""
        raise SkyUnavailable(
            f"no orbital elements for {resolved!r}{asked}; cached: {cached}"
            + (" — the element cache is empty; it fills when the box is online" if not elements else "")
        )

    def _ages(self, element: ElementSet | None, satellite: Any | None) -> dict[str, Any]:
        age_hours = self.cache.age_hours(element)
        epoch_days = None
        if satellite is not None:
            epoch = satellite.epoch.utc_datetime()
            epoch_days = max(0.0, (self.now().astimezone(timezone.utc) - epoch).total_seconds() / 86400.0)
        return {
            "tle_age_hours": round(age_hours, 1) if age_hours is not None else None,
            "elements_age_days": round(epoch_days, 1) if epoch_days is not None else None,
        }

    # --- passes ------------------------------------------------------------
    def passes(self, element: ElementSet, hours: float, min_altitude: float | None = None) -> list[Pass]:
        sf = _sf()
        ts = sf["ts"]
        satellite = element.build(ts)
        house = self.house()
        start = self.now()
        t0 = ts.from_datetime(start.astimezone(timezone.utc))
        t1 = ts.from_datetime((start + timedelta(hours=float(hours))).astimezone(timezone.utc))
        threshold = self.min_altitude if min_altitude is None else float(min_altitude)
        times, events = satellite.find_events(house, t0, t1, altitude_degrees=threshold)
        eph = self._eph_or_none()
        observer = (eph["earth"] + house) if eph is not None else None
        difference = satellite - house

        out: list[Pass] = []
        current: dict[str, Any] = {}
        for t, event in zip(times, events):
            alt, az, _ = difference.at(t).altaz()
            if event == 0:
                current = {"rise": t, "rise_az": az.degrees}
            elif event == 1:
                if "rise" not in current:
                    # A pass already under way at t0: it culminates before it
                    # rises in this window. Treat "now" as its rise.
                    current = {"rise": t0, "rise_az": difference.at(t0).altaz()[1].degrees}
                current["culm"] = t
                current["alt"] = alt.degrees
                current["culm_az"] = az.degrees
            elif event == 2 and "culm" in current:
                sunlit = dark = None
                if eph is not None and observer is not None:
                    tc = current["culm"]
                    sunlit = bool(satellite.at(tc).is_sunlit(eph))
                    sun_alt = observer.at(tc).observe(eph["sun"]).apparent().altaz()[0].degrees
                    dark = bool(sun_alt < DARK_SUN_ALTITUDE)
                out.append(Pass(
                    rise=self._local(current["rise"]),
                    rise_azimuth=float(current["rise_az"]),
                    culmination=self._local(current["culm"]),
                    max_altitude=float(current["alt"]),
                    culmination_azimuth=float(current["culm_az"]),
                    set=self._local(t),
                    set_azimuth=float(az.degrees),
                    sunlit=sunlit,
                    dark_at_house=dark,
                ))
                current = {}
        return out

    def next_pass(self, satellite: str | None = None, hours: float | None = None) -> dict[str, Any]:
        hours = float(DEFAULT_WINDOW_HOURS if hours is None else hours)
        hours = max(1.0, min(hours, float(MAX_WINDOW_HOURS)))
        now = self.now()
        try:
            element = self.resolve(satellite)
        except SkyUnavailable as err:
            return {"status": "error", "error": str(err), "spoken": f"I can't say: {err}."}
        sat = element.build(_sf()["ts"])
        found = self.passes(element, hours)
        visible = [p for p in found if p.visible]
        ages = self._ages(element, sat)
        age = _age_phrase(ages["elements_age_days"], ages["tle_age_hours"])
        name = _spoken_name(element.name)
        first = found[0] if found else None
        best = visible[0] if visible else None
        ephemeris_missing = self._eph_or_none() is None

        if best is not None:
            adjective = "bright, high overhead" if best.max_altitude >= BRIGHT_ALTITUDE else "visible"
            spoken = (
                f"{name.capitalize()} is next visible {_when(best.rise, now)}: it comes up in the "
                f"{compass(best.rise_azimuth)}, reaches {int(round(best.max_altitude))} degrees in the "
                f"{compass(best.culmination_azimuth)} at {_hhmm(best.culmination)}, and is gone by "
                f"{_hhmm(best.set)} in the {compass(best.set_azimuth)} — {adjective}, {age}."
            )
        elif first is not None:
            if ephemeris_missing:
                why = "whether it is lit is unknown until the ephemeris is downloaded"
            elif first.sunlit is False:
                why = "it is in the Earth's shadow"
            else:
                why = "it is daylight here"
            spoken = (
                f"No visible pass of {name} in the next {int(hours)} hours; the next pass is "
                f"{_when(first.rise, now)}, up to {int(round(first.max_altitude))} degrees in the "
                f"{compass(first.culmination_azimuth)}, but {why} — {age}."
            )
        else:
            spoken = (
                f"No pass of {name} above {int(self.min_altitude)} degrees in the next "
                f"{int(hours)} hours, {age}."
            )
        return {
            "status": "ok",
            "satellite": element.name,
            "now": _iso(now),
            "window_hours": int(hours),
            "min_altitude": self.min_altitude,
            "pass": first.as_dict() if first else None,
            "next_visible": best.as_dict() if best else None,
            "passes_in_window": len(found),
            "visible_in_window": len(visible),
            "ephemeris": not ephemeris_missing,
            **ages,
            "spoken": spoken,
        }

    def overhead_now(self, min_altitude: float | None = None) -> dict[str, Any]:
        threshold = self.min_altitude if min_altitude is None else float(min_altitude)
        now = self.now()
        sf = _sf()
        ts = sf["ts"]
        t = ts.from_datetime(now.astimezone(timezone.utc))
        elements = self.elements()
        eph = self._eph_or_none()
        house = self.house()
        overhead: list[dict[str, Any]] = []
        below: list[str] = []
        missing: list[str] = []
        oldest_hours: float | None = None
        oldest_days: float | None = None
        for name in self.satellites:
            try:
                element = self.resolve(name, elements)
            except SkyUnavailable:
                missing.append(name)
                continue
            sat = element.build(ts)
            alt, az, distance = (sat - house).at(t).altaz()
            ages = self._ages(element, sat)
            if ages["tle_age_hours"] is not None:
                oldest_hours = max(oldest_hours or 0.0, ages["tle_age_hours"])
            if ages["elements_age_days"] is not None:
                oldest_days = max(oldest_days or 0.0, ages["elements_age_days"])
            if alt.degrees >= threshold:
                overhead.append({
                    "satellite": element.name,
                    "altitude": int(round(alt.degrees)),
                    "azimuth": int(round(az.degrees)),
                    "direction": compass(az.degrees),
                    "range_km": int(round(distance.km)),
                    "sunlit": bool(sat.at(t).is_sunlit(eph)) if eph is not None else None,
                })
            else:
                below.append(element.name)
        age = _age_phrase(oldest_days, oldest_hours)
        if overhead:
            parts = [
                f"{_spoken_name(o['satellite'])} is up now, {o['altitude']} degrees in the "
                f"{o['direction']}, {o['range_km']} km away"
                + (", sunlit" if o["sunlit"] else (", in shadow" if o["sunlit"] is False else ""))
                for o in overhead
            ]
            spoken = "; ".join(p[0].upper() + p[1:] for p in parts) + f" — {age}."
        elif below:
            names = _join([_spoken_name(n) for n in below])
            verb = "is" if len(below) == 1 else "are"
            spoken = (
                f"Nothing tracked is above {int(threshold)} degrees right now; "
                f"{names} {verb} below the horizon — {age}."
            )
        else:
            spoken = "I have no orbital elements for any tracked satellite; the cache fills when the box is online."
        return {
            "status": "ok",
            "now": _iso(now),
            "min_altitude": threshold,
            "overhead": overhead,
            "below_horizon": below,
            "no_elements": missing,
            "tracked": list(self.satellites),
            "tle_age_hours": round(oldest_hours, 1) if oldest_hours is not None else None,
            "elements_age_days": round(oldest_days, 1) if oldest_days is not None else None,
            "spoken": spoken,
        }

    # --- the moon ----------------------------------------------------------
    def moon_phase(self) -> dict[str, Any]:
        now = self.now()
        try:
            eph = self.ephemeris.kernel()
        except SkyUnavailable as err:
            return {"status": "error", "error": str(err), "spoken": f"I can't say: {err}."}
        sf = _sf()
        ts, almanac = sf["ts"], sf["almanac"]
        t = ts.from_datetime(now.astimezone(timezone.utc))
        angle = float(almanac.moon_phase(eph, t).degrees)
        illumination = float(almanac.fraction_illuminated(eph, "moon", t)) * 100.0
        name = moon_phase_name(angle)
        t1 = ts.from_datetime((now + timedelta(days=32)).astimezone(timezone.utc))
        times, phases = almanac.find_discrete(t, t1, almanac.moon_phases(eph))
        next_full = next_new = None
        for when, phase in zip(times, phases):
            if phase == 2 and next_full is None:
                next_full = self._local(when)
            elif phase == 0 and next_new is None:
                next_new = self._local(when)
        percent = int(round(illumination))
        described = {"full moon": "full", "new moon": "new"}.get(name, f"a {name}")
        spoken = f"The moon is {described}, {percent} percent lit"
        if name == "full moon" and next_new:
            spoken += f"; new {_when(next_new, now)}."
        elif name == "new moon" and next_full:
            spoken += f"; full {_when(next_full, now)}."
        else:
            first = next_full if (next_full and next_new and next_full < next_new) else next_new
            second = next_new if first is next_full else next_full
            first_word = "full" if first is next_full else "new"
            second_word = "new" if first is next_full else "full"
            if first:
                spoken += f"; {first_word} {_when(first, now)}"
            if second:
                spoken += f", {second_word} {_when(second, now)}"
            spoken += "."
        return {
            "status": "ok",
            "now": _iso(now),
            "phase": name,
            "phase_angle": round(angle, 1),
            "illumination": round(illumination, 1),
            "waxing": angle < 180.0,
            "next_full": _iso(next_full),
            "next_new": _iso(next_new),
            "spoken": spoken,
        }

    # --- the planets ----------------------------------------------------------
    def night(self) -> dict[str, Any]:
        """Tonight's dusk and dawn (sun at -6°) from the house clock; 'now' if already dark."""
        now = self.now()
        eph = self.ephemeris.kernel()
        sf = _sf()
        ts, almanac = sf["ts"], sf["almanac"]
        observer = eph["earth"] + self.house()
        t = ts.from_datetime(now.astimezone(timezone.utc))
        sun_alt = observer.at(t).observe(eph["sun"]).apparent().altaz()[0].degrees
        already_dark = bool(sun_alt < DARK_SUN_ALTITUDE)
        horizon = t + 2
        if already_dark:
            dusk_t = t
        else:
            settings, real = almanac.find_settings(observer, eph["sun"], t, horizon,
                                                   horizon_degrees=DARK_SUN_ALTITUDE)
            real_settings = [s for s, ok in zip(settings, real) if ok]
            if not real_settings:
                raise SkyUnavailable("the sun does not set below civil twilight in the next two days here")
            dusk_t = real_settings[0]
        risings, real = almanac.find_risings(observer, eph["sun"], dusk_t, dusk_t + 2,
                                             horizon_degrees=DARK_SUN_ALTITUDE)
        real_risings = [r for r, ok in zip(risings, real) if ok and r.tt > dusk_t.tt]
        if not real_risings:
            raise SkyUnavailable("the sun does not rise again in the next two days here")
        dawn_t = real_risings[0]
        return {"dusk_t": dusk_t, "dawn_t": dawn_t, "already_dark": already_dark,
                "dusk": self._local(dusk_t), "dawn": self._local(dawn_t)}

    def planets_tonight(self) -> dict[str, Any]:
        now = self.now()
        try:
            eph = self.ephemeris.kernel()
            night = self.night()
        except SkyUnavailable as err:
            return {"status": "error", "error": str(err), "spoken": f"I can't say: {err}."}
        sf = _sf()
        ts, almanac = sf["ts"], sf["almanac"]
        observer = eph["earth"] + self.house()
        dusk_t, dawn_t = night["dusk_t"], night["dawn_t"]
        # Five-minute samples across the night: enough to place a maximum to
        # the nearest few minutes, few enough to be one vectorised call each.
        # (skyfield hands back numpy arrays; nothing here needs numpy itself.)
        steps = max(2, int(math.ceil((dawn_t.tt - dusk_t.tt) * 24 * 12)) + 1)
        stride = (dawn_t.tt - dusk_t.tt) / (steps - 1)
        grid = ts.tt_jd([dusk_t.tt + i * stride for i in range(steps)])
        planets: list[dict[str, Any]] = []
        hidden: list[str] = []
        for label, key in PLANETS:
            alt, az, _ = observer.at(grid).observe(eph[key]).apparent().altaz()
            degrees = [float(d) for d in alt.degrees]
            if max(degrees) <= 0.0:
                hidden.append(label)
                continue
            k = max(range(len(degrees)), key=degrees.__getitem__)
            up_at_dusk = degrees[0] > 0.0
            up_at_dawn = degrees[-1] > 0.0
            rises = sets = None
            r_times, r_real = almanac.find_risings(observer, eph[key], dusk_t, dawn_t)
            for when, ok in zip(r_times, r_real):
                if ok:
                    rises = self._local(when)
                    break
            s_times, s_real = almanac.find_settings(observer, eph[key], dusk_t, dawn_t)
            for when, ok in zip(s_times, s_real):
                if ok:
                    sets = self._local(when)
                    break
            planets.append({
                "name": label,
                "up_at_dusk": up_at_dusk,
                "up_at_dawn": up_at_dawn,
                "rises": _iso(rises),
                "sets": _iso(sets),
                "best": {
                    "time": _iso(self._local(grid[k])),
                    "altitude": int(round(degrees[k])),
                    "direction": compass(float(az.degrees[k])),
                },
                "low": degrees[k] < 10.0,
            })
        phrases: list[str] = []
        for p in planets:
            best = p["best"]
            best_at = datetime.fromisoformat(best["time"])
            where = (
                f"low in the {best['direction']}" if p["low"]
                else f"{best['altitude']} degrees in the {best['direction']}"
            )
            # "Highest at dawn" is a planet still climbing when the sky
            # brightens; say "by dawn" for that and a clock time otherwise.
            peaks_at_dawn = (night["dawn"] - best_at) <= timedelta(minutes=10)
            peak = "by dawn" if peaks_at_dawn else f"at {_hhmm(best_at)}"
            if p["up_at_dusk"] and p["sets"]:
                phrases.append(
                    f"{p['name']} is {where} at dusk and sets by "
                    f"{_hhmm(datetime.fromisoformat(p['sets']))}"
                )
            elif p["up_at_dusk"]:
                phrases.append(f"{p['name']} is up all night, highest {peak}, {where}")
            elif p["rises"] and p["up_at_dawn"]:
                rise = _hhmm(datetime.fromisoformat(p["rises"]))
                if peaks_at_dawn:
                    phrases.append(f"{p['name']} rises at {rise} and is {where} by dawn")
                else:
                    phrases.append(f"{p['name']} rises at {rise} and is highest {peak}, {where}")
            elif p["rises"] and p["sets"]:
                phrases.append(
                    f"{p['name']} is up from {_hhmm(datetime.fromisoformat(p['rises']))} to "
                    f"{_hhmm(datetime.fromisoformat(p['sets']))}, {where}"
                )
            else:
                phrases.append(f"{p['name']} is {where}")
        opening = (
            f"It is already dark; until dawn at {_hhmm(night['dawn'])}"
            if night["already_dark"]
            else f"Tonight, between dusk at {_hhmm(night['dusk'])} and dawn at {_hhmm(night['dawn'])}"
        )
        if phrases:
            spoken = f"{opening}: " + "; ".join(phrases) + "."
        else:
            spoken = f"{opening}, no naked-eye planet is above the horizon."
        if hidden:
            spoken += f" {_join(hidden)} {'is' if len(hidden) == 1 else 'are'} not up."
        return {
            "status": "ok",
            "now": _iso(now),
            "night": {"dusk": _iso(night["dusk"]), "dawn": _iso(night["dawn"]),
                      "already_dark": night["already_dark"]},
            "planets": planets,
            "not_up": hidden,
            "spoken": spoken,
        }

    # --- entity snapshots ----------------------------------------------------
    def next_pass_snapshot(self, satellite: str) -> dict[str, Any]:
        result = self.next_pass(satellite, DEFAULT_WINDOW_HOURS)
        if result.get("status") != "ok":
            return {"state": STATE_UNKNOWN, "satellite": satellite, "reason": result.get("error")}
        first = result.get("pass")
        attributes: dict[str, Any] = {
            "satellite": result["satellite"],
            "tle_age_hours": result["tle_age_hours"],
            "elements_age_days": result["elements_age_days"],
            "window_hours": result["window_hours"],
        }
        if not first:
            return {"state": STATE_NONE, **attributes}
        attributes.update({
            "max_alt": first["max_altitude"],
            "direction": first["direction"],
            "rise_direction": first["rise_direction"],
            "culmination": first["culmination"],
            "set": first["set"],
            "set_direction": first["set_direction"],
            "visible": first["visible"],
            "next_visible": (result.get("next_visible") or {}).get("rise"),
        })
        return {"state": first["rise"], **attributes}

    def moon_snapshot(self) -> dict[str, Any]:
        result = self.moon_phase()
        if result.get("status") != "ok":
            return {"state": STATE_UNKNOWN, "reason": result.get("error")}
        return {
            "state": result["phase"],
            "illumination": result["illumination"],
            "phase_angle": result["phase_angle"],
            "waxing": result["waxing"],
            "next_full": result["next_full"],
            "next_new": result["next_new"],
        }


# --- entities ------------------------------------------------------------------
class NextPassEntity(Entity):
    """``sky.<name>_next_pass``: when the next pass rises, and what it will be like."""

    def __init__(self, data: SkyData, satellite: str) -> None:
        self._data = data
        self._satellite = satellite
        short = _short_name(satellite)
        self._attr_name = f"{short} next pass"
        self._attr_unique_id = f"sky_{short.lower()}_next_pass"
        self._attr_icon = "mdi:satellite-variant"
        self._attr_should_poll = True
        self._attr_state = STATE_UNKNOWN
        self._attr_extra_attributes = {"satellite": satellite}

    def recompute(self) -> dict[str, Any]:
        snapshot = self._data.next_pass_snapshot(self._satellite)
        self._attr_state = snapshot.pop("state")
        self._attr_extra_attributes = snapshot
        return snapshot

    async def async_update(self) -> None:
        await asyncio.to_thread(self.recompute)


class MoonEntity(Entity):
    """``sky.moon``: the phase as a word, the numbers as attributes."""

    def __init__(self, data: SkyData) -> None:
        self._data = data
        self._attr_name = "Moon"
        self._attr_unique_id = "sky_moon"
        self._attr_icon = "mdi:moon-waxing-gibbous"
        self._attr_should_poll = True
        self._attr_state = STATE_UNKNOWN
        self._attr_extra_attributes = {}

    def recompute(self) -> dict[str, Any]:
        snapshot = self._data.moon_snapshot()
        self._attr_state = snapshot.pop("state")
        self._attr_extra_attributes = snapshot
        return snapshot

    async def async_update(self) -> None:
        await asyncio.to_thread(self.recompute)


# --- setup -------------------------------------------------------------------------
def get_sky(jarvis: "Jarvis") -> SkyData | None:
    """The configured SkyData, or None when `sky:` is not set up."""
    return jarvis.data.get(DOMAIN)


def _coordinates(jarvis: "Jarvis", config: dict[str, Any]) -> tuple[float, float, float]:
    core = (jarvis.config or {}).get("jarvis") or {}
    latitude = config.get("latitude", core.get("latitude", 0.0))
    longitude = config.get("longitude", core.get("longitude", 0.0))
    elevation = config.get("elevation", core.get("elevation", 0))
    try:
        return float(latitude), float(longitude), float(elevation)
    except (TypeError, ValueError):
        _LOGGER.warning("sky: invalid latitude/longitude (%r, %r); using 0,0", latitude, longitude)
        return 0.0, 0.0, 0.0


def _under_config(jarvis: "Jarvis", value: Any, default: str) -> Path:
    path = Path(str(value or default)).expanduser()
    if path.is_absolute():
        return path
    return Path(jarvis.config_dir) / path


def _house_clock(jarvis: "Jarvis") -> Callable[[], datetime]:
    """The house's clock: `jarvis: time_zone:`, or whatever a test put in its place.

    Read on every call rather than captured once, so "tonight" follows a
    timezone changed in the console without a restart — the same reason the
    time triggers re-read it.
    """
    from ...automation.util import DATA_CLOCK, configured_clock

    def now() -> datetime:
        clock = jarvis.data.get(DATA_CLOCK) or configured_clock(jarvis)
        return clock.now()

    return now


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if not isinstance(config, dict):
        config = {}

    latitude, longitude, elevation = _coordinates(jarvis, config)
    if latitude == 0.0 and longitude == 0.0:
        _LOGGER.warning(
            "sky: no latitude/longitude configured under `jarvis:` — passes will be "
            "computed for 0°N 0°E, which is in the Gulf of Guinea"
        )

    download = bool(config.get("download", True))
    sources = config.get("sources")
    if sources is None:
        sources = list(DEFAULT_SOURCES)
    elif isinstance(sources, str):
        sources = [sources]
    satellites = config.get("satellites") or list(DEFAULT_SATELLITES)
    if isinstance(satellites, str):
        satellites = [satellites]
    satellites = [str(s) for s in satellites]

    house_now = _house_clock(jarvis)
    cache = TleCache(
        _under_config(jarvis, config.get("tle_cache"), DEFAULT_TLE_CACHE),
        sources=[str(s) for s in sources],
        refresh_hours=float(config.get("refresh_hours", DEFAULT_REFRESH_HOURS)),
        download=download,
        # The house's clock, so "12 hours old" and "tonight" agree on what
        # time it is — and so a frozen clock in a test freezes both.
        now=lambda: house_now().astimezone(timezone.utc),
    )
    ephemeris = Ephemeris(
        _under_config(jarvis, config.get("ephemeris"), DEFAULT_EPHEMERIS),
        url=str(config.get("ephemeris_url") or DEFAULT_EPHEMERIS_URL),
        download=download,
    )
    data = SkyData(
        latitude, longitude, elevation,
        cache=cache,
        ephemeris=ephemeris,
        now=house_now,
        satellites=satellites,
        min_altitude=float(config.get("min_altitude", DEFAULT_MIN_ALTITUDE)),
    )
    jarvis.data[DOMAIN] = data

    try:
        _sf()
    except SkyUnavailable as err:
        # The entities and tools still exist and say why they cannot answer;
        # the integration does not take the house down over a missing wheel.
        _LOGGER.error("sky: %s", err)

    # Downloads happen after setup returns, never on the way to it: a box
    # that boots offline must boot at the same speed as one that does not.
    if download:
        async def _warm() -> None:
            await cache.async_refresh()
            await ephemeris.async_ensure()
            await _recompute_all(jarvis)

        jarvis.async_create_task(_warm())
        jarvis.async_create_task(cache.async_refresh_loop())
    else:
        _LOGGER.info("sky: downloads are off; answering from %s alone", cache.directory)

    interval = float(config.get("update_interval", DEFAULT_UPDATE_INTERVAL))
    platform = EntityPlatform(jarvis, DOMAIN, DOMAIN, scan_interval=max(interval, 5.0))
    jarvis.data[f"{DOMAIN}_platform"] = platform
    entities: list[Entity] = [NextPassEntity(data, name) for name in satellites]
    entities.append(MoonEntity(data))
    # update_before_add: the first state is a computed one, not "unknown" for
    # `update_interval` seconds — and the computation is in a thread.
    await platform.async_add_entities(entities, update_before_add=True)

    _register_tools(jarvis, data)
    return True


async def _recompute_all(jarvis: "Jarvis") -> None:
    platform = jarvis.data.get(f"{DOMAIN}_platform")
    if platform is None:
        return
    for entity in list(platform.entities.values()):
        try:
            await entity.async_update_state()
        except Exception:  # noqa: BLE001 - one entity's failure must not stop the others
            _LOGGER.exception("sky: recompute of %s failed", entity.entity_id)


def _register_tools(jarvis: "Jarvis", data: SkyData) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("sky: no LLM tool registry; the entities still work")
        return

    from ...llm.tools import TIER_DIRECT, schema_object

    async def tool_next_pass(args: dict[str, Any], context: Any = None) -> Any:
        satellite = str(args.get("satellite") or "")
        try:
            hours = float(args.get("hours") or DEFAULT_WINDOW_HOURS)
        except (TypeError, ValueError):
            hours = float(DEFAULT_WINDOW_HOURS)
        return await asyncio.to_thread(_guarded, data.next_pass, satellite, hours)

    async def tool_overhead_now(args: dict[str, Any], context: Any = None) -> Any:
        raw = args.get("min_altitude")
        try:
            threshold = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            threshold = None
        return await asyncio.to_thread(_guarded, data.overhead_now, threshold)

    async def tool_moon_phase(args: dict[str, Any], context: Any = None) -> Any:
        return await asyncio.to_thread(_guarded, data.moon_phase)

    async def tool_planets_tonight(args: dict[str, Any], context: Any = None) -> Any:
        return await asyncio.to_thread(_guarded, data.planets_tonight)

    registry.register(
        name="next_pass",
        description=(
            "When a satellite next passes over the house: rise, highest point and set "
            "times in the house's zone, how high, which direction, and whether it will be "
            "visible (lit by the sun while the sky here is dark). Default is the "
            "International Space Station; 'Tiangong' and any cached name work too. "
            "Read `spoken` back to the user; it already carries the caveat about how old "
            "the orbital elements are."
        ),
        parameters=schema_object(
            {
                "satellite": {
                    "type": "string",
                    "description": "which satellite: 'ISS' (default), 'Tiangong', or a catalogue name",
                },
                "hours": {
                    "type": "integer",
                    "description": "how far ahead to look, in hours (default 48)",
                },
            },
            [],
        ),
        handler=tool_next_pass,
        tier=TIER_DIRECT,
        read_only=True,
    )
    registry.register(
        name="overhead_now",
        description=(
            "Which tracked satellites are above the horizon over the house right now, "
            "with altitude, direction and range."
        ),
        parameters=schema_object(
            {
                "min_altitude": {
                    "type": "number",
                    "description": "degrees above the horizon that counts as up (default 10)",
                },
            },
            [],
        ),
        handler=tool_overhead_now,
        tier=TIER_DIRECT,
        read_only=True,
    )
    registry.register(
        name="moon_phase",
        description=(
            "The moon tonight: phase name, percentage lit, and when it is next full and next new."
        ),
        parameters=schema_object({}, []),
        handler=tool_moon_phase,
        tier=TIER_DIRECT,
        read_only=True,
    )
    registry.register(
        name="planets_tonight",
        description=(
            "Which naked-eye planets are above the horizon between dusk and dawn tonight, "
            "when each rises or sets, and where to look."
        ),
        parameters=schema_object({}, []),
        handler=tool_planets_tonight,
        tier=TIER_DIRECT,
        read_only=True,
    )


def _guarded(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    """Run one computation; a missing wheel or file is an answer, not a traceback."""
    started = time.monotonic()
    try:
        result = fn(*args)
    except SkyUnavailable as err:
        return {"status": "error", "error": str(err), "spoken": f"I can't say: {err}."}
    except ValueError as err:
        # skyfield's EphemerisRangeError is a ValueError: a date outside the
        # file (de421 stops at 2050; an excerpt sooner) is a fact to report.
        return {
            "status": "error",
            "error": f"the ephemeris cannot answer for that date: {err}",
            "spoken": "I can't say: the ephemeris on this box does not cover that date.",
        }
    if isinstance(result, dict):
        result.setdefault("computed_in_ms", int((time.monotonic() - started) * 1000))
    return result


__all__ = [
    "DOMAIN",
    "Ephemeris",
    "ElementSet",
    "Pass",
    "SkyData",
    "SkyUnavailable",
    "TleCache",
    "async_setup",
    "compass",
    "get_sky",
    "moon_phase_name",
    "parse_elements",
]
