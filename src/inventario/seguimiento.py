"""Seguimiento: compara lo que se publicó originalmente con las ventas que ya se conocen.

Solo se evalúa la publicación original de cada corte, porque fue la decisión que se tomó
en su momento. Las ventas, en cambio, incluyen las correcciones ya entregadas.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from inventario.bandeja import cargar_configuracion
from inventario.ingesta import ingerir
from inventario.negocio import cargar_negocio
from inventario.pedido import costo_del_pedido
from inventario.publicacion import Rutas, listar_publicaciones

_SERIE_DIA = ["date", "store_nbr", "family"]


def _desempeno_de_corte(rutas: Rutas, version, ventas: pd.DataFrame, negocio) -> dict:
    carpeta = rutas.publicados / version.relativa
    pronostico = pd.read_csv(carpeta / "pronostico.csv", parse_dates=["date"])
    pedido = pd.read_csv(carpeta / "pedido.csv", parse_dates=["fecha_corte", "desde", "hasta"])

    comparables = pronostico.merge(ventas[[*_SERIE_DIA, "sales"]], on=_SERIE_DIA, how="inner")
    venta_dias = comparables["sales"].sum()
    costo = costo_del_pedido(pedido, ventas, negocio)
    completo = bool(len(costo)) and bool((costo["dias_observados"] == negocio.horizonte_dias).all())
    return {
        "corte": version.corte.isoformat(),
        "dias_con_venta": comparables["date"].nunique(),
        "completo": completo,
        "wape_q50": (comparables["sales"] - comparables["q50"]).abs().sum() / venta_dias if venta_dias else None,
        "cobertura_q10_q90": (
            comparables["sales"].between(comparables["q10"], comparables["q90"]).mean() if len(comparables) else None
        ),
        "venta": costo["venta_real"].sum(),
        "pedido": costo["pedido"].sum(),
        "faltante": costo["faltante"].sum(),
        "sobrante": costo["sobrante"].sum(),
        "costo": costo["costo"].sum(),
    }


def _reporte_markdown(tabla: pd.DataFrame, fecha_verdad: date) -> str:
    lineas = [
        "# Seguimiento del desempeño",
        "",
        f"Publicaciones originales frente a las ventas conocidas al {fecha_verdad.isoformat()}, con sus correcciones.",
        "",
    ]
    completos = tabla[tabla["completo"]]
    if not completos.empty:
        wape = (completos["wape_q50"] * completos["venta"]).sum() / completos["venta"].sum()
        lineas += [
            f"- **Cortes con semana completa:** {len(completos)} de {len(tabla)}",
            f"- **WAPE del cuantil 0.50:** {wape:.3f}",
            f"- **Cobertura del intervalo 0.10 a 0.90:** {completos['cobertura_q10_q90'].mean():.1%}",
            f"- **Costo de negocio:** {completos['costo'].sum():,.0f}",
            "",
        ]
    lineas += [
        "| Corte | Días con venta | Completo | WAPE | Cobertura | Venta | Pedido | Costo |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for fila in tabla.itertuples():
        wape_texto = "" if pd.isna(fila.wape_q50) else f"{fila.wape_q50:.3f}"
        cobertura = "" if pd.isna(fila.cobertura_q10_q90) else f"{fila.cobertura_q10_q90:.1%}"
        lineas.append(
            f"| {fila.corte} | {fila.dias_con_venta} | {'sí' if fila.completo else 'no'} | {wape_texto} | {cobertura} "
            f"| {fila.venta:,.0f} | {fila.pedido:,.0f} | {fila.costo:,.0f} |"
        )
    return "\n".join(lineas) + "\n"


def actualizar_seguimiento(rutas: Rutas, fecha_verdad: date) -> int:
    """Reescribe el reporte. No es una publicación: se recalcula con lo que se conoce hoy."""
    originales = [v for v in listar_publicaciones(rutas) if v.corte == v.conocimiento]
    if not originales:
        return 0
    fuentes = cargar_configuracion(rutas.config_fuentes)
    negocio = cargar_negocio(rutas.config_negocio)
    tablas, _ = ingerir(rutas.bandeja, rutas.historico, fecha_verdad, fuentes)
    ventas = tablas["train"]
    tabla = pd.DataFrame([_desempeno_de_corte(rutas, v, ventas, negocio) for v in originales])

    rutas.seguimiento.mkdir(parents=True, exist_ok=True)
    salidas = {
        "desempeno.csv": tabla.to_csv(index=False, lineterminator="\n"),
        "reporte.md": _reporte_markdown(tabla, fecha_verdad),
    }
    for nombre, contenido in salidas.items():
        temporal = rutas.seguimiento / f".{nombre}.tmp"
        temporal.write_text(contenido, encoding="utf-8", newline="\n")
        os.replace(temporal, rutas.seguimiento / nombre)
    return len(originales)
