"""Modelo de ML: predicción central con gradient boosting e incertidumbre por errores reales.

Cada fila de entrenamiento simula un corte pasado: sus variables solo usan lo conocido
en ese corte y su objetivo es la venta de un día posterior. Así, el modelo se entrena en
las mismas condiciones en las que se usa.

La incertidumbre sale de los errores fuera de muestra de cada serie. Por eso, cambiar
los costos cambia el pedido sin reentrenar (DECISIONES.md, D-020).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from inventario.negocio import ConfigNegocio
from inventario.pedido import calcular_pedido, costo_del_pedido, unidades_a_pedir
from inventario.pronostico import malla_de_series, nombre_cuantil, pronosticar_baseline, ventas_conocidas

_SERIE = ["store_nbr", "family"]
_NUMERICAS = [
    "horizonte", "dia_semana", "dia_mes", "mes", "es_quincena",
    "media_7", "media_28", "media_84", "ceros_28", "promo_28",
    "mediana_dia", "media_dia_4",
    "feriado", "evento", "petroleo", "cambio_petroleo", "cluster",
]
_CATEGORICAS = ["store_nbr", "family", "tipo_tienda"]
_DIAS_DE_HISTORIA = 84
_TIPOS_FERIADO = {"Holiday", "Additional", "Bridge", "Transfer"}


@dataclass(frozen=True)
class Entradas:
    """Lo que necesita el modelo. ``petroleo`` incluye la fecha en que llegó cada precio."""

    ventas: pd.DataFrame
    tiendas: pd.DataFrame
    feriados: pd.DataFrame
    petroleo: pd.DataFrame


def _feriados_por_tienda(entradas: Entradas) -> pd.DataFrame:
    """Un feriado aplica a todas las tiendas, a su provincia o a su ciudad, según su alcance."""
    feriados = entradas.feriados
    tiendas = entradas.tiendas[["store_nbr", "city", "state"]]
    if feriados.empty:
        return pd.DataFrame({
            "date": pd.Series(dtype="datetime64[ns]"), "store_nbr": pd.Series(dtype="int64"),
            "feriado": pd.Series(dtype="int8"), "evento": pd.Series(dtype="int8"),
        })
    activos = feriados[~feriados["transferred"].astype(bool)].assign(
        feriado=lambda t: t["type"].isin(_TIPOS_FERIADO).astype("int8"),
        evento=lambda t: (t["type"] == "Event").astype("int8"),
    )
    cruce = activos.merge(tiendas, how="cross")
    aplica = (
        (cruce["locale"] == "National")
        | ((cruce["locale"] == "Regional") & (cruce["locale_name"] == cruce["state"]))
        | ((cruce["locale"] == "Local") & (cruce["locale_name"] == cruce["city"]))
    )
    return cruce[aplica].groupby(["date", "store_nbr"], as_index=False)[["feriado", "evento"]].max()


def _estadisticas_por_serie(conocidas: pd.DataFrame, origen: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    ventana = conocidas[conocidas["date"] >= origen - pd.Timedelta(days=_DIAS_DE_HISTORIA)]
    dias = (origen - ventana["date"]).dt.days
    por_serie = ventana.groupby(_SERIE)
    estadisticas = pd.DataFrame({
        "media_84": por_serie["sales"].mean(),
        "media_28": ventana[dias <= 28].groupby(_SERIE)["sales"].mean(),
        "media_7": ventana[dias <= 7].groupby(_SERIE)["sales"].mean(),
        "ceros_28": (ventana[dias <= 28]["sales"] == 0).groupby([ventana[dias <= 28][c] for c in _SERIE]).mean(),
        "promo_28": (ventana[dias <= 28]["onpromotion"] > 0).groupby([ventana[dias <= 28][c] for c in _SERIE]).mean(),
    }).reset_index()
    con_dia = ventana.assign(dia_semana=ventana["date"].dt.dayofweek)
    claves = [*_SERIE, "dia_semana"]
    por_dia = pd.DataFrame({
        "mediana_dia": con_dia.groupby(claves)["sales"].median(),
        "media_dia_4": con_dia[dias <= 28].groupby(claves)["sales"].mean(),
    }).reset_index()
    return estadisticas, por_dia


def _petroleo_conocido(entradas: Entradas, origen: pd.Timestamp) -> tuple[float, float]:
    precios = entradas.petroleo
    conocidos = precios[(precios["entrega"] <= origen) & precios["dcoilwtico"].notna()].sort_values("date")
    if conocidos.empty:
        return np.nan, np.nan
    ultimo = conocidos["dcoilwtico"].iloc[-1]
    previos = conocidos[conocidos["date"] <= conocidos["date"].iloc[-1] - pd.Timedelta(days=28)]
    cambio = ultimo / previos["dcoilwtico"].iloc[-1] - 1 if not previos.empty else np.nan
    return ultimo, cambio


def caracteristicas(entradas: Entradas, origen: date, horizonte: int, feriados: pd.DataFrame | None = None) -> pd.DataFrame:
    """Una fila por tienda, familia y día objetivo, con lo conocido en ``origen``."""
    marca = pd.Timestamp(origen)
    conocidas = ventas_conocidas(entradas.ventas, origen)
    objetivo = pd.DataFrame({"horizonte": range(1, horizonte + 1)})
    objetivo["date"] = marca + pd.to_timedelta(objetivo["horizonte"], unit="D")
    objetivo["dia_semana"] = objetivo["date"].dt.dayofweek
    objetivo["dia_mes"] = objetivo["date"].dt.day
    objetivo["mes"] = objetivo["date"].dt.month
    objetivo["es_quincena"] = ((objetivo["dia_mes"] == 15) | objetivo["date"].dt.is_month_end).astype("int8")

    tabla = malla_de_series(conocidas, entradas.tiendas["store_nbr"]).merge(objetivo, how="cross")
    if not conocidas.empty:
        estadisticas, por_dia = _estadisticas_por_serie(conocidas, marca)
        tabla = tabla.merge(estadisticas, on=_SERIE, how="left").merge(por_dia, on=[*_SERIE, "dia_semana"], how="left")
    feriados = _feriados_por_tienda(entradas) if feriados is None else feriados
    tabla = tabla.merge(feriados, on=["date", "store_nbr"], how="left")
    tabla[["feriado", "evento"]] = tabla[["feriado", "evento"]].fillna(0)
    tabla["petroleo"], tabla["cambio_petroleo"] = _petroleo_conocido(entradas, marca)
    catalogo = entradas.tiendas.rename(columns={"type": "tipo_tienda"})[["store_nbr", "cluster", "tipo_tienda"]]
    tabla = tabla.merge(catalogo, on="store_nbr", how="left")
    for columna in _NUMERICAS:
        if columna not in tabla:
            tabla[columna] = np.nan
    return tabla


def _matriz(tabla: pd.DataFrame, categorias: dict[str, list]) -> pd.DataFrame:
    """Una categoría no vista en el entrenamiento, como una familia nueva, queda como desconocida."""
    matriz = tabla[_NUMERICAS].astype("float64").copy()
    for columna in _CATEGORICAS:
        conocidas = tabla[columna].where(tabla[columna].isin(categorias[columna]))
        matriz[columna] = pd.Categorical(conocidas, categories=categorias[columna])
    return matriz


def origenes_previos(fecha: date, cantidad: int, horizonte: int, dia_semana: int) -> list[date]:
    """Cortes pasados con todos sus días objetivo ya conocidos en ``fecha``, del más antiguo al más nuevo."""
    origen = fecha - timedelta(days=horizonte + 1)
    while origen.weekday() != dia_semana:
        origen -= timedelta(days=1)
    return [origen - timedelta(days=7 * i) for i in range(cantidad)][::-1]


def _filas_con_objetivo(entradas: Entradas, origenes: Iterable[date], horizonte: int) -> pd.DataFrame:
    feriados = _feriados_por_tienda(entradas)
    reales = entradas.ventas[["date", *_SERIE, "sales"]]
    partes = []
    for origen in origenes:
        partes.append(
            caracteristicas(entradas, origen, horizonte, feriados)
            .merge(reales, on=["date", *_SERIE], how="inner")
            .assign(origen=pd.Timestamp(origen))
        )
    return pd.concat(partes, ignore_index=True)


def ajustar(entradas: Entradas, origenes: list[date], horizonte: int, parametros: dict | None = None) -> dict:
    """Entrena con los cortes indicados. La semilla fija permite repetir el resultado."""
    filas = _filas_con_objetivo(entradas, origenes, horizonte)
    categorias = {c: sorted(filas[c].dropna().unique().tolist()) for c in _CATEGORICAS}
    configuracion = {
        "max_iter": 400, "learning_rate": 0.05, "max_leaf_nodes": 63, "min_samples_leaf": 40,
        "l2_regularization": 1.0, "early_stopping": False, "random_state": 7, "categorical_features": "from_dtype",
    } | (parametros or {})
    modelo = HistGradientBoostingRegressor(**configuracion)
    modelo.fit(_matriz(filas, categorias), np.log1p(filas["sales"]))
    return {"modelo": modelo, "categorias": categorias, "horizonte": horizonte}


def predecir(artefacto: dict, entradas: Entradas, origen: date) -> pd.DataFrame:
    tabla = caracteristicas(entradas, origen, artefacto["horizonte"])
    estimado = np.expm1(artefacto["modelo"].predict(_matriz(tabla, artefacto["categorias"])))
    return tabla[["date", *_SERIE]].assign(prediccion=np.clip(estimado, 0, None))


def errores_fuera_de_muestra(artefacto: dict, entradas: Entradas, origenes: Iterable[date]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Errores por día y por periodo de cortes que el modelo no vio al entrenarse."""
    reales = entradas.ventas[["date", *_SERIE, "sales"]]
    dias = []
    periodos = []
    for origen in origenes:
        comparacion = predecir(artefacto, entradas, origen).merge(reales, on=["date", *_SERIE], how="inner")
        comparacion["error"] = comparacion["sales"] - comparacion["prediccion"]
        dias.append(comparacion[[*_SERIE, "error"]])
        periodos.append(comparacion.groupby(_SERIE, as_index=False)["error"].sum())
    return pd.concat(dias, ignore_index=True), pd.concat(periodos, ignore_index=True)


