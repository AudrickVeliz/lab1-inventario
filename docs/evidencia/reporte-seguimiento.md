# Seguimiento del desempeño

Publicaciones originales frente a las ventas conocidas al 2017-08-16, con sus correcciones.

Cortes con semana completa: 19 de 20.

| Métrica | Publicado | Baseline con los mismos datos |
|---|---|---|
| Costo de negocio | 17,046,687 (-2.0%) | 17,401,970 |
| WAPE del cuantil 0.50 | 0.140 | 0.150 |
| Cobertura del intervalo 0.10 a 0.90 | 76.9% | 70.9% |

## Por corte

| Corte | Modelo | Días con venta | Completo | WAPE | Cobertura | Venta | Pedido | Costo | Costo del baseline |
|---|---|---|---|---|---|---|---|---|---|
| 2017-04-03 | gradient_boosting | 7 | sí | 0.127 | 79.5% | 5,960,493 | 6,488,702 | 707,162 | 671,559 |
| 2017-04-10 | gradient_boosting | 7 | sí | 0.168 | 73.4% | 5,671,034 | 6,367,924 | 925,731 | 970,689 |
| 2017-04-17 | gradient_boosting | 7 | sí | 0.146 | 78.3% | 5,794,625 | 6,173,784 | 1,130,567 | 1,069,350 |
| 2017-04-24 | gradient_boosting | 7 | sí | 0.168 | 72.9% | 6,338,930 | 6,544,380 | 1,061,454 | 1,221,802 |
| 2017-05-01 | gradient_boosting | 7 | sí | 0.147 | 75.6% | 6,372,130 | 6,625,117 | 1,048,698 | 1,146,304 |
| 2017-05-08 | gradient_boosting | 7 | sí | 0.162 | 73.6% | 5,719,588 | 6,391,239 | 966,391 | 1,138,300 |
| 2017-05-15 | gradient_boosting | 7 | sí | 0.136 | 77.2% | 6,068,016 | 6,325,797 | 954,426 | 1,004,843 |
| 2017-05-22 | gradient_boosting | 7 | sí | 0.130 | 77.8% | 5,844,879 | 6,313,452 | 781,375 | 857,112 |
| 2017-05-29 | gradient_boosting | 7 | sí | 0.134 | 73.8% | 6,751,157 | 7,115,663 | 790,684 | 1,170,575 |
| 2017-06-05 | gradient_boosting | 7 | sí | 0.129 | 78.8% | 6,086,499 | 6,378,470 | 770,853 | 649,131 |
| 2017-06-12 | gradient_boosting | 7 | sí | 0.135 | 76.8% | 5,862,848 | 6,380,089 | 769,708 | 746,618 |
| 2017-06-19 | gradient_boosting | 7 | sí | 0.117 | 80.8% | 5,735,651 | 6,294,845 | 726,262 | 722,606 |
| 2017-06-26 | gradient_boosting | 7 | sí | 0.122 | 79.6% | 6,276,253 | 6,704,851 | 702,373 | 673,627 |
| 2017-07-03 | gradient_boosting | 7 | sí | 0.112 | 79.6% | 6,086,065 | 6,505,976 | 832,212 | 633,610 |
| 2017-07-10 | gradient_boosting | 7 | sí | 0.127 | 79.1% | 5,762,682 | 6,270,524 | 858,649 | 802,234 |
| 2017-07-17 | gradient_boosting | 7 | sí | 0.128 | 79.5% | 5,742,574 | 6,211,330 | 764,632 | 789,383 |
| 2017-07-24 | gradient_boosting | 7 | sí | 0.131 | 81.0% | 5,991,104 | 6,307,934 | 873,020 | 926,058 |
| 2017-07-31 | gradient_boosting | 7 | sí | 0.152 | 71.6% | 6,321,802 | 7,130,329 | 992,852 | 981,722 |
| 2017-08-07 | gradient_boosting | 7 | sí | 0.204 | 72.2% | 5,348,859 | 6,305,583 | 1,389,637 | 1,226,448 |
| 2017-08-14 | gradient_boosting | 1 | no | 0.144 | 81.9% | 762,662 | 6,207,801 | 5,255,170 | 5,500,782 |

## Decisiones de modelo

| Fecha | Reemplaza | Vigente | Motivo |
|---|---|---|---|
| 2017-04-03 | sí | 2017-04-03 | el costo mejora 4.0% y la cobertura no empeora |
| 2017-05-01 | no | 2017-04-03 | el costo mejora -22.6%, menos que el mínimo de 2.0% |
| 2017-05-29 | no | 2017-04-03 | el costo mejora -5.4%, menos que el mínimo de 2.0% |
| 2017-06-26 | no | 2017-04-03 | el costo mejora -1.0%, menos que el mínimo de 2.0% |
| 2017-07-24 | sí | 2017-07-24 | el costo mejora 12.5% y la cobertura no empeora |
