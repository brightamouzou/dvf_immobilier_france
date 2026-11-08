"""Analyse exploratoire des ventes immobilières en Île-de-France (DVF géolocalisées, 2021-2025).

Étapes :
    1. Télécharger les fichiers DVF des 8 départements d'Île-de-France
    2. Charger les données utiles
    3. Nettoyer : ventes d'un seul logement, surfaces valides, valeurs aberrantes (IQR)
    4. Analyser : prix au m², évolution, Paris vs petite couronne, effet de composition
    5. Produire deux graphiques dans le dossier figures/

Usage :
    uv run python analyse_dvf.py
"""
import urllib.request
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

DEPARTEMENTS = ["75", "77", "78", "91", "92", "93", "94", "95"]
PETITE_COURONNE = ["92", "93", "94"]
ANNEES = [2021, 2022, 2023, 2024, 2025]
URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/departements/{dep}.csv.gz"
DOSSIER_DONNEES = Path("data")
DOSSIER_FIGURES = Path("figures")

LOGEMENTS = ["Appartement", "Maison"]
COMMERCE = "Local industriel. commercial ou assimilé"
COLONNES = ["id_mutation", "date_mutation", "nature_mutation", "valeur_fonciere",
            "code_departement", "nom_commune", "type_local", "surface_reelle_bati"]


# ---------------------------------------------------------------------------
# 1. Téléchargement
# ---------------------------------------------------------------------------
def telecharger() -> None:
    """Télécharge les fichiers manquants (environ 60 Mo au total)."""
    DOSSIER_DONNEES.mkdir(exist_ok=True)
    for annee in ANNEES:
        for dep in DEPARTEMENTS:
            fichier = DOSSIER_DONNEES / f"{annee}_{dep}.csv.gz"
            if fichier.exists():
                continue
            print(f"Téléchargement {annee} / {dep}…")
            urllib.request.urlretrieve(URL.format(annee=annee, dep=dep), fichier)


# ---------------------------------------------------------------------------
# 2. Chargement
# ---------------------------------------------------------------------------
def charger() -> pd.DataFrame:
    morceaux = []
    for annee in ANNEES:
        for dep in DEPARTEMENTS:
            df = pd.read_csv(DOSSIER_DONNEES / f"{annee}_{dep}.csv.gz", usecols=COLONNES,
                             dtype={"code_departement": str, "id_mutation": str},
                             low_memory=False)
            df["annee"] = annee
            morceaux.append(df)
    return pd.concat(morceaux, ignore_index=True)


# ---------------------------------------------------------------------------
# 3. Nettoyage
# ---------------------------------------------------------------------------
def nettoyer(brut: pd.DataFrame) -> pd.DataFrame:
    ventes = brut[brut["nature_mutation"] == "Vente"].copy()

    # Une mutation (vente) peut regrouper plusieurs biens : son prix est alors global
    # et ne peut pas être attribué à un seul logement. On ne garde que les mutations
    # portant sur exactement un logement, sans local commercial.
    ventes["est_logement"] = ventes["type_local"].isin(LOGEMENTS)
    ventes["est_commerce"] = ventes["type_local"] == COMMERCE
    par_mutation = ventes.groupby("id_mutation")[["est_logement", "est_commerce"]].sum()
    simples = par_mutation[(par_mutation["est_logement"] == 1)
                           & (par_mutation["est_commerce"] == 0)].index

    logements = ventes[ventes["id_mutation"].isin(simples) & ventes["est_logement"]].copy()
    logements = logements.dropna(subset=["valeur_fonciere", "surface_reelle_bati"])
    logements = logements[(logements["surface_reelle_bati"] >= 9)       # surface minimale légale
                          & (logements["valeur_fonciere"] > 0)]
    logements["prix_m2"] = logements["valeur_fonciere"] / logements["surface_reelle_bati"]

    # Valeurs aberrantes : méthode IQR, calculée par département et type de bien,
    # car un prix normal à Paris serait aberrant en Seine-et-Marne.
    groupes = logements.groupby(["code_departement", "type_local"])["prix_m2"]
    q1 = groupes.transform(lambda s: s.quantile(0.25))
    q3 = groupes.transform(lambda s: s.quantile(0.75))
    ecart = q3 - q1
    propres = logements[logements["prix_m2"].between(q1 - 1.5 * ecart,
                                                     q3 + 1.5 * ecart)].copy()

    print(f"Lignes brutes : {len(brut):,}")
    print(f"Ventes d'un seul logement : {len(logements):,}")
    print(f"Valeurs aberrantes retirées (IQR) : {len(logements) - len(propres):,}")
    print(f"Ventes retenues : {len(propres):,}\n")
    return propres.drop(columns=["est_logement", "est_commerce"])


# ---------------------------------------------------------------------------
# 4. Analyses
# ---------------------------------------------------------------------------
def prix_par_departement(df: pd.DataFrame) -> None:
    print("== Prix médian au m² par département, 2025 ==")
    d25 = df[df["annee"] == 2025]
    print(d25.pivot_table(index="code_departement", columns="type_local",
                          values="prix_m2", aggfunc="median").round(0).to_string(), "\n")


def evolution(app: pd.DataFrame) -> pd.DataFrame:
    print("== Prix médian au m² des appartements par année ==")
    evol = app.pivot_table(index="annee", columns="code_departement",
                           values="prix_m2", aggfunc="median")
    evol["IDF"] = app.groupby("annee")["prix_m2"].median()
    print(evol.round(0).to_string())
    print("\nVariation 2021 -> 2025 (%) :")
    print(((evol.loc[2025] / evol.loc[2021] - 1) * 100).round(1).to_string(), "\n")
    return evol


