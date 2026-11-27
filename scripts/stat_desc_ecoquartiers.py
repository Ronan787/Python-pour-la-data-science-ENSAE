"""
OBJECTIF
    Statistiques descriptives sur les écoquartiers, comparées au reste de la
    France : taux de passoires énergétiques et carte des écoquartiers.

SOURCE
    - DPE nettoyés : s3://mboutbal/diffusion/dpe_clean.parquet
      (produit par scripts/data_cleaning_ademe.py)
    - DPE situés dans un écoquartier : resultats/dpe_ecoquartiers.parquet
      (produit par scripts/croisement_dpe_ecoquartiers.py)
    - Périmètres des écoquartiers : scripts/data_cleaning_ecoquartiers.py

ÉTAPES
    1. taux_passoires()          compare les trois groupes de DPE définis dans
                                 utils_ecoquartiers.py (France entière,
                                 écoquartiers, communes d'écoquartier hors
                                 écoquartier). Enregistre un tableau (csv) et un
                                 graphique (png).
    2. carte_ecoquartiers()      carte de France des écoquartiers (html).
    3. tableaux_ecoquartiers()   statistiques descriptives : écoquartiers par label,
                                 par région, caractéristiques (contexte, type
                                 d'opération, ANRU), nombre de DPE par écoquartier
                                 (csv) et deux figures (png).
    Les fichiers sont écrits dans figures_ecoquartiers/ puis envoyés dans le
    dossier diffusion du bucket S3, d'où le rapport Quarto les lit.

REMARQUES
    Aucun filtre sur l'année de construction : elle est inutile pour un taux et
    retirerait surtout des logements neufs dont l'année n'est pas renseignée
    (valeur 0). Les définitions (passoire, groupes, intervalle de confiance) sont
    dans utils_ecoquartiers.py.

UTILISATION
    python scripts/croisement_dpe_ecoquartiers.py   # une fois, pour créer le résultat
    python scripts/stat_desc_ecoquartiers.py
"""

import html

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_cleaning_ecoquartiers import TYPES_OPERATION, load_clean_ecoquartiers
from utils_ecoquartiers import (
    COULEURS_GROUPES, COULEURS_LABEL, DPE_CLEAN, LABELS, OUT_DIR,
    SQL_COMPTES, communes_ecoquartiers, compter_par_groupe, enregistrer_figure,
    load_dpe_eco, upload_to_s3, wilson,
)

# --- Paramètres -------------------------------------------------------------

SEUIL_DPE_CARTE = 30  # nombre minimal de DPE pour afficher un taux sur la carte

# Fichiers produits par ce script, envoyés sur S3
FICHIERS = [
    "taux_passoires.csv", "taux_passoires.png", "carte_ecoquartiers.html",
    "ecoquartiers_labels.csv", "ecoquartiers_regions.csv",
    "ecoquartiers_caracteristiques.csv", "ecoquartiers_labels_annees.png",
    "ecoquartiers_distributions.png",
]


# --- 1. Taux de passoires ---------------------------------------------------


