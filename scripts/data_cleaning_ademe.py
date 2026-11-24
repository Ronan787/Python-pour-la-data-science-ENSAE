"""
OBJECTIF
    1. Appeler les données extraites par le script scripts/data_extraction_ademe.py depuis S3
    2. Nettoyer et formater les données DPE
"""
import duckdb
import pandas as pd
import s3fs

REQUETE_DPE_RENOMMES = """
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


def decrire_dpe_pre_nettoyage(dpe_brut):
    """Fonction exploratoire pour afficher les valeurs manquantes et les
    distributions avant filtrage"""
    # Compter les NULL et les chaînes vides pour chaque var
    comptages_sql = [
        f"""COUNT(*) - COUNT(NULLIF(TRIM("{colonne}"::VARCHAR), '')) AS "{colonne}" """
        for colonne in dpe_brut.columns
    ]
    manquants_par_variable = dpe_brut.aggregate(", ".join(comptages_sql)).df().T
    print(manquants_par_variable.rename(columns={0: "nb_manquants"}).to_string())

    # Bornes et quartiles des variables non textuelles
    dpe_brut.create_view("dpe_pour_bilan", replace=True)
    statistiques_par_variable = duckdb.sql("SUMMARIZE dpe_pour_bilan").df()
    colonnes_distribution = ["column_name", "min", "q25", "q50", "q75", "max"]
    distributions = statistiques_par_variable.loc[
        statistiques_par_variable["column_type"] != "VARCHAR", colonnes_distribution
    ]
    print("\nBornes et quartiles")
    print(distributions.to_string(index=False))

    # Nb de DPE dans chaque variable de modalité
    for variable in ["type_batiment", "classe_energie", "classe_ges"]:
        effectifs_par_modalite = dpe_brut.aggregate(
            f"{variable}, COUNT(*) AS nb_dpe", variable
        ).order("nb_dpe DESC").df()
        print(f"\nEffectifs par {variable}")
        print(effectifs_par_modalite.to_string(index=False))


# %%
# Paramètres des percentiles après stats desc sur la base brute non nettoyée
P99_ENERGIE = 551.07
P99_GES = 102.38


def clean_dpe(chemin_parquet, regles_conservation):
    """Lire, renommer, filtrer et winsoriser les DPE"""
    dpe_brut = duckdb.sql(REQUETE_DPE_RENOMMES, params=[chemin_parquet])
    filtre_conservation = " AND ".join(
        f"({condition})" for condition in regles_conservation.values()
    )
    dpe_filtre = dpe_brut.filter(filtre_conservation)

    # Création 2 variables pour indiquer si sont concernées par windsorisation
    dpe_avec_indicateurs = dpe_filtre.project(f"""
        *,
        CASE WHEN conso_energie > {P99_ENERGIE} THEN 1 ELSE 0 END AS windso_energie,
        CASE WHEN emissions_ges > {P99_GES} THEN 1 ELSE 0 END AS windso_ges
    """)

    # Plafonner les deux colonnes aux P99 estimés sur la base brute
    return dpe_avec_indicateurs.project(f"""
        * REPLACE (
            LEAST(conso_energie, {P99_ENERGIE}) AS conso_energie,
            LEAST(emissions_ges, {P99_GES}) AS emissions_ges
        )
    """)


def bilan_nettoyage(chemin_parquet, regles_conservation, fichier_nettoye):
    """Compter les exclusions et les DPE winsorisés."""
    dpe_brut = duckdb.sql(REQUETE_DPE_RENOMMES, params=[chemin_parquet])

    # Compter chaque motif d’exclusion sur la base brute.
    comptages_sql = [
        f'COUNT(*) FILTER (WHERE ({condition}) IS NOT TRUE) AS "{motif}"'
        for motif, condition in regles_conservation.items()
    ]
    exclusions = dpe_brut.aggregate(", ".join(comptages_sql)).df().iloc[0]

    # Additionner les indicateurs 0/1 dans le Parquet déjà nettoyé
    winsorisations = duckdb.read_parquet(fichier_nettoye).aggregate("""
        COALESCE(SUM(windso_energie), 0) AS "Consommation énergie plafonnée au P99",
        COALESCE(SUM(windso_ges), 0) AS "Émissions GES plafonnées au P99"
    """).df().iloc[0]

    effectifs = pd.concat([exclusions, winsorisations]).astype("int64")
    return effectifs.rename_axis("Motif").reset_index(name="DPE concernés")


# La clé décrit le motif d’exclusion. La condition indique quels DPE garder
regles_conservation = {
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


if __name__ == "__main__":
    brut = "s3://mboutbal/diffusion/dpe_date_22_11.parquet"
    parquet = "dpe_clean.parquet"
    csv = "tableau_diagnostic_nettoyage.csv"
    s3 = s3fs.S3FileSystem(endpoint_url="https://minio.lab.sspcloud.fr")

    clean_dpe(brut, regles_conservation).write_parquet(parquet)
    bilan_nettoyage(brut, regles_conservation, parquet).to_csv(csv, index=False)

    for fichier in (parquet, csv):
        s3.put(fichier, f"mboutbal/diffusion/{fichier}")
