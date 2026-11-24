"""
OBJECTIF
    Croiser les DPE (points géographiques) avec les périmètres des ÉcoQuartiers :
    savoir quels DPE se trouvent dans quel écoquartier.

ÉTAPES
    0. iter_dpe_batches()  lit le parquet DPE par paquets (10 millions de lignes
                        ne tiennent pas en mémoire d'un coup).
    1. dpe_to_points()  garde les DPE géolocalisés (coordonnées présentes et
                        geo_score suffisant) et les transforme en points
                        en EPSG:2154.
    2. cross_dpe_ecoquartiers()  jointure spatiale « le point est dans le
                        polygone », puis contrôles (doublons, comptes).

REMARQUES
    Règle retenue par le groupe : un DPE appartient à un écoquartier si son
    point est à l'intérieur du périmètre (predicate "within").
    Tous les écoquartiers sont conservés (décision du groupe), quel que soit leur
    label : la colonne eco_label permet de les comparer ensuite.
    Les bâtiments collectifs ont été retirés du nettoyage des DPE (décision du
    groupe) : les écoquartiers, souvent composés de collectifs, sont donc
    sous-représentés. À signaler dans le rapport.
    Si deux écoquartiers se chevauchent, un DPE peut apparaître deux fois : le
    script le signale, et garde l'écoquartier d'id le plus petit.
    Le seuil GEO_SCORE_MIN est à fixer et à justifier dans le rapport.
    Colonnes DPE attendues : numero_dpe, latitude, longitude, geo_score.

UTILISATION
    python scripts/crossing_dpe_ecoquartiers.py
    (ou importer cross_dpe_ecoquartiers depuis un autre script)
"""

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import s3fs
from pathlib import Path

from data_cleaning_ecoquartiers import CRS_TRAVAIL, load_clean_ecoquartiers

# --- Paramètres -------------------------------------------------------------

# DPE nettoyés (fichier parquet produit par le nettoyage des DPE).
# À COMPLÉTER quand le nettoyage est terminé : bucket et nom du fichier.
DPE_BUCKET = "mboutbal"
DPE_FILE = "dpe_date_22_11.parquet"        # nom exact du parquet nettoyé dans diffusion/
DPE_S3_PATH = f"{DPE_BUCKET}/diffusion/{DPE_FILE}"

GEO_SCORE_MIN = 0.5  # à fixer et à justifier (voir la distribution de geo_score)
COLONNES_DPE = ["numero_dpe", "latitude", "longitude"]
TAILLE_PAQUET = 500_000
OUT_DIR = Path("resultats")
OUT_FILE = OUT_DIR / "dpe_ecoquartiers.parquet"
COLONNES_ECO = ["id", "nom", "label", "label_an", "livre", "anru", "surface_ha"]


def dpe_to_points(dpe: pd.DataFrame) -> gpd.GeoDataFrame:
    """
    Transforme les DPE géolocalisés en points projetés en EPSG:2154.
    """
    manquantes = [c for c in COLONNES_DPE if c not in dpe.columns]
    if manquantes:
        raise ValueError(f"Colonnes DPE absentes : {manquantes}")

    dpe = dpe.dropna(subset=["latitude", "longitude"])
    if "geo_score" in dpe.columns:
        dpe = dpe[dpe["geo_score"] >= GEO_SCORE_MIN]

    pts = gpd.GeoDataFrame(
        dpe,
        geometry=gpd.points_from_xy(dpe["longitude"], dpe["latitude"]),
        crs="EPSG:4326",
    )
    return pts.to_crs(CRS_TRAVAIL)


def cross_dpe_ecoquartiers(batches, eco: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Renvoie les DPE situés dans un écoquartier, avec les attributs de
    l'écoquartier (une ligne par DPE). batches : un DataFrame, ou un itérable
    de DataFrames (paquets de DPE).
    """
    if isinstance(batches, pd.DataFrame):
        batches = [batches]

    eco_min = eco[COLONNES_ECO + ["geometry"]].rename(
        columns={"id": "eco_id", "nom": "eco_nom", "label": "eco_label",
                 "label_an": "eco_label_an", "livre": "eco_livre",
                 "anru": "eco_anru"}
    )
    xmin, ymin, xmax, ymax = eco_min.total_bounds

    n_total = n_geoloc = 0
    morceaux = []
    for i, dpe in enumerate(batches, start=1):
        n_total += len(dpe)
        pts = dpe_to_points(dpe)
        n_geoloc += len(pts)
        pts = pts.cx[xmin:xmax, ymin:ymax]  # pré-filtre sur l'emprise
        joint = gpd.sjoin(pts, eco_min, how="inner", predicate="within")
        morceaux.append(joint.drop(columns="index_right"))
        print(f"  paquet {i} : {n_total} DPE lus, "
              f"{sum(len(m) for m in morceaux)} dans un écoquartier (avant dédoublonnage)")

    print(f"DPE géolocalisés retenus : {n_geoloc} sur {n_total}")
    if "geo_score" not in dpe.columns:
        print("Attention : pas de colonne geo_score, aucun filtre de qualité.")

    joint = pd.concat(morceaux, ignore_index=True)

    # Chevauchements : un DPE ne doit compter qu'une fois
    doublons = joint["numero_dpe"].duplicated(keep=False)
    if doublons.any():
        print(f"Attention : {joint.loc[doublons, 'numero_dpe'].nunique()} DPE "
              "dans plusieurs écoquartiers ; on garde l'id le plus petit.")
        joint = joint.sort_values("eco_id").drop_duplicates("numero_dpe")

    print(f"{len(joint)} DPE dans {joint['eco_id'].nunique()} écoquartiers "
          f"(sur {len(eco)}).")
    return gpd.GeoDataFrame(joint.reset_index(drop=True), crs=CRS_TRAVAIL)


def iter_dpe_batches():
    """
    Lit les DPE nettoyés depuis le dossier diffusion du bucket S3, paquet par
    paquet (generateur de DataFrames).
    """
    if "A_COMPLETER" in (DPE_BUCKET, DPE_FILE):
        raise ValueError("Renseignez DPE_BUCKET et DPE_FILE (nom du parquet DPE nettoyé).")
    fs = s3fs.S3FileSystem(
        client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
    )
    with fs.open(DPE_S3_PATH, "rb") as f:
        for batch in pq.ParquetFile(f).iter_batches(batch_size=TAILLE_PAQUET):
            yield batch.to_pandas()


def save_result(dpe_eco: gpd.GeoDataFrame) -> None:
    """
    Enregistre le résultat dans resultats/ (fichier temporaire puis renommage).
    """
    OUT_DIR.mkdir(exist_ok=True)
    tmp = OUT_FILE.with_suffix(".tmp")
    dpe_eco.to_parquet(tmp)
    tmp.replace(OUT_FILE)
    print(f"Résultat enregistré : {OUT_FILE}")


# ----- Exécution du script --------------------------------------------

if __name__ == "__main__":
    eco = load_clean_ecoquartiers()
    dpe_eco = cross_dpe_ecoquartiers(iter_dpe_batches(), eco)
    save_result(dpe_eco)

    print("DPE par dernier label :")
    print(dpe_eco["eco_label"].value_counts().sort_index())
    print("Écoquartiers sans aucun DPE :",
          len(eco) - dpe_eco["eco_id"].nunique())
    print("DPE par écoquartier :")
    print(dpe_eco.groupby("eco_id").size().describe().round(1))