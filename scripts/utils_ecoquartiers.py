"""
OBJECTIF
    Outils partagés par les scripts sur les écoquartiers : accès à S3, constantes,
    comptage des DPE par groupe, intervalle de confiance et taux attendu.

CONTENU
    1. Paramètres        chemins, noms des groupes, labels, couleurs.
    2. S3                get_fs(), upload_to_s3().
    3. Chargement        load_dpe_eco(), communes_ecoquartiers().
    4. Comptages         compter_par_groupe() : le même comptage SQL pour les trois
                         groupes de DPE comparés dans le rapport.
    5. Statistiques      wilson(), observe_et_attendu().
    6. Figures           barres_groupees(), enregistrer_figure().

LES TROIS GROUPES DE DPE COMPARÉS
    - France entière (inclut les écoquartiers, environ 1 % des DPE) ;
    - écoquartiers : DPE dont le point est dans un périmètre d'écoquartier ;
    - communes d'écoquartier hors écoquartier : les autres DPE de ces communes.
      Ce troisième groupe sert de témoin, car les écoquartiers sont plutôt dans
      des communes urbaines qui ne ressemblent pas à la moyenne nationale.

REMARQUES
    Passoire = classe F ou G, calculée séparément pour l'énergie et pour les
    émissions de GES.
    Communes d'écoquartier = code commune principal de la base écoquartiers
    + communes des DPE situés dans un écoquartier. Un écoquartier à cheval sur
    plusieurs communes, ou une commune fusionnée depuis, peut être mal rattaché.
    Intervalles de confiance à 95 % : méthode de Wilson. Ils supposent des DPE
    indépendants, ce qui n'est pas exact (plusieurs DPE d'un même immeuble) :
    ils sont donc plutôt trop étroits.
    Taux attendu (standardisation indirecte) : on découpe les DPE en catégories
    (modèle de DPE × type de bâtiment × période de construction). Le taux attendu
    d'un groupe est la moyenne, pondérée par les effectifs du groupe, des taux de
    passoires de ces catégories en France entière. C'est le taux qu'aurait le
    groupe si, à structure donnée, il avait les mêmes performances que la France.
    Un écart « observé - attendu » négatif signifie que le groupe fait mieux que
    ce que sa structure seule laisserait prévoir.
"""

import mimetypes
import shutil
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")  # pas d'écran : on enregistre seulement des fichiers
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import s3fs

# --- 1. Paramètres ----------------------------------------------------------

S3_ENDPOINT = "https://minio.lab.sspcloud.fr"
S3_BUCKET = "cbouyer"  # bucket où sont enregistrés les résultats (dossier diffusion)

DPE_CLEAN = "s3://mboutbal/diffusion/dpe_clean.parquet"  # DPE nettoyés (France)
DPE_ECO = Path("resultats/dpe_ecoquartiers.parquet")  # DPE dans un écoquartier
OUT_DIR = Path("figures_ecoquartiers")  # tableaux et figures, avant envoi sur S3

GROUPES = ["France entière", "Écoquartiers", "Communes d'écoquartier hors écoquartier"]
COULEURS_GROUPES = ["#7f7f7f", "#31a354", "#6baed6"]
LABELS = {1: "En projet", 2: "En chantier", 3: "Livré", 4: "Confirmé"}
COULEURS_LABEL = {1: "#9ecae1", 2: "#fdae6b", 3: "#31a354", 4: "#006d2c"}

# Colonnes lues dans le fichier des DPE d'écoquartier (sans la géométrie)
COLONNES_DPE_ECO = [
    "numero_dpe", "cog", "classe_energie", "classe_ges", "eco_id", "eco_label",
    "modele_dpe", "type_batiment", "annee_construction", "conso_energie",
    "emissions_ges",
]

