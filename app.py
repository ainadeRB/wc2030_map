"""Coupe du Monde 2030 – Maroc : cartographie interactive des hôtels
et points d'intérêt (stades, sites d'entraînement, aéroports...).

Lancer avec : streamlit run app.py
"""
import html as html_lib
import json
from io import BytesIO
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.utilities import escape_backticks
from jinja2 import Template
from streamlit_folium import st_folium

from src.data_loader import ALLOCATION_COLUMNS, load_hotels, load_pois
from src.geo import bounds_for, haversine_km
from src.photos import get_hotel_photos, get_thumbnail_data_uri
from src.routing import get_travel_times_with_fallback, using_ors
from src.styling import (
    build_palette_map,
    contrasting_icon_color,
    scale_radius,
    DEFAULT_COLOR,
    POI_TYPE_COLORS,
    POI_TYPE_ICON,
    QUALITATIVE_PALETTE,
    STAR_COLORS,
)

st.set_page_config(page_title="WC2030 Maroc – Carte Hôtels", page_icon="🗺️", layout="wide")

DATA_DIR = Path("data")
DEFAULT_HOTELS = DATA_DIR / "sample_hotels.xlsx"
DEFAULT_POIS = DATA_DIR / "sample_poi.xlsx"
# Fichiers réels de l'utilisateur : écrasés à chaque upload, conservés entre les sessions.
HOTELS_PERSIST_PATH = DATA_DIR / "hotels.xlsx"
POIS_PERSIST_PATH = DATA_DIR / "poi.xlsx"
# Préférences d'affichage (taille/couleur des bulles) : réécrit à chaque
# changement, pour ne pas avoir à refaire ses couleurs à chaque redémarrage
# de l'app (la session Streamlit, elle, ne survit pas à un redémarrage).
UI_PREFS_PATH = DATA_DIR / "ui_prefs.json"

COLOR_MODES = {
    "Nouveau classement assimilé (étoiles)": "stars",
    "Nouveau classement assimilé (valeur brute)": "Nouveau classement assimilé",
    "Catégorie": "Catégorie",
    "Nouveau Statut vérifié": "Nouveau Statut vérifié",
    "Ville hôte": "Ville hôte",
    "Signature": "Signature",
    "Risque": "Risque",
    "Visite": "Visite",
    "Note Booking (tranches)": "booking_bands",
}

# Fonds de carte gratuits, sans clé API (CARTO exige désormais une clé,
# on utilise donc Esri/OpenStreetMap/OpenTopoMap qui restent libres d'accès).
BASEMAPS = {
    "Standard (rues, recommandé)": {"tiles": "OpenStreetMap", "attr": None, "max_zoom": 19},
    "Clair épuré (gris)": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attr": "Tiles &copy; Esri — Esri, DeLorme, NAVTEQ",
        "max_native_zoom": 16, "max_zoom": 19,
    },
    "Satellite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Tiles &copy; Esri — Esri, Maxar, Earthstar Geographics",
        "max_zoom": 19,
    },
    "Relief": {
        "tiles": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "attr": "OpenTopoMap",
        "max_native_zoom": 17, "max_zoom": 19,
    },
}


class _EstimateBanner(folium.MacroElement):
    """Bandeau d'avertissement ajouté comme un vrai contrôle Leaflet (coin
    "topright"), pas comme un <div> flottant par-dessus la carte : Leaflet
    limite lui-même (via son propre CSS) les événements de souris à la seule
    zone du contrôle, donc ça ne peut jamais bloquer le survol/clic des
    bulles ailleurs sur la carte — contrairement à un <div
    style="position:fixed"> ad hoc, dont le calcul de taille par le
    navigateur peut être imprévisible selon le contexte."""

    _template = Template(u"""
        {% macro script(this, kwargs) %}
        (function() {
            var banner = L.control({position: "topright"});
            banner.onAdd = function(map) {
                var div = L.DomUtil.create("div");
                div.innerHTML = {{ this.text|tojson }};
                div.style.background = "#ffe066";
                div.style.color = "#5c4400";
                div.style.padding = "6px 12px";
                div.style.marginTop = "6px";
                div.style.borderRadius = "6px";
                div.style.fontWeight = "600";
                div.style.fontSize = "13px";
                div.style.boxShadow = "0 1px 4px rgba(0,0,0,0.3)";
                div.style.border = "1px solid #f0c419";
                return div;
            };
            banner.addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
    """)

    def __init__(self, text):
        super().__init__()
        self._name = "EstimateBanner"
        self.text = text


# Zoom minimum à partir duquel les badges "note Booking" apparaissent
# au-dessus des bulles d'hôtel — en dessous, avec des milliers d'hôtels
# affichés, ça surchargerait complètement la carte.
BOOKING_BADGE_MIN_ZOOM = 15
BOOKING_BADGE_CLASS = "booking-badge-wrap"


class _BadgeZoomVisibility(folium.MacroElement):
    """Affiche/masque les badges "note Booking" (classe BOOKING_BADGE_CLASS)
    selon le niveau de zoom courant, en CSS pur piloté par une seule classe
    sur le conteneur de la carte (mise à jour sur l'évènement "zoomend") —
    plutôt que de parcourir chaque badge un par un à chaque zoom, ce qui
    serait bien plus coûteux avec des milliers d'hôtels."""

    _template = Template(u"""
        {% macro header(this, kwargs) %}
        <style>
        .leaflet-container:not(.show-booking-badges) .""" + BOOKING_BADGE_CLASS + """ { display: none !important; }
        </style>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (function() {
            var map = {{ this._parent.get_name() }};
            function updateBadgeVisibility() {
                var el = map.getContainer();
                if (map.getZoom() >= {{ this.min_zoom }}) {
                    el.classList.add("show-booking-badges");
                } else {
                    el.classList.remove("show-booking-badges");
                }
            }
            map.on("zoomend", updateBadgeVisibility);
            updateBadgeVisibility();
        })();
        {% endmacro %}
    """)

    def __init__(self, min_zoom):
        super().__init__()
        self._name = "BadgeZoomVisibility"
        self.min_zoom = min_zoom


def booking_badge_html(note) -> str:
    """Petit badge façon Booking.com (fond bleu, texte blanc, note à une
    décimale et virgule française) au-dessus d'une bulle d'hôtel ; fond
    gris clair et "-" pour un hôtel sans note."""
    has_note = note is not None and not (isinstance(note, float) and pd.isna(note))
    if has_note:
        text = f"{float(note):.1f}".replace(".", ",")
        bg, fg = "#003580", "#ffffff"
    else:
        text, bg, fg = "-", "#e0e0e0", "#767676"
    return (
        f'<div class="{BOOKING_BADGE_CLASS}" style="display:inline-block;background:{bg};color:{fg};'
        f'font:700 11px/1.4 Arial,sans-serif;padding:1px 6px;border-radius:4px;'
        f'white-space:nowrap;box-shadow:0 1px 2px rgba(0,0,0,0.35);">{text}</div>'
    )

HOTEL_INFO_FIELDS = [
    "Nom", "Ville hôte", "Ville", "Catégorie", "Nouveau classement assimilé",
    "Nouveau Statut vérifié", "Capacité act (cha.)", "#Chambres alloues total",
    "Allocations", "PMC vérif", "Propriétaire", "Opérateur", "Signature",
    "Signature Prop", "Signature Op", "Risque", "Visite", "Note Booking", "ID",
]
DEFAULT_TOOLTIP_FIELDS = [
    "Nom", "Ville hôte", "Nouveau classement assimilé", "Capacité act (cha.)", "Nouveau Statut vérifié",
]

# Rayon (px) des hôtels sans valeur pour le champ de taille choisi : fixe,
# non affecté par le curseur d'échelle des bulles.
BASE_DOT_RADIUS = 3

# Taille (px) du badge rond des points d'intérêt. Un DivIcon HTML plutôt que
# folium.Icon (Leaflet.AwesomeMarkers) : ce dernier n'accepte qu'une palette
# de couleurs de fond nommée fixe, pas une couleur arbitraire choisie par
# l'utilisateur.
POI_MARKER_SIZE_PX = 26