def pronosticar_ml(artefacto: dict, entradas: Entradas, fecha_corte: date, config: ConfigNegocio) -> pd.DataFrame:
    """Mismo formato que el baseline: los dos modelos son intercambiables al publicar."""
    tabla = predecir(artefacto, entradas, fecha_corte)
    errores = artefacto["errores_dia"].groupby(_SERIE)["error"]
    cuantiles = errores.quantile(list(config.cuantiles)).unstack()
    columnas = [nombre_cuantil(q) for q in config.cuantiles]
    cuantiles.columns = columnas
    cuantiles["observaciones"] = errores.size()
    tabla = tabla.merge(cuantiles.reset_index(), on=_SERIE, how="left")
    for columna in columnas:
        tabla[columna] = (tabla["prediccion"] + tabla[columna].fillna(0.0)).clip(lower=0)
    tabla["observaciones"] = tabla["observaciones"].fillna(0).astype("int64")
    tabla.insert(0, "fecha_corte", pd.Timestamp(fecha_corte))
    return tabla[["fecha_corte", "date", *_SERIE, *columnas, "observaciones"]].sort_values(["date", *_SERIE], ignore_index=True)


def calcular_pedido_ml(artefacto: dict, entradas: Entradas, fecha_corte: date, config: ConfigNegocio) -> pd.DataFrame:
    """Total predicho más el cuantil crítico de los errores del total de esa serie."""
    totales = predecir(artefacto, entradas, fecha_corte).groupby(_SERIE, as_index=False)["prediccion"].sum()
    grupos = totales["family"].map(config.grupo_de)
    totales["grupo"] = grupos.map(lambda g: g.nombre)
    totales["cuantil_critico"] = grupos.map(lambda g: g.cuantil_critico)

    errores = artefacto["errores_periodo"].merge(totales[[*_SERIE, "cuantil_critico"]], on=_SERIE)
    ajustes = [
        filas.groupby(_SERIE)["error"].quantile(cuantil).rename("ajuste")
        for cuantil, filas in errores.groupby("cuantil_critico")
    ]
    conteo = errores.groupby(_SERIE).size().rename("bloques_observados")
    if ajustes:
        totales = totales.merge(pd.concat(ajustes), on=_SERIE, how="left").merge(conteo, on=_SERIE, how="left")
    else:
        totales = totales.assign(ajuste=np.nan, bloques_observados=np.nan)
    estimado = (totales["prediccion"] + totales["ajuste"].fillna(0.0)).clip(lower=0)
    totales["bloques_observados"] = totales["bloques_observados"].fillna(0).astype("int64")
    totales["pedido"] = unidades_a_pedir(estimado)
    totales.insert(0, "fecha_corte", pd.Timestamp(fecha_corte))
    totales.insert(1, "desde", pd.Timestamp(fecha_corte + timedelta(days=1)))
    totales.insert(2, "hasta", pd.Timestamp(fecha_corte + timedelta(days=config.horizonte_dias)))
    columnas = ["fecha_corte", "desde", "hasta", *_SERIE, "grupo", "cuantil_critico", "bloques_observados", "pedido"]
    return totales[columnas].sort_values(_SERIE, ignore_index=True)


