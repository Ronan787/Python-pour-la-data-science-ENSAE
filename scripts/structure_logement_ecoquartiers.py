"""
OBJECTIF
    Comparer la structure des logements diagnostiqués dans les écoquartiers à
    celle du reste de la France, pour savoir si elle suffit à expliquer l'écart
    de taux de passoires observé (scripts/stat_desc_ecoquartiers.py).

SOURCE
    Mêmes données que scripts/stat_desc_ecoquartiers.py : dpe_clean.parquet
    (France entière), resultats/dpe_ecoquartiers.parquet (écoquartiers).

ÉTAPES
    1. compter_par_groupe()   compte les DPE (et les passoires) par groupe,
                              modèle de DPE, type de bâtiment et période de
                              construction (voir utils_ecoquartiers.py).
    2. tableau_structure()    part de chaque modalité dans chaque groupe.
    3. tableau_par_modele()   taux de passoires par modèle de DPE (neuf, vente...).
    4. tableau_standardise()  taux de passoires observé et taux attendu pour
                              la structure du groupe (voir utils_ecoquartiers.py).
    5. graphique_structure()  barres de la structure des trois groupes.

REMARQUES
    modele_dpe distingue les DPE établis pour un logement neuf (Neuf), une
    vente (Vente), une location (Location) ou une copropriété (Copropriété).
    Les logements neufs sont surreprésentés dans la base DPE, et encore plus
    dans les écoquartiers.
    Le type « Bâtiment collectif à usage principal d'habitation » est un DPE
    établi pour l'immeuble entier (affiché « Immeuble entier » sur la figure) ;
    les appartements sont dans le type « Logement ».

UTILISATION
    python scripts/croisement_dpe_ecoquartiers.py   # une fois
    python scripts/structure_logement_ecoquartiers.py
"""

import matplotlib.pyplot as plt
import pandas as pd

from data_cleaning_ecoquartiers import load_clean_ecoquartiers
from utils_ecoquartiers import (
    CELLULE, DPE_CLEAN, GROUPES, OUT_DIR, SQL_CELLULES, barres_groupees,
    communes_ecoquartiers, compter_par_groupe, enregistrer_figure, load_dpe_eco,
    observe_et_attendu, taux_france_par_cellule, upload_to_s3,
)

# --- Paramètres -------------------------------------------------------------

PERIODES = ["Avant 1946", "1946-1970", "1971-1990", "1991-2005",
            "2006-2012", "2013-2021", "Inconnue"]
TITRES = {"modele_dpe": "Modèle de DPE", "type_batiment": "Type de bâtiment",
          "periode": "Période de construction"}
FICHIERS = ["structure_logement.png", "structure_logement.csv",
            "taux_passoires_par_modele.csv", "taux_passoires_standardise.csv"]


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
    taux_france = taux_france_par_cellule(cellules[cellules["groupe"] == GROUPES[0]])
    lignes = [
        {"groupe": groupe,
         **observe_et_attendu(cellules[cellules["groupe"] == groupe], taux_france)}
        for groupe in GROUPES
    ]
    return pd.DataFrame(lignes)


# --- 5. Graphique -----------------------------------------------------------


def graphique_structure(parts: pd.DataFrame, chemin) -> None:
    """
    Trois panneaux (modèle de DPE, type de bâtiment, période) : part de chaque
    modalité dans chacun des trois groupes.
    """
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5),
                             gridspec_kw={"width_ratios": [1, 1, 1.5]})
    for ax, dimension in zip(axes, CELLULE):
        tab = parts[parts["dimension"] == dimension]
        croise = tab.pivot(index="modalite", columns="groupe", values="part_pct").fillna(0)
        if dimension == "periode":  # ordre chronologique
            croise = croise.reindex([p for p in PERIODES if p in croise.index])
        else:  # modalités les plus fréquentes en premier
            croise = croise.loc[croise.sum(axis=1).sort_values(ascending=False).index]
        croise.index = [m.replace("Bâtiment collectif à usage principal d'habitation",
                                  "Immeuble entier") for m in croise.index]
        barres_groupees(ax, croise)
        ax.tick_params(axis="x", labelrotation=30, labelsize=8)
        for etiquette in ax.get_xticklabels():
            etiquette.set_ha("right")
        ax.set_title(TITRES[dimension])
    axes[0].set_ylabel("Part des DPE du groupe (%)")
    fig.suptitle("Structure des logements diagnostiqués")
    enregistrer_figure(fig, chemin, legende=axes[0].get_legend_handles_labels())


def structure_logement(eco, dpe_eco, dpe_path=DPE_CLEAN, out_dir=OUT_DIR) -> dict:
    """
    Calcule les tableaux et la figure ; renvoie les tableaux.
    """
    communes = communes_ecoquartiers(eco, dpe_eco)
    cellules = compter_par_groupe(dpe_path, dpe_eco, communes, SQL_CELLULES,
                                  par=", ".join(CELLULE))
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
    dpe_eco = load_dpe_eco()

    pd.set_option("display.width", 200)
    for nom, tableau in structure_logement(eco, dpe_eco).items():
        print(f"\n{nom}")
        print(tableau.round(4).to_string(index=False))

    for fichier in FICHIERS:
        upload_to_s3(OUT_DIR / fichier)