# Compte, pour un groupe de DPE : effectif et nombre de passoires (F ou G)
SQL_COMPTES = """
    COUNT(*) AS n,
    SUM((classe_energie IN ('F', 'G'))::INTEGER) AS passoires_energie,
    SUM((classe_ges IN ('F', 'G'))::INTEGER) AS passoires_ges
"""
# Période de construction : « Inconnue » regroupe les années manquantes et
# inférieures à 1700 (valeurs 0 ou 1 non renseignées, souvent des logements neufs)
SQL_PERIODE = """
    CASE WHEN annee_construction IS NULL OR annee_construction < 1700 THEN 'Inconnue'
         WHEN annee_construction <= 1945 THEN 'Avant 1946'
         WHEN annee_construction <= 1970 THEN '1946-1970'
         WHEN annee_construction <= 1990 THEN '1971-1990'
         WHEN annee_construction <= 2005 THEN '1991-2005'
         WHEN annee_construction <= 2012 THEN '2006-2012'
         ELSE '2013-2021' END"""
# Une catégorie de DPE : modèle de DPE, type de bâtiment, période de construction
CELLULE = ["modele_dpe", "type_batiment", "periode"]
SQL_CELLULES = f"""
    COALESCE(modele_dpe, 'Non renseigné') AS modele_dpe,
    COALESCE(type_batiment, 'Non renseigné') AS type_batiment,
    {SQL_PERIODE} AS periode,
    {SQL_COMPTES}"""


# --- 2. S3 ------------------------------------------------------------------


def get_fs() -> s3fs.S3FileSystem:
    """
    Système de fichiers S3 du SSP Cloud (utilise les identifiants de la session).
    """
    return s3fs.S3FileSystem(client_kwargs={"endpoint_url": S3_ENDPOINT})


def upload_to_s3(fichier, bucket: str = S3_BUCKET) -> None:
    """
    Enregistre un fichier local dans le dossier diffusion du bucket, avec le
    bon type de contenu pour que les images et les cartes s'affichent.
    """
    fichier = Path(fichier)
    chemin = f"{bucket}/diffusion/{fichier.name}"
    type_contenu = mimetypes.guess_type(fichier.name)[0] or "application/octet-stream"
    with open(fichier, "rb") as f_in:
        with get_fs().open(chemin, "wb", ContentType=type_contenu,
                           ContentDisposition="inline") as f_out:
            shutil.copyfileobj(f_in, f_out)
    print(f"Fichier exporté vers s3://{chemin}")


# --- 3. Chargement ----------------------------------------------------------


def load_dpe_eco() -> pd.DataFrame:
    """
    Lit les DPE situés dans un écoquartier (fichier produit par
    scripts/croisement_dpe_ecoquartiers.py), sans la géométrie.
    """
    if not DPE_ECO.exists():
        raise FileNotFoundError(
            f"{DPE_ECO} introuvable : lancez d'abord "
            "python scripts/croisement_dpe_ecoquartiers.py"
        )
    return pd.read_parquet(DPE_ECO, columns=COLONNES_DPE_ECO)


def communes_ecoquartiers(eco, dpe_eco) -> pd.DataFrame:
    """
    Codes commune des écoquartiers : commune principale de la base
    écoquartiers + communes des DPE situés dans un écoquartier.
    """
    codes = set(eco["insee"].dropna()) | set(dpe_eco["cog"].dropna())
    return pd.DataFrame({"cog": sorted(codes)})


# --- 4. Comptages par groupe ------------------------------------------------


def compter_par_groupe(dpe_path, dpe_eco, communes, colonnes, par="",
                       groupes=GROUPES) -> pd.DataFrame:
    """
    Exécute la même requête SQL pour chacun des groupes de DPE.
    colonnes : expressions du SELECT (par exemple SQL_COMPTES) ;
    par : colonnes du GROUP BY (vide = une seule ligne par groupe) ;
    groupes : sous-ensemble de GROUPES à calculer (les trois par défaut).
    Renvoie un tableau dont la première colonne, « groupe », nomme le groupe.
    """
    con = duckdb.connect()
    con.register("communes", communes)
    con.register("dpe_eco", dpe_eco)
    con.register("numeros_eco", dpe_eco[["numero_dpe"]].dropna().drop_duplicates())

    # groupe -> (table à lire, filtre, paramètres de la requête)
    sources = {
        GROUPES[0]: ("read_parquet(?)", "", [str(dpe_path)]),
        GROUPES[1]: ("dpe_eco", "", []),
        GROUPES[2]: ("read_parquet(?)",
                     """WHERE cog IN (SELECT cog FROM communes)
                        AND numero_dpe NOT IN (SELECT numero_dpe FROM numeros_eco)""",
                     [str(dpe_path)]),
    }
    morceaux = []
    for groupe in groupes:
        source, filtre, params = sources[groupe]
        requete = f"SELECT {colonnes} FROM {source} {filtre}"
        if par:
            requete += f" GROUP BY {par}"
        tab = con.execute(requete, params).df()
        tab.insert(0, "groupe", groupe)
        morceaux.append(tab)
    return pd.concat(morceaux, ignore_index=True)


