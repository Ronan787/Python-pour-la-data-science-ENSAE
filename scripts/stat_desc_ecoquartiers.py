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
    1. taux_passoires()          compare trois groupes de DPE :
                                 - France entière ;
                                 - écoquartiers ;
                                 - communes d'écoquartier, hors écoquartier.
                                 Enregistre un tableau (csv) et un graphique (png).
    2. carte_ecoquartiers()      carte de France des écoquartiers (html).
    3. tableaux_ecoquartiers()   statistiques descriptives : écoquartiers par label,
                                 par région, caractéristiques (contexte, type
                                 d'opération, ANRU), nombre de DPE par écoquartier
                                 (csv) et deux figures (png).
    Les fichiers sont écrits dans figures_ecoquartiers/ puis envoyés dans le
    dossier diffusion du bucket S3, d'où le rapport Quarto les lit.

REMARQUES
    Passoire = étiquette F ou G, calculée séparément pour l'énergie et pour les
    émissions de GES. Aucun filtre sur l'année de construction : elle est
    inutile pour un taux et retirerait surtout des logements neufs dont l'année
    n'est pas renseignée (valeur 0).
    « France entière » inclut les écoquartiers (environ 1 % des DPE).
    Communes d'écoquartier = code commune principal de la base écoquartiers
    + communes des DPE situés dans un écoquartier. Un écoquartier à cheval sur
    plusieurs communes, ou une commune fusionnée depuis, peut être mal rattaché.
    Intervalles de confiance à 95 % : méthode de Wilson. Ils supposent des DPE
    indépendants, ce qui n'est pas exact (plusieurs DPE d'un même immeuble) :
    ils sont donc plutôt trop étroits.

UTILISATION
    python scripts/croisement_dpe_ecoquartiers.py   # une fois, pour créer le résultat
    python scripts/stat_desc_ecoquartiers.py
"""

import html
import mimetypes
import shutil
from pathlib import Path

import duckdb
import folium
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")  # pas d'écran : on enregistre seulement des fichiers
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import s3fs

from data_cleaning_ecoquartiers import TYPES_OPERATION, load_clean_ecoquartiers

# --- Paramètres -------------------------------------------------------------

DPE_CLEAN = "s3://mboutbal/diffusion/dpe_clean.parquet"
DPE_ECO = Path("resultats/dpe_ecoquartiers.parquet")
S3_BUCKET = "cbouyer"
OUT_DIR = Path("figures_ecoquartiers")

SEUIL_DPE_CARTE = 30  # nombre minimal de DPE pour afficher un taux sur la carte
GROUPES = ["France entière", "Écoquartiers", "Communes d'écoquartier hors écoquartier"]
LABELS = {1: "En projet", 2: "En chantier", 3: "Livré", 4: "Confirmé"}
COULEURS_LABEL = {1: "#9ecae1", 2: "#fdae6b", 3: "#31a354", 4: "#006d2c"}

FOND_CARTE = ("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
              "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}")
ATTRIBUTION_FOND = "Fond de carte : Esri, HERE, Garmin, OpenStreetMap contributors"

# Compte, pour un groupe de DPE : effectif et nombre de passoires (F ou G)
SQL_COMPTES = """
    COUNT(*) AS n,
    SUM((classe_energie IN ('F', 'G'))::INTEGER) AS passoires_energie,
    SUM((classe_ges IN ('F', 'G'))::INTEGER) AS passoires_ges
