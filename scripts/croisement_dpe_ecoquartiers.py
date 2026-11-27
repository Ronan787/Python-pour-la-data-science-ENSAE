"""
OBJECTIF
    Croiser les DPE (points géographiques) avec les périmètres des ÉcoQuartiers :
    savoir quels DPE se trouvent dans quel écoquartier.

ÉTAPES
    0. iter_dpe_batches()  lit le parquet DPE par paquets (10 millions de lignes
                        ne tiennent pas en mémoire d'un coup).
    1. dpe_to_points()  garde les DPE géolocalisés (coordonnées présentes, et
                        score_geocodage suffisant si un seuil est fixé) et les
                        transforme en points en EPSG:2154.
    2. cross_dpe_ecoquartiers()  jointure spatiale « le point est dans le
                        polygone », puis contrôles (doublons, comptes).

REMARQUES
    Règle retenue par le groupe : un DPE appartient à un écoquartier si son
    point est à l'intérieur du périmètre (predicate "within").
    Tous les écoquartiers sont conservés (décision du groupe), quel que soit leur
    label : la colonne eco_label permet de les comparer ensuite.
    Décision du groupe : les bâtiments collectifs sont conservés. Le type
    « Bâtiment collectif à usage principal d'habitation » est un DPE établi pour
    l'immeuble entier ; les appartements sont dans le type « Logement ». Pour ne
    pas compter deux fois un même immeuble, les statistiques pourront séparer ces
    types (colonne type_batiment).
    Si deux écoquartiers se chevauchent, un DPE peut apparaître deux fois : le
    script le signale, et garde l'écoquartier d'id le plus petit.
    Aucun seuil n'est appliqué par défaut sur score_geocodage (GEO_SCORE_MIN =
    None) : la colonne est conservée dans le résultat pour tester plusieurs
    seuils ensuite. Le seuil final est à fixer et à justifier dans le rapport.
    Colonnes DPE attendues (noms du fichier nettoyé) : numero_dpe, latitude,
    longitude, score_geocodage (facultative).

UTILISATION
    python scripts/croisement_dpe_ecoquartiers.py
    (ou importer cross_dpe_ecoquartiers depuis un autre script)
"""

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

from data_cleaning_ecoquartiers import CRS_TRAVAIL, load_clean_ecoquartiers
from utils_ecoquartiers import DPE_CLEAN, DPE_ECO, get_fs

# --- Paramètres -------------------------------------------------------------

# DPE nettoyés (produits par scripts/data_cleaning_ademe.py), sans le « s3:// »
DPE_S3_PATH = DPE_CLEAN.removeprefix("s3://")

GEO_SCORE_COL = "score_geocodage"
GEO_SCORE_MIN = None  # ex. 0.5 ; None = pas de filtre (voir la distribution du score)
COLONNES_DPE = ["numero_dpe", "latitude", "longitude"]
TAILLE_PAQUET = 500_000
COLONNES_ECO = ["id", "nom", "label", "label_an", "livre", "anru", "surface_ha"]


def dpe_to_points(dpe: pd.DataFrame) -> gpd.GeoDataFrame:
    """
    Transforme les DPE géolocalisés en points projetés en EPSG:2154.
    """
    manquantes = [c for c in COLONNES_DPE if c not in dpe.columns]
    if manquantes:
        raise ValueError(f"Colonnes DPE absentes : {manquantes}")

    dpe = dpe.dropna(subset=["latitude", "longitude"])
    if GEO_SCORE_MIN is not None:
        dpe = dpe[dpe[GEO_SCORE_COL] >= GEO_SCORE_MIN]

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
        # identifiant de ligne unique sur tous les paquets (pour les doublons)
        dpe = dpe.assign(dpe_ligne=range(n_total, n_total + len(dpe)))
        n_total += len(dpe)
        pts = dpe_to_points(dpe)
        n_geoloc += len(pts)
        pts = pts.cx[xmin:xmax, ymin:ymax]  # pré-filtre sur l'emprise
        joint = gpd.sjoin(pts, eco_min, how="inner", predicate="within")
        morceaux.append(joint.drop(columns="index_right"))
        print(f"  paquet {i} : {n_total} DPE lus, "
              f"{sum(len(m) for m in morceaux)} dans un écoquartier (avant dédoublonnage)")

    print(f"DPE géolocalisés retenus : {n_geoloc} sur {n_total}")
    if GEO_SCORE_MIN is None:
        print("Pas de filtre sur le score de géocodage (GEO_SCORE_MIN = None).")

    joint = pd.concat(morceaux, ignore_index=True)

    # Chevauchements : une ligne de DPE ne doit compter qu'une fois
    doublons = joint["dpe_ligne"].duplicated(keep=False)
    if doublons.any():
        print(f"Attention : {joint.loc[doublons, 'dpe_ligne'].nunique()} DPE "
              "dans plusieurs écoquartiers ; on garde l'id le plus petit.")
        joint = joint.sort_values("eco_id").drop_duplicates("dpe_ligne")
    joint = joint.drop(columns="dpe_ligne")

    # Information : un même numero_dpe présent sur plusieurs lignes de la base
    repetes = int(joint["numero_dpe"].duplicated().sum())
    if repetes:
        print(f"Information : {repetes} ligne(s) avec un numero_dpe déjà présent "
              "dans le résultat (doublons de la base DPE).")

    print(f"{len(joint)} DPE dans {joint['eco_id'].nunique()} écoquartiers "
          f"(sur {len(eco)}).")
    return gpd.GeoDataFrame(joint.reset_index(drop=True), crs=CRS_TRAVAIL)


def iter_dpe_batches():
    """
    Lit les DPE nettoyés depuis le dossier diffusion du bucket S3, paquet par
    paquet (générateur de DataFrames).
    """
    with get_fs().open(DPE_S3_PATH, "rb") as f:
        for batch in pq.ParquetFile(f).iter_batches(batch_size=TAILLE_PAQUET):
            yield batch.to_pandas()


def save_result(dpe_eco: gpd.GeoDataFrame) -> None:
    """
    Enregistre le résultat dans resultats/ (fichier temporaire puis renommage).
    """
    DPE_ECO.parent.mkdir(exist_ok=True)
    tmp = DPE_ECO.with_suffix(".tmp")
    dpe_eco.to_parquet(tmp)
    tmp.replace(DPE_ECO)
    print(f"Résultat enregistré : {DPE_ECO}")


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