"""Baseline: cuantiles del mismo día de la semana en las últimas semanas conocidas."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import pandas as pd

from inventario.negocio import ConfigNegocio

_SERIE = ["store_nbr", "family"]
# Semanas adicionales para seguir teniendo observaciones si faltan días.
SEMANAS_DE_MARGEN = 2


def nombre_cuantil(cuantil: float) -> str:
    return f"q{round(cuantil * 100):02d}"


def ventas_conocidas(ventas: pd.DataFrame, fecha_corte: date) -> pd.DataFrame:
    """Barrera adicional: una venta del día del corte o posterior todavía no puede conocerse."""
    return ventas[ventas["date"] < pd.Timestamp(fecha_corte)]


def ventana_reciente(conocidas: pd.DataFrame, semanas: int, dias_por_semana: int = 7) -> pd.DataFrame:
    """Parte de la última fecha conocida: si la fuente se atrasa, usa lo más reciente que tenga."""
    if conocidas.empty:
        return conocidas
    dias = dias_por_semana * (semanas + SEMANAS_DE_MARGEN)
    inicio = conocidas["date"].max() - pd.Timedelta(days=dias)
    return conocidas[conocidas["date"] > inicio]


def malla_de_series(conocidas: pd.DataFrame, tiendas: Iterable[int]) -> pd.DataFrame:
    """Todas las tiendas del catálogo por todas las familias conocidas."""
    indice = pd.MultiIndex.from_product(
        [sorted(set(tiendas)), sorted(conocidas["family"].unique())], names=_SERIE
    )
    return indice.to_frame(index=False)


def pronosticar_baseline(
    ventas: pd.DataFrame,
    tiendas: Iterable[int],
    fecha_corte: date,
    config: ConfigNegocio,
) -> pd.DataFrame:
    """Pronóstico diario por tienda y familia para los días posteriores al corte.

    Una serie sin observaciones se pronostica en 0 y se identifica con ``observaciones`` = 0,
    para que el pronóstico no se interprete como una venta esperada nula.
    """
    conocidas = ventas_conocidas(ventas, fecha_corte)
    columnas = [nombre_cuantil(q) for q in config.cuantiles]
    fechas = [fecha_corte + timedelta(days=h) for h in range(1, config.horizonte_dias + 1)]
    objetivo = pd.DataFrame({"date": pd.to_datetime(fechas)})
    objetivo["dia"] = objetivo["date"].dt.dayofweek
    tabla = malla_de_series(conocidas, tiendas).merge(objetivo, how="cross")

    recientes = ventana_reciente(conocidas, config.semanas_baseline)
    if recientes.empty:
        tabla[columnas] = 0.0
        tabla["observaciones"] = 0
    else:
        recientes = recientes.assign(dia=recientes["date"].dt.dayofweek).sort_values("date")
        claves = [*_SERIE, "dia"]
        observadas = recientes.groupby(claves).tail(config.semanas_baseline).groupby(claves)["sales"]
        cuantiles = observadas.quantile(list(config.cuantiles)).unstack()
        cuantiles.columns = columnas
        cuantiles["observaciones"] = observadas.size()
        tabla = tabla.merge(cuantiles.reset_index(), on=claves, how="left")
        tabla[columnas] = tabla[columnas].fillna(0.0)
        tabla["observaciones"] = tabla["observaciones"].fillna(0).astype("int64")

    tabla.insert(0, "fecha_corte", pd.Timestamp(fecha_corte))
    orden = ["fecha_corte", "date", *_SERIE, *columnas, "observaciones"]
    return tabla[orden].sort_values(["date", *_SERIE], ignore_index=True)
