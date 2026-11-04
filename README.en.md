# Diagnostic engine for time-series blocks

*[Versión en español](README.md)*

**You declare the block in a configuration file and the engine tells you what
you are betting on before you quote a number.**

It is not a button that estimates models. It is the opposite: it runs the full
diagnostic battery, compares what you asked for against what the tests say and
**warns you when they don't match**.

```bash
git clone https://github.com/pabloguerrerocr/motor-series.git
cd motor-series
pip install -r requirements.txt
python motor.py                 # all blocks
python motor.py financiero      # just one
```

It runs on its own: if it cannot find the warehouse, it uses
[`datos/mensual.csv`](datos/mensual.csv), a copy of the same panel (8 monthly series
from FRED and the Central Bank of Costa Rica, 2015-2025, exported from
`main_marts.mensual` in
[warehouse-sector-externo](https://github.com/pabloguerrerocr/warehouse-sector-externo)).
Both sources give exactly the same results.

## What it finds in my thesis data

Six warnings across the three blocks in [`bloques.yml`](bloques.yml):

| Block | What it asks for | What the engine says |
|---|---|---|
| `comercial` | `auto` | VAR in differences (Johansen finds no cointegration). ADF and KPSS contradict each other on the real exchange rate, and the lag criteria range from 2 (BIC) to 12 (AIC) |
| `financiero` | `auto` | VECM (Johansen rank 1). The lag criteria do not agree |
| `financiero_mal_pedido` | VECM with 8 lags | Three warnings: a VECM without cointegration, 4 observations per parameter and disagreeing criteria |

## How a block is declared

```yaml
fuente:
  tipo: duckdb
  ruta: ../warehouse-sector-externo/sector_externo.duckdb
  consulta: SELECT serie_id, fecha, valor FROM main_marts.mensual

bloques:
  - nombre: financiero
    series: [ipi_eeuu, ffr, reservas, tpm]   # the order IS the Cholesky order
    transformacion: log
    sin_log: [ffr, tpm]                      # interest rates stay in levels
    especificacion: auto                     # auto | var_niveles | var_diferencias | vecm
    rezagos: 3                               # auto uses the BIC
    horizonte: 24
```

Changing the analysis means changing those six lines. The source can be DuckDB or
a CSV with `serie_id, fecha, valor`.

## What it runs

| | |
|---|---|
| Unit roots | ADF and KPSS on every series, **flagging where they contradict each other** |
| Lag selection | AIC, BIC, HQIC and FPE, with a warning if they disagree |
| Cointegration | Pairwise Engle-Granger and Johansen trace test |
| Sample slack | Observations per parameter against the 10-to-1 rule |
| Estimation | VAR in levels, VAR in differences or VECM, **whatever you asked for** |
| Outputs | Five CSVs, the variance decomposition and a `reporte.md` |

## What makes it different: it warns instead of obeying

A real example, included in `bloques.yml` on purpose. A VECM with 8 lags is
requested on a block where it doesn't belong:

```
BLOQUE  financiero_mal_pedido
  4 variables · 132 observaciones · 2015-01 a 2025-12
  especificación pedida: vecm  (rango de Johansen: 0)
  ADVERTENCIAS: 3
    ! Pediste VECM y Johansen no encuentra ninguna relación de cointegración
      al 5 %. Un VECM sin cointegración estima un término de corrección que no
      existe. Lo natural acá es VAR en diferencias.
    ! 132 observaciones para 33 parámetros por ecuación: 4.0 por parámetro.
      Debajo de 10 el modelo sobreajusta. Bajá rezagos o sacá una variable.
    ! Los criterios de rezagos no coinciden: {'aic': 3, 'bic': 2, 'hqic': 2, 'fpe': 3}.
      BIC es el parsimonioso y AIC el generoso; con muestras cortas suele
      convenir BIC.
```

In English, the three warnings say: (1) you asked for a VECM but Johansen finds no
cointegrating relationship at 5%, so the model estimates an error-correction term
that does not exist and a VAR in differences is the natural choice; (2) 132
observations for 33 parameters per equation is 4.0 per parameter, and below 10 the
model overfits; (3) the lag criteria disagree.

**The model is estimated anyway.** The engine neither blocks nor corrects on its
own: it puts the warning at the top of the report, where it cannot go unread. The
worst possible outcome in this field is a plausible VECM on variables that do not
cointegrate, and nobody notices until someone asks.

## What the engine does NOT do, and why

- **It does not choose the specification.** VAR or VECM, in levels or in
  differences: that depends on theory and on the question being asked, not only
  on the p-value. The `auto` mode proposes the option consistent with the tests
  and says so; it never imposes it.
- **It does not choose the Cholesky ordering.** Changing the order of the
  variables changes the impulse responses. The criterion is which variable is more
  exogenous, and that is economics. It comes straight from the order in which the
  series are written.
- **It does not resolve contradictions.** When ADF says stationary and KPSS says
  unit root, the engine flags `AMBIGUO` (ambiguous) and moves on. Choosing is the
  analyst's job, and the reason should be written down.
- **It does not interpret.** It produces the tables. The sentence —«the financial
  channel dominates the real one in all 14 combinations»— is written by a person.
- **It does not read new sources on its own.** Each provider publishes
  differently: the Central Bank of Costa Rica reports year-to-date totals, FRED
  mixes frequencies. That is solved upstream, in the warehouse.

## Outputs

```
salidas/<bloque>/
  01_raices_unitarias.csv
  02_seleccion_rezagos.csv
  03_engle_granger.csv
  04_johansen.csv
  05_holgura_muestral.csv
  06_descomposicion_varianza.csv
  reporte.md          <- warnings come first
```

## With the full warehouse

To read straight from DuckDB, the three repositories sit side by side and the
warehouse is built first:

```bash
git clone https://github.com/pabloguerrerocr/sector-externo-cr-eeuu.git
git clone https://github.com/pabloguerrerocr/warehouse-sector-externo.git
git clone https://github.com/pabloguerrerocr/motor-series.git
cd warehouse-sector-externo
pip install -r requirements.txt dbt-duckdb
python 07_pruebas.py
cd dbt && DBT_PROFILES_DIR=. dbt build && cd ../..
cd motor-series && python motor.py
```

Tested with Python 3.13, statsmodels 0.15 and DuckDB 2.0.