"""


# --- Envoi sur S3 -----------------------------------------------------------


def upload_to_s3(s3_bucket, file):
    """
    Enregistre le fichier dans le dossier diffusion du bucket défini.
    """
    fs = s3fs.S3FileSystem(
        client_kwargs={"endpoint_url": "https://minio.lab.sspcloud.fr"}
    )
    file = Path(file)
    chemin = f"{s3_bucket}/diffusion/{file.name}"
    content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    with open(file, "rb") as f_in:
        with fs.open(chemin, "wb", ContentType=content_type,
                     ContentDisposition="inline") as f_out:
            shutil.copyfileobj(f_in, f_out)
    print(f"Fichier exporté vers s3://{chemin}")


# --- Chargement -------------------------------------------------------------


def load_dpe_eco() -> pd.DataFrame:
    """
    Lit les DPE situés dans un écoquartier (sans la géométrie).
    """
    if not DPE_ECO.exists():
        raise FileNotFoundError(
            f"{DPE_ECO} introuvable : lancez d'abord "
            "python scripts/croisement_dpe_ecoquartiers.py"
        )
    colonnes = ["numero_dpe", "cog", "classe_energie", "classe_ges", "eco_id"]
    return pd.read_parquet(DPE_ECO, columns=colonnes)


def communes_ecoquartiers(eco, dpe_eco) -> pd.DataFrame:
    """
    Codes commune des écoquartiers : commune principale de la base
    écoquartiers + communes des DPE situés dans un écoquartier.
    """
    codes = set(eco["insee"].dropna()) | set(dpe_eco["cog"].dropna())
    return pd.DataFrame({"cog": sorted(codes)})


# --- 1. Taux de passoires ---------------------------------------------------


def wilson(k, n, z=1.96):
    """
    Intervalle de confiance de Wilson pour une proportion k / n.
    """
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    p = k / n
    centre = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    demi = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
    return centre - demi, centre + demi


def effectifs_groupes(dpe_path, dpe_eco, communes) -> pd.DataFrame:
    """
    Effectif et nombre de passoires (énergie, GES) pour les trois groupes.
    """
    con = duckdb.connect()
    con.register("communes", communes)
    con.register(
        "numeros_eco", dpe_eco[["numero_dpe"]].dropna().drop_duplicates()
    )
    con.register("dpe_eco", dpe_eco)

    france = con.execute(
        f"SELECT {SQL_COMPTES} FROM read_parquet(?)", [str(dpe_path)]
    ).df()
    ecoquartiers = con.execute(f"SELECT {SQL_COMPTES} FROM dpe_eco").df()
    temoin = con.execute(
        f"""SELECT {SQL_COMPTES} FROM read_parquet(?)
            WHERE cog IN (SELECT cog FROM communes)
              AND numero_dpe NOT IN (SELECT numero_dpe FROM numeros_eco)""",
        [str(dpe_path)],
    ).df()

    tab = pd.concat([france, ecoquartiers, temoin], ignore_index=True)
    tab.insert(0, "groupe", GROUPES)
    return tab


def tableau_passoires(effectifs: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute à chaque groupe les taux de passoires et leurs intervalles de confiance.
    """
    tab = effectifs.copy()
    for suffixe in ("energie", "ges"):
        k = tab[f"passoires_{suffixe}"]
        tab[f"taux_{suffixe}"] = k / tab["n"]
        bas, haut = wilson(k, tab["n"])
        tab[f"ic_bas_{suffixe}"] = bas
        tab[f"ic_haut_{suffixe}"] = haut
    return tab


def graphique_passoires(tab: pd.DataFrame, chemin: Path) -> None:
    """
    Barres des taux de passoires par groupe (énergie à gauche, GES à droite).
    """
    couleurs = ["#7f7f7f", "#31a354", "#6baed6"]
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
        ax.bar(x, taux, color=couleurs, yerr=erreurs, capsize=4)
        hauts = 100 * tab[f"ic_haut_{suffixe}"].to_numpy()
        for xi, t, h in zip(x, taux, hauts):
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
    fig.tight_layout()
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


def taux_passoires(eco, dpe_eco, dpe_path=DPE_CLEAN, out_dir=OUT_DIR) -> pd.DataFrame:
    """
    Calcule le tableau des taux de passoires et enregistre csv et png.
    """
    communes = communes_ecoquartiers(eco, dpe_eco)
    print(f"{len(communes)} communes d'écoquartier.")
    tab = tableau_passoires(effectifs_groupes(dpe_path, dpe_eco, communes))
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


def carte_ecoquartiers(eco, dpe_eco, chemin: Path) -> None:
    """
    Carte de France : un point et un périmètre par écoquartier, colorés selon
    le dernier label obtenu.
    """
    res = stats_par_ecoquartier(eco, dpe_eco)
    centres = gpd.GeoSeries(res.geometry.centroid, crs=res.crs).to_crs(4326)
    contours = res.geometry.simplify(10).to_crs(4326)  # allège le fichier html

    # Fond gris clair d'Esri : sans clé d'accès, et lisible même si le fichier
    # html est ouvert depuis le disque (OpenStreetMap refuse ce cas).
    carte = folium.Map(
        location=[46.6, 2.5], zoom_start=6, prefer_canvas=True,
        tiles=FOND_CARTE, attr=ATTRIBUTION_FOND,
    )
    for ligne, point, contour in zip(res.itertuples(), centres, contours):
        couleur = COULEURS_LABEL.get(ligne.label, "#999999")
        bulle = _texte_bulle(ligne)
        folium.GeoJson(
            contour.__geo_interface__,
            style_function=lambda _, c=couleur: {
                "color": c, "fillColor": c, "fillOpacity": 0.4, "weight": 1},
            tooltip=folium.Tooltip(bulle),
        ).add_to(carte)
        folium.CircleMarker(
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


def graphique_labels_annees(res: gpd.GeoDataFrame, chemin: Path) -> None:
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
    bas = np.zeros(len(croise))
    for k in LABELS:
        ax2.bar(croise.index, croise[k], bottom=bas, color=COULEURS_LABEL[k],
                label=LABELS[k])
        bas += croise[k].to_numpy()
    ax2.set_title("Par année du dernier label")
    ax2.set_xlabel("Année du dernier label (hors années non renseignées)")
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


def graphique_distributions(res: gpd.GeoDataFrame, chemin: Path) -> None:
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
    fig.tight_layout()
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


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

    for fichier in sorted(OUT_DIR.iterdir()):
        upload_to_s3(S3_BUCKET, fichier)