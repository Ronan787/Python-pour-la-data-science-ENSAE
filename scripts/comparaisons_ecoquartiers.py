"""
OBJECTIF
    Compléter la comparaison des écoquartiers avec le reste de la France :
    1. selon le dernier label obtenu (en projet, en chantier, livré, confirmé) ;
    2. selon la répartition des classes énergie et GES, et la consommation par
       classe (mêmes graphiques que scripts/stat_desc.py, avec les écoquartiers).

SOURCE
    dpe_clean.parquet (France entière) et resultats/dpe_ecoquartiers.parquet
    (écoquartiers), comme scripts/stat_desc_ecoquartiers.py.

ÉTAPES
    1. taux_passoires_par_label()  taux de passoires observé et taux attendu
                                   (voir utils_ecoquartiers.py) pour chaque label.
    2. repartition_classes()       part de chaque classe énergie et GES pour les
                                   trois groupes.
    3. quantiles_consommation()    quantiles de consommation et d'émissions par
                                   classe, France entière et écoquartiers.
    Les fichiers sont écrits dans figures_ecoquartiers/ puis envoyés sur S3.

REMARQUES
    Les écoquartiers « en projet » ou « en chantier » contiennent surtout du bâti
    existant (les périmètres couvrent des quartiers déjà construits) : leurs DPE
    décrivent le plus souvent l'état d'avant l'opération. Seuls les labels
    « livré » et « confirmé » correspondent à des quartiers construits.
    Boîtes à moustaches : boîte = quartiles, trait = médiane, moustaches = 5e et
    95e percentiles. Elles sont tracées à partir de quantiles calculés dans la
    base (7 millions de DPE) plutôt que sur les données brutes. Les consommations
    sont plafonnées au 99e percentile par le nettoyage (winsorisation).

UTILISATION
    python scripts/croisement_dpe_ecoquartiers.py   # une fois
    python scripts/comparaisons_ecoquartiers.py
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_cleaning_ecoquartiers import load_clean_ecoquartiers
from utils_ecoquartiers import (
    CELLULE, COULEURS_GROUPES, COULEURS_LABEL, DPE_CLEAN, GROUPES, OUT_DIR,
    SQL_CELLULES, barres_groupees, communes_ecoquartiers, compter_par_groupe,
    enregistrer_figure, load_dpe_eco, observe_et_attendu, taux_france_par_cellule,
    upload_to_s3,
)

# --- Paramètres -------------------------------------------------------------

CLASSES = list("ABCDEFG")
QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
# Lignes du tableau par label : un label seul, puis regroupements
ENSEMBLES_LABELS = {
    "Label 1 : en projet": [1], "Label 2 : en chantier": [2],
    "Label 3 : livré": [3], "Label 4 : confirmé": [4],
    "Labels 1-2 (projet, chantier)": [1, 2], "Labels 3-4 (livré, confirmé)": [3, 4],
}
# Variables comparées : nom, colonne de la classe, colonne de la mesure
VARIABLES = {"energie": ("classe_energie", "conso_energie"),
             "ges": ("classe_ges", "emissions_ges")}
FICHIERS = ["passoires_par_label.png", "passoires_par_label.csv",
            "repartition_classes.png", "repartition_classes.csv",
            "boxplots_consommation.png", "quantiles_consommation.csv"]


# --- 1. Taux de passoires par label -----------------------------------------


def taux_passoires_par_label(dpe_path, dpe_eco, communes) -> pd.DataFrame:
    """
    Pour chaque label (ou regroupement de labels) : effectif, taux observé (avec
    intervalle de confiance) et taux attendu pour la structure du groupe.
    """
    # Taux par catégorie de DPE en France entière
    france = compter_par_groupe(dpe_path, dpe_eco, communes, SQL_CELLULES,
                                par=", ".join(CELLULE), groupes=GROUPES[:1])
    taux_france = taux_france_par_cellule(france)
    # Effectifs par catégorie de DPE dans les écoquartiers, selon le label
    eco = compter_par_groupe(
        dpe_path, dpe_eco, communes, f"eco_label AS label, {SQL_CELLULES}",
        par="eco_label, " + ", ".join(CELLULE), groupes=GROUPES[1:2])

    lignes = []
    for nom, labels in ENSEMBLES_LABELS.items():
        g = eco[eco["label"].isin(labels)]
        g = g.groupby(CELLULE, as_index=False)[
            ["n", "passoires_energie", "passoires_ges"]].sum()
        if not g.empty:
            lignes.append({"ensemble": nom, **observe_et_attendu(g, taux_france)})
    return pd.DataFrame(lignes)


def graphique_label(tab: pd.DataFrame, chemin) -> None:
    """
    Taux de passoires observé (barres, avec intervalle de confiance) et taux
    attendu pour la structure (losange noir), pour chacun des quatre labels.
    """
    # On garde les quatre labels seuls, pas les regroupements « Labels 1-2 »...
    tab = tab[tab["ensemble"].str.startswith("Label ")]
    couleurs = [COULEURS_LABEL[i] for i in (1, 2, 3, 4)][: len(tab)]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, suffixe, titre in zip(axes, ("energie", "ges"),
                                  ("Classe énergie", "Classe GES")):
        obs = 100 * tab[f"taux_observe_{suffixe}"].to_numpy()
        err = 100 * np.vstack([
            tab[f"taux_observe_{suffixe}"] - tab[f"ic_bas_{suffixe}"],
            tab[f"ic_haut_{suffixe}"] - tab[f"taux_observe_{suffixe}"],
        ])
        x = np.arange(len(tab))
        ax.bar(x, obs, color=couleurs, yerr=err, capsize=4, label="Observé")
        ax.scatter(x, 100 * tab[f"taux_attendu_{suffixe}"], marker="D", color="black",
                   zorder=3, label="Attendu avec la structure du groupe")
        ax.set_xticks(x)
        ax.set_xticklabels([e.replace(" : ", "\n") for e in tab["ensemble"]], fontsize=8)
        ax.set_title(titre)
    axes[0].set_ylabel("Part de DPE classés F ou G (%)")
    fig.suptitle("Taux de passoires des écoquartiers selon le dernier label")
    enregistrer_figure(fig, chemin, legende=axes[0].get_legend_handles_labels())


# --- 2. Répartition des classes ---------------------------------------------


def repartition_classes(dpe_path, dpe_eco, communes) -> pd.DataFrame:
    """
    Part de chaque classe énergie et GES dans chaque groupe.
    Colonnes : groupe, classe, n, part_pct, variable.
    """
    morceaux = []
    for variable, (classe, _) in VARIABLES.items():
        tab = compter_par_groupe(dpe_path, dpe_eco, communes,
                                 f"{classe} AS classe, COUNT(*) AS n", par=classe)
        tab["part_pct"] = 100 * tab["n"] / tab.groupby("groupe")["n"].transform("sum")
        morceaux.append(tab.assign(variable=variable))
    return pd.concat(morceaux, ignore_index=True)


def graphique_repartition(rep: pd.DataFrame, chemin) -> None:
    """
    Part de chaque classe (A à G) dans les trois groupes, énergie et GES.
    """
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, variable, titre in zip(axes, VARIABLES, ("Classe énergie", "Classe GES")):
        t = rep[rep["variable"] == variable]
        croise = t.pivot(index="classe", columns="groupe", values="part_pct")
        barres_groupees(ax, croise.reindex(CLASSES).fillna(0))
        ax.set_title(titre)
    axes[0].set_ylabel("Part des DPE du groupe (%)")
    fig.suptitle("Répartition des classes énergie et GES")
    enregistrer_figure(fig, chemin, legende=axes[0].get_legend_handles_labels())


# --- 3. Consommation par classe ---------------------------------------------


def quantiles_consommation(dpe_path, dpe_eco, communes) -> pd.DataFrame:
    """
    Quantiles (5, 25, 50, 75, 95 %) de la consommation d'énergie par classe
    énergie et des émissions de GES par classe GES, pour la France entière et
    les écoquartiers. Colonnes : groupe, classe, n, q05 ... q95, variable.
    """
    liste = "[" + ", ".join(str(q) for q in QUANTILES) + "]"
    noms = [f"q{int(100 * v):02d}" for v in QUANTILES]
    morceaux = []
    for variable, (classe, mesure) in VARIABLES.items():
        tab = compter_par_groupe(
            dpe_path, dpe_eco, communes,
            f"{classe} AS classe, COUNT(*) AS n, "
            f"quantile_cont({mesure}, {liste}) AS q",
            par=classe, groupes=GROUPES[:2])
        # La colonne q contient une liste de 5 valeurs : une colonne par quantile
        q = pd.DataFrame(tab["q"].tolist(), columns=noms)
        morceaux.append(pd.concat([tab[["groupe", "classe", "n"]], q], axis=1)
                        .assign(variable=variable))
    return pd.concat(morceaux, ignore_index=True)


def graphique_boxplots(quant: pd.DataFrame, chemin) -> None:
    """
    Boîtes à moustaches de la consommation (kWh/m²/an) par classe énergie et des
    émissions (kg CO2/m²/an) par classe GES : France entière contre écoquartiers.
    """
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    titres = {"energie": ("Consommation d'énergie primaire (kWh/m²/an)", "classe énergie"),
              "ges": ("Émissions de GES (kg CO2/m²/an)", "classe GES")}
    for ax, variable in zip(axes, VARIABLES):
        boites, positions, couleurs = [], [], []
        for j, classe in enumerate(CLASSES):
            for i, (groupe, couleur) in enumerate(zip(GROUPES[:2], COULEURS_GROUPES[:2])):
                ligne = quant[(quant["groupe"] == groupe) & (quant["variable"] == variable)
                              & (quant["classe"] == classe)]
                if ligne.empty:
                    continue
                r = ligne.iloc[0]
                # bxp dessine une boîte à partir de statistiques déjà calculées
                boites.append({"whislo": r["q05"], "q1": r["q25"], "med": r["q50"],
                               "q3": r["q75"], "whishi": r["q95"], "fliers": []})
                positions.append(j + (i - 0.5) * 0.38)
                couleurs.append(couleur)
        bp = ax.bxp(boites, positions=positions, widths=0.34, patch_artist=True,
                    medianprops={"color": "black"}, manage_ticks=False)
        for patch, couleur in zip(bp["boxes"], couleurs):
            patch.set_facecolor(couleur)
        ax.set_xticks(range(len(CLASSES)))
        ax.set_xticklabels(CLASSES)
        ax.set_xlabel(titres[variable][1])
        ax.set_ylabel(titres[variable][0])
    poignees = [plt.Rectangle((0, 0), 1, 1, color=c) for c in COULEURS_GROUPES[:2]]
    fig.suptitle("Consommation et émissions par classe "
                 "(boîtes : quartiles ; moustaches : 5e-95e percentiles)")
    enregistrer_figure(fig, chemin, legende=(poignees, GROUPES[:2]))


def comparaisons(eco, dpe_eco, dpe_path=DPE_CLEAN, out_dir=OUT_DIR) -> dict:
    """
    Calcule tous les tableaux et figures de ce script ; renvoie les tableaux.
    """
    out_dir.mkdir(exist_ok=True)
    communes = communes_ecoquartiers(eco, dpe_eco)

    par_label = taux_passoires_par_label(dpe_path, dpe_eco, communes)
    graphique_label(par_label, out_dir / "passoires_par_label.png")

    repartition = repartition_classes(dpe_path, dpe_eco, communes)
    graphique_repartition(repartition, out_dir / "repartition_classes.png")

    quant = quantiles_consommation(dpe_path, dpe_eco, communes)
    graphique_boxplots(quant, out_dir / "boxplots_consommation.png")

    tableaux = {"passoires_par_label": par_label,
                "repartition_classes": repartition,
                "quantiles_consommation": quant}
    for nom, tab in tableaux.items():
        tab.to_csv(out_dir / f"{nom}.csv", index=False)
    return tableaux


# ----- Exécution du script --------------------------------------------

if __name__ == "__main__":
    eco = load_clean_ecoquartiers()
    dpe_eco = load_dpe_eco()

    pd.set_option("display.width", 220)
    tableaux = comparaisons(eco, dpe_eco)
    print("\npassoires_par_label")
    print(tableaux["passoires_par_label"].round(4).to_string(index=False))
    print("\nrepartition_classes (part en %)")
    print(tableaux["repartition_classes"].pivot_table(
        index=["variable", "classe"], columns="groupe", values="part_pct").round(1).to_string())
    print("\nquantiles_consommation (médiane)")
    print(tableaux["quantiles_consommation"][["groupe", "variable", "classe", "n", "q50"]]
          .round(1).to_string(index=False))

    for fichier in FICHIERS:
        upload_to_s3(OUT_DIR / fichier)