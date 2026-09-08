"""Coupe du Monde 2030 – Maroc : cartographie interactive des hôtels
et points d'intérêt (stades, sites d'entraînement, aéroports...).

Lancer avec : streamlit run app.py
"""
import html as html_lib
from io import BytesIO
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.utilities import escape_backticks
from streamlit_folium import st_folium

from src.data_loader import ALLOCATION_COLUMNS, load_hotels, load_pois
from src.geo import bounds_for, haversine_km
from src.photos import get_hotel_photos, get_thumbnail_data_uri
from src.routing import get_travel_times_with_fallback, using_ors
from src.styling import (
    build_palette_map,
    scale_radius,
    DEFAULT_COLOR,
    POI_TYPE_COLORS,
    POI_TYPE_ICON,
    STAR_COLORS,
)

st.set_page_config(page_title="WC2030 Maroc – Carte Hôtels", page_icon="🗺️", layout="wide")

DATA_DIR = Path("data")
DEFAULT_HOTELS = DATA_DIR / "sample_hotels.xlsx"
DEFAULT_POIS = DATA_DIR / "sample_poi.xlsx"
# Fichiers réels de l'utilisateur : écrasés à chaque upload, conservés entre les sessions.
HOTELS_PERSIST_PATH = DATA_DIR / "hotels.xlsx"
POIS_PERSIST_PATH = DATA_DIR / "poi.xlsx"