def tableau_passoires(effectifs: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute à chaque groupe les taux de passoires et leurs intervalles de confiance.
    effectifs : une ligne par groupe, avec n, passoires_energie et passoires_ges.
    """
    tab = effectifs.copy()
    for suffixe in ("energie", "ges"):
        k = tab[f"passoires_{suffixe}"]
        tab[f"taux_{suffixe}"] = k / tab["n"]
        bas, haut = wilson(k, tab["n"])
        tab[f"ic_bas_{suffixe}"] = bas
        tab[f"ic_haut_{suffixe}"] = haut
    return tab


def graphique_passoires(tab: pd.DataFrame, chemin) -> None:
    """
    Barres des taux de passoires par groupe (énergie à gauche, GES à droite).
    """
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, suffixe, titre in zip(
        axes, ("energie", "ges"), ("Classe énergie", "Classe GES")
    ):
        taux = 100 * tab[f"taux_{suffixe}"].to_numpy()
        erreurs = 100 * np.vstack([
            tab[f"taux_{suffixe}"] - tab[f"ic_bas_{suffixe}"],
            tab[f"ic_haut_{suffixe}"] - tab[f"taux_{suffixe}"],
        ])
        x = np.arange(len(tab))
        ax.bar(x, taux, color=COULEURS_GROUPES, yerr=erreurs, capsize=4)
        hauts = 100 * tab[f"ic_haut_{suffixe}"].to_numpy()
        for xi, t, h in zip(x, taux, hauts):  # taux écrit au-dessus de chaque barre
            ax.text(xi, h + 0.5, f"{t:.1f} %", ha="center", va="bottom", fontsize=9)
        ax.set_ylim(0, hauts.max() * 1.15)
        ax.set_xticks(x)
        ax.set_xticklabels(
            [g.replace(" hors ", "\nhors ").replace("Communes", "Communes\n")
             if len(g) > 20 else g for g in tab["groupe"]],
            fontsize=8,
        )
        ax.set_title(titre)
    axes[0].set_ylabel("Part de DPE classés F ou G (%)")
    fig.suptitle("Taux de passoires énergétiques (intervalle de confiance à 95 %)")
    enregistrer_figure(fig, chemin)


def taux_passoires(eco, dpe_eco, dpe_path=DPE_CLEAN, out_dir=OUT_DIR) -> pd.DataFrame:
    """
    Calcule le tableau des taux de passoires et enregistre csv et png.
    """
    communes = communes_ecoquartiers(eco, dpe_eco)
    print(f"{len(communes)} communes d'écoquartier.")
    effectifs = compter_par_groupe(dpe_path, dpe_eco, communes, SQL_COMPTES)
    tab = tableau_passoires(effectifs)
    out_dir.mkdir(exist_ok=True)
    tab.to_csv(out_dir / "taux_passoires.csv", index=False)
    graphique_passoires(tab, out_dir / "taux_passoires.png")
    return tab


# --- 2. Carte des écoquartiers ---------------------------------------------


def stats_par_ecoquartier(eco, dpe_eco) -> gpd.GeoDataFrame:
    """
    Ajoute à chaque écoquartier son nombre de DPE et son taux de passoires
    (énergie), vide si moins de SEUIL_DPE_CARTE DPE.
    """
    par_eco = dpe_eco.groupby("eco_id").agg(
        n_dpe=("numero_dpe", "size"),
        n_passoires=("classe_energie", lambda s: int(s.isin(["F", "G"]).sum())),
    )
    res = eco.merge(par_eco, left_on="id", right_index=True, how="left")
    res["n_dpe"] = res["n_dpe"].fillna(0).astype(int)
    res["taux_passoires"] = (res["n_passoires"] / res["n_dpe"]).where(
        res["n_dpe"] >= SEUIL_DPE_CARTE
    )
    return res


def _texte_bulle(ligne) -> str:
    """
    Texte HTML affiché au survol d'un écoquartier.
    """
    if ligne.n_dpe == 0:
        dpe = "aucun DPE"
    elif pd.isna(ligne.taux_passoires):
        dpe = f"{ligne.n_dpe} DPE (trop peu pour un taux)"
    else:
        dpe = f"{ligne.n_dpe} DPE, {100 * ligne.taux_passoires:.1f} % de passoires"
    annee = "non renseignée" if pd.isna(ligne.label_an) else int(ligne.label_an)
    return (
        f"<b>{html.escape(str(ligne.nom))}</b><br>"
        f"{html.escape(str(ligne.com))}<br>"
        f"Label : {LABELS.get(ligne.label, ligne.label)} (année {annee})<br>"
        f"Surface : {ligne.surface_ha:.1f} ha<br>{dpe}"
    )


def carte_ecoquartiers(eco, dpe_eco, chemin) -> None:
    """
    Carte de France : un point et un périmètre par écoquartier, colorés selon
    le dernier label obtenu.
    """
    res = stats_par_ecoquartier(eco, dpe_eco)
    centres = gpd.GeoSeries(res.geometry.centroid, crs=res.crs).to_crs(4326)
    contours = res.geometry.simplify(10).to_crs(4326)  # allège le fichier html

    # Fond de carte OpenStreetMap par défaut, comme les cartes de stat_desc.py.
    # Il s'affiche quand la carte est lue depuis son adresse web (le rapport le
    # fait) ; OpenStreetMap le refuse si le fichier html est ouvert depuis le disque.
    carte = folium.Map(location=[46.6, 2.5], zoom_start=6, prefer_canvas=True)
    for ligne, point, contour in zip(res.itertuples(), centres, contours):
        couleur = COULEURS_LABEL.get(ligne.label, "#999999")
        bulle = _texte_bulle(ligne)
        folium.GeoJson(  # périmètre
            contour.__geo_interface__,
            style_function=lambda _, c=couleur: {
                "color": c, "fillColor": c, "fillOpacity": 0.4, "weight": 1},
            tooltip=folium.Tooltip(bulle),
        ).add_to(carte)
        folium.CircleMarker(  # point, pour repérer les petits écoquartiers
            [point.y, point.x], radius=4, color=couleur, fill=True,
            fill_color=couleur, fill_opacity=0.9, weight=1,
            tooltip=folium.Tooltip(bulle),
        ).add_to(carte)

    lignes = "".join(
        f'<span style="color:{COULEURS_LABEL[k]}">●</span> {v}<br>'
        for k, v in LABELS.items()
    )
    legende = f"""
    <div style="position:fixed; bottom:25px; left:25px; z-index:9999;
                background:white; padding:10px; border:1px solid #aaa;">
      <b>Dernier label obtenu</b><br>{lignes}
      <div style="margin-top:6px; font-size:11px;">
        <i>Sources : Ministère de la Transition écologique,<br>
        ÉcoQuartiers (2021) ; ADEME, DPE 2013-2021</i>
      </div>
    </div>"""
    carte.get_root().html.add_child(folium.Element(legende))
    carte.save(chemin)


# --- 3. Statistiques descriptives des écoquartiers ---------------------------


def tableau_labels(res: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Par dernier label : nombre d'écoquartiers, surface médiane, nombre
    d'écoquartiers avec au moins un DPE, total de DPE et médiane de DPE par
    écoquartier (parmi ceux qui ont au moins un DPE).
    res : résultat de stats_par_ecoquartier().
    """
    tab = res.groupby("label").agg(
        ecoquartiers=("id", "size"),
        surface_mediane_ha=("surface_ha", "median"),
        avec_dpe=("n_dpe", lambda s: int((s > 0).sum())),
        dpe_total=("n_dpe", "sum"),
        dpe_median=("n_dpe", lambda s: s[s > 0].median()),
    ).reset_index()
    tab.insert(1, "libelle", tab["label"].map(LABELS))
    total = pd.DataFrame([{
        "label": "", "libelle": "Ensemble", "ecoquartiers": len(res),
        "surface_mediane_ha": res["surface_ha"].median(),
        "avec_dpe": int((res["n_dpe"] > 0).sum()),
        "dpe_total": int(res["n_dpe"].sum()),
        "dpe_median": res.loc[res["n_dpe"] > 0, "n_dpe"].median(),
    }])
    return pd.concat([tab, total], ignore_index=True)


def tableau_regions(res: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Nombre d'écoquartiers par région, dont ceux dont le dernier label est
    « livré » ou « confirmé » (labels 3 et 4).
    """
    tab = res.assign(region=res["regio"].fillna("Non renseignée")).groupby(
        "region").agg(
        ecoquartiers=("id", "size"), livres=("livre", "sum"),
        dpe_total=("n_dpe", "sum"),
    ).reset_index()
    return tab.sort_values("ecoquartiers", ascending=False, ignore_index=True)


def tableau_caracteristiques(res: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Contexte urbain, types d'opération (cumulables : un écoquartier peut en
    avoir plusieurs) et zone ANRU : nombre d'écoquartiers et part du total.
    """
    n = len(res)
    lignes = []
    contexte = res["contexte"].fillna("Non renseigné").value_counts()
    lignes += [("Contexte urbain", m, int(k)) for m, k in contexte.items()]
    for colonne, libelle in TYPES_OPERATION.items():
        lignes.append(("Type d'opération (cumulable)", libelle, int(res[colonne].sum())))
    lignes.append(("Type d'opération (cumulable)", "Non renseigné",
                   int(res[list(TYPES_OPERATION)[0]].isna().sum())))
    lignes.append(("Zone ANRU", "Oui", int(res["anru"].sum())))
    tab = pd.DataFrame(lignes, columns=["rubrique", "modalite", "ecoquartiers"])
    tab["part_pct"] = 100 * tab["ecoquartiers"] / n
    return tab


def graphique_labels_annees(res: gpd.GeoDataFrame, chemin) -> None:
    """
    Nombre d'écoquartiers par dernier label (gauche) et par année du dernier
    label, empilé par label (droite).
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2),
                                   gridspec_kw={"width_ratios": [1, 2]})
    par_label = res["label"].value_counts().reindex(list(LABELS), fill_value=0)
    ax1.bar([LABELS[k] for k in par_label.index], par_label.to_numpy(),
            color=[COULEURS_LABEL[k] for k in par_label.index])
    for i, v in enumerate(par_label.to_numpy()):
        ax1.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    ax1.set_ylabel("Nombre d'écoquartiers")
    ax1.set_title("Par dernier label")
    ax1.tick_params(axis="x", labelsize=8)

    annees = res.dropna(subset=["label_an"]).assign(annee=lambda d: d["label_an"].astype(int))
    croise = pd.crosstab(annees["annee"], annees["label"]).reindex(
        columns=list(LABELS), fill_value=0)
    bas = np.zeros(len(croise))  # hauteur déjà occupée, pour empiler les labels
    for k in LABELS:
        ax2.bar(croise.index, croise[k], bottom=bas, color=COULEURS_LABEL[k],
                label=LABELS[k])
        bas += croise[k].to_numpy()
    ax2.set_title("Par année du dernier label")
    ax2.set_xlabel("Année du dernier label (hors années non renseignées)")
    ax2.legend(fontsize=8)
    enregistrer_figure(fig, chemin)


def graphique_distributions(res: gpd.GeoDataFrame, chemin) -> None:
    """
    Histogrammes de la surface (ha) et du nombre de DPE par écoquartier, en
    échelle logarithmique : quelques très grands écoquartiers sinon écraseraient
    le graphique.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    surf = res["surface_ha"].dropna()
    ax1.hist(surf, bins=np.logspace(np.log10(surf.min()), np.log10(surf.max()), 25),
             color="#31a354", edgecolor="white")
    ax1.set_xscale("log")
    ax1.axvline(surf.median(), color="black", linestyle="--")
    ax1.set_title(f"Surface (médiane : {surf.median():.0f} ha)")
    ax1.set_xlabel("Hectares (échelle logarithmique)")
    ax1.set_ylabel("Nombre d'écoquartiers")

    avec = res.loc[res["n_dpe"] > 0, "n_dpe"]
    ax2.hist(avec, bins=np.logspace(0, np.log10(avec.max()), 25),
             color="#6baed6", edgecolor="white")
    ax2.set_xscale("log")
    ax2.axvline(avec.median(), color="black", linestyle="--")
    ax2.set_title(f"DPE par écoquartier (médiane : {avec.median():.0f} ; "
                  f"{int((res['n_dpe'] == 0).sum())} sans DPE)")
    ax2.set_xlabel("Nombre de DPE (échelle logarithmique, écoquartiers avec DPE)")
    enregistrer_figure(fig, chemin)


def tableaux_ecoquartiers(eco, dpe_eco, out_dir=OUT_DIR) -> dict:
    """
    Calcule les tableaux et figures descriptifs ; renvoie les tableaux.
    """
    res = stats_par_ecoquartier(eco, dpe_eco)
    tableaux = {
        "ecoquartiers_labels": tableau_labels(res),
        "ecoquartiers_regions": tableau_regions(res),
        "ecoquartiers_caracteristiques": tableau_caracteristiques(res),
    }
    out_dir.mkdir(exist_ok=True)
    for nom, tab in tableaux.items():
        tab.to_csv(out_dir / f"{nom}.csv", index=False)
    graphique_labels_annees(res, out_dir / "ecoquartiers_labels_annees.png")
    graphique_distributions(res, out_dir / "ecoquartiers_distributions.png")
    return tableaux


# ----- Exécution du script --------------------------------------------

if __name__ == "__main__":
    OUT_DIR.mkdir(exist_ok=True)
    eco = load_clean_ecoquartiers()
    dpe_eco = load_dpe_eco()

    tab = taux_passoires(eco, dpe_eco)
    print(tab.round(4).to_string(index=False))

    carte_ecoquartiers(eco, dpe_eco, OUT_DIR / "carte_ecoquartiers.html")
    print("Carte enregistrée.")

    for nom, tableau in tableaux_ecoquartiers(eco, dpe_eco).items():
        print(f"\n{nom}")
        print(tableau.round(1).to_string(index=False))

    for fichier in FICHIERS:
        upload_to_s3(OUT_DIR / fichier)