# --- 5. Statistiques --------------------------------------------------------


def wilson(k, n, z=1.96):
    """
    Intervalle de confiance de Wilson (95 %) pour une proportion k / n.
    Renvoie les bornes basse et haute.
    """
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    p = k / n
    centre = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    demi = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
    return centre - demi, centre + demi


def taux_france_par_cellule(cellules_france: pd.DataFrame) -> pd.DataFrame:
    """
    Taux de passoires (énergie, GES) de chaque catégorie de DPE en France entière.
    cellules_france : une ligne par catégorie, avec les colonnes de CELLULE, n,
    passoires_energie et passoires_ges.
    """
    f = cellules_france.set_index(CELLULE)
    return pd.DataFrame({
        "energie": f["passoires_energie"] / f["n"],
        "ges": f["passoires_ges"] / f["n"],
    })


def observe_et_attendu(cellules_groupe: pd.DataFrame, taux_france: pd.DataFrame) -> dict:
    """
    Taux de passoires observé (avec intervalle de confiance) et taux attendu
    avec la structure du groupe, pour l'énergie et le GES.
    cellules_groupe : une ligne par catégorie de DPE, avec les colonnes de CELLULE,
    n, passoires_energie et passoires_ges ;
    taux_france : résultat de taux_france_par_cellule().
    """
    g = cellules_groupe.set_index(CELLULE)
    n = g["n"].sum()
    res = {"n": int(n)}
    for suffixe in ("energie", "ges"):
        k = g[f"passoires_{suffixe}"].sum()
        bas, haut = wilson(k, n)
        observe = k / n
        attendu = (g["n"] * taux_france.loc[g.index, suffixe]).sum() / n
        res[f"taux_observe_{suffixe}"] = observe
        res[f"ic_bas_{suffixe}"] = float(bas)
        res[f"ic_haut_{suffixe}"] = float(haut)
        res[f"taux_attendu_{suffixe}"] = attendu
        res[f"ecart_{suffixe}"] = observe - attendu
    return res


# --- 6. Figures -------------------------------------------------------------


def barres_groupees(ax, tab: pd.DataFrame) -> None:
    """
    Barres côte à côte des trois groupes, pour chaque ligne de tab.
    tab : une ligne par catégorie (en index), une colonne par groupe.
    """
    x = np.arange(len(tab))
    largeur = 0.27
    for i, (groupe, couleur) in enumerate(zip(GROUPES, COULEURS_GROUPES)):
        ax.bar(x + (i - 1) * largeur, tab[groupe], largeur, color=couleur, label=groupe)
    ax.set_xticks(x)
    ax.set_xticklabels(tab.index)


def enregistrer_figure(fig, chemin, legende=None) -> None:
    """
    Enregistre la figure en png puis la ferme. legende : (poignées, étiquettes),
    placée en bas de la figure pour ne pas recouvrir les graphiques.
    """
    marge_bas = 0
    if legende is not None:
        fig.legend(*legende, loc="lower center", ncol=len(legende[1]),
                   fontsize=9, frameon=False)
        marge_bas = 0.07
    fig.tight_layout(rect=(0, marge_bas, 1, 1))  # laisse la place à la légende
    fig.savefig(chemin, dpi=150)
    plt.close(fig)