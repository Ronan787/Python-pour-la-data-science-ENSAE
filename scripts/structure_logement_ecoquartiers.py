"""
OBJECTIF
    Comparer la structure des logements diagnostiqués dans les écoquartiers à
    celle du reste de la France, pour savoir si elle suffit à expliquer l'écart
    de taux de passoires observé (scripts/stat_desc_ecoquartiers.py).

SOURCE
    Mêmes données que scripts/stat_desc_ecoquartiers.py : dpe_clean.parquet
    (France entière), resultats/dpe_ecoquartiers.parquet (écoquartiers).

ÉTAPES
    1. effectifs_cellules()   compte les DPE (et les passoires) par groupe,
                              modèle de DPE, type de bâtiment et période de
                              construction, pour les trois groupes : France
                              entière, écoquartiers, communes d'écoquartier
                              hors écoquartier.
    2. tableau_structure()    part de chaque modalité dans chaque groupe.
    3. tableau_par_modele()   taux de passoires par modèle de DPE (neuf, vente...).
    4. tableau_standardise()  taux de passoires observé et taux attendu pour
                              la structure du groupe, avec les taux par
                              catégorie de la France entière.
    5. graphique_structure()  barres de la structure des trois groupes.

REMARQUES
    modele_dpe distingue les DPE établis pour un logement neuf (Neuf), une
    vente (Vente), une location (Location) ou une copropriété (Copropriété).
    Les logements neufs sont surreprésentés dans la base DPE, et encore plus
    dans les écoquartiers.
    Période de construction : « Inconnue » regroupe les années manquantes et
    inférieures à 1700 (valeurs 0 ou 1 non renseignées, souvent des logements
    neufs).
    Taux attendu (standardisation indirecte) : somme, sur chaque catégorie
    (modèle de DPE × type de bâtiment × période), du nombre de DPE du groupe
    multiplié par le taux de passoires de cette catégorie en France entière,
    divisée par le nombre de DPE du groupe. C'est le taux qu'aurait le groupe
    si, à structure donnée, il avait les mêmes performances que la France.
    Un écart observé - attendu négatif signifie que les écoquartiers font mieux
    que ce que leur structure seule laisserait prévoir.

UTILISATION
    python scripts/croisement_dpe_ecoquartiers.py   # une fois
    python scripts/structure_logement_ecoquartiers.py
"""

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_cleaning_ecoquartiers import load_clean_ecoquartiers
from stat_desc_ecoquartiers import (
    DPE_CLEAN, DPE_ECO, GROUPES, OUT_DIR, S3_BUCKET, SQL_COMPTES,
    communes_ecoquartiers, upload_to_s3,
)

# --- Paramètres -------------------------------------------------------------

PERIODES = ["Avant 1946", "1946-1970", "1971-1990", "1991-2005",
            "2006-2012", "2013-2021", "Inconnue"]
SQL_PERIODE = """
    CASE WHEN annee_construction IS NULL OR annee_construction < 1700 THEN 'Inconnue'
         WHEN annee_construction <= 1945 THEN 'Avant 1946'
         WHEN annee_construction <= 1970 THEN '1946-1970'
         WHEN annee_construction <= 1990 THEN '1971-1990'
         WHEN annee_construction <= 2005 THEN '1991-2005'
         WHEN annee_construction <= 2012 THEN '2006-2012'
         ELSE '2013-2021' END"""
# Une catégorie de DPE : modèle de DPE, type de bâtiment, période de construction
SQL_CELLULES = f"""
    COALESCE(modele_dpe, 'Non renseigné') AS modele_dpe,
    COALESCE(type_batiment, 'Non renseigné') AS type_batiment,
    {SQL_PERIODE} AS periode,
    {SQL_COMPTES}"""
CELLULE = ["modele_dpe", "type_batiment", "periode"]
COLONNES_ECO = ["numero_dpe", "cog", "classe_energie", "classe_ges",
                "modele_dpe", "type_batiment", "annee_construction"]


def load_dpe_eco_structure() -> pd.DataFrame:
    """
    Lit les DPE situés dans un écoquartier, avec les variables de structure.
    """
    if not DPE_ECO.exists():
        raise FileNotFoundError(
            f"{DPE_ECO} introuvable : lancez d'abord "
            "python scripts/croisement_dpe_ecoquartiers.py"
        )
    return pd.read_parquet(DPE_ECO, columns=COLONNES_ECO)


# --- 1. Effectifs par catégorie ---------------------------------------------


def effectifs_cellules(dpe_path, dpe_eco, communes) -> pd.DataFrame:
    """
    Nombre de DPE et de passoires par groupe et par catégorie.
    Colonnes : groupe, modele_dpe, type_batiment, periode, n,
    passoires_energie, passoires_ges.
    """
    con = duckdb.connect()
    con.register("communes", communes)
    con.register("dpe_eco", dpe_eco)
    con.register("numeros_eco", dpe_eco[["numero_dpe"]].dropna().drop_duplicates())

    requetes = {
        GROUPES[0]: ("read_parquet(?)", "", [str(dpe_path)]),
        GROUPES[1]: ("dpe_eco", "", []),
        GROUPES[2]: ("read_parquet(?)",
                     """WHERE cog IN (SELECT cog FROM communes)
                        AND numero_dpe NOT IN (SELECT numero_dpe FROM numeros_eco)""",
                     [str(dpe_path)]),
    }
    morceaux = []
    for groupe, (source, filtre, params) in requetes.items():
        tab = con.execute(
            f"SELECT {SQL_CELLULES} FROM {source} {filtre} "
            "GROUP BY modele_dpe, type_batiment, periode",
            params,
        ).df()
        morceaux.append(tab.assign(groupe=groupe))
    return pd.concat(morceaux, ignore_index=True)


