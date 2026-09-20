"""Motor de diagnóstico para bloques de series de tiempo.

    python motor.py                 # corre todos los bloques de bloques.yml
    python motor.py financiero      # solo uno

Declarás el bloque en `bloques.yml` —qué series, qué transformación, qué
especificación querés— y el motor corre la batería completa de diagnóstico y te
dice **qué te estás jugando** antes de estimar nada.

Lo que el motor NO hace, a propósito:

  · No elige la especificación por vos. Si pedís VECM y Johansen no encuentra
    cointegración, lo estima igual y lo marca como ADVERTENCIA. La decisión es
    económica, no estadística.
  · No elige el orden de Cholesky. Cambiarlo cambia las impulso-respuesta, y el
    criterio es cuál variable es más exógena. Va en el archivo de configuración,
    en el orden en que escribís las series.
  · No interpreta. Produce las tablas; la frase la escribe una persona.
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd
import yaml

sys.stdout.reconfigure(encoding="utf-8")
AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "salidas")

from statsmodels.tsa.api import VAR                                  # noqa: E402
from statsmodels.tsa.stattools import adfuller, coint, kpss          # noqa: E402
from statsmodels.tsa.vector_ar.vecm import (VECM, coint_johansen,    # noqa: E402
                                            select_coint_rank)

# statsmodels reinstala sus filtros al importarse, asi que va despues.
warnings.simplefilter("ignore")
for _cat in ("InterpolationWarning", "ValueWarning", "RuntimeWarning"):
    warnings.filterwarnings("ignore")


# ───────────────────────────────────────────────────────── carga
def cargar(cfg):
    """Devuelve el panel ancho: una columna por serie, índice de fechas."""
    f = cfg["fuente"]
    if f["tipo"] == "duckdb":
        import duckdb
        ruta = os.path.join(AQUI, f["ruta"]) if not os.path.isabs(f["ruta"]) else f["ruta"]
        con = duckdb.connect(ruta, read_only=True)
        d = con.execute(f["consulta"]).df()
        con.close()
    elif f["tipo"] == "csv":
        d = pd.read_csv(os.path.join(AQUI, f["ruta"]), parse_dates=["fecha"])
    else:
        sys.exit(f"Fuente no soportada: {f['tipo']}")
    d["fecha"] = pd.to_datetime(d["fecha"])
    ancho = d.pivot_table(index="fecha", columns="serie_id", values="valor").sort_index()
    return ancho.asfreq(pd.infer_freq(ancho.index) or "MS")


def preparar(panel, bloque):
    """Recorta a las series del bloque, transforma y quita filas incompletas."""
    faltan = [s for s in bloque["series"] if s not in panel.columns]
    if faltan:
        sys.exit(f"[{bloque['nombre']}] estas series no están en la fuente: {faltan}")
    d = panel[bloque["series"]].copy()
    sin_log = set(bloque.get("sin_log", []))
    if bloque.get("transformacion", "nivel") == "log":
        for c in d.columns:
            if c in sin_log:
                continue
            if (d[c] <= 0).any():
                print(f"    aviso: {c} tiene valores <= 0, no se logaritma")
                continue
            d[c] = np.log(d[c])
            d = d.rename(columns={c: f"log_{c}"})
    return d.dropna()


# ────────────────────────────────────────────── diagnóstico
def raices_unitarias(d, alfa=0.05):
    """ADF y KPSS sobre cada serie. La fila interesante es donde se contradicen."""
    filas = []
    for c in d.columns:
        x = d[c].dropna()
        p_adf = adfuller(x, autolag="AIC")[1]
        p_kpss = kpss(x, regression="c", nlags="auto")[1]
        adf_dice = "estacionaria" if p_adf < alfa else "raiz unitaria"
        kpss_dice = "raiz unitaria" if p_kpss < alfa else "estacionaria"
        filas.append({
            "serie": c, "p_adf": round(p_adf, 4), "p_kpss": round(p_kpss, 4),
            "adf_dice": adf_dice, "kpss_dice": kpss_dice,
            "veredicto": adf_dice if adf_dice == kpss_dice else "AMBIGUO",
        })
    return pd.DataFrame(filas)


def seleccion_rezagos(d, maxlags=12):
    """AIC, BIC y HQIC. Que discrepen es lo normal, y hay que saberlo."""
    maxlags = min(maxlags, max(1, len(d) // (len(d.columns) + 1) - 1))
    sel = VAR(d).select_order(maxlags=maxlags)
    elegidos = {k: int(v) for k, v in sel.selected_orders.items()}
    return pd.DataFrame([elegidos]), elegidos


def engle_granger(d, alfa=0.05):
    """Cointegración por pares. Complementa a Johansen, no lo reemplaza."""
    filas = []
    cols = list(d.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            p = coint(d[a], d[b])[1]
            filas.append({"serie_a": a, "serie_b": b, "p_valor": round(p, 4),
                          "cointegran": "si" if p < alfa else "no"})
    return pd.DataFrame(filas)


def johansen(d, rezagos, det_order=0):
    """Traza de Johansen. Devuelve la tabla y el rango sugerido al 5 %."""
    k_ar_diff = max(1, rezagos - 1)
    r = coint_johansen(d, det_order, k_ar_diff)
    filas = []
    for i in range(len(r.lr1)):
        filas.append({
            "hipotesis": f"r <= {i}",
            "estadistico_traza": round(float(r.lr1[i]), 3),
            "critico_90": round(float(r.cvt[i, 0]), 3),
            "critico_95": round(float(r.cvt[i, 1]), 3),
            "critico_99": round(float(r.cvt[i, 2]), 3),
            "rechaza_al_95": "si" if r.lr1[i] > r.cvt[i, 1] else "no",
        })
    tabla = pd.DataFrame(filas)
    try:
        rango = int(select_coint_rank(d, det_order, k_ar_diff, signif=0.05).rank)
    except Exception:
        rango = int((tabla["rechaza_al_95"] == "si").sum())
    return tabla, rango


def holgura_muestral(d, rezagos):
    """Parámetros por ecuación contra observaciones. La regla de oro es 10 a 1."""
    k, T = len(d.columns), len(d)
    params = k * rezagos + 1
    return {"variables": k, "observaciones": T, "rezagos": rezagos,
            "parametros_por_ecuacion": params, "obs_por_parametro": round(T / params, 1)}


# ───────────────────────────────────────────── advertencias
def advertir(bloque, ru, coint_rango, holgura, rezagos_sugeridos, pedido):
    """El núcleo del motor: qué no encaja entre lo que pediste y lo que dicen los datos."""
    av = []
    ambiguas = ru.loc[ru.veredicto == "AMBIGUO", "serie"].tolist()
    if ambiguas:
        av.append(f"ADF y KPSS se contradicen en {', '.join(ambiguas)}. El orden de "
                  f"integración no está resuelto por las pruebas: decidilo con criterio "
                  f"y dejalo escrito.")
    no_estacionarias = ru.loc[ru.veredicto == "raiz unitaria", "serie"].tolist()

    if pedido == "vecm" and coint_rango == 0:
        av.append("Pediste VECM y Johansen no encuentra ninguna relación de "
                  "cointegración al 5 %. Un VECM sin cointegración estima un "
                  "término de corrección que no existe. Lo natural acá es VAR en "
                  "diferencias.")
    if pedido == "var_niveles" and coint_rango > 0:
        av.append(f"Pediste VAR en niveles y Johansen encuentra {coint_rango} "
                  f"relación(es) de cointegración. Un VAR en niveles ignora el "
                  f"ajuste de largo plazo; el VECM lo aprovecha.")
    if pedido == "var_diferencias" and coint_rango > 0:
        av.append(f"Pediste VAR en diferencias y hay {coint_rango} relación(es) de "
                  f"cointegración: diferenciar descarta información de largo plazo.")
    if pedido == "vecm" and not no_estacionarias:
        av.append("Pediste VECM pero ninguna serie parece tener raíz unitaria. "
                  "El VECM asume variables I(1).")
    if holgura["obs_por_parametro"] < 10:
        av.append(f"{holgura['observaciones']} observaciones para "
                  f"{holgura['parametros_por_ecuacion']} parámetros por ecuación: "
                  f"{holgura['obs_por_parametro']} por parámetro. Debajo de 10 el "
                  f"modelo sobreajusta. Bajá rezagos o sacá una variable.")
    distintos = set(rezagos_sugeridos.values())
    if len(distintos) > 1:
        av.append(f"Los criterios de rezagos no coinciden: {rezagos_sugeridos}. "
                  f"BIC es el parsimonioso y AIC el generoso; con muestras cortas "
                  f"suele convenir BIC.")
    return av


# ──────────────────────────────────────────────── estimación
def estimar(d, pedido, rezagos, rango, horizonte):
    """Estima lo que se pidió. Si falla, se reporta el fallo, no se sustituye."""
    try:
        if pedido == "vecm":
            m = VECM(d, k_ar_diff=max(1, rezagos - 1),
                     coint_rank=max(1, rango), deterministic="ci").fit()
            return m, m.irf(horizonte), None
        datos = d.diff().dropna() if pedido == "var_diferencias" else d
        m = VAR(datos).fit(rezagos)
        return m, m.irf(horizonte), m.fevd(horizonte)
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"


def correr(nombre, bloque, panel, cfg):
    print(f"\n{'=' * 70}\nBLOQUE  {nombre}\n{'=' * 70}")
    d = preparar(panel, bloque)
    pedido = bloque.get("especificacion", "auto")
    horizonte = bloque.get("horizonte", 24)
    print(f"  {len(d.columns)} variables · {len(d)} observaciones · "
          f"{d.index.min():%Y-%m} a {d.index.max():%Y-%m}")

    ru = raices_unitarias(d)
    tab_rez, sug = seleccion_rezagos(d, bloque.get("max_rezagos", 12))
    rezagos = bloque.get("rezagos", "auto")
    rezagos = int(sug.get("bic", 1)) if rezagos == "auto" else int(rezagos)
    rezagos = max(1, rezagos)
    eg = engle_granger(d)
    joh, rango = johansen(d, rezagos)
    hol = holgura_muestral(d, rezagos)

    if pedido == "auto":
        no_est = (ru.veredicto == "raiz unitaria").any()
        pedido = "vecm" if (no_est and rango > 0) else (
            "var_diferencias" if no_est else "var_niveles")
        print(f"  especificación: auto -> {pedido}  (rango de Johansen: {rango})")
    else:
        print(f"  especificación pedida: {pedido}  (rango de Johansen: {rango})")

    avisos = advertir(bloque, ru, rango, hol, sug, pedido)
    modelo, irf, fevd = estimar(d, pedido, rezagos, rango, horizonte)

    carpeta = os.path.join(SALIDA, nombre)
    os.makedirs(carpeta, exist_ok=True)
    ru.to_csv(os.path.join(carpeta, "01_raices_unitarias.csv"), index=False)
    tab_rez.to_csv(os.path.join(carpeta, "02_seleccion_rezagos.csv"), index=False)
    eg.to_csv(os.path.join(carpeta, "03_engle_granger.csv"), index=False)
    joh.to_csv(os.path.join(carpeta, "04_johansen.csv"), index=False)
    pd.DataFrame([hol]).to_csv(os.path.join(carpeta, "05_holgura_muestral.csv"), index=False)
    if isinstance(fevd, str) or fevd is None:
        pass
    else:
        filas = []
        for i, v in enumerate(d.columns):
            for h in (1, 6, 12, horizonte):
                if h <= horizonte:
                    for j, causa in enumerate(d.columns):
                        filas.append({"variable": v, "horizonte": h, "fuente": causa,
                                      "pc_varianza": round(float(fevd.decomp[i, h - 1, j]) * 100, 2)})
        pd.DataFrame(filas).to_csv(os.path.join(carpeta, "06_descomposicion_varianza.csv"),
                                   index=False)

    reporte(carpeta, nombre, bloque, d, ru, sug, rezagos, eg, joh, rango, hol,
            pedido, avisos, modelo, fevd)

    print(f"  {'ADVERTENCIAS: ' + str(len(avisos)) if avisos else 'sin advertencias'}")
    for a in avisos:
        print(f"    ! {a}")
    if isinstance(fevd, str):
        print(f"    ! la estimación falló: {fevd}")
    print(f"  salidas -> {os.path.relpath(carpeta, AQUI)}")
    return avisos


def reporte(carpeta, nombre, bloque, d, ru, sug, rezagos, eg, joh, rango, hol,
            pedido, avisos, modelo, fevd):
    L = [f"# Bloque `{nombre}`", ""]
    if avisos:
        L += ["## ⚠ Advertencias", "",
              "Lo que no encaja entre lo que pediste y lo que dicen los datos.", ""]
        L += [f"{i}. {a}" for i, a in enumerate(avisos, 1)] + [""]
    else:
        L += ["## Sin advertencias", "",
              "La especificación es consistente con las pruebas.", ""]

    L += ["## Qué se corrió", "",
          f"- Variables: {', '.join(d.columns)}",
          f"- Observaciones: {len(d)} ({d.index.min():%Y-%m} a {d.index.max():%Y-%m})",
          f"- Especificación: `{pedido}` con {rezagos} rezago(s)",
          f"- Rango de cointegración de Johansen al 5 %: {rango}",
          f"- Holgura: {hol['obs_por_parametro']} observaciones por parámetro "
          f"(la regla de oro pide 10)", "",
          "## Raíces unitarias", "", ru.to_markdown(index=False), "",
          f"Criterios de rezagos: `{sug}`", "",
          "## Cointegración por pares (Engle-Granger)", "", eg.to_markdown(index=False), "",
          "## Johansen (traza)", "", joh.to_markdown(index=False), ""]

    if modelo is not None and fevd is not None and not isinstance(fevd, str):
        L += ["## Descomposición de varianza al horizonte final", ""]
        h = bloque.get("horizonte", 24) - 1
        t = pd.DataFrame(fevd.decomp[:, h, :] * 100,
                         index=d.columns, columns=d.columns).round(2)
        L += [t.to_markdown(), "",
              "*Filas: la variable explicada. Columnas: de dónde viene la varianza.*", ""]
    L += ["---", "", "El orden de las variables define el orden de Cholesky y por lo",
          "tanto las impulso-respuesta. Está tomado tal cual de `bloques.yml`:",
          "el motor no lo decide.", ""]
    with open(os.path.join(carpeta, "reporte.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    cfg = yaml.safe_load(open(os.path.join(AQUI, "bloques.yml"), encoding="utf-8"))
    panel = cargar(cfg)
    print(f"Panel cargado: {panel.shape[1]} series, {panel.shape[0]} fechas")
    pedidos = [a for a in sys.argv[1:] if not a.startswith("-")]
    total = 0
    for b in cfg["bloques"]:
        if pedidos and b["nombre"] not in pedidos:
            continue
        total += len(correr(b["nombre"], b, panel, cfg))
    print(f"\n{'=' * 70}\n{total} advertencia(s) en total. "
          f"Leelas antes de citar cualquier número.")
