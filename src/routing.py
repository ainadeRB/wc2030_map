"""Temps de trajet routier entre un point de référence et des hôtels, via le
service de routage public OSRM (table API), avec un cache disque persistant
pour ne jamais recalculer deux fois le même trajet."""
import json
from pathlib import Path

import pandas as pd
import requests

CACHE_PATH = Path("data") / "travel_time_cache.json"
OSRM_BASE_URL = "https://router.project-osrm.org"
CHUNK_SIZE = 90  # nombre de destinations par appel, pour rester sous les limites du serveur public
REQUEST_TIMEOUT = 15


def _origin_key(lat: float, lon: float) -> str:
    return f"{round(lat, 4)},{round(lon, 4)}"


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def _fetch_durations_minutes(origin, destinations, profile="driving"):
    """Interroge OSRM pour un lot de destinations. Retourne une liste de
    durées en minutes (ou None si indisponible), alignée avec `destinations`."""
    coords = [f"{origin[1]},{origin[0]}"] + [f"{lon},{lat}" for lat, lon in destinations]
    coords_str = ";".join(coords)
    dest_idx = ";".join(str(i) for i in range(1, len(destinations) + 1))
    url = f"{OSRM_BASE_URL}/table/v1/{profile}/{coords_str}?sources=0&destinations={dest_idx}&annotations=duration"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        durations_s = resp.json()["durations"][0]
    except (requests.RequestException, KeyError, ValueError, IndexError):
        return [None] * len(destinations)
    return [round(d / 60, 1) if d is not None else None for d in durations_s]


def get_travel_times_minutes(origin, hotels_df: pd.DataFrame, id_col="ID",
                              lat_col="Latitude", lon_col="Longitude", profile="driving"):
    """Retourne {ID hôtel: minutes ou None} pour les hôtels donnés. Les
    trajets déjà calculés pour ce point de référence (arrondi à ~10 m) sont
    lus depuis le cache disque ; seuls les trajets manquants déclenchent un
    appel réseau, par lots, puis sont ajoutés au cache."""
    cache = _load_cache()
    okey = _origin_key(*origin)
    bucket = cache.setdefault(okey, {})

    rows = hotels_df[[id_col, lat_col, lon_col]].dropna(subset=[lat_col, lon_col])
    missing = [(str(r[id_col]), r[lat_col], r[lon_col]) for _, r in rows.iterrows() if str(r[id_col]) not in bucket]

    results = {}
    newly_cached = False
    for i in range(0, len(missing), CHUNK_SIZE):
        chunk = missing[i:i + CHUNK_SIZE]
        durations = _fetch_durations_minutes(origin, [(lat, lon) for _, lat, lon in chunk], profile=profile)
        for (hid, _, _), minutes in zip(chunk, durations):
            results[hid] = minutes
            # Seuls les résultats obtenus sont mis en cache : un échec (None,
            # ex. service injoignable) reste "manquant" et sera retenté au
            # prochain appel, plutôt que d'être figé définitivement.
            if minutes is not None:
                bucket[hid] = minutes
                newly_cached = True

    if newly_cached:
        cache[okey] = bucket
        _save_cache(cache)

    return {str(hid): bucket.get(str(hid), results.get(str(hid))) for hid in hotels_df[id_col]}