CATEGORICAL_FILTERS = [
    ("Ville hôte", "Ville hôte"),
    ("Ville", "Ville"),
    ("Catégorie", "Catégorie"),
    ("Nouveau classement assimilé", "Nouveau classement assimilé"),
    ("Nouveau Statut vérifié", "Nouveau Statut vérifié"),
    ("Propriétaire", "Propriétaire"),
    ("Opérateur", "Opérateur"),
    ("Signature Prop", "Signature Prop"),
    ("Signature Op", "Signature Op"),
    ("Signature global", "Signature"),
    ("Risque", "Risque"),
    ("Visite", "Visite"),
]

NUMERIC_FILTERS = [
    ("Capacité (chambres)", "Capacité act (cha.)"),
    ("Prix moyen chambre (PMC vérif)", "PMC vérif"),
    ("Chambres allouées (total)", "#Chambres alloues total"),
    ("Note Booking", "Note Booking"),
]

DATE_FILTERS = [
    ("Date d'ouverture", "Date ouverture"),
    ("Date dernière rénovation", "Date dernière réno"),
    ("Date prochaine rénovation", "Date prochaine réno"),
]

# Un point de référence est soit posé librement (clic sur la carte ou
# coordonnées saisies), soit un point d'intérêt choisi dans la liste. Seul le
# second cas a un vrai trajet routier associé (voir sidebar_distance_filter) :
# un point libre reste toujours en distance à vol d'oiseau.
REF_SOURCE_MANUAL = "Point choisi (clic sur la carte ou coordonnées)"
REF_SOURCE_POI = "Point d'intérêt"


# Clés de session_state persistées sur disque pour la taille/couleur des
# bulles (voir load_ui_prefs/save_ui_prefs) : simples (une valeur), les
# "colormap_*" (une par critère de couleur déjà utilisé, dynamiques) sont
# gérées à part car leur nombre dépend de ce que l'utilisateur a exploré.
STYLE_PREF_KEYS = ["size_mode", "size_scale", "color_mode_label"]


def load_ui_prefs() -> dict:
    if UI_PREFS_PATH.exists():
        try:
            return json.loads(UI_PREFS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_ui_prefs(prefs: dict) -> None:
    try:
        UI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        UI_PREFS_PATH.write_text(json.dumps(prefs))
    except OSError:
        pass


def _decode_color_map(color_mode: str, saved_map: dict) -> dict:
    """Les clés d'un dict de couleurs redeviennent des chaînes après un
    aller-retour JSON ; le mode "stars" a besoin de vraies clés entières
    pour matcher les valeurs (déjà des int/float) issues du dataframe."""
    if color_mode != "stars":
        return dict(saved_map)
    decoded = {}
    for k, v in saved_map.items():
        try:
            decoded[int(float(k))] = v
        except (TypeError, ValueError):
            continue
    return decoded


def hydrate_style_prefs():
    """Pré-remplit session_state avec les préférences de taille/couleur des
    bulles sauvegardées sur disque, avant que les widgets correspondants ne
    soient créés — pour que l'app redémarre exactement comme l'utilisateur
    l'a laissée plutôt qu'avec les couleurs par défaut."""
    if st.session_state.get("_style_prefs_hydrated"):
        return
    st.session_state["_style_prefs_hydrated"] = True
    prefs = load_ui_prefs()
    for key in STYLE_PREF_KEYS:
        if key in prefs:
            st.session_state.setdefault(key, prefs[key])
    for state_key, saved_map in prefs.get("colormaps", {}).items():
        color_mode = state_key[len("colormap_"):] if state_key.startswith("colormap_") else state_key
        st.session_state.setdefault(state_key, _decode_color_map(color_mode, saved_map))
    if "poi_colormap" in prefs:
        st.session_state.setdefault("poi_colormap", dict(prefs["poi_colormap"]))
    if prefs.get("booking_band_edges"):
        st.session_state.setdefault("booking_band_edges", list(prefs["booking_band_edges"]))
    if prefs.get("booking_band_colors"):
        st.session_state.setdefault("booking_band_colors", list(prefs["booking_band_colors"]))
    if prefs.get("booking_band_ids"):
        st.session_state.setdefault("booking_band_ids", list(prefs["booking_band_ids"]))
    if "booking_band_next_id" in prefs:
        st.session_state.setdefault("booking_band_next_id", prefs["booking_band_next_id"])
    if prefs.get("booking_band_missing_color"):
        st.session_state.setdefault("booking_band_missing_color", prefs["booking_band_missing_color"])


def _default_if_unset(key, **defaults):
    """kwargs de valeur par défaut d'un widget, seulement si sa clé n'est
    pas déjà dans session_state — évite l'avertissement Streamlit sur une
    valeur par défaut redondante avec une clé déjà pré-remplie (ex. via
    hydrate_style_prefs), sans perdre le défaut sensé pour un tout nouvel
    utilisateur qui n'a encore aucune préférence sauvegardée."""
    return {} if key in st.session_state else defaults


def init_state():
    hydrate_style_prefs()
    st.session_state.setdefault("ref_point", None)
    st.session_state.setdefault("ref_source", REF_SOURCE_MANUAL)
    st.session_state.setdefault("radius_km", 15)
    st.session_state.setdefault("distance_filter_on", False)
    st.session_state.setdefault("distance_mode", "Distance (vol d'oiseau)")
    st.session_state.setdefault("escort_mode", False)
    st.session_state.setdefault("escort_reduction_pct", 20)
    st.session_state.setdefault("max_travel_minutes", 30)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Hôtels filtrés")
    return buffer.getvalue()


def get_color_map(color_mode, values):
    """Palette éditable par l'utilisateur pour le critère de couleur choisi,
    conservée en session pour ne pas revenir aux couleurs par défaut à
    chaque interaction."""
    state_key = f"colormap_{color_mode}"
    if state_key not in st.session_state:
        if color_mode == "stars":
            st.session_state[state_key] = {v: STAR_COLORS.get(int(v), DEFAULT_COLOR) for v in values}
        else:
            st.session_state[state_key] = build_palette_map(values)
    else:
        for v in values:
            if v not in st.session_state[state_key]:
                st.session_state[state_key][v] = DEFAULT_COLOR
    return st.session_state[state_key]


def get_poi_color_map(poi_types):
    """Palette éditable par l'utilisateur pour la couleur des points
    d'intérêt (une couleur par type/onglet), conservée en session."""
    state_key = "poi_colormap"
    if state_key not in st.session_state:
        st.session_state[state_key] = {t: POI_TYPE_COLORS.get(t, DEFAULT_COLOR) for t in poi_types}
    else:
        for t in poi_types:
            if t not in st.session_state[state_key]:
                st.session_state[state_key][t] = POI_TYPE_COLORS.get(t, DEFAULT_COLOR)
    return st.session_state[state_key]


# Tranches par défaut pour la coloration "Note Booking (tranches)" : 3
# tranches (faible/moyen/élevé), bornes 0 et 10 fixes, incluses à gauche et
# exclues à droite sauf la dernière (10 inclus, sinon une note de 10,0
# pile ne rentrerait dans aucune tranche).
BOOKING_BAND_DEFAULT_EDGES = [0.0, 6.0, 8.0, 10.0]
BOOKING_BAND_DEFAULT_COLORS = ["#c62828", "#f9a825", "#2e7d32"]
# Nettement plus clair que DEFAULT_COLOR (#616161, pensé pour du texte/une
# bordure) : ici c'est le remplissage d'une bulle entière, un gris aussi
# foncé écrase visuellement les vraies tranches de couleur à côté.
BOOKING_BAND_DEFAULT_MISSING_COLOR = "#d9d9d9"


def get_booking_bands():
    """(edges, colors, ids, missing_color) éditables par l'utilisateur pour
    la coloration par tranche de note Booking, conservés en session (et sur
    disque, voir hydrate_style_prefs) — même principe que
    get_color_map/get_poi_color_map pour les autres critères.

    `ids` donne à chaque tranche un identifiant STABLE, indépendant de sa
    position dans la liste : les widgets (color_picker, number_input) de
    l'éditeur sont callés par cet identifiant plutôt que par leur index de
    boucle, sinon insérer une tranche au milieu décale les positions et
    Streamlit, qui garde l'état de chaque widget par sa clé, réaffiche la
    valeur de l'ANCIENNE tranche qui occupait cette position plutôt que la
    nouvelle — un bug bien réel observé lors des tests (les couleurs se
    décalaient silencieusement d'une tranche après un ajout).

    `missing_color` est la couleur des hôtels sans note Booking du tout —
    éditable séparément, pas figée sur le gris générique de l'app."""
    st.session_state.setdefault("booking_band_edges", list(BOOKING_BAND_DEFAULT_EDGES))
    st.session_state.setdefault("booking_band_colors", list(BOOKING_BAND_DEFAULT_COLORS))
    st.session_state.setdefault("booking_band_ids", list(range(len(BOOKING_BAND_DEFAULT_COLORS))))
    st.session_state.setdefault("booking_band_next_id", len(BOOKING_BAND_DEFAULT_COLORS))
    st.session_state.setdefault("booking_band_missing_color", BOOKING_BAND_DEFAULT_MISSING_COLOR)
    ids = st.session_state["booking_band_ids"]
    colors = st.session_state["booking_band_colors"]
    # Robustesse : si edges/colors ont été chargés depuis un ui_prefs.json
    # plus ancien (sans "ids"), ou désynchronisés d'une façon ou d'une
    # autre, on complète plutôt que de planter.
    while len(ids) < len(colors):
        ids.append(st.session_state["booking_band_next_id"])
        st.session_state["booking_band_next_id"] += 1
    del ids[len(colors):]
    return st.session_state["booking_band_edges"], colors, ids, st.session_state["booking_band_missing_color"]


def _next_booking_band_id():
    st.session_state.setdefault("booking_band_next_id", 0)
    new_id = st.session_state["booking_band_next_id"]
    st.session_state["booking_band_next_id"] += 1
    return new_id


def booking_band_index(value, edges):
    """Indice (0-based) de la tranche à laquelle appartient `value`, ou None
    si `value` est manquante. Bornes incluses à gauche, exclues à droite,
    sauf la toute dernière tranche (incluse des deux côtés)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    n_bands = len(edges) - 1
    for i in range(n_bands):
        lo, hi = edges[i], edges[i + 1]
        if lo <= value < hi or (i == n_bands - 1 and lo <= value <= hi):
            return i
    return 0 if value < edges[0] else n_bands - 1


def render_booking_band_editor(edges, colors, ids):
    """UI pour éditer les tranches de la coloration "Note Booking
    (tranches)" : bornes 0 et 10 fixes (imposées par le format de la note),
    une borne min et une borne max affichées pour chaque tranche (la min
    n'est jamais éditable directement — elle recopie toujours la max de la
    tranche précédente, pour garantir qu'elles restent identiques des deux
    côtés), au 0,1 près (les flèches haut/bas de l'input numérique), une
    couleur par tranche, et un bouton pour en ajouter une nouvelle (scinde
    en deux la tranche la plus large actuellement). Les widgets éditables
    sont callés par `ids[i]` (identifiant stable de la tranche), jamais
    par `i` lui-même (voir la docstring de get_booking_bands pour
    pourquoi).

    La borne min et la max fixe (dernière tranche) sont affichées avec
    st.metric plutôt qu'un number_input désactivé : un number_input garde
    son état par sa clé même désactivé, donc un nouveau `value=` passé à
    un rechargement ultérieur est silencieusement ignoré (Streamlit ne
    l'utilise que pour le tout premier rendu de cette clé) — bug constaté
    à l'usage, la min affichée restait bloquée sur l'ancienne valeur après
    avoir changé la max de la tranche précédente. st.metric n'a pas cet
    état : il affiche toujours la valeur qu'on lui passe, à chaque
    rechargement."""
    n_bands = len(edges) - 1
    for i in range(n_bands):
        is_last = i == n_bands - 1
        band_id = ids[i]
        st.markdown(f"**Tranche {i + 1}**")
        min_col, max_col, color_col = st.columns([1, 1, 1])
        min_col.metric("Min", f"{edges[i]:.1f}".replace(".", ","))
        if is_last:
            max_col.metric("Max", "10,0 (fixe)")
        else:
            next_hi = edges[i + 2] if i + 2 < len(edges) else 10.0
            new_hi = max_col.number_input(
                "Max", min_value=round(edges[i] + 0.1, 1), max_value=round(next_hi - 0.1, 1),
                value=edges[i + 1], step=0.1, format="%.1f", key=f"booking_band_max_{band_id}",
            )
            edges[i + 1] = round(new_hi, 1)
        colors[i] = color_col.color_picker("Couleur", colors[i], key=f"booking_band_color_{band_id}")

    st.markdown("**Sans note Booking**")
    st.color_picker("Couleur", key="booking_band_missing_color")

    can_add = any(round(edges[i + 1] - edges[i], 1) >= 0.2 for i in range(n_bands))
    if st.button("➕ Ajouter une tranche", disabled=not can_add, key="add_booking_band"):
        widths = [edges[i + 1] - edges[i] for i in range(n_bands)]
        idx = widths.index(max(widths))
        mid = round((edges[idx] + edges[idx + 1]) / 2, 1)
        if edges[idx] < mid < edges[idx + 1]:
            edges.insert(idx + 1, mid)
            colors.insert(idx + 1, QUALITATIVE_PALETTE[len(colors) % len(QUALITATIVE_PALETTE)])
            ids.insert(idx + 1, _next_booking_band_id())
        st.rerun()
    if not can_add:
        st.caption("Résolution maximale atteinte (0,1) — impossible d'ajouter une tranche plus fine.")


MISSING_LABEL = "Non renseigné"
# Champs numériques affichés avec une décimale plutôt qu'arrondis à l'entier.
ONE_DECIMAL_FIELDS = {"Note Booking"}


def format_field_value(field, val):
    """Formate une valeur pour l'affichage, ou renvoie None si elle est
    manquante (à charge de l'appelant d'afficher un texte comme
    "Non renseigné" en italique dans ce cas)."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if field in ONE_DECIMAL_FIELDS:
            return f"{val:.1f}"
        return f"{val:,.0f}".replace(",", " ")
    # Échappement HTML : les valeurs viennent d'un fichier Excel externe et
    # peuvent contenir des caractères (<, >, &, guillemets, backtick...) qui
    # casseraient le HTML/JS généré pour les infobulles si insérés tels quels.
    return html_lib.escape(str(val))


def _main_photo_html(hotel_id, width, style):
    """Vignette de la première photo de l'hôtel, ou None si aucune photo
    n'existe ou n'a pas pu être chargée (fichier corrompu, format non
    supporté...) — jamais d'exception propagée jusqu'à l'appelant, pour ne
    jamais faire disparaître tout le marqueur à cause d'une seule photo."""
    try:
        photos = get_hotel_photos(hotel_id)
    except Exception:
        return None, 0
    if not photos:
        return None, 0
    try:
        uri = get_thumbnail_data_uri(photos[0], width)
    except Exception:
        return None, len(photos)
    return f'<img src="{uri}" style="{style}">', len(photos)


# Largeur FIXE (pas max-width) du texte des infobulles/popups : Leaflet
# positionne ces éléments en `position:absolute`/`fixed`, et pour ce type
# d'élément, une largeur seulement "max" (auto en dessous) fait calculer au
# navigateur une largeur "shrink-to-fit" — qui, combinée à un mot très long
# sans espace, peut s'effondrer à la largeur d'un seul caractère (chaque
# lettre sur sa propre ligne). Une largeur fixe élimine complètement ce
# calcul et garantit un retour à la ligne normal, quelle que soit la
# longueur du texte, sans jamais dépasser le bord de la carte.
# `white-space:normal` est indispensable ici aussi : Leaflet force
# temporairement `nowrap` sur SON conteneur (le `.leaflet-tooltip` autour du
# nôtre) pour mesurer sa taille naturelle avant de figer sa hauteur en
# pixels — sans notre propre `white-space:normal` explicite, notre `<div>`
# hériterait ce `nowrap` le temps de cette mesure, Leaflet figerait alors
# une hauteur d'une seule ligne, et le texte, une fois revenu à un
# affichage normal sur plusieurs lignes, déborderait du fond blanc devenu
# trop petit.
TOOLTIP_TEXT_WIDTH_PX = 440
POPUP_TEXT_WIDTH_PX = 480


def build_tooltip_html(row, fields):
    photo_html, n_photos = _main_photo_html(row.get("ID"), 160, "display:block;border-radius:4px;margin-bottom:4px;")

    text_parts = []
    for field in fields:
        formatted = format_field_value(field, row.get(field))
        value_html = formatted if formatted is not None else f"<i>{MISSING_LABEL}</i>"
        if field == "Nom":
            text_parts.insert(0, f"<b>{formatted or MISSING_LABEL}</b>")
        else:
            text_parts.append(f"{field} : {value_html}")
    if n_photos > 1:
        text_parts.append(f'<span style="color:#666;font-size:0.85em;">📸 +{n_photos - 1} photo(s) — clique pour voir</span>')
    text_html = f'<div style="width:{TOOLTIP_TEXT_WIDTH_PX}px;overflow-wrap:break-word;white-space:normal;">' + "<br>".join(text_parts or [f"<i>{MISSING_LABEL}</i>"]) + "</div>"
    return (photo_html or "") + text_html


def build_popup_html(row):
    def fmt(field, suffix=""):
        val = format_field_value(field, row.get(field))
        return f"{val}{suffix}" if val is not None else f"<i>{MISSING_LABEL}</i>"

    lines = []
    photo_html, n_photos = _main_photo_html(row.get("ID"), POPUP_TEXT_WIDTH_PX, "display:block;border-radius:4px;margin-bottom:6px;max-width:100%;")
    if photo_html:
        lines.append(photo_html)
        if n_photos > 1:
            lines.append(f'<span style="color:#666;font-size:0.85em;">📸 +{n_photos - 1} autre(s) photo(s) — galerie sous la carte ⬇️</span>')

    lines += [
        f"<b>{format_field_value('Nom', row.get('Nom')) or MISSING_LABEL}</b>",
        f"Ville hôte : {fmt('Ville hôte')}",
        f"Catégorie : {fmt('Catégorie')}",
        f"Classement : {fmt('Nouveau classement assimilé')}",
        f"Statut : {fmt('Nouveau Statut vérifié')}",
        f"Capacité : {fmt('Capacité act (cha.)', ' ch.')}",
        f"Chambres allouées : {fmt('#Chambres alloues total')}",
        f"Allocations : {fmt('Allocations')}",
        f"PMC : {fmt('PMC vérif', ' MAD')}",
        f"Signature : {fmt('Signature')}",
        f"Risque : {fmt('Risque')}",
        f"Note Booking : {fmt('Note Booking')}",
    ]
    return f'<div style="width:{POPUP_TEXT_WIDTH_PX}px;overflow-wrap:break-word;white-space:normal;">' + "<br>".join(lines) + "</div>"


# Tolérance (km) pour rattacher un clic sur un marqueur à l'hôtel dont il
# provient : le marqueur est posé exactement sur les coordonnées de
# l'hôtel, une petite marge suffit à absorber les imprécisions de rendu.
HOTEL_CLICK_TOLERANCE_KM = 0.05


def find_hotel_at(df: pd.DataFrame, lat: float, lon: float):
    """Hôtel de `df` le plus proche de (lat, lon), ou None si aucun n'est à
    moins de HOTEL_CLICK_TOLERANCE_KM (donc pas vraiment "le même point")."""
    coords = df.dropna(subset=["Latitude", "Longitude"])
    if coords.empty:
        return None
    dists = haversine_km(lat, lon, coords["Latitude"].values, coords["Longitude"].values)
    idx = int(np.argmin(dists))
    if dists[idx] > HOTEL_CLICK_TOLERANCE_KM:
        return None
    return coords.iloc[idx]


def render_photo_gallery(hotel_row: pd.Series):
    """Galerie complète des photos d'un hôtel, affichée sous la carte quand
    on clique sur sa bulle — le popup Leaflet lui-même ne peut afficher
    qu'une seule photo de façon fiable (voir historique), donc la galerie
    complète vit ici, en HTML/CSS Streamlit natif plutôt que dans le popup."""
    hotel_id = str(hotel_row.get("ID"))
    try:
        photos = get_hotel_photos(hotel_id)
    except Exception:
        photos = []
    if not photos:
        return

    name = format_field_value("Nom", hotel_row.get("Nom")) or hotel_id
    with st.container(border=True):
        header_col, close_col = st.columns([6, 1])
        header_col.markdown(f"#### 📸 Photos — {name}")
        if close_col.button("✕ Fermer", key=f"close_gallery_{hotel_id}"):
            st.session_state["photos_gallery_dismissed_for"] = hotel_id
            st.rerun()
        cols = st.columns(4)
        shown = 0
        for photo in photos:
            try:
                cols[shown % 4].image(str(photo), width="stretch")
                shown += 1
            except Exception:
                continue
        if shown == 0:
            st.caption("Aucune des photos de cet hôtel n'a pu être chargée.")


@st.cache_data(show_spinner=False)
def _cached_hotels(file_bytes_or_path, is_upload):
    if is_upload:
        return load_hotels(BytesIO(file_bytes_or_path))
    return load_hotels(file_bytes_or_path)


@st.cache_data(show_spinner=False)
def _cached_pois(file_bytes_or_path, is_upload):
    if is_upload:
        return load_pois(BytesIO(file_bytes_or_path))
    return load_pois(file_bytes_or_path)


def _load_with_persistence(uploader_label, uploader_key, persist_path: Path, default_path: Path, loader_bytes_fn):
    """Widget d'upload qui écrit le fichier déposé sur disque (écrasé à chaque
    nouvel upload), pour qu'il soit rechargé automatiquement aux prochaines
    sessions sans avoir à le redéposer. Retourne (dataframe, message d'état).
    À appeler dans un bloc `with` (container/sidebar) pour que les widgets
    s'affichent au bon endroit."""
    uploaded = st.file_uploader(uploader_label, type=["xlsx", "xls"], key=uploader_key)

    if uploaded is not None:
        persist_path.parent.mkdir(parents=True, exist_ok=True)
        persist_path.write_bytes(uploaded.getvalue())
        st.success(f"Fichier enregistré → `{persist_path}`")

    if persist_path.exists():
        mtime = pd.Timestamp.fromtimestamp(persist_path.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
        df = loader_bytes_fn(persist_path.read_bytes(), True)
        status = f"✅ Fichier réel chargé (`{persist_path.name}`, mis à jour le {mtime})"
        if st.button("🗑️ Revenir aux données de démo", key=f"reset_{uploader_key}"):
            persist_path.unlink(missing_ok=True)
            st.rerun()
    else:
        df = loader_bytes_fn(default_path, False)
        status = f"ℹ️ Données de démonstration (`{default_path.name}`) — dépose ton fichier ci-dessus pour le remplacer durablement."

    return df, status


def poi_layer_label(row):
    """Nom de couche affichée pour un point d'intérêt : le type (onglet)
    seul, ou "Type — Sous-type" quand l'onglet distingue des sous-catégories
    (ex. les sites d'entraînement "VSTS" / "TBC" / "RBC"), pour que chacune
    devienne une couche indépendante, activable séparément."""
    sous = row.get("Sous-type")
    if sous is not None and not (isinstance(sous, float) and pd.isna(sous)) and str(sous).strip():
        return f"{row['Type']} — {sous}"
    return row["Type"]


def sidebar_data_sources():
    with st.sidebar.container(border=True):
        st.markdown("### 📂 Données")
        hotels_df, hotels_status = _load_with_persistence(
            "Fichier hôtels (Excel)", "hotel_upload", HOTELS_PERSIST_PATH, DEFAULT_HOTELS, _cached_hotels
        )
        st.caption(hotels_status)

        try:
            pois_df, pois_status = _load_with_persistence(
                "Fichier points d'intérêt (Excel)", "poi_upload", POIS_PERSIST_PATH, DEFAULT_POIS, _cached_pois
            )
            st.caption(pois_status)
        except Exception:
            pois_df = pd.DataFrame(columns=["Nom", "Type", "Ville", "Latitude", "Longitude"])
            st.warning("Impossible de lire le fichier de points d'intérêt.")

    if not pois_df.empty:
        pois_df = pois_df.assign(**{"Couche POI": pois_df.apply(poi_layer_label, axis=1)})

    return hotels_df, pois_df


def sidebar_filters(df: pd.DataFrame, data_version=0):
    # `data_version` (mtime du fichier hôtels réel, voir main()) fait partie
    # de la clé de chaque widget de filtre : la valeur d'un widget Streamlit
    # n'est prise en compte qu'à SA toute première création pour une clé
    # donnée — sur les reruns suivants (y compris après un nouvel upload
    # Excel avec des dates/valeurs différentes), Streamlit ignore le nouveau
    # `value=` et garde l'ancien réglage. Sans ce préfixe, un curseur ou une
    # plage de dates réglés sur les bornes d'un ANCIEN fichier restait figé
    # et filtrait silencieusement des hôtels du NOUVEAU fichier qui tombent
    # hors de cette plage périmée — observé en conditions réelles (import
    # d'un nouveau hotels.xlsx, "Hôtels affichés" resté bloqué sous le vrai
    # total). En changeant de préfixe à chaque nouveau fichier, chaque
    # widget redémarre avec la vraie plage complète de CE fichier ; il garde
    # sa valeur normalement tant que le fichier ne change pas.
    filtered = df.copy()

    with st.sidebar.container(border=True):
        st.markdown("### 🔍 Filtres")

        with st.expander("🏙️ Localisation & classification", expanded=True):
            for label, col in CATEGORICAL_FILTERS[:5]:
                options = sorted(filtered[col].dropna().unique().tolist())
                if not options:
                    continue
                chosen = st.multiselect(label, options, default=[], key=f"filt_{data_version}_{col}")
                if chosen:
                    filtered = filtered[filtered[col].isin(chosen)]

        with st.expander("✍️ Parties prenantes & statut"):
            for label, col in CATEGORICAL_FILTERS[5:]:
                options = sorted(filtered[col].dropna().unique().tolist())
                if not options:
                    continue
                chosen = st.multiselect(label, options, default=[], key=f"filt_{data_version}_{col}")
                if chosen:
                    filtered = filtered[filtered[col].isin(chosen)]

        with st.expander("🔢 Capacité, prix & notes"):
            for label, col in NUMERIC_FILTERS:
                series = filtered[col].dropna()
                if series.empty:
                    continue
                vmin, vmax = float(series.min()), float(series.max())
                if vmin == vmax:
                    continue
                lo, hi = st.slider(label, min_value=float(np.floor(vmin)), max_value=float(np.ceil(vmax)),
                                    value=(float(np.floor(vmin)), float(np.ceil(vmax))), key=f"filt_{data_version}_{col}")
                filtered = filtered[filtered[col].between(lo, hi) | filtered[col].isna()]

        with st.expander("📅 Dates"):
            for label, col in DATE_FILTERS:
                series = filtered[col].dropna()
                if series.empty:
                    continue
                dmin, dmax = series.min().date(), series.max().date()
                if dmin == dmax:
                    continue
                lo, hi = st.date_input(label, value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"filt_{data_version}_{col}")
                if isinstance(lo, tuple):
                    lo, hi = lo
                filtered = filtered[(filtered[col].dt.date.between(lo, hi)) | filtered[col].isna()]

        with st.expander("🔎 Recherche"):
            search = st.text_input("Nom de l'hôtel contient...", "")
            only_geo = st.checkbox("Afficher uniquement les hôtels géolocalisés", value=True)
            if search:
                filtered = filtered[filtered["Nom"].str.contains(search, case=False, na=False)]
            if only_geo:
                filtered = filtered[filtered["Géolocalisé"]]

    return filtered


def sidebar_map_settings(hotels_df: pd.DataFrame, pois_df: pd.DataFrame):
    with st.sidebar.container(border=True):
        st.markdown("### 🗺️ Carte")

        basemap_choice = st.selectbox("Fond de carte", list(BASEMAPS.keys()), index=0)

        st.markdown("**Couches**")
        show_hotels = st.checkbox("Hôtels", value=True)
        active_poi_layers = []
        poi_types = sorted(pois_df["Type"].dropna().unique().tolist()) if not pois_df.empty else []
        if poi_types:
            st.caption("Points d'intérêt")
            for t in poi_types:
                layer_names = sorted(pois_df.loc[pois_df["Type"] == t, "Couche POI"].dropna().unique().tolist())
                if len(layer_names) <= 1:
                    layer_name = layer_names[0] if layer_names else t
                    if st.checkbox(f"　{t}", value=True, key=f"poi_{layer_name}"):
                        active_poi_layers.append(layer_name)
                else:
                    st.caption(f"　{t}")
                    for layer_name in layer_names:
                        sub_label = layer_name.split(" — ", 1)[-1]
                        if st.checkbox(f"　　{sub_label}", value=True, key=f"poi_{layer_name}"):
                            active_poi_layers.append(layer_name)

        poi_color_map = get_poi_color_map(poi_types)
        if poi_types:
            with st.expander("🎨 Couleurs des points d'intérêt"):
                st.caption("La couleur de l'icône à l'intérieur (noir/blanc) s'adapte automatiquement pour rester lisible.")
                for t in poi_types:
                    poi_color_map[t] = st.color_picker(t, poi_color_map.get(t, DEFAULT_COLOR), key=f"poi_cp_{t}")

        st.markdown("**Taille des bulles**")
        size_options = dict(NUMERIC_FILTERS)
        size_label = st.selectbox("Taille selon", list(size_options.keys()), key="size_mode", **_default_if_unset("size_mode", index=0))
        size_col = size_options[size_label]
        size_scale = st.slider("Échelle des bulles", min_value=0.4, max_value=3.0, step=0.1, key="size_scale", **_default_if_unset("size_scale", value=1.0))
        st.caption("Un hôtel sans valeur pour ce champ garde un petit point de taille fixe, quelle que soit l'échelle.")

        st.markdown("**Style des bulles**")
        color_label = st.selectbox("Couleur selon", list(COLOR_MODES.keys()), key="color_mode_label", **_default_if_unset("color_mode_label", index=0))
        color_mode = COLOR_MODES[color_label]

        if color_mode == "booking_bands":
            values = []
            edges, colors, ids, missing_color = get_booking_bands()
            color_map = {"edges": edges, "colors": colors, "missing_color": missing_color}
            with st.expander("🎨 Personnaliser les couleurs", expanded=False):
                render_booking_band_editor(edges, colors, ids)
        else:
            if color_mode == "stars":
                values = sorted(v for v in hotels_df["Étoiles (estimées)"].dropna().unique().tolist())
            elif color_mode in hotels_df.columns:
                values = sorted(hotels_df[color_mode].dropna().unique().tolist())
            else:
                values = []
            color_map = get_color_map(color_mode, values)

            with st.expander("🎨 Personnaliser les couleurs"):
                if values:
                    for v in values:
                        label = f"{int(v)} ★" if color_mode == "stars" else str(v)
                        color_map[v] = st.color_picker(label, color_map.get(v, DEFAULT_COLOR), key=f"cp_{color_mode}_{v}")
                else:
                    st.caption("Aucune valeur à colorer pour ce critère.")

        st.markdown("**Infos au survol**")
        tooltip_fields = st.multiselect(
            "Champs affichés en survolant un hôtel", HOTEL_INFO_FIELDS,
            default=DEFAULT_TOOLTIP_FIELDS, key="tooltip_fields",
        )

    # Sauvegarde sur disque de la taille/couleur des bulles à chaque
    # interaction, pour retrouver exactement la même config après un
    # redémarrage de l'app plutôt que de devoir refaire ses couleurs.
    save_ui_prefs({
        **{k: st.session_state[k] for k in STYLE_PREF_KEYS if k in st.session_state},
        "colormaps": {k: v for k, v in st.session_state.items() if k.startswith("colormap_")},
        "poi_colormap": st.session_state.get("poi_colormap", {}),
        "booking_band_edges": st.session_state.get("booking_band_edges", []),
        "booking_band_colors": st.session_state.get("booking_band_colors", []),
        "booking_band_ids": st.session_state.get("booking_band_ids", []),
        "booking_band_next_id": st.session_state.get("booking_band_next_id", 0),
        "booking_band_missing_color": st.session_state.get(
            "booking_band_missing_color", BOOKING_BAND_DEFAULT_MISSING_COLOR
        ),
    })

    return show_hotels, active_poi_layers, color_mode, color_label, color_map, poi_color_map, basemap_choice, tooltip_fields, size_col, size_label, size_scale


def sidebar_distance_filter(pois_df: pd.DataFrame):
    with st.sidebar.container(border=True):
        st.markdown("### 📍 Distance / Temps de trajet")
        st.session_state["distance_filter_on"] = st.checkbox(
            "Activer le filtre", value=st.session_state["distance_filter_on"]
        )
        if not st.session_state["distance_filter_on"]:
            return

        source_options = [REF_SOURCE_MANUAL, REF_SOURCE_POI]
        st.session_state["ref_source"] = st.radio(
            "Point de référence", source_options,
            index=source_options.index(st.session_state["ref_source"]),
        )
        is_poi_source = st.session_state["ref_source"] == REF_SOURCE_POI and not pois_df.empty

        if is_poi_source:
            st.session_state["distance_mode"] = st.radio(
                "Filtrer selon", ["Distance (vol d'oiseau)", "Temps de trajet (voiture)"],
                index=["Distance (vol d'oiseau)", "Temps de trajet (voiture)"].index(st.session_state["distance_mode"]),
            )
        else:
            st.session_state["distance_mode"] = "Distance (vol d'oiseau)"
            st.caption(
                "Un point cliqué sur la carte ou saisi à la main n'a pas de trajet routier "
                "associé : seule la distance à vol d'oiseau est disponible. Choisis un point "
                "d'intérêt comme référence pour activer le temps de trajet."
            )
        is_travel_time = st.session_state["distance_mode"] == "Temps de trajet (voiture)"

        st.session_state["radius_km"] = st.slider(
            "Rayon (km)", min_value=1, max_value=150, value=st.session_state["radius_km"],
        )
        if is_travel_time:
            st.session_state["max_travel_minutes"] = st.slider(
                "Temps de trajet max (minutes)", min_value=5, max_value=180,
                value=st.session_state["max_travel_minutes"], step=5,
            )
            st.session_state["escort_mode"] = st.checkbox(
                "🚔 Mode Escorte (trajet accéléré)", value=st.session_state["escort_mode"],
                help="Simule un trajet escorté : réduit le temps de trajet calculé d'un pourcentage donné.",
            )
            if st.session_state["escort_mode"]:
                st.session_state["escort_reduction_pct"] = st.slider(
                    "Réduction du temps de trajet (%)", min_value=0, max_value=60,
                    value=st.session_state["escort_reduction_pct"],
                )
            provider = "OpenRouteService (clé configurée)" if using_ors() else "OSRM public (aucune clé configurée)"
            st.caption(f"Service de routage actif : **{provider}**. Chaque trajet calculé est mis en cache sur disque et n'est jamais recalculé.")

        if is_poi_source:
            poi_types = sorted(pois_df["Type"].dropna().unique().tolist())
            poi_type_choice = st.selectbox("Type de point d'intérêt", poi_types, key="poi_type_choice")
            subset = pois_df[pois_df["Type"] == poi_type_choice]

            city_choice = "Toutes"
            if subset["Ville"].notna().any():
                cities = sorted(subset["Ville"].dropna().unique().tolist())
                city_choice = st.selectbox("Ville", ["Toutes"] + cities, key=f"poi_city_choice_{poi_type_choice}")
                if city_choice != "Toutes":
                    subset = subset[subset["Ville"] == city_choice]

            subset_sorted = subset.sort_values("Nom").reset_index(drop=True)
            placeholder = "— Choisir un point d'intérêt —"
            poi_options = [placeholder] + subset_sorted["Nom"].tolist()
            poi_choice = st.selectbox(
                "Point d'intérêt", poi_options, key=f"poi_ref_choice_{poi_type_choice}_{city_choice}"
            )
            if poi_choice != placeholder:
                selected = subset_sorted[subset_sorted["Nom"] == poi_choice].iloc[0]
                st.session_state["ref_point"] = (float(selected["Latitude"]), float(selected["Longitude"]))
        else:
            col1, col2 = st.columns(2)
            with col1:
                lat_in = st.number_input("Latitude", value=float(st.session_state["ref_point"][0]) if st.session_state["ref_point"] else 0.0, format="%.6f")
            with col2:
                lon_in = st.number_input("Longitude", value=float(st.session_state["ref_point"][1]) if st.session_state["ref_point"] else 0.0, format="%.6f")
            apply_manual = st.button("Utiliser ces coordonnées")
            if apply_manual and (lat_in != 0.0 or lon_in != 0.0):
                st.session_state["ref_point"] = (lat_in, lon_in)
            st.caption("Ou clique directement sur la carte pour poser le point.")

        if st.button("Réinitialiser le point"):
            st.session_state["ref_point"] = None
            st.session_state["distance_filter_on"] = False


def build_map(hotels_df, pois_df, show_hotels, active_poi_layers, color_mode, color_map, poi_color_map, basemap_choice, tooltip_fields, size_col, size_scale, n_estimated=0):
    # Pas de cache ici : st.cache_resource renvoyait le MÊME objet
    # folium.Map (mêmes noms de variables JS internes) à plusieurs reprises
    # au composant st_folium, qui n'est pas conçu pour recevoir deux fois le
    # même rendu — ça produisait des erreurs JS ("Map container is already
    # initialized", variable "is not defined") et la carte disparaissait.
    # Reconstruire à chaque rerun est plus lent mais fiable.
    center = [31.7917, -7.0926]
    zoom = 5.4
    all_points = hotels_df[["Latitude", "Longitude"]].dropna() if show_hotels else pd.DataFrame(columns=["Latitude", "Longitude"])
    b = bounds_for(all_points) if not all_points.empty else None

    basemap = BASEMAPS.get(basemap_choice, next(iter(BASEMAPS.values())))
    m = folium.Map(location=center, zoom_start=zoom, tiles=None, prefer_canvas=True,
                    max_zoom=basemap.get("max_zoom", 19))
    tile_kwargs = {"tiles": basemap["tiles"], "name": basemap_choice, "max_zoom": basemap.get("max_zoom", 19)}
    if basemap["attr"]:
        tile_kwargs["attr"] = basemap["attr"]
    if basemap.get("max_native_zoom"):
        tile_kwargs["max_native_zoom"] = basemap["max_native_zoom"]
    folium.TileLayer(**tile_kwargs).add_to(m)

    if show_hotels and not hotels_df.empty:
        size_series = hotels_df[size_col].dropna() if size_col in hotels_df.columns else pd.Series(dtype=float)
        size_min, size_max = (size_series.min(), size_series.max()) if not size_series.empty else (0, 1)
        hotel_layer = folium.FeatureGroup(name="Hôtels", show=True)
        for _, row in hotels_df.iterrows():
            if pd.isna(row["Latitude"]) or pd.isna(row["Longitude"]):
                continue
            size_value = row.get(size_col)
            has_size = size_value is not None and not (isinstance(size_value, float) and pd.isna(size_value))
            radius = scale_radius(size_value, size_min, size_max) * size_scale if has_size else BASE_DOT_RADIUS
            if color_mode == "booking_bands":
                band_idx = booking_band_index(row.get("Note Booking"), color_map["edges"])
                color = (
                    color_map["colors"][band_idx]
                    if band_idx is not None
                    else color_map.get("missing_color", DEFAULT_COLOR)
                )
            else:
                cat_value = row["Étoiles (estimées)"] if color_mode == "stars" else row.get(color_mode)
                has_value = cat_value is not None and not (isinstance(cat_value, float) and pd.isna(cat_value))
                color = color_map.get(cat_value, DEFAULT_COLOR) if has_value else DEFAULT_COLOR

            popup_html = build_popup_html(row)
            folium.CircleMarker(
                location=[row["Latitude"], row["Longitude"]],
                radius=radius,
                color=color,
                weight=1.5,
                fill=True,
                fill_color=color,
                fill_opacity=0.75,
                tooltip=folium.Tooltip(escape_backticks(build_tooltip_html(row, tooltip_fields)), sticky=True, direction="auto"),
                popup=folium.Popup(popup_html, max_width=POPUP_TEXT_WIDTH_PX + 60),
            ).add_to(hotel_layer)
            folium.Marker(
                location=[row["Latitude"], row["Longitude"]],
                icon=folium.DivIcon(
                    html=booking_badge_html(row.get("Note Booking")),
                    icon_size=(40, 16),
                    icon_anchor=(20, int(radius) + 12),
                ),
            ).add_to(hotel_layer)
        hotel_layer.add_to(m)
        _BadgeZoomVisibility(BOOKING_BADGE_MIN_ZOOM).add_to(m)

    for layer_name in active_poi_layers:
        subset = pois_df[pois_df["Couche POI"] == layer_name]
        if subset.empty:
            continue
        layer = folium.FeatureGroup(name=layer_name, show=True)
        base_type = subset["Type"].iloc[0]
        bg_color = poi_color_map.get(base_type, POI_TYPE_COLORS.get(base_type, "#333333"))
        icon = POI_TYPE_ICON.get(base_type, "map-marker")
        glyph_color = contrasting_icon_color(bg_color)
        badge_html = (
            f'<div style="background:{bg_color};width:{POI_MARKER_SIZE_PX}px;height:{POI_MARKER_SIZE_PX}px;'
            f'border-radius:50%;border:2px solid rgba(0,0,0,0.35);box-shadow:0 1px 3px rgba(0,0,0,0.4);'
            f'display:flex;align-items:center;justify-content:center;">'
            f'<i class="fa fa-{icon}" style="color:{glyph_color};font-size:{POI_MARKER_SIZE_PX - 12}px;"></i></div>'
        )
        for _, row in subset.iterrows():
            poi_tooltip = escape_backticks(html_lib.escape(f"{row['Nom']} ({layer_name})"))
            folium.Marker(
                location=[row["Latitude"], row["Longitude"]],
                tooltip=folium.Tooltip(poi_tooltip, sticky=True, direction="auto"),
                icon=folium.DivIcon(
                    html=badge_html,
                    icon_size=(POI_MARKER_SIZE_PX, POI_MARKER_SIZE_PX),
                    icon_anchor=(POI_MARKER_SIZE_PX // 2, POI_MARKER_SIZE_PX // 2),
                ),
            ).add_to(layer)
        layer.add_to(m)

    if n_estimated:
        banner_text = f"⚠️ Estimation interpolée ({n_estimated} hôtel(s)) — pas encore un vrai temps de trajet"
        _EstimateBanner(banner_text).add_to(m)

    ref_point = st.session_state.get("ref_point")
    if ref_point and st.session_state.get("distance_filter_on"):
        radius_km = st.session_state["radius_km"]
        folium.Marker(location=list(ref_point), icon=folium.Icon(color="red", icon="crosshairs", prefix="fa"),
                       tooltip="Point de référence").add_to(m)
        # Purement décoratif : ce cercle ne doit jamais intercepter le
        # survol/clic des bulles qu'il recouvre, peu importe l'ordre
        # d'empilement des calques. "interactive" est une option Leaflet
        # valide mais absente de la liste blanche interne de folium
        # (path_options ignore silencieusement tout kwarg qu'elle ne
        # reconnaît pas) — on l'injecte donc directement après coup.
        search_circle = folium.Circle(location=list(ref_point), radius=radius_km * 1000, color="#d62728",
                                       fill=True, fill_opacity=0.05, weight=2)
        search_circle.options["interactive"] = False
        search_circle.add_to(m)
    elif ref_point:
        folium.Marker(location=list(ref_point), icon=folium.Icon(color="red", icon="crosshairs", prefix="fa"),
                       tooltip="Point de référence (clic)").add_to(m)

    if b:
        m.fit_bounds(b)

    return m


def main():
    init_state()
    st.title("🗺️ Coupe du Monde 2030 – Maroc — Cartographie Hôtels & Sites")
    st.caption(
        "Carte interactive type Kepler.gl : couches activables, filtres sur toutes les colonnes, "
        "bulles proportionnelles à la capacité et colorées par qualité, filtre par distance en cliquant sur la carte."
    )

    hotels_df, pois_df = sidebar_data_sources()
    show_hotels, active_poi_layers, color_mode, color_label, color_map, poi_color_map, basemap_choice, tooltip_fields, size_col, size_label, size_scale = sidebar_map_settings(hotels_df, pois_df)
    hotels_data_version = HOTELS_PERSIST_PATH.stat().st_mtime if HOTELS_PERSIST_PATH.exists() else 0
    filtered_df = sidebar_filters(hotels_df, hotels_data_version)
    sidebar_distance_filter(pois_df)

    n_estimated_for_banner = 0
    if st.session_state["distance_filter_on"] and st.session_state["ref_point"]:
        lat0, lon0 = st.session_state["ref_point"]
        dist = haversine_km(lat0, lon0, filtered_df["Latitude"].values, filtered_df["Longitude"].values)
        filtered_df = filtered_df.assign(**{"Distance (km)": dist})
        filtered_df = filtered_df[filtered_df["Distance (km)"] <= st.session_state["radius_km"]]

        if st.session_state["distance_mode"] == "Temps de trajet (voiture)" and not filtered_df.empty:
            with st.spinner(f"Calcul du temps de trajet pour {len(filtered_df)} hôtel(s) (mise en cache pour les prochaines fois)..."):
                travel_times, travel_error, n_estimated = get_travel_times_with_fallback((lat0, lon0), filtered_df)
            n_estimated_for_banner = n_estimated
            raw_minutes = pd.to_numeric(
                pd.Series([travel_times.get(str(hid)) for hid in filtered_df["ID"]], index=filtered_df.index),
                errors="coerce",
            )
            if st.session_state["escort_mode"]:
                effective_minutes = raw_minutes * (1 - st.session_state["escort_reduction_pct"] / 100)
            else:
                effective_minutes = raw_minutes
            filtered_df = filtered_df.assign(**{"Temps de trajet (min)": effective_minutes.round(1)})
            if raw_minutes.notna().sum() == 0:
                st.warning(
                    f"⚠️ Impossible d'obtenir les temps de trajet pour le moment "
                    f"({travel_error or 'raison inconnue'}). Le filtre par distance à vol d'oiseau reste actif."
                )
            else:
                info_bits = []
                if n_estimated:
                    info_bits.append(f"{n_estimated} valeur(s) estimée(s) (pas encore de calcul réel, voir scripts/estimate_travel_times.py)")
                if raw_minutes.isna().any():
                    info_bits.append(f"{int(raw_minutes.isna().sum())} hôtel(s) sans aucune valeur, réelle ou estimée (exclus du filtre)")
                if info_bits:
                    st.caption("ℹ️ " + " · ".join(info_bits) + (f" — {travel_error}" if travel_error else ""))
                filtered_df = filtered_df[filtered_df["Temps de trajet (min)"] <= st.session_state["max_travel_minutes"]]

    n_total = len(hotels_df)
    n_geo = int(hotels_df["Géolocalisé"].sum())
    n_shown = len(filtered_df)

    kpi_cols = st.columns(5)
    kpi_cols[0].metric("Hôtels (base)", f"{n_total}")
    kpi_cols[1].metric("Géolocalisés", f"{n_geo}", help="Nombre d'hôtels avec coordonnées valides dans la base")
    kpi_cols[2].metric("Hôtels affichés", f"{n_shown}")
    kpi_cols[3].metric("Chambres (capacité totale)", f"{int(filtered_df['Capacité act (cha.)'].sum(skipna=True)):,}".replace(",", " "))
    kpi_cols[4].metric("Chambres allouées", f"{int(filtered_df['#Chambres alloues total'].sum(skipna=True)):,}".replace(",", " "))

    fmap = build_map(filtered_df, pois_df, show_hotels, active_poi_layers, color_mode, color_map, poi_color_map, basemap_choice, tooltip_fields, size_col, size_scale, n_estimated_for_banner)
    map_state = st_folium(fmap, use_container_width=True, height=720, key="main_map",
                           returned_objects=["last_clicked", "last_object_clicked"])

    # Traité avant tout ce qui peut déclencher un st.rerun() ci-dessous (le
    # rerun reconstruit la carte avec de nouveaux objets folium, donc de
    # nouveaux noms internes, ce qui repositionne les couches côté
    # composant et perd sa valeur "last_object_clicked" avant qu'on ait pu
    # la lire) : on identifie l'hôtel cliqué tout de suite et on ne garde
    # que son ID en session, pas la valeur volatile du composant.
    obj_clicked = map_state.get("last_object_clicked") if map_state else None
    if obj_clicked:
        clicked_hotel = find_hotel_at(filtered_df, obj_clicked["lat"], obj_clicked["lng"])
        if clicked_hotel is not None:
            st.session_state["selected_hotel_for_gallery"] = str(clicked_hotel.get("ID"))

    selected_id = st.session_state.get("selected_hotel_for_gallery")
    if selected_id and st.session_state.get("photos_gallery_dismissed_for") != selected_id:
        selected_rows = filtered_df[filtered_df["ID"].astype(str) == selected_id]
        if not selected_rows.empty:
            render_photo_gallery(selected_rows.iloc[0])

    if map_state and map_state.get("last_clicked"):
        clicked = map_state["last_clicked"]
        new_point = (clicked["lat"], clicked["lng"])
        if new_point != st.session_state.get("ref_point"):
            # Un clic sur la carte pose toujours un point "libre" : on repasse
            # explicitement en distance à vol d'oiseau (pas de trajet routier
            # associé à un point cliqué au hasard).
            st.session_state["ref_point"] = new_point
            st.session_state["ref_source"] = REF_SOURCE_MANUAL
            st.session_state["distance_mode"] = "Distance (vol d'oiseau)"
            st.rerun()

    st.markdown(f"**Légende couleur : {color_label}**")
    if color_mode == "booking_bands":
        edges, colors = color_map["edges"], color_map["colors"]
        legend_items = [
            (f"[{edges[i]:.1f} – {edges[i + 1]:.1f}{']' if i == len(colors) - 1 else '['}", colors[i])
            for i in range(len(colors))
        ]
        legend_items.append(("Sans note", color_map.get("missing_color", DEFAULT_COLOR)))
    else:
        legend_items = [
            (f"{int(v)} ★" if color_mode == "stars" else str(v), c)
            for v, c in sorted(color_map.items(), key=lambda kv: str(kv[0]))
        ]
    legend_html = " &nbsp; ".join(
        f'<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{c};margin-right:4px;"></span>{lbl}'
        for lbl, c in legend_items
    )
    st.markdown(legend_html, unsafe_allow_html=True)

    st.subheader("📋 Liste des hôtels filtrés")
    display_cols = [c for c in [
        "ID", "Nom", "Ville hôte", "Ville", "Catégorie", "Nouveau classement assimilé",
        "Nouveau Statut vérifié", "Capacité act (cha.)", "#Chambres alloues total",
        "Allocations", "PMC vérif", "Signature", "Risque", "Visite", "Note Booking",
        "Distance (km)", "Temps de trajet (min)",
    ] if c in filtered_df.columns]
    st.dataframe(filtered_df[display_cols].sort_values(display_cols[0]), width="stretch", height=350)

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        st.download_button(
            "⬇️ Télécharger la sélection (Excel)",
            data=to_excel_bytes(filtered_df[display_cols]),
            file_name="hotels_filtres_wc2030.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with dl_col2:
        st.download_button(
            "⬇️ Télécharger la sélection (CSV)",
            data=filtered_df[display_cols].to_csv(index=False).encode("utf-8-sig"),
            file_name="hotels_filtres_wc2030.csv",
            mime="text/csv",
        )

    with st.expander("ℹ️ À propos de cet outil / prochaines étapes"):
        st.markdown(
            """
            - **Carte** : choisis le fond de carte (clair épuré, standard, satellite, relief) et active/désactive les hôtels et chaque couche de point d'intérêt, dans le bloc "🗺️ Carte" de la barre latérale — une couche par type (onglet du fichier POI), et une couche séparée par sous-type quand l'onglet en distingue (ex. sites d'entraînement VSTS / TBC / RBC).
            - **Filtres** : tous les champs du fichier hôtels sont filtrables, regroupés dans le bloc "🔍 Filtres" (localisation, classification, capacité, prix, dates, parties prenantes, signature, risque, visite).
            - **Bulles** : taille = champ numérique au choix (capacité, chambres allouées, PMC, note Booking), ajustable avec le curseur "Échelle des bulles" — un hôtel sans valeur pour ce champ garde un point fixe, non affecté par le curseur ; couleur = critère choisi (classement, catégorie, statut, ville hôte, signature, risque, visite), avec une couleur personnalisable pour chaque valeur via "🎨 Personnaliser les couleurs". À partir d'un certain niveau de zoom, un badge façon Booking.com (fond bleu, note à une décimale) apparaît au-dessus de chaque bulle — gris avec un tiret pour un hôtel sans note.
            - **Survol** : choisis les informations affichées au survol d'un hôtel dans "Infos au survol" (le clic affiche toujours la fiche complète). Un champ sans valeur s'affiche en italique ("Non renseigné") plutôt que d'être masqué.
            - **Distance / Temps de trajet** : active le filtre, puis choisis la source du point de référence — "Point choisi" (clic sur la carte ou coordonnées saisies, toujours en distance à vol d'oiseau) ou "Point d'intérêt" (permet en plus le temps de trajet réel en voiture). En mode point d'intérêt, choisis d'abord le type (stade, site d'entraînement...), puis la ville si l'onglet en propose une, puis le point précis. En mode temps de trajet, le rayon vol d'oiseau sert de pré-filtre, puis le temps réel est calculé via un service de routage en ligne (mis en cache sur disque — un trajet n'est jamais recalculé) ; le "Mode Escorte" permet de simuler un trajet accéléré d'un pourcentage réglable. Un bandeau jaune en haut à droite de la carte signale quand des temps affichés sont des estimations interpolées (pas encore de vrai calcul, voir `scripts/estimate_travel_times.py`).
            - **Photos** : dépose des images dans `data/photos/<ID de l'hôtel>/` (ex. `data/photos/HTL-0001/facade.jpg`) — une vignette apparaît automatiquement au survol, une version plus grande au clic. Quand un hôtel a plusieurs photos, cliquer sur sa bulle ouvre aussi la galerie complète juste sous la carte (bouton "✕ Fermer" pour la masquer). Aucune modification du fichier Excel n'est nécessaire.
            - **Données** : dépose tes fichiers Excel réels (hôtels + POI) dans la barre latérale — l'app détecte automatiquement les colonnes. En attendant, des données de démonstration sont utilisées.
            """
        )


if __name__ == "__main__":
    main()