def _metricas(pronosticos: list[pd.DataFrame], pedidos: list[pd.DataFrame], ventas: pd.DataFrame, config: ConfigNegocio) -> dict:
    """Las mismas métricas para todos los modelos comparados."""
    diario = pd.concat(pronosticos, ignore_index=True).merge(
        ventas[["date", *_SERIE, "sales"]], on=["date", *_SERIE], how="inner"
    )
    costo = costo_del_pedido(pd.concat(pedidos, ignore_index=True), ventas, config)
    return {
        "costo": float(costo["costo"].sum()),
        "wape": float((diario["sales"] - diario["q50"]).abs().sum() / diario["sales"].sum()),
        "cobertura": float(diario["sales"].between(diario["q10"], diario["q90"]).mean()),
        "cobertura_pedido": float((costo["venta_real"] <= costo["pedido"]).mean()),
    }


def evaluar_baseline(entradas: Entradas, origenes: Iterable[date], config: ConfigNegocio) -> dict:
    tiendas = entradas.tiendas["store_nbr"].tolist()
    origenes = list(origenes)
    return _metricas(
        [pronosticar_baseline(entradas.ventas, tiendas, o, config) for o in origenes],
        [calcular_pedido(entradas.ventas, tiendas, o, config) for o in origenes],
        entradas.ventas, config,
    )


def evaluar_ml(artefacto: dict, entradas: Entradas, origenes: Iterable[date], config: ConfigNegocio) -> dict:
    origenes = list(origenes)
    return _metricas(
        [pronosticar_ml(artefacto, entradas, o, config) for o in origenes],
        [calcular_pedido_ml(artefacto, entradas, o, config) for o in origenes],
        entradas.ventas, config,
    )
