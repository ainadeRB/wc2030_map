"""Photos des hôtels : convention de stockage local (data/photos/<id>/,
prioritaire — jamais affectée par ce qui suit) + génération de vignettes
encodées en data URI pour être affichées dans les infobulles et popups
Leaflet sans dépendre d'un serveur d'images séparé.

Repli sur des URLs distantes (Cloudinary) pour la version hébergée, qui n'a
pas de disque persistant pour stocker de vraies photos : voir
scripts/upload_photos_to_cloud.py, qui envoie les photos locales vers
Cloudinary et génère data/photos_manifest.json (hotel_id -> liste d'URLs),
ce fichier étant lui bien versionné dans git (ce sont juste des URLs)."""
import base64
import json
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image

PHOTOS_DIR = Path("data") / "photos"
PHOTOS_MANIFEST_PATH = Path("data") / "photos_manifest.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _photos_dir_signature(hotel_id: str):
    """Signature bon marché (nb de fichiers + dernière modif) pour invalider
    le cache Streamlit quand des photos sont ajoutées/retirées à chaud."""
    folder = PHOTOS_DIR / str(hotel_id)
    if not folder.is_dir():
        return None
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS)
    return tuple((f.name, f.stat().st_mtime) for f in files)


@st.cache_data(show_spinner=False)
def _load_photos_manifest(mtime: float) -> dict:
    """URLs distantes par ID d'hôtel (voir docstring du module). `mtime` ne
    sert qu'à invalider le cache si le fichier change (ex. après un nouveau
    `git pull` sur le déploiement)."""
    try:
        return json.loads(PHOTOS_MANIFEST_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_hotel_photos(hotel_id) -> list:
    """Photos de l'hôtel `hotel_id`. En priorité les fichiers locaux de
    data/photos/<hotel_id>/ (list[Path], comme avant — jamais affecté par ce
    qui suit) ; à défaut, les URLs du manifeste distant si présentes
    (list[str]) — c'est ce second cas qui alimente la version hébergée, sans
    disque local pour de vraies photos. Liste vide si ni l'un ni l'autre."""
    sig = _photos_dir_signature(hotel_id)
    if sig:
        return [PHOTOS_DIR / str(hotel_id) / name for name, _ in sig]
    if not PHOTOS_MANIFEST_PATH.exists():
        return []
    manifest = _load_photos_manifest(PHOTOS_MANIFEST_PATH.stat().st_mtime)
    return list(manifest.get(str(hotel_id), []))


@st.cache_data(show_spinner=False)
def _image_to_data_uri(path_str: str, mtime: float, max_width: int) -> str:
    path = Path(path_str)
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, max(1, int(img.height * ratio))))
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=72)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _cloudinary_thumbnail_url(url: str, max_width: int) -> str:
    """Vignette via une transformation Cloudinary insérée dans l'URL
    elle-même : rien à télécharger ni redimensionner côté serveur Streamlit,
    contrairement au chemin local ci-dessus (Cloudinary s'en charge)."""
    marker = "/upload/"
    if marker not in url:
        return url
    prefix, suffix = url.split(marker, 1)
    return f"{prefix}{marker}w_{max_width},c_limit,q_72,f_auto/{suffix}"


def get_thumbnail_data_uri(photo, max_width: int = 160) -> str:
    """Source d'image prête pour <img src="...">. `photo` est soit un
    fichier local (Path — vignette base64 générée et mise en cache par
    fichier + date de modification, comme avant), soit une URL distante
    (str — cas de la version hébergée, voir get_hotel_photos) : la vignette
    est alors produite à la volée par Cloudinary via l'URL, sans rien
    télécharger ni traiter côté serveur Streamlit."""
    if isinstance(photo, Path):
        return _image_to_data_uri(str(photo), photo.stat().st_mtime, max_width)
    return _cloudinary_thumbnail_url(str(photo), max_width)
