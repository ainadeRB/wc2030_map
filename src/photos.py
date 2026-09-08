"""Photos des hôtels : convention de stockage local + génération de
vignettes encodées en data URI (pour être affichées dans les infobulles et
popups Leaflet sans dépendre d'un serveur d'images séparé)."""
import base64
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image

PHOTOS_DIR = Path("data") / "photos"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _photos_dir_signature(hotel_id: str):
    """Signature bon marché (nb de fichiers + dernière modif) pour invalider
    le cache Streamlit quand des photos sont ajoutées/retirées à chaud."""
    folder = PHOTOS_DIR / str(hotel_id)
    if not folder.is_dir():
        return None
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS)
    return tuple((f.name, f.stat().st_mtime) for f in files)


def get_hotel_photos(hotel_id) -> list[Path]:
    """Liste les fichiers image du dossier data/photos/<hotel_id>/, triés
    par nom. Retourne une liste vide si l'hôtel n'a pas de dossier photos."""
    sig = _photos_dir_signature(hotel_id)
    if not sig:
        return []
    return [PHOTOS_DIR / str(hotel_id) / name for name, _ in sig]


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


def get_thumbnail_data_uri(path: Path, max_width: int = 160) -> str:
    """Vignette (JPEG, redimensionnée, encodée en base64) prête à être
    insérée directement dans du HTML via <img src="...">. Mise en cache par
    fichier + date de modification, donc recalculée seulement si la photo
    change."""
    return _image_to_data_uri(str(path), path.stat().st_mtime, max_width)
