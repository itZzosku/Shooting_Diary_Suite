"""The ballistic calculator's "Fetch weather": the diary's FMI client (diary/fmi.py, reached through the bridge)."""
from .bridge import fmi

parse = fmi.parse
nearest = fmi.nearest
station_pressure = fmi.station_pressure
distance_km = fmi.distance_km
current = fmi.current
_fetch = fmi._fetch
_cache = fmi._cache
