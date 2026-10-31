# Monitor de Bonos Soberanos

App de Streamlit con tres solapas:

1. **Resumen**: cotización, valor técnico, paridad, pago final, TNA, TEM, TIR y duration de:
   - Bonos en pesos: S26F7, T31Y7, T30J7 (tasa fija), TZXY7, TZX27 (CER), TMF27, TML27 (TAMAR) y TZV27 (dólar linked).
   - Bonos en dólares: Bonares (AL29, AL30, AL35, AE38, AL41), Globales (GD29 a GD46), BOPREAL (S1 A-D y S4) y San Juan 2035 (SJO35).
2. **Escenarios**: senda mensual editable de inflación (mueve el CER), TAMAR, A3500 y dólar MEP. Muestra el retorno de cada bono en pesos y en dólares.
3. **Expectativas de mercado**: inflación, TAMAR y dólar breakeven, tipo de cambio real esperado y tasa real breakeven. Cada par se arma con bonos que vencen el mismo día o, si no hay, con el vencimiento más cercano. Debajo de cada valor se indica a qué período corresponde, contando los rezagos.

## Fuentes

| Dato | Fuente | Respaldo si falla |
|---|---|---|
| Precios | [data912](https://data912.com) `/live/*` (último operado o, con el mercado cerrado, cierre) | Último cierre histórico de data912; si tampoco hay, un precio fijo de ejemplo (cierre del 30/10/2026). La columna **Fuente** indica cuál se usó. |
| Dólar MEP | data912 `/live/mep` (AL30) | AL30 / AL30D |
| CER, A3500, TAMAR, inflación, REM | API de Estadísticas Monetarias del BCRA (v4.0, con v3.0 como alternativa) | Valores implícitos en los precios de 1816 del 30/10/2026 |

El botón **🔄 Actualizar** limpia el cache y vuelve a consultar las APIs. Los precios se cachean 5 minutos y el BCRA 6 horas.

## Convenciones (las mismas que 1816)

- TIR = TEA con descuento `(1+TIR)^(días/365)`. TEM = `(1+TIR)^(1/12) − 1`.
- TNA: tasa fija por plazo remanente; CER, dólar linked y USD en 180/360; TAMAR en 32/365.
- CER: el bono cobra el CER de 10 hábiles antes del vencimiento. Para la TIR real se supone CER constante desde t−10 de la liquidación.
- TAMAR: capitaliza la TAMAR de bancos privados de 10 hábiles antes, más el margen, convertida con la convención 32/365.
- Dólar linked: paga 100 × A3500 de 3 hábiles antes del vencimiento.
- Liquidación T+1 por defecto, con calendario de feriados en `calendario.py`.

## Calibración

`tests/test_calibracion.py` compara el motor contra los indicadores de 1816 del 30/10/2026:

- Bonos en pesos: TIR y TNA idénticas. En los TAMAR la diferencia es menor a 0,5 pb.
- Bonos en dólares: paridad idéntica; TIR con diferencias de 0 a 0,4 pb.

Supuestos a revisar:

- **TMF27**: el margen (4,57%) se calibró para replicar la TIR de 1816. El de TML27 (3,63%) sale del resultado de la licitación.
- **SJO35**: las condiciones (cupón 9,55% semestral, 3 amortizaciones anuales 2033-2035) se infirieron del cupón corrido, el current yield y la duration de 1816. Coinciden en TIR y paridad.
- **BPO28**: sin precio de referencia; el precio de ejemplo es ilustrativo.

## Correr localmente

```bash
pip install -r requirements.txt
streamlit run app.py
python -m pytest -q
```
