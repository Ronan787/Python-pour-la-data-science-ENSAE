"""
OBJECTIF
    Nettoyer les données des ÉcoQuartiers extraites par
    scripts/data_extraction_ecoquartiers.py et les projeter en Lambert-93
    (EPSG:2154), la projection de travail du projet.

ÉTAPES
    1. Retirer l'outre-mer (codes commune en 97 et 98) : le champ de l'étude est
       la France métropolitaine, comme pour Filosofi (fichier métropole).
    2. Remplacer par une valeur manquante l'année de label codée 0.
    3. Ne garder que les polygones : la réparation des géométries invalides
       peut avoir créé des GeometryCollection.
    4. Transformer anru en booléen, créer l'indicateur livre (label 3 ou 4),
       éclater typeop en variables booléennes op_* et supprimer descr (texte
       tronqué à 254 octets, inutilisable).
    5. Projeter en EPSG:2154 et calculer la surface en hectares.

REMARQUES
    label : 1 = en projet, 2 = en chantier, 3 = livré, 4 = confirmé (« vécu »).
    Décision du groupe : tous les écoquartiers sont conservés, quel que soit
    leur label. L'indicateur livre (label 3 ou 4 = quartier construit) et la
    colonne label permettent de comparer ensuite les labels entre eux.
    label_an est l'année du dernier label, pas celle de la livraison des
    logements.
    typeop est éclaté en variables booléennes op_* (un écoquartier peut cumuler
    plusieurs types) ; typeop vaut NULL pour une trentaine d'écoquartiers.
    La fonction clean_ecoquartiers() est importable par les scripts suivants
    (croisement avec les DPE, statistiques descriptives).

UTILISATION
    pip install -r requirements.txt
    python scripts/data_cleaning_ecoquartiers.py
"""

import pandas as pd
from shapely.geometry import MultiPolygon

from data_extraction_ecoquartiers import (
    download_from_s3,
    extract_zip,
    load_ecoquartiers,
)

# --- Paramètres -------------------------------------------------------------

PREFIXES_OUTRE_MER = ("97", "98")
CRS_TRAVAIL = "EPSG:2154"

# Emprise approximative de la France métropolitaine, Corse comprise (WGS 84)
LON_MIN, LON_MAX = -5.5, 10.0
LAT_MIN, LAT_MAX = 41.0, 51.5

# typeop : liste de types séparés par ", " (les types contiennent eux-mêmes " - ").
# Nom de la variable créée -> modalité exacte dans le fichier.
TYPES_OPERATION = {
    "op_extension": "Extension maîtrisée",
    "op_quartier_existant": "Renouvellement - Quartier existant",
    "op_friche": "Renouvellement - Reconversion friche",
    "op_prioritaire": "Renouvellement - Quartier prioritaire",
    "op_historique": "Renouvellement - Quartier historique",
}


# --- Nettoyage --------------------------------------------------------------


def _polygones_seulement(geom):
    """
    Renvoie la géométrie si c'est un polygone, sinon l'assemblage des polygones
    qu'elle contient (None s'il n'y en a pas).
    """
    if geom is None or geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    morceaux = []
    for partie in getattr(geom, "geoms", []):
        if partie.geom_type == "Polygon":
            morceaux.append(partie)
        elif partie.geom_type == "MultiPolygon":
            morceaux.extend(partie.geoms)
    return MultiPolygon(morceaux) if morceaux else None


def _eclater_typeop(gdf):
    """
    Crée une variable booléenne par type d'opération (voir TYPES_OPERATION).
    typeop vaut parfois "NULL" (texte) ou est vide : les variables sont alors
    manquantes. Un écoquartier peut cumuler plusieurs types.
    """
    typeop = gdf["typeop"].astype("string").str.strip()
    typeop = typeop.mask(typeop.str.upper().isin(["NULL", ""]))
    listes = typeop.str.split(", ")

    connus = set(TYPES_OPERATION.values())
    inconnus = {t for l in listes.dropna() for t in l} - connus
    if inconnus:
        print(f"Attention : types d'opération non prévus : {sorted(inconnus)}")

    for nom, modalite in TYPES_OPERATION.items():
        gdf[nom] = pd.array(
            [pd.NA if l is pd.NA or l is None else modalite in l for l in listes],
            dtype="boolean",
        )
    print(f"typeop manquant (NULL) : {int(typeop.isna().sum())}")
    return gdf


