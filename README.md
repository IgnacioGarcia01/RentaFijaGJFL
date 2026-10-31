# Renta Fija Soberana

App de Streamlit que replica la planilla `Soberanos_por_asset_class.xlsx`, con precios en vivo.

## Solapas

1. **Resumen** (hoja *Resumen*): los mismos parámetros que la planilla.
   - **Shock de precio** aplicado a todos los bonos.
   - **Proyección TAMAR**: Manual, Promedio 5 días o Último dato.
   - **Inflación mensual proyectada** (tabla editable por mes de IPC).
   - Tablas de bonos con precio base y precio usado, TIR (TEA), TNA, TEM, MD, liquidación y moneda.
   - **Sensibilidad** de la TIR al shock de precio.
   - Bonos en pesos: S26F7, T31Y7, T30J7 (tasa fija), TZXY7, TZX27 (CER), TMF27, TML27 (TAMAR) y TZV27 (dólar linked).
   - Bonos en dólares: AL29, AL30, AL35, AE38, AL41, AO28, GD29 a GD46, BOPREAL (S1 A-D y S4) y San Juan 2035.
2. **Escenarios**: senda mensual editable de inflación (CER), TAMAR, A3500 y dólar MEP. Arranca con la proyección del Resumen. Muestra el retorno a vencimiento en pesos y en dólares, y el de los bonos en USD a un horizonte.
3. **Expectativas de mercado** (hoja *Breakeven*, extendida):
   - Inflación breakeven, con la grilla de sensibilidad a precios del Excel.
   - TAMAR breakeven y dólar breakeven.
   - Tipo de cambio real esperado.
   - Tasa real breakeven.
   - Cada par se arma con bonos del mismo vencimiento o, si no hay, con el más cercano.
   - Debajo de cada valor se indica a qué fechas o meses de IPC corresponde, contando los rezagos.

## Fórmulas (iguales a la planilla)

| Instrumento | Pago / flujo | TIR |
|---|---|---|
| LECAP / BONCAP | 100 × (1+TEM)^(DAYS360(emisión, vto)/30) | XIRR |
| CER | 100 × CER(vto − 10 hábiles) / CER(emisión − 10 hábiles). El CER se proyecta con la inflación mensual: del 16/m al 15/m+1 crece al IPC de m−1 | XIRR (nominal). También se muestra la TIR real (CER t−10 constante) |
| TAMAR | 100 × (1+TEM)^(DAYS360/30), con TEM = ((1 + (prom. TAMAR[emisión−10, vto−10] + margen)·32/365)^(365/32))^(1/12) − 1 | XIRR |
| Dólar linked | 100 × A3500(vto − 3 hábiles) | XIRR. También se muestra la TIR en USD |
| Bonos en USD | Intereses = VR × cupón × YEARFRAC(base 0). Paridad = precio / (VR + int. corridos) | XIRR. MD = Macaulay |

- **Inflación breakeven** (hoja Breakeven):
  - CER final = CER base × P_CER / 100 × (pago tasa fija / P_tasa fija).
  - Inflación mensual = (1 + acumulada)^((365/12)/días) − 1.
  - Los días se cuentan entre CER(liq − 10) y CER(vto − 10).

## Verificación

`python -m pytest -q` (35 tests):

- `tests/test_excel.py`: reproduce los valores de la planilla al 30/10/2026:
  - T31Y7, TZXY7, AL35D, AO28D, BPC7D y SJO35, más el breakeven T31Y7/TZXY7, todos exactos.
  - TML27: diferencia menor a 5 pb (ver nota).
- `tests/test_calibracion.py`: compara contra 1816 (BYMA 30/10/2026):
  - Tasa fija y dólar linked: exactos. TAMAR: menos de 1 pb.
  - CER: alrededor de 2 pb con datos de respaldo (falta el IPC de septiembre); exactos con el BCRA en vivo.
  - Bonos en USD: hasta 3 pb, porque 1816 descuenta en la fecha de pago hábil y la planilla en la teórica.

**Nota sobre feriados:** la hoja *Feriados* de la planilla no tiene 2027, y la fórmula de TAMAR usa solo sus primeras 17 filas. La app usa el calendario real (`calendario.py`). Con las listas de la planilla, los tests reproducen el Excel al centavo.

**Márgenes TAMAR:**
- TML27: 5,4%, como en la planilla.
- TMF27: 6,5%, calibrado para replicar el pago proyectado de 1816 con la misma fórmula. Con ese margen, la TAMAR que 1816 proyecta es 24,22%, consistente con su dato de referencia.

## Fuentes de datos

| Dato | Fuente | Respaldo |
|---|---|---|
| Precios | data912 `/live/*` (último operado; con el mercado cerrado, cierre) | Último cierre histórico de data912 y, si no hay, un precio fijo de ejemplo. La columna **Fuente** indica cuál se usó |
| Dólar MEP | data912 `/live/mep` (AL30) | AL30 / AL30D |
| CER, A3500, TAMAR, IPC, REM | API de Estadísticas Monetarias del BCRA (v4.0 / v3.0) | Series de la planilla (`data/respaldo.json`) |

El botón **🔄 Actualizar** limpia el cache y vuelve a consultar.

## Correr localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```
