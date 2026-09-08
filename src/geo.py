"""Fonctions géographiques (distances, bornes de carte)."""
import numpy as np

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance à vol d'oiseau (km) entre un point (lat1, lon1) et un
    tableau de points (lat2, lon2). Vectorisé avec numpy."""
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return EARTH_RADIUS_KM * c


def bounds_for(df, lat_col="Latitude", lon_col="Longitude", pad=0.05):
    """Retourne [[lat_min, lon_min], [lat_max, lon_max]] avec une marge,
    pour cadrer une carte folium sur un ensemble de points."""
    if df.empty:
        return None
    lat_min, lat_max = df[lat_col].min(), df[lat_col].max()
    lon_min, lon_max = df[lon_col].min(), df[lon_col].max()
    return [[lat_min - pad, lon_min - pad], [lat_max + pad, lon_max + pad]]
