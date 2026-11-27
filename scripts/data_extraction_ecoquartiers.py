"""
OBJECTIF
    Récupérer les contours des ÉcoQuartiers (shapefile open data du ministère de
    la Transition écologique) et les charger dans un GeoDataFrame vérifié.

SOURCE
    https://www.ecoquartiers.logement.gouv.fr/document/opendata-les-ecoquartiers/
    Le zip du shapefile a été téléchargé une fois pour toutes depuis le site du
    ministère et déposé dans le dossier diffusion du bucket S3.
    Dictionnaire des champs : 2022_Ecoquartier_Dictionnaire_opendata_shapefile.xlsx

ÉTAPES
    1. download_from_s3()    copie le zip depuis S3 dans ecoquartiers/.
                             Si le zip est déjà présent, il n'est pas recopié.
    2. extract_zip()         décompresse le zip et renvoie le chemin du .shp.
    3. load_ecoquartiers()   lit le shapefile, répare les textes tronqués,
                             vérifie la structure et renvoie un GeoDataFrame
                             en WGS 84 (EPSG:4326).

REMARQUES
    Champs attendus : id, insee, com, regio, dept, nom, label, label_an,
    contexte, descr, typeop, anru (+ la géométrie du périmètre).
    - label : dernier label obtenu (1 à 4) ; label_an : année de ce label.
      Ce n'est PAS l'année de livraison des logements.
    - insee : commune principale seulement (un écoquartier peut en couvrir
      plusieurs).
    - typeop : liste de types séparés par '-' ; anru vaut 'x' si zone ANRU.
    Encodage : les textes du shapefile sont en UTF-8, mais le format DBF limite
    un champ à 254 octets : les textes plus longs (surtout descr) sont coupés,
    parfois au milieu d'un accent, ce qui fait planter la lecture classique
    (UnicodeDecodeError). On lit donc les octets bruts (ENCODING="") puis on
    décode soi-même en UTF-8, en retirant l'octet coupé. La colonne descr
    reste tronquée : ne pas s'en servir pour l'analyse.
    Les DPE (latitude / longitude) sont aussi en WGS 84 : pas de reprojection
    nécessaire pour la jointure spatiale. Pour calculer des surfaces ou des
    distances, passer d'abord en EPSG:2154.

UTILISATION
    Le zip doit déjà être dans le dossier diffusion du bucket S3 (nom du bucket :
    S3_BUCKET dans utils_ecoquartiers.py ; nom du zip : ZIP_NAME ci-dessus).
    pip install -r requirements.txt
    python scripts/data_extraction_ecoquartiers.py
"""

import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

from utils_ecoquartiers import S3_BUCKET, get_fs

# --- Paramètres -------------------------------------------------------------

ZIP_NAME = "opendata-les-ecoquartiers.zip"  # nom du zip dans S3

S3_PATH = f"{S3_BUCKET}/diffusion/{ZIP_NAME}"
DATA_DIR = Path("ecoquartiers")
ZIP_PATH = DATA_DIR / ZIP_NAME

CRS_ATTENDU = "EPSG:4326"
COLONNES_ATTENDUES = [
    "id", "insee", "com", "regio", "dept", "nom",
    "label", "label_an", "contexte", "descr", "typeop", "anru",
]


# --- Récupération depuis S3 -------------------------------------------------


