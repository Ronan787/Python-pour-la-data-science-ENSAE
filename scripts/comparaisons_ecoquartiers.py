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
                                   (structure du groupe, taux par catégorie de la
                                   France entière) pour chaque label.
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
    Le taux attendu est défini comme dans scripts/structure_logement_ecoquartiers.py.
    Boîtes à moustaches : boîte = quartiles, trait = médiane, moustaches = 5e et
    95e percentiles. Elles sont tracées à partir de quantiles calculés dans la
    base (7 millions de DPE) plutôt que sur les données brutes. Les consommations
    sont plafonnées au 99e percentile par le nettoyage (winsorisation).

UTILISATION
    python scripts/croisement_dpe_ecoquartiers.py   # une fois
    python scripts/comparaisons_ecoquartiers.py
"""

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_cleaning_ecoquartiers import load_clean_ecoquartiers
from stat_desc_ecoquartiers import (
    COULEURS_LABEL, DPE_CLEAN, DPE_ECO, GROUPES, OUT_DIR, S3_BUCKET,
    communes_ecoquartiers, upload_to_s3, wilson,
)
from structure_logement_ecoquartiers import CELLULE, SQL_CELLULES

# --- Paramètres -------------------------------------------------------------

COLONNES_ECO = ["numero_dpe", "cog", "classe_energie", "classe_ges", "modele_dpe",
                "type_batiment", "annee_construction", "conso_energie",
                "emissions_ges", "eco_label"]
CLASSES = list("ABCDEFG")
QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
COULEURS_GROUPES = ["#7f7f7f", "#31a354", "#6baed6"]
# Lignes du tableau par label : un label seul, puis regroupements
ENSEMBLES_LABELS = {
    "Label 1 : en projet": [1], "Label 2 : en chantier": [2],
    "Label 3 : livré": [3], "Label 4 : confirmé": [4],
    "Labels 1-2 (projet, chantier)": [1, 2], "Labels 3-4 (livré, confirmé)": [3, 4],
}


def load_dpe_eco_complet() -> pd.DataFrame:
    """
    Lit les DPE situés dans un écoquartier, avec leurs variables utiles.
    """
    if not DPE_ECO.exists():
        raise FileNotFoundError(
            f"{DPE_ECO} introuvable : lancez d'abord "
            "python scripts/croisement_dpe_ecoquartiers.py"
        )
    return pd.read_parquet(DPE_ECO, columns=COLONNES_ECO)


# --- 1. Taux de passoires par label -----------------------------------------


def cellules_france(dpe_path) -> pd.DataFrame:
    """
    Nombre de DPE et de passoires par catégorie (modèle, type, période),
    France entière.
    """
    return duckdb.connect().execute(
        f"SELECT {SQL_CELLULES} FROM read_parquet(?) "
        "GROUP BY modele_dpe, type_batiment, periode",
        [str(dpe_path)],
    ).df()


def cellules_ecoquartiers_par_label(dpe_eco) -> pd.DataFrame:
    """
    Même comptage pour les DPE d'écoquartier, par dernier label.
    """
    con = duckdb.connect()
    con.register("dpe_eco", dpe_eco)
    return con.execute(
        f"SELECT eco_label AS label, {SQL_CELLULES} FROM dpe_eco "
        "GROUP BY eco_label, modele_dpe, type_batiment, periode"
    ).df()


def taux_passoires_par_label(france: pd.DataFrame, par_label: pd.DataFrame) -> pd.DataFrame:
    """
    Pour chaque label (ou regroupement) : effectif, taux observé (avec intervalle
    de confiance) et taux attendu pour la structure du groupe.
    """
    f = france.set_index(CELLULE)
    taux_france = pd.DataFrame({
        "energie": f["passoires_energie"] / f["n"],
        "ges": f["passoires_ges"] / f["n"],
    })
    lignes = []
    for nom, labels in ENSEMBLES_LABELS.items():
        g = par_label[par_label["label"].isin(labels)]
        g = g.groupby(CELLULE)[["n", "passoires_energie", "passoires_ges"]].sum()
        if g.empty:
            continue
        n = g["n"].sum()
        ligne = {"ensemble": nom, "n": int(n)}
        for suffixe in ("energie", "ges"):
            k = g[f"passoires_{suffixe}"].sum()
            bas, haut = wilson(k, n)
            ligne[f"taux_observe_{suffixe}"] = k / n
            ligne[f"ic_bas_{suffixe}"] = float(bas)
            ligne[f"ic_haut_{suffixe}"] = float(haut)
            ligne[f"taux_attendu_{suffixe}"] = (
                g["n"] * taux_france.loc[g.index, suffixe]).sum() / n
            ligne[f"ecart_{suffixe}"] = (
                ligne[f"taux_observe_{suffixe}"] - ligne[f"taux_attendu_{suffixe}"])
        lignes.append(ligne)
    return pd.DataFrame(lignes)


def graphique_label(tab: pd.DataFrame, chemin) -> None:
    """
    Taux de passoires observé (barres, avec intervalle de confiance) et taux
    attendu pour la structure (losange noir), pour chacun des quatre labels.
    """
    tab = tab[tab["ensemble"].str.startswith("Label ") & ~tab["ensemble"].str.startswith("Labels")]
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
    fig.legend(*axes[0].get_legend_handles_labels(), loc="lower center", ncol=2,
               fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


# --- 2 et 3. Classes et consommation ----------------------------------------


def sources_groupes(con, dpe_path, dpe_eco, communes) -> dict:
    """
    Source SQL de chacun des trois groupes : (table, filtre, paramètres).
    """
    con.register("communes", communes)
    con.register("dpe_eco", dpe_eco)
    con.register("numeros_eco", dpe_eco[["numero_dpe"]].dropna().drop_duplicates())
    return {
        GROUPES[0]: ("read_parquet(?)", "", [str(dpe_path)]),
        GROUPES[1]: ("dpe_eco", "", []),
        GROUPES[2]: ("read_parquet(?)",
                     """WHERE cog IN (SELECT cog FROM communes)
                        AND numero_dpe NOT IN (SELECT numero_dpe FROM numeros_eco)""",
                     [str(dpe_path)]),
    }


def repartition_classes(dpe_path, dpe_eco, communes) -> pd.DataFrame:
    """
    Part de chaque classe énergie et GES dans chaque groupe.
    Colonnes : groupe, variable, classe, n, part_pct.
    """
    con = duckdb.connect()
    sources = sources_groupes(con, dpe_path, dpe_eco, communes)
    morceaux = []
    for groupe, (source, filtre, params) in sources.items():
        for variable, colonne in (("energie", "classe_energie"), ("ges", "classe_ges")):
            tab = con.execute(
                f"SELECT {colonne} AS classe, COUNT(*) AS n FROM {source} {filtre} "
                f"GROUP BY {colonne}", params).df()
            tab["part_pct"] = 100 * tab["n"] / tab["n"].sum()
            morceaux.append(tab.assign(groupe=groupe, variable=variable))
    return pd.concat(morceaux, ignore_index=True)


def graphique_repartition(rep: pd.DataFrame, chemin) -> None:
    """
    Part de chaque classe (A à G) dans les trois groupes, énergie et GES.
    """
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, variable, titre in zip(axes, ("energie", "ges"), ("Classe énergie", "Classe GES")):
        x = np.arange(len(CLASSES))
        for i, (groupe, couleur) in enumerate(zip(GROUPES, COULEURS_GROUPES)):
            t = rep[(rep["groupe"] == groupe) & (rep["variable"] == variable)]
            t = t.set_index("classe")["part_pct"].reindex(CLASSES).fillna(0)
            ax.bar(x + (i - 1) * 0.27, t.to_numpy(), 0.27, color=couleur, label=groupe)
        ax.set_xticks(x)
        ax.set_xticklabels(CLASSES)
        ax.set_title(titre)
    axes[0].set_ylabel("Part des DPE du groupe (%)")
    fig.suptitle("Répartition des classes énergie et GES")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="lower center", ncol=3,
               fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


def quantiles_consommation(dpe_path, dpe_eco, communes) -> pd.DataFrame:
    """
    Quantiles (5, 25, 50, 75, 95 %) de la consommation d'énergie par classe
    énergie et des émissions de GES par classe GES, pour la France entière et
    les écoquartiers. Colonnes : groupe, variable, classe, n, q05 ... q95.
    """
    con = duckdb.connect()
    sources = sources_groupes(con, dpe_path, dpe_eco, communes)
    liste = "[" + ", ".join(str(q) for q in QUANTILES) + "]"
    morceaux = []
    for groupe in GROUPES[:2]:
        source, filtre, params = sources[groupe]
        for variable, mesure, classe in (("energie", "conso_energie", "classe_energie"),
                                         ("ges", "emissions_ges", "classe_ges")):
            tab = con.execute(
                f"SELECT {classe} AS classe, COUNT(*) AS n, "
                f"quantile_cont({mesure}, {liste}) AS q FROM {source} {filtre} "
                f"GROUP BY {classe}", params).df()
            q = pd.DataFrame(tab["q"].tolist(), columns=[f"q{int(100 * v):02d}" for v in QUANTILES])
            morceaux.append(pd.concat([tab[["classe", "n"]], q], axis=1)
                            .assign(groupe=groupe, variable=variable))
    return pd.concat(morceaux, ignore_index=True)


def graphique_boxplots(quant: pd.DataFrame, chemin) -> None:
    """
    Boîtes à moustaches de la consommation (kWh/m²/an) par classe énergie et des
    émissions (kg CO2/m²/an) par classe GES : France entière contre écoquartiers.
    """
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    titres = {"energie": ("Consommation d'énergie primaire (kWh/m²/an)", "classe énergie"),
              "ges": ("Émissions de GES (kg CO2/m²/an)", "classe GES")}
    for ax, variable in zip(axes, ("energie", "ges")):
        boites, positions, couleurs = [], [], []
        for j, classe in enumerate(CLASSES):
            for i, (groupe, couleur) in enumerate(zip(GROUPES[:2], COULEURS_GROUPES[:2])):
                ligne = quant[(quant["groupe"] == groupe) & (quant["variable"] == variable)
                              & (quant["classe"] == classe)]
                if ligne.empty:
                    continue
                r = ligne.iloc[0]
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
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in COULEURS_GROUPES[:2]]
    fig.legend(handles, GROUPES[:2], loc="lower center", ncol=2, fontsize=9, frameon=False)
    fig.suptitle("Consommation et émissions par classe (boîtes : quartiles ; moustaches : 5e-95e percentiles)")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


def comparaisons(eco, dpe_eco, dpe_path=DPE_CLEAN, out_dir=OUT_DIR) -> dict:
    """
    Calcule tous les tableaux et figures de ce script ; renvoie les tableaux.
    """
    out_dir.mkdir(exist_ok=True)
    communes = communes_ecoquartiers(eco, dpe_eco)

    par_label = taux_passoires_par_label(
        cellules_france(dpe_path), cellules_ecoquartiers_par_label(dpe_eco))
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
    dpe_eco = load_dpe_eco_complet()

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

    for fichier in ("passoires_par_label.png", "passoires_par_label.csv",
                    "repartition_classes.png", "repartition_classes.csv",
                    "boxplots_consommation.png", "quantiles_consommation.csv"):
        upload_to_s3(S3_BUCKET, OUT_DIR / fichier)