"""
OBJECTIF
    1. Appeler les données extraites par le script scripts/data_extraction_ademe.py depuis S3
    2. Nettoyer et formater les données DPE
"""
import duckdb
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


def clean_dpe(chemin_parquet, regles_conservation):
    """Lire, renommer et conserver les DPE qui respectent toutes les règles"""
    dpe_brut = duckdb.sql(REQUETE_DPE_RENOMMES, params=[chemin_parquet])
    filtre_conservation = " AND ".join(
        f"({condition})" for condition in regles_conservation.values()
    )
    return dpe_brut.filter(filtre_conservation)


def bilan_nettoyage(chemin_parquet, regles_conservation):
    """Compter les exclusions par motif sur les données avant filtrage"""
    dpe_brut = duckdb.sql(REQUETE_DPE_RENOMMES, params=[chemin_parquet])

    # DPE dont la condition est fausse ou renvoie NULL
    comptages_sql = [
        f'COUNT(*) FILTER (WHERE ({condition}) IS NOT TRUE) AS "{motif_exclusion}"'
        for motif_exclusion, condition in regles_conservation.items()
    ]
    exclusions_par_motif = dpe_brut.aggregate(", ".join(comptages_sql)).df().iloc[0]
    return exclusions_par_motif.rename_axis("Motif").reset_index(name="DPE concernés")


# La clé décrit le motif d’exclusion. La condition indique quels DPE garder.
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
    chemin_parquet = "s3://mboutbal/diffusion/dpe_date_22_11.parquet"
    s3 = s3fs.S3FileSystem(endpoint_url="https://minio.lab.sspcloud.fr")

    # Optionnel
    # dpe_brut = duckdb.sql(REQUETE_DPE_RENOMMES, params=[chemin_parquet])
    # decrire_dpe_pre_nettoyage(dpe_brut)  # optionnel, but exploratoire

    # Calculer le bilan une seule fois et l’enregistrer sur S3.
    tableau_diagnostic_nettoyage = bilan_nettoyage(chemin_parquet, regles_conservation)
    with s3.open("mboutbal/diffusion/tableau_diagnostic_nettoyage.csv", "w") as fichier:
        tableau_diagnostic_nettoyage.to_csv(fichier, index=False)

    # Écrire la base nettoyée en Parquet, puis l’envoyer sur S3.
    dpe_nettoye = clean_dpe(chemin_parquet, regles_conservation)
    dpe_nettoye.write_parquet("dpe_clean.parquet")
    s3.put("dpe_clean.parquet", "mboutbal/diffusion/dpe_clean.parquet")