def download_from_s3() -> None:
    """
    Copie le zip depuis le dossier diffusion du bucket S3 vers DATA_DIR.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if ZIP_PATH.exists():
        print(f"{ZIP_PATH} déjà présent, pas de nouvelle copie.")
        return

    tmp = ZIP_PATH.with_suffix(".tmp")
    get_fs().get(S3_PATH, str(tmp))
    tmp.replace(ZIP_PATH)  # le .zip n'existe jamais à l'état partiel
    print(f"Fichier copié depuis s3://{S3_PATH}")


# --- Décompression ----------------------------------------------------------


def extract_zip() -> Path:
    """
    Décompresse le zip dans DATA_DIR et renvoie le chemin du fichier .shp.
    """
    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(DATA_DIR)

    shp_files = sorted(DATA_DIR.rglob("*.shp"))
    if len(shp_files) != 1:
        raise ValueError(
            f"Un seul .shp attendu, {len(shp_files)} trouvé(s) : "
            f"{[p.name for p in shp_files]}"
        )
    print(f"Shapefile extrait : {shp_files[0].name}")
    return shp_files[0]


# --- Réparation de l'encodage -----------------------------------------------


def _decoder_utf8(valeur):
    """
    Décode en UTF-8 un texte lu octet par octet (chaque octet est alors un
    caractère latin-1). Renvoie (texte, tronque), où tronque vaut True si le
    texte était coupé au milieu d'un caractère (limite de 254 octets du DBF).
    """
    if not isinstance(valeur, str):
        return valeur, False
    try:
        octets = valeur.encode("latin-1")
    except UnicodeEncodeError:
        return valeur, False  # déjà du vrai texte : on n'y touche pas
    try:
        return octets.decode("utf-8"), False
    except UnicodeDecodeError as err:
        coupe_en_fin = (
            err.reason == "unexpected end of data"
            and err.end == len(octets)
            and len(octets) >= 253
        )
        if coupe_en_fin:
            return octets[: err.start].decode("utf-8"), True
        return valeur, False


def _reparer_textes(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Décode en UTF-8 toutes les colonnes de texte et signale les textes coupés.
    """
    nb_tronques = 0
    for col in gdf.columns.drop(gdf.geometry.name):
        if not (
            pd.api.types.is_object_dtype(gdf[col])
            or pd.api.types.is_string_dtype(gdf[col])
        ):
            continue
        resultats = [_decoder_utf8(v) for v in gdf[col]]
        gdf[col] = [texte for texte, _ in resultats]
        nb_tronques += sum(tronque for _, tronque in resultats)

    if nb_tronques:
        print(f"{nb_tronques} texte(s) coupé(s) à 254 octets (limite DBF) réparé(s).")

    # Un "Ã" restant dans un texte signalerait un accent mal décodé
    for col in gdf.columns.drop(gdf.geometry.name):
        if pd.api.types.is_string_dtype(gdf[col]) or pd.api.types.is_object_dtype(gdf[col]):
            if gdf[col].astype("string").str.contains("Ã", na=False).any():
                print(f"Attention : accents mal décodés dans la colonne {col}.")
    return gdf


# --- Lecture et vérifications -----------------------------------------------


def load_ecoquartiers(shp_path: Path) -> gpd.GeoDataFrame:
    """
    Lit le shapefile et vérifie sa structure. Renvoie un GeoDataFrame en WGS 84.
    """
    # ENCODING="" : GDAL ne décode rien, on le fait nous-mêmes juste après
    gdf = gpd.read_file(shp_path, ENCODING="")
    gdf = _reparer_textes(gdf)

    # Colonnes
    manquantes = [c for c in COLONNES_ATTENDUES if c not in gdf.columns]
    if manquantes:
        raise ValueError(
            f"Colonnes absentes du shapefile : {manquantes}. "
            f"Colonnes présentes : {list(gdf.columns)}"
        )

    # Projection : le dictionnaire annonce du WGS 84
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_ATTENDU)
    elif gdf.crs != CRS_ATTENDU:
        gdf = gdf.to_crs(CRS_ATTENDU)

    # Identifiant unique
    if not gdf["id"].is_unique:
        raise ValueError("L'identifiant id n'est pas unique.")

    # Géométries : on attend des polygones, valides
    if gdf.geometry.isna().any() or gdf.geometry.is_empty.any():
        raise ValueError("Certains écoquartiers n'ont pas de géométrie.")
    types = gdf.geom_type.value_counts().to_dict()
    if not set(types) <= {"Polygon", "MultiPolygon"}:
        print(f"Attention : géométries qui ne sont pas des polygones : {types}")
    nb_invalides = int((~gdf.geometry.is_valid).sum())
    if nb_invalides:
        print(f"{nb_invalides} géométrie(s) invalide(s) : correction avec make_valid.")
        gdf["geometry"] = gdf.geometry.make_valid()

    # Code commune : texte sur 5 caractères (jamais un entier, cf. 2A / 2B)
    gdf["insee"] = gdf["insee"].astype("string").str.strip().str.zfill(5)

    return gdf


# ----- Exécution du script --------------------------------------------
# Récupère le zip depuis S3, le décompresse puis résume le contenu.

if __name__ == "__main__":
    download_from_s3()
    gdf = load_ecoquartiers(extract_zip())

    print(f"{len(gdf)} écoquartiers, CRS {gdf.crs}")
    print("Colonnes :", list(gdf.columns))
    print("Types de géométrie :")
    print(gdf.geom_type.value_counts())
    print("Répartition par dernier label :")
    print(gdf["label"].value_counts().sort_index())
    print("Années du dernier label :")
    print(gdf["label_an"].value_counts().sort_index())