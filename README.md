# Motor de diagnóstico para bloques de series de tiempo

**Declarás el bloque en un archivo de configuración y el motor te dice qué te
estás jugando antes de que cites un número.**

No es un botón que estima modelos. Es lo contrario: corre la batería completa de
diagnóstico, compara lo que pediste contra lo que dicen las pruebas y **te
advierte cuando no encajan**.

```bash
python motor.py                 # todos los bloques
python motor.py financiero      # solo uno
```

## Cómo se declara un bloque

```yaml
fuente:
  tipo: duckdb
  ruta: ../warehouse-sector-externo/sector_externo.duckdb
  consulta: SELECT serie_id, fecha, valor FROM main_marts.mensual

bloques:
  - nombre: financiero
    series: [ipi_eeuu, ffr, reservas, tpm]   # el orden ES el orden de Cholesky
    transformacion: log
    sin_log: [ffr, tpm]                      # las tasas van en nivel
    especificacion: auto                     # auto | var_niveles | var_diferencias | vecm
    rezagos: 3                               # auto usa el BIC
    horizonte: 24
```

Cambiar de análisis es cambiar esas seis líneas. La fuente puede ser DuckDB o un
CSV con `serie_id, fecha, valor`.

## Lo que corre

| | |
|---|---|
| Raíces unitarias | ADF y KPSS sobre cada serie, **y la marca cuando se contradicen** |
| Selección de rezagos | AIC, BIC, HQIC y FPE, con aviso si no coinciden |
| Cointegración | Engle-Granger por pares y Johansen por traza |
| Holgura muestral | Observaciones por parámetro contra la regla de 10 a 1 |
| Estimación | VAR en niveles, VAR en diferencias o VECM, **lo que pediste** |
| Salidas | Cinco CSV, la descomposición de varianza y un `reporte.md` |

## Lo que hace distinto: advierte en vez de obedecer

Un ejemplo real, incluido en `bloques.yml` a propósito. Se pide un VECM con 8
rezagos sobre un bloque donde eso no corresponde:

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
    ! Los criterios de rezagos no coinciden: {'aic': 3, 'bic': 2, 'hqic': 2}.
```

**El modelo se estima igual.** El motor no bloquea ni corrige por su cuenta: deja
la advertencia arriba del reporte, donde no se puede no leer. El peor resultado
posible en este terreno es un VECM plausible sobre variables que no cointegran, y
eso nadie lo nota hasta que alguien pregunta.

## Lo que el motor NO hace, y por qué

- **No elige la especificación.** VAR o VECM, en niveles o en diferencias: eso
  depende de la teoría y de qué pregunta se está haciendo, no solo del p-valor.
  El modo `auto` propone la opción consistente con las pruebas y lo dice; nunca
  la impone.
- **No elige el orden de Cholesky.** Cambiar el orden de las variables cambia las
  impulso-respuesta. El criterio es cuál variable es más exógena, y eso es
  economía. Sale tal cual del orden en que se escriben las series.
- **No resuelve las contradicciones.** Cuando ADF dice estacionaria y KPSS dice
  raíz unitaria, el motor marca `AMBIGUO` y sigue. Elegir es del analista, y
  conviene que quede escrito por qué.
- **No interpreta.** Produce las tablas. La frase —«el canal financiero domina al
  real en las 14 combinaciones»— la escribe una persona.
- **No lee fuentes nuevas solas.** Cada proveedor publica distinto: el BCCR
  acumula dentro del año, FRED mezcla frecuencias. Eso se resuelve aguas arriba,
  en el warehouse.

## Salidas

```
salidas/<bloque>/
  01_raices_unitarias.csv
  02_seleccion_rezagos.csv
  03_engle_granger.csv
  04_johansen.csv
  05_holgura_muestral.csv
  06_descomposicion_varianza.csv
  reporte.md          <- las advertencias van primero
```

## Requisitos

```bash
pip install pandas numpy statsmodels duckdb pyyaml tabulate
```
