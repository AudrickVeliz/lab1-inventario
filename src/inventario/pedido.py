"""Pedido sugerido: cuantil crítico del total del periodo que cubre el pedido.

Se usa el cuantil del total, no la suma de cuantiles diarios, porque esa suma supone
que todos los días son altos o bajos a la vez y exagera la incertidumbre.

No hay datos de inventario: el pedido supone que el periodo empieza sin existencias
(DECISIONES.md, D-014).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import numpy as np
import pandas as pd

from inventario.negocio import ConfigNegocio
from inventario.pronostico import malla_de_series, ventana_reciente, ventas_conocidas

_SERIE = ["store_nbr", "family"]


def unidades_a_pedir(totales: pd.Series) -> pd.Series:
    """Redondea al entero superior; antes elimina el ruido de punto flotante, que agregaría una unidad."""
    return np.ceil(totales.round(6)).astype("int64")


def _totales_por_bloque(conocidas: pd.DataFrame, config: ConfigNegocio) -> pd.DataFrame:
    """Totales de bloques consecutivos, contados hacia atrás desde la última fecha conocida."""
    horizonte = config.horizonte_dias
    recientes = ventana_reciente(conocidas, config.semanas_baseline, horizonte)
    if recientes.empty:
        return pd.DataFrame(columns=[*_SERIE, "total"])
    distancia = (recientes["date"].max() - recientes["date"]).dt.days
    con_bloque = recientes.assign(bloque=distancia // horizonte)
    usadas = con_bloque[con_bloque["bloque"] < config.semanas_baseline]
    totales = usadas.groupby([*_SERIE, "bloque"])["sales"].agg(total="sum", dias="size").reset_index()
    return totales[totales["dias"] >= config.dias_minimos_por_bloque]


def calcular_pedido(
    ventas: pd.DataFrame,
    tiendas: Iterable[int],
    fecha_corte: date,
    config: ConfigNegocio,
) -> pd.DataFrame:
    """Unidades sugeridas por tienda y familia para los días posteriores al corte."""
    conocidas = ventas_conocidas(ventas, fecha_corte)
    tabla = malla_de_series(conocidas, tiendas)
    grupos = tabla["family"].map(config.grupo_de)
    tabla["grupo"] = grupos.map(lambda g: g.nombre)
    tabla["cuantil_critico"] = grupos.map(lambda g: g.cuantil_critico)

    totales = _totales_por_bloque(conocidas, config)
    if totales.empty:
        tabla["total_estimado"] = 0.0
        tabla["bloques_observados"] = 0
    else:
        totales = totales.merge(tabla[[*_SERIE, "cuantil_critico"]], on=_SERIE)
        estimados = [
            filas.groupby(_SERIE)["total"].quantile(cuantil).rename("total_estimado")
            for cuantil, filas in totales.groupby("cuantil_critico")
        ]
        conteo = totales.groupby(_SERIE).size().rename("bloques_observados")
        tabla = tabla.merge(pd.concat(estimados), on=_SERIE, how="left").merge(conteo, on=_SERIE, how="left")
        tabla["total_estimado"] = tabla["total_estimado"].fillna(0.0)
        tabla["bloques_observados"] = tabla["bloques_observados"].fillna(0).astype("int64")

    tabla["pedido"] = unidades_a_pedir(tabla["total_estimado"])
    tabla.insert(0, "fecha_corte", pd.Timestamp(fecha_corte))
    tabla.insert(1, "desde", pd.Timestamp(fecha_corte + timedelta(days=1)))
    tabla.insert(2, "hasta", pd.Timestamp(fecha_corte + timedelta(days=config.horizonte_dias)))
    return tabla.drop(columns="total_estimado").sort_values(_SERIE, ignore_index=True)


def costo_del_pedido(pedido: pd.DataFrame, ventas: pd.DataFrame, config: ConfigNegocio) -> pd.DataFrame:
    """Compara el pedido con la venta real. Solo evalúa días con venta conocida."""
    resultado = pedido.copy()
    resultado["venta_real"] = 0.0
    resultado["dias_observados"] = 0
    for (desde, hasta), filas in pedido.groupby(["desde", "hasta"]):
        periodo = ventas[ventas["date"].between(desde, hasta)]
        real = periodo.groupby(_SERIE)["sales"].agg(venta_real="sum", dias_observados="size")
        unidos = filas[_SERIE].merge(real, on=_SERIE, how="left").set_index(filas.index)
        resultado.loc[filas.index, "venta_real"] = unidos["venta_real"].fillna(0.0)
        resultado.loc[filas.index, "dias_observados"] = unidos["dias_observados"].fillna(0).astype("int64")

    costos = resultado["family"].map(config.grupo_de)
    resultado["faltante"] = (resultado["venta_real"] - resultado["pedido"]).clip(lower=0)
    resultado["sobrante"] = (resultado["pedido"] - resultado["venta_real"]).clip(lower=0)
    resultado["costo"] = (
        costos.map(lambda g: g.quiebre) * resultado["faltante"]
        + costos.map(lambda g: g.merma) * resultado["sobrante"]
    )
    return resultado
