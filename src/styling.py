"""Palettes de couleurs et échelles visuelles pour la carte."""
import re
import numpy as np

# Palette qualitative (couleurs distinctes, lisibles sur fond clair et satellite)
QUALITATIVE_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
]

# Échelle de qualité (étoiles) : rouge -> orange -> vert -> bleu foncé
STAR_COLORS = {
    1: "#b71c1c",
    2: "#e65100",
    3: "#f9a825",
    4: "#2e7d32",
    5: "#0d47a1",
}
DEFAULT_COLOR = "#616161"

# Clés = noms des onglets du fichier POI (ce sont eux qui définissent les
# couches). Une valeur par défaut ("Point d'intérêt"-like) s'applique à tout
# type non listé ici (voir POI_TYPE_COLORS.get(...) / POI_TYPE_ICON.get(...)).
POI_TYPE_COLORS = {
    "Stades": "#c62828",
    "Sites d'entraînement": "#2e7d32",
    "Aéroports": "#6a1b9a",
    "Fan Festival": "#00838f",
    "Autres": "#616161",
}
POI_TYPE_ICON = {
    "Stades": "flag",
    "Sites d'entraînement": "futbol-o",
    "Aéroports": "plane",
    "Fan Festival": "users",
    "Autres": "map-marker",
}


def extract_star_rating(value):
    """Essaie d'extraire un nombre d'étoiles (1-5) depuis un texte du type
    '5 étoiles', '5*', 'Catégorie 5', etc. Retourne None si non trouvé."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    match = re.search(r"([1-5])", str(value))
    return int(match.group(1)) if match else None


def color_for_category(value, palette_map):
    """Couleur stable pour une valeur catégorielle, via un dict palette_map
    {valeur: couleur} construit une seule fois par colonne."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return DEFAULT_COLOR
    return palette_map.get(value, DEFAULT_COLOR)


def build_palette_map(values):
    uniques = sorted({v for v in values if v is not None and str(v) != "nan"})
    return {v: QUALITATIVE_PALETTE[i % len(QUALITATIVE_PALETTE)] for i, v in enumerate(uniques)}


def color_for_star(rating):
    if rating is None:
        return DEFAULT_COLOR
    return STAR_COLORS.get(int(rating), DEFAULT_COLOR)


def scale_radius(value, vmin, vmax, r_min=4, r_max=22):
    """Rayon de bulle (px) proportionnel à `value`, borné entre r_min et r_max."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return r_min
    if vmax <= vmin:
        return (r_min + r_max) / 2
    ratio = (value - vmin) / (vmax - vmin)
    ratio = min(max(ratio, 0), 1)
    return r_min + ratio * (r_max - r_min)


def hex_to_rgba(hex_color, alpha=0.75):
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"