COLOR_MODES = {
    "Nouveau classement assimilé (étoiles)": "stars",
    "Nouveau classement assimilé (valeur brute)": "Nouveau classement assimilé",
    "Catégorie": "Catégorie",
    "Nouveau Statut vérifié": "Nouveau Statut vérifié",
    "Ville hôte": "Ville hôte",
    "Signature": "Signature",
    "Risque": "Risque",
    "Visite": "Visite",
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

HOTEL_INFO_FIELDS = [
    "Nom", "Ville hôte", "Ville", "Catégorie", "Nouveau classement assimilé",
    "Nouveau Statut vérifié", "Capacité act (cha.)", "#Chambres alloues total",
    "PMC vérif", "Propriétaire", "Opérateur", "Signature", "Signature Prop",
    "Signature Op", "Risque", "Visite", "Note Booking", "ID",
]
DEFAULT_TOOLTIP_FIELDS = [
    "Nom", "Ville hôte", "Nouveau classement assimilé", "Capacité act (cha.)", "Nouveau Statut vérifié",
]

# Rayon (px) des hôtels sans valeur pour le champ de taille choisi : fixe,
# non affecté par le curseur d'échelle des bulles.
BASE_DOT_RADIUS = 3

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


def init_state():
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


def build_tooltip_html(row, fields):
    parts = []
    photo_html, _ = _main_photo_html(row.get("ID"), 160, "display:block;border-radius:4px;margin-bottom:4px;")
    if photo_html:
        parts.append(photo_html)

    text_parts = []
    for field in fields:
        formatted = format_field_value(field, row.get(field))
        value_html = formatted if formatted is not None else f"<i>{MISSING_LABEL}</i>"
        if field == "Nom":
            text_parts.insert(0, f"<b>{formatted or MISSING_LABEL}</b>")
        else:
            text_parts.append(f"{field} : {value_html}")
    parts.extend(text_parts or [f"<i>{MISSING_LABEL}</i>"])
    return "<br>".join(parts)


def build_popup_html(row):
    def fmt(field, suffix=""):
        val = format_field_value(field, row.get(field))
        return f"{val}{suffix}" if val is not None else f"<i>{MISSING_LABEL}</i>"

    lines = []
    photo_html, n_photos = _main_photo_html(row.get("ID"), 320, "display:block;border-radius:4px;margin-bottom:6px;max-width:100%;")
    if photo_html:
        lines.append(photo_html)
        if n_photos > 1:
            lines.append(f'<span style="color:#666;font-size:0.85em;">+{n_photos - 1} autre(s) photo(s) dans data/photos/{row.get("ID")}/</span>')

    lines += [
        f"<b>{format_field_value('Nom', row.get('Nom')) or MISSING_LABEL}</b>",
        f"Ville hôte : {fmt('Ville hôte')}",
        f"Catégorie : {fmt('Catégorie')}",
        f"Classement : {fmt('Nouveau classement assimilé')}",
        f"Statut : {fmt('Nouveau Statut vérifié')}",
        f"Capacité : {fmt('Capacité act (cha.)', ' ch.')}",
        f"Chambres allouées : {fmt('#Chambres alloues total')}",
        f"PMC : {fmt('PMC vérif', ' MAD')}",
        f"Signature : {fmt('Signature')}",
        f"Risque : {fmt('Risque')}",
        f"Note Booking : {fmt('Note Booking')}",
    ]
    return "<br>".join(lines)


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


def sidebar_filters(df: pd.DataFrame):
    filtered = df.copy()

    with st.sidebar.container(border=True):
        st.markdown("### 🔍 Filtres")

        with st.expander("🏙️ Localisation & classification", expanded=True):
            for label, col in CATEGORICAL_FILTERS[:5]:
                options = sorted(filtered[col].dropna().unique().tolist())
                if not options:
                    continue
                chosen = st.multiselect(label, options, default=[], key=f"filt_{col}")
                if chosen:
                    filtered = filtered[filtered[col].isin(chosen)]

        with st.expander("✍️ Parties prenantes & statut"):
            for label, col in CATEGORICAL_FILTERS[5:]:
                options = sorted(filtered[col].dropna().unique().tolist())
                if not options:
                    continue
                chosen = st.multiselect(label, options, default=[], key=f"filt_{col}")
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
                                    value=(float(np.floor(vmin)), float(np.ceil(vmax))), key=f"filt_{col}")
                filtered = filtered[filtered[col].between(lo, hi) | filtered[col].isna()]

        with st.expander("📅 Dates"):
            for label, col in DATE_FILTERS:
                series = filtered[col].dropna()
                if series.empty:
                    continue
                dmin, dmax = series.min().date(), series.max().date()
                if dmin == dmax:
                    continue
                lo, hi = st.date_input(label, value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"filt_{col}")
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
        if not pois_df.empty:
            st.caption("Points d'intérêt")
            for t in sorted(pois_df["Type"].dropna().unique().tolist()):
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

        st.markdown("**Taille des bulles**")
        size_options = dict(NUMERIC_FILTERS)
        size_label = st.selectbox("Taille selon", list(size_options.keys()), index=0, key="size_mode")
        size_col = size_options[size_label]
        size_scale = st.slider("Échelle des bulles", min_value=0.4, max_value=3.0, value=1.0, step=0.1, key="size_scale")
        st.caption("Un hôtel sans valeur pour ce champ garde un petit point de taille fixe, quelle que soit l'échelle.")

        st.markdown("**Style des bulles**")
        color_label = st.selectbox("Couleur selon", list(COLOR_MODES.keys()), index=0)
        color_mode = COLOR_MODES[color_label]

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

    return show_hotels, active_poi_layers, color_mode, color_label, color_map, basemap_choice, tooltip_fields, size_col, size_label, size_scale


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
            "Rayon de recherche (km, vol d'oiseau)" if is_travel_time else "Rayon (km)",
            min_value=1, max_value=150, value=st.session_state["radius_km"],
        )
        if is_travel_time:
            st.caption("Pré-filtre toujours appliqué en premier, pour ne calculer le temps de trajet que sur une zone raisonnable.")

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