# --- 2 à 4. Tableaux --------------------------------------------------------


def tableau_structure(cellules: pd.DataFrame) -> pd.DataFrame:
    """
    Part (en %) de chaque modalité de modèle de DPE, type de bâtiment et période
    de construction dans chaque groupe.
    """
    lignes = []
    for dimension in CELLULE:
        tab = cellules.groupby(["groupe", dimension], as_index=False)["n"].sum()
        tab["part_pct"] = 100 * tab["n"] / tab.groupby("groupe")["n"].transform("sum")
        tab = tab.rename(columns={dimension: "modalite"})
        tab.insert(0, "dimension", dimension)
        lignes.append(tab)
    return pd.concat(lignes, ignore_index=True)


def tableau_par_modele(cellules: pd.DataFrame) -> pd.DataFrame:
    """
    Taux de passoires par groupe et par modèle de DPE (neuf contre ancien).
    """
    tab = cellules.groupby(["modele_dpe", "groupe"], as_index=False)[
        ["n", "passoires_energie", "passoires_ges"]].sum()
    tab["taux_energie"] = tab["passoires_energie"] / tab["n"]
    tab["taux_ges"] = tab["passoires_ges"] / tab["n"]
    return tab


def tableau_standardise(cellules: pd.DataFrame) -> pd.DataFrame:
    """
    Taux de passoires observé et taux attendu avec la structure du groupe et les
    taux par catégorie de la France entière.
    """
    france = cellules[cellules["groupe"] == GROUPES[0]].set_index(CELLULE)
    taux_france = pd.DataFrame({
        "energie": france["passoires_energie"] / france["n"],
        "ges": france["passoires_ges"] / france["n"],
    })
    lignes = []
    for groupe in GROUPES:
        g = cellules[cellules["groupe"] == groupe].set_index(CELLULE)
        n = g["n"].sum()
        ligne = {"groupe": groupe, "n": int(n)}
        for suffixe in ("energie", "ges"):
            observe = g[f"passoires_{suffixe}"].sum() / n
            attendu = (g["n"] * taux_france.loc[g.index, suffixe]).sum() / n
            ligne[f"taux_observe_{suffixe}"] = observe
            ligne[f"taux_attendu_{suffixe}"] = attendu
            ligne[f"ecart_{suffixe}"] = observe - attendu
        lignes.append(ligne)
    return pd.DataFrame(lignes)


# --- 5. Graphique -----------------------------------------------------------


def graphique_structure(parts: pd.DataFrame, chemin) -> None:
    """
    Trois panneaux (modèle de DPE, type de bâtiment, période) : part de chaque
    modalité dans chacun des trois groupes.
    """
    couleurs = ["#7f7f7f", "#31a354", "#6baed6"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5),
                             gridspec_kw={"width_ratios": [1, 1, 1.5]})
    titres = {"modele_dpe": "Modèle de DPE", "type_batiment": "Type de bâtiment",
              "periode": "Période de construction"}
    for ax, dimension in zip(axes, CELLULE):
        tab = parts[parts["dimension"] == dimension]
        croise = tab.pivot(index="modalite", columns="groupe", values="part_pct").fillna(0)
        croise = croise[GROUPES]
        if dimension == "periode":
            croise = croise.reindex([p for p in PERIODES if p in croise.index])
        else:
            croise = croise.loc[croise.sum(axis=1).sort_values(ascending=False).index]
        x = np.arange(len(croise))
        largeur = 0.27
        for i, (groupe, couleur) in enumerate(zip(GROUPES, couleurs)):
            ax.bar(x + (i - 1) * largeur, croise[groupe], largeur, color=couleur,
                   label=groupe)
        etiquettes = [m.replace("Bâtiment collectif à usage principal d'habitation",
                                "Immeuble entier") for m in croise.index]
        ax.set_xticks(x)
        ax.set_xticklabels(etiquettes, rotation=30, ha="right", fontsize=8)
        ax.set_title(titres[dimension])
    axes[0].set_ylabel("Part des DPE du groupe (%)")
    fig.suptitle("Structure des logements diagnostiqués")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="lower center", ncol=3,
               fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, 0.06, 1, 1))  # laisse la place à la légende
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


def structure_logement(eco, dpe_eco, dpe_path=DPE_CLEAN, out_dir=OUT_DIR) -> dict:
    """
    Calcule les tableaux et la figure ; renvoie les tableaux.
    """
    communes = communes_ecoquartiers(eco, dpe_eco)
    cellules = effectifs_cellules(dpe_path, dpe_eco, communes)
    parts = tableau_structure(cellules)
    tableaux = {
        "structure_logement": parts,
        "taux_passoires_par_modele": tableau_par_modele(cellules),
        "taux_passoires_standardise": tableau_standardise(cellules),
    }
    out_dir.mkdir(exist_ok=True)
    for nom, tab in tableaux.items():
        tab.to_csv(out_dir / f"{nom}.csv", index=False)
    graphique_structure(parts, out_dir / "structure_logement.png")
    return tableaux


# ----- Exécution du script --------------------------------------------

if __name__ == "__main__":
    eco = load_clean_ecoquartiers()
    dpe_eco = load_dpe_eco_structure()

    pd.set_option("display.width", 200)
    for nom, tableau in structure_logement(eco, dpe_eco).items():
        print(f"\n{nom}")
        print(tableau.round(4).to_string(index=False))

    for fichier in ("structure_logement.png", "structure_logement.csv",
                    "taux_passoires_par_modele.csv", "taux_passoires_standardise.csv"):
        upload_to_s3(S3_BUCKET, OUT_DIR / fichier)