def clean_ecoquartiers(gdf):
    """
    Nettoie le GeoDataFrame renvoyé par load_ecoquartiers() (WGS 84) et le
    renvoie en EPSG:2154.
    """
    gdf = gdf.copy()
    print(f"{len(gdf)} écoquartiers avant nettoyage.")

    # 1. Outre-mer : le code commune commence par 97 ou 98
    outre_mer = gdf["insee"].str.startswith(PREFIXES_OUTRE_MER, na=False)
    print(f"Outre-mer retirés : {int(outre_mer.sum())}")
    gdf = gdf[~outre_mer].copy()

    # Contrôles : format du code commune et emprise de la métropole
    code_ok = gdf["insee"].str.match(r"^(\d{5}|2[AB]\d{3})$", na=False)
    if not code_ok.all():
        print(f"Attention : {int((~code_ok).sum())} code(s) commune au format inattendu.")
    bornes = gdf.geometry.bounds
    hors_emprise = (
        (bornes["minx"] < LON_MIN) | (bornes["maxx"] > LON_MAX)
        | (bornes["miny"] < LAT_MIN) | (bornes["maxy"] > LAT_MAX)
    )
    if hors_emprise.any():
        print(
            f"Attention : {int(hors_emprise.sum())} écoquartier(s) hors de "
            f"l'emprise de la métropole, ids : {list(gdf.loc[hors_emprise, 'id'])}"
        )

    # 2. Année de label : 0 signifie « manquante »
    gdf["label_an"] = gdf["label_an"].astype("Int64")
    est_zero = (gdf["label_an"] == 0).fillna(False)
    print(f"label_an = 0 remplacé par une valeur manquante : {int(est_zero.sum())}")
    gdf["label_an"] = gdf["label_an"].mask(est_zero)

    # 3. Polygones seulement
    nb_collections = int((gdf.geom_type == "GeometryCollection").sum())
    gdf["geometry"] = gdf.geometry.apply(_polygones_seulement)
    sans_polygone = gdf.geometry.isna()
    print(
        f"GeometryCollection ramenées à des polygones : {nb_collections} ; "
        f"écoquartiers sans polygone supprimés : {int(sans_polygone.sum())}"
    )
    gdf = gdf[~sans_polygone].copy()

    # 4. Variables
    gdf["anru"] = (
        gdf["anru"].astype("string").str.strip().str.lower().eq("x")
        .fillna(False).astype(bool)
    )
    gdf["livre"] = gdf["label"].isin([3, 4])
    gdf = _eclater_typeop(gdf)
    gdf = gdf.drop(columns=["descr"])  # tronquée à 254 octets

    # 5. Projection et surface
    gdf = gdf.to_crs(CRS_TRAVAIL)
    gdf["surface_ha"] = gdf.geometry.area / 10_000

    gdf = gdf.reset_index(drop=True)
    print(f"{len(gdf)} écoquartiers après nettoyage.")
    return gdf


def load_clean_ecoquartiers():
    """
    Récupère, lit et nettoie les écoquartiers (de S3 jusqu'au GeoDataFrame).
    """
    download_from_s3()
    return clean_ecoquartiers(load_ecoquartiers(extract_zip()))


# ----- Exécution du script --------------------------------------------
# Enchaîne extraction, lecture et nettoyage, puis résume le résultat.

if __name__ == "__main__":
    eco = load_clean_ecoquartiers()

    print(f"\n{len(eco)} écoquartiers en métropole, CRS {eco.crs}")
    print("Répartition par dernier label :")
    print(eco["label"].value_counts().sort_index())
    print(f"Livrés ou confirmés (label 3 ou 4) : {int(eco['livre'].sum())}")
    print("Années du dernier label (manquantes comprises) :")
    print(eco["label_an"].value_counts(dropna=False).sort_index())
    print("Surface (hectares) :")
    print(eco["surface_ha"].describe().round(1))
    print(f"En zone ANRU : {int(eco['anru'].sum())}")
    print("Modalités de contexte :")
    print(eco["contexte"].value_counts(dropna=False))
    print("Types d'opération (nombre d'écoquartiers concernés) :")
    print(eco[list(TYPES_OPERATION)].sum().astype(int))
    print("Les 5 plus grandes surfaces (ha) :")
    print(eco.nlargest(5, "surface_ha")[["id", "nom", "label", "surface_ha"]])