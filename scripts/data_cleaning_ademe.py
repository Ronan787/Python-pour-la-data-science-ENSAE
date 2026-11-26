"""
OBJECTIF
    1. Appeler les données extraites par le script scripts/data_extraction_ademe.py depuis S3
    2. Nettoyer et formater les données DPE

CONFIGURATION
    - Paramétrer S3_BUCKET avec son propre paramètre de bucket pour que
    les données puissent être sauvegardées sans échec (autorisation d'écriture dans S3)
"""
import duckdb
import pandas as pd
import s3fs

# -------- Paramètres ----------#
S3_BUCKET = "mboutbal"  # A paramètrer pour sauvegarder une copie dans son propre bucket

FICHIER_PARQUET_NETTOYE = "dpe_clean.parquet"  # Nom du fichier à sauvegarder
FICHIER_CSV_BILAN = "tableau_diagnostic_nettoyage.csv"  # Nom du tableau à sauvegarder

# Récupération extraction effectuée dans script data_extraction_ademe
chemin_fichier_brut = "s3://mboutbal/diffusion/dpe_date_22_11.parquet"

# Seuils de windsorisation fixés à partir de l'exploration de la base de DPE brute
P99_ENERGIE = 551.07
P99_GES = 102.38


# ------------ Requêtes SQL ---------------------#
# Lecture et renommage des colonnes
def lire_dpe(chemin_parquet):
    """Ouvrir le Parquet avec DuckDB et renommer les colonnes utiles."""
    # SELECT * garde toutes les colonnes. Rename pour renommer les var
    # Le ? reçoit le chemin du Parquet au moment de la lecture
    requete_lecture = """
        SELECT * RENAME (
            nom_methode_dpe AS methode_dpe,
            version_methode_dpe AS version_methode,
            date_etablissement_dpe AS date_dpe,
            tr001_modele_dpe_type_libelle AS modele_dpe,
            tr002_type_batiment_description AS type_batiment,
            consommation_energie AS conso_energie,
            classe_consommation_energie AS classe_energie,
            estimation_ges AS emissions_ges,
            classe_estimation_ges AS classe_ges,
            tv016_departement_code AS code_departement,
            code_insee_commune_actualise AS cog,
            geo_adresse AS adresse,
            geo_score AS score_geocodage
        )
        FROM read_parquet(?)
    """
    # DuckDB remplace le ? par le chemin du fichier
    return duckdb.sql(requete_lecture, params=[chemin_parquet])


# Règles appliquées au nettoyage de données. La clé décrit le motif d’exclusion et
# la condition indique quels DPE garder
requete_nettoyage_sql = {
    "Coordonnées manquantes": """
        latitude IS NOT NULL AND longitude IS NOT NULL
        AND NOT isnan(latitude) AND NOT isnan(longitude)
    """,
    "Outre-mer": """
        LEFT(COALESCE(cog, ''), 2) NOT IN ('97', '98')
        AND LEFT(COALESCE(code_departement, ''), 2) NOT IN ('97', '98')
    """,
    "Date hors 2013–2021 ou manquante": "YEAR(date_dpe) BETWEEN 2013 AND 2021",
    "Classe énergie invalide ou manquante": "classe_energie IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')",
    "Classe GES invalide ou manquante": "classe_ges IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')",
    "Centre commercial": "modele_dpe IS DISTINCT FROM 'Centre commercial'",
    "Consommation invalide ou manquante": "isfinite(conso_energie) AND conso_energie >= 0",
    "Émissions GES invalides ou manquantes": "isfinite(emissions_ges) AND emissions_ges >= 0",
}


# Fonction optionnelle pour explorer les données
def decrire_dpe_pre_nettoyage(dpe):
    categories = ["modele_dpe", "type_batiment", "classe_energie", "classe_ges"]
    # Compter les NULL par colonnes
    na = [
        f'COUNT(*) FILTER (WHERE "{c}" IS NULL) AS "NA {c}"'
        for c in dpe.columns
    ]
    # Modalités des variables catégorielles
    modalites = [
        f'LIST(DISTINCT "{c}") AS "Modalités {c}"'
        for c in categories
    ]

    print(dpe.aggregate(", ".join(na + modalites)).df().T.to_string(header=False))


# Pour lancer la fonction :
# decrire_dpe_pre_nettoyage(lire_dpe(chemin_fichier_brut))


# -------- Fonction de nettoyage ----------#
def clean_dpe(chemin_fichier_brut, requete_nettoyage_sql):
    """Lire, renommer, filtrer et winsoriser les DPE"""
    # Lecture du fichier en duckdb et requête de renommage des var
    dpe_brut = lire_dpe(chemin_fichier_brut)

    # Garder les DPE qui remplissent toutes les conditions
    filtre_conservation = " AND ".join(
        f"({condition})" for condition in requete_nettoyage_sql.values()
    )
    dpe_filtre = dpe_brut.filter(filtre_conservation)

    # Création de 2 variables pour indiquer quels DPE ont été windsorisé
    dpe_avec_indicateurs = dpe_filtre.project(f"""
        *,
        CASE WHEN conso_energie > {P99_ENERGIE} THEN 1 ELSE 0 END AS windso_energie,
        CASE WHEN emissions_ges > {P99_GES} THEN 1 ELSE 0 END AS windso_ges
    """)

    # Windsoriser les deux colonnes aux P99 avec commande "REPLACE"
    return dpe_avec_indicateurs.project(f"""
        * REPLACE (
            LEAST(conso_energie, {P99_ENERGIE}) AS conso_energie,
            LEAST(emissions_ges, {P99_GES}) AS emissions_ges
        )
    """)


# ------- Fonction pour faire un bilan du nb de variables exclues -----#
def bilan_nettoyage(chemin_fichier_brut, requete_nettoyage_sql, fichier_nettoye):
    """Compter les exclusions et les DPE winsorisés."""
    dpe_brut = lire_dpe(chemin_fichier_brut)

    # Compter sur la base brute combien de DPE ne respectent pas les critères
    comptages_sql = [
        f'COUNT(*) FILTER (WHERE ({condition}) IS NOT TRUE) AS "{motif}"'
        for motif, condition in requete_nettoyage_sql.items()
    ]
    exclusions = dpe_brut.aggregate(", ".join(comptages_sql)).df().iloc[0]

    # Additionner les indicateurs 0/1 dans le Parquet déjà nettoyé
    winsorisations = duckdb.read_parquet(fichier_nettoye).aggregate("""
        COALESCE(SUM(windso_energie), 0) AS "Consommation énergie plafonnée au P99",
        COALESCE(SUM(windso_ges), 0) AS "Émissions GES plafonnées au P99"
    """).df().iloc[0]

    effectifs = pd.concat([exclusions, winsorisations]).astype("int64")
    return effectifs.rename_axis("Motif").reset_index(name="DPE concernés")


if __name__ == "__main__":
    s3 = s3fs.S3FileSystem(endpoint_url="https://minio.lab.sspcloud.fr")

    clean_dpe(
        chemin_fichier_brut, requete_nettoyage_sql
        ).write_parquet(FICHIER_PARQUET_NETTOYE)

    bilan_nettoyage(
        chemin_fichier_brut, requete_nettoyage_sql, FICHIER_PARQUET_NETTOYE
        ).to_csv(FICHIER_CSV_BILAN, index=False)

    for fichier in (FICHIER_PARQUET_NETTOYE, FICHIER_CSV_BILAN):
        s3.put(fichier, f"{S3_BUCKET}/diffusion/{fichier}")