def volume_de_ventes(df: pd.DataFrame) -> None:
    print("== Nombre de ventes par année ==")
    volume = df.groupby("annee").size()
    print(volume.to_string())
    print(f"2021 -> 2024 : {(volume[2024] / volume[2021] - 1) * 100:+.1f} %")
    print(f"2024 -> 2025 : {(volume[2025] / volume[2024] - 1) * 100:+.1f} %\n")


def paris_vs_petite_couronne(app: pd.DataFrame) -> None:
    print("== Paris vs petite couronne (appartements 2025) ==")
    a25 = app[app["annee"] == 2025]
    paris = a25.loc[a25["code_departement"] == "75", "prix_m2"]
    couronne = a25.loc[a25["code_departement"].isin(PETITE_COURONNE), "prix_m2"]

    # Les prix au m² ne suivent pas une loi normale (distribution asymétrique) :
    # on utilise un test non paramétrique, Mann-Whitney, plutôt qu'un test t.
    test = stats.mannwhitneyu(paris, couronne, alternative="greater")
    print(f"Médiane Paris : {paris.median():.0f} €/m² (n={len(paris):,})")
    print(f"Médiane petite couronne : {couronne.median():.0f} €/m² (n={len(couronne):,})")
    print(f"Rapport : {paris.median() / couronne.median():.2f}")
    print(f"Mann-Whitney U, p-value : {test.pvalue:.2e}\n")


def effet_de_composition(app: pd.DataFrame) -> None:
    # Le prix médian régional monte alors qu'il baisse dans presque chaque département :
    # la part de Paris (le plus cher) dans les ventes augmente. C'est un paradoxe de Simpson.
    print("== Part de Paris dans les ventes d'appartements (%) ==")
    part = app.groupby("annee")["code_departement"].apply(lambda s: (s == "75").mean() * 100)
    print(part.round(1).to_string(), "\n")


def correlations(df: pd.DataFrame) -> None:
    print("== Corrélations de Spearman (2025) ==")
    d25 = df[df["annee"] == 2025]
    for type_local in LOGEMENTS:
        sous = d25[d25["type_local"] == type_local]
        rho_prix, _ = stats.spearmanr(sous["surface_reelle_bati"], sous["valeur_fonciere"])
        rho_m2, _ = stats.spearmanr(sous["surface_reelle_bati"], sous["prix_m2"])
        print(f"{type_local} : surface / prix = {rho_prix:.2f} ; "
              f"surface / prix au m² = {rho_m2:.2f}")
    print()


def arrondissements(app: pd.DataFrame) -> None:
    print("== Paris : arrondissements les moins et les plus chers (appartements 2025) ==")
    p25 = app[(app["annee"] == 2025) & (app["code_departement"] == "75")]
    arr = p25.groupby("nom_commune")["prix_m2"].agg(["median", "size"])
    arr = arr[arr["size"] >= 100].sort_values("median")
    print(arr.head(3).round(0).to_string())
    print(arr.tail(3).round(0).to_string(), "\n")


def prix_par_surface(app: pd.DataFrame) -> None:
    print("== Prix médian au m² par surface (appartements IDF 2025) ==")
    a25 = app[app["annee"] == 2025]
    tranches = pd.cut(a25["surface_reelle_bati"], [0, 30, 50, 80, 120, np.inf],
                      labels=["<30 m²", "30-50 m²", "50-80 m²", "80-120 m²", ">120 m²"])
    print(a25.groupby(tranches, observed=True)["prix_m2"].median().round(0).to_string(), "\n")


# ---------------------------------------------------------------------------
# 5. Graphiques
# ---------------------------------------------------------------------------
def graphiques(app: pd.DataFrame, evol: pd.DataFrame) -> None:
    DOSSIER_FIGURES.mkdir(exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    for dep in ["75", "92", "94", "93", "78"]:
        ax.plot(evol.index, evol[dep], marker="o", label=dep)
    ax.plot(evol.index, evol["IDF"], marker="o", linestyle="--", color="black",
            label="Île-de-France")
    ax.set_title("Prix médian au m² des appartements, 2021-2025")
    ax.set_xlabel("Année")
    ax.set_ylabel("€ / m²")
    ax.set_xticks(evol.index)
    ax.legend(title="Département", loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.tight_layout()
    fig.savefig(DOSSIER_FIGURES / "evolution_prix.png", dpi=150)

    fig, ax = plt.subplots(figsize=(9, 5))
    a25 = app[app["annee"] == 2025]
    ordre = a25.groupby("code_departement")["prix_m2"].median().sort_values().index
    ax.boxplot([a25.loc[a25["code_departement"] == d, "prix_m2"] for d in ordre],
               tick_labels=list(ordre), showfliers=False)
    ax.set_title("Distribution du prix au m² des appartements par département, 2025")
    ax.set_xlabel("Département")
    ax.set_ylabel("€ / m²")
    fig.tight_layout()
    fig.savefig(DOSSIER_FIGURES / "distribution_prix_2025.png", dpi=150)
    print(f"Graphiques enregistrés dans {DOSSIER_FIGURES}/")


def main() -> None:
    telecharger()
    df = nettoyer(charger())
    app = df[df["type_local"] == "Appartement"]

    prix_par_departement(df)
    evol = evolution(app)
    volume_de_ventes(df)
    paris_vs_petite_couronne(app)
    effet_de_composition(app)
    correlations(df)
    arrondissements(app)
    prix_par_surface(app)
    graphiques(app, evol)


if __name__ == "__main__":
    main()