def build_map(hotels_df, pois_df, show_hotels, active_poi_layers, color_mode, color_map, basemap_choice, tooltip_fields, size_col, size_scale, n_estimated=0):
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
                tooltip=folium.Tooltip(escape_backticks(build_tooltip_html(row, tooltip_fields)), sticky=True),
                popup=folium.Popup(popup_html, max_width=280),
            ).add_to(hotel_layer)
        hotel_layer.add_to(m)

    for layer_name in active_poi_layers:
        subset = pois_df[pois_df["Couche POI"] == layer_name]
        if subset.empty:
            continue
        layer = folium.FeatureGroup(name=layer_name, show=True)
        base_type = subset["Type"].iloc[0]
        color = POI_TYPE_COLORS.get(base_type, "#333333")
        icon = POI_TYPE_ICON.get(base_type, "map-marker")
        for _, row in subset.iterrows():
            poi_tooltip = escape_backticks(html_lib.escape(f"{row['Nom']} ({layer_name})"))
            folium.Marker(
                location=[row["Latitude"], row["Longitude"]],
                tooltip=folium.Tooltip(poi_tooltip, sticky=True),
                icon=folium.Icon(color="lightgray", icon_color=color, icon=icon, prefix="fa"),
            ).add_to(layer)
        layer.add_to(m)

    if n_estimated:
        banner_html = f'''
        <div style="position:fixed; top:56px; right:10px; z-index:9999;
                    background:#ffe066; color:#5c4400; padding:6px 12px;
                    border-radius:6px; font-weight:600; font-size:13px;
                    box-shadow:0 1px 4px rgba(0,0,0,0.3); border:1px solid #f0c419;">
            ⚠️ Estimation interpolée ({n_estimated} hôtel(s)) — pas encore un vrai temps de trajet
        </div>
        '''
        m.get_root().html.add_child(folium.Element(banner_html))

    ref_point = st.session_state.get("ref_point")
    if ref_point and st.session_state.get("distance_filter_on"):
        radius_km = st.session_state["radius_km"]
        folium.Marker(location=list(ref_point), icon=folium.Icon(color="red", icon="crosshairs", prefix="fa"),
                       tooltip="Point de référence").add_to(m)
        folium.Circle(location=list(ref_point), radius=radius_km * 1000, color="#d62728",
                       fill=True, fill_opacity=0.05, weight=2).add_to(m)
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
    show_hotels, active_poi_layers, color_mode, color_label, color_map, basemap_choice, tooltip_fields, size_col, size_label, size_scale = sidebar_map_settings(hotels_df, pois_df)
    filtered_df = sidebar_filters(hotels_df)
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

    fmap = build_map(filtered_df, pois_df, show_hotels, active_poi_layers, color_mode, color_map, basemap_choice, tooltip_fields, size_col, size_scale, n_estimated_for_banner)
    map_state = st_folium(fmap, use_container_width=True, height=720, key="main_map",
                           returned_objects=["last_clicked"])
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
        "PMC vérif", "Signature", "Risque", "Visite", "Note Booking", "Distance (km)",
        "Temps de trajet (min)",
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
            - **Bulles** : taille = champ numérique au choix (capacité, chambres allouées, PMC, note Booking), ajustable avec le curseur "Échelle des bulles" — un hôtel sans valeur pour ce champ garde un point fixe, non affecté par le curseur ; couleur = critère choisi (classement, catégorie, statut, ville hôte, signature, risque, visite), avec une couleur personnalisable pour chaque valeur via "🎨 Personnaliser les couleurs".
            - **Survol** : choisis les informations affichées au survol d'un hôtel dans "Infos au survol" (le clic affiche toujours la fiche complète). Un champ sans valeur s'affiche en italique ("Non renseigné") plutôt que d'être masqué.
            - **Distance / Temps de trajet** : active le filtre, puis choisis la source du point de référence — "Point choisi" (clic sur la carte ou coordonnées saisies, toujours en distance à vol d'oiseau) ou "Point d'intérêt" (permet en plus le temps de trajet réel en voiture). En mode point d'intérêt, choisis d'abord le type (stade, site d'entraînement...), puis la ville si l'onglet en propose une, puis le point précis. En mode temps de trajet, le rayon vol d'oiseau sert de pré-filtre, puis le temps réel est calculé via un service de routage en ligne (mis en cache sur disque — un trajet n'est jamais recalculé) ; le "Mode Escorte" permet de simuler un trajet accéléré d'un pourcentage réglable. Un bandeau jaune en haut à droite de la carte signale quand des temps affichés sont des estimations interpolées (pas encore de vrai calcul, voir `scripts/estimate_travel_times.py`).
            - **Photos** : dépose des images dans `data/photos/<ID de l'hôtel>/` (ex. `data/photos/HTL-0001/facade.jpg`) — une vignette apparaît automatiquement au survol ; le clic affiche toutes les photos (la première en grand, les suivantes en galerie de vignettes). Aucune modification du fichier Excel n'est nécessaire.
            - **Données** : dépose tes fichiers Excel réels (hôtels + POI) dans la barre latérale — l'app détecte automatiquement les colonnes. En attendant, des données de démonstration sont utilisées.
            """
        )


if __name__ == "__main__":
    main()
