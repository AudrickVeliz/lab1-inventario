"""Seguimiento: compara lo que se publicó originalmente con las ventas que ya se conocen.

Solo se evalúa la publicación original de cada corte, porque fue la decisión que se tomó
en su momento. Las ventas, en cambio, incluyen las correcciones ya entregadas. Cada corte
se compara también con lo que habría recomendado el baseline con los mismos datos.
"""

from __future__ import annotations

import json
import os
from datetime import date

import pandas as pd

from inventario.bandeja import cargar_configuracion
from inventario.entrenamiento import decisiones
from inventario.ingesta import ingerir
from inventario.negocio import cargar_negocio
from inventario.pedido import calcular_pedido, costo_del_pedido
from inventario.pronostico import pronosticar_baseline
from inventario.publicacion import Rutas, listar_publicaciones

_SERIE_DIA = ["date", "store_nbr", "family"]


def _medir(pronostico: pd.DataFrame, pedido: pd.DataFrame, ventas: pd.DataFrame, negocio) -> dict:
    comparables = pronostico.merge(ventas[[*_SERIE_DIA, "sales"]], on=_SERIE_DIA, how="inner")
    venta_dias = comparables["sales"].sum()
    costo = costo_del_pedido(pedido, ventas, negocio)
    return {
        "dias_con_venta": comparables["date"].nunique(),
        "completo": bool(len(costo)) and bool((costo["dias_observados"] == negocio.horizonte_dias).all()),
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


def _desempeno_de_corte(rutas: Rutas, version, ventas: pd.DataFrame, negocio) -> dict:
    carpeta = rutas.publicados / version.relativa
    pronostico = pd.read_csv(carpeta / "pronostico.csv", parse_dates=["date"])
    pedido = pd.read_csv(carpeta / "pedido.csv", parse_dates=["fecha_corte", "desde", "hasta"])
    metadatos = json.loads((carpeta / "metadatos.json").read_text(encoding="utf-8"))
    fila = {"corte": version.corte.isoformat(), "modelo": metadatos["modelo"], **_medir(pronostico, pedido, ventas, negocio)}

    # El baseline con los mismos datos preparados: la referencia que el modelo debe superar.
    procesados = rutas.procesados / version.relativa
    previas = pd.read_csv(procesados / "ventas.csv.gz", parse_dates=["date"])
    tiendas = pd.read_csv(procesados / "tiendas.csv")["store_nbr"].tolist()
    referencia = _medir(
        pronosticar_baseline(previas, tiendas, version.corte, negocio),
        calcular_pedido(previas, tiendas, version.corte, negocio),
        ventas, negocio,
    )
    fila |= {
        "wape_baseline": referencia["wape_q50"],
        "cobertura_baseline": referencia["cobertura_q10_q90"],
        "costo_baseline": referencia["costo"],
    }
    return fila


def _wape_ponderado(tabla: pd.DataFrame, columna: str) -> float:
    return (tabla[columna] * tabla["venta"]).sum() / tabla["venta"].sum()


def _reporte_markdown(tabla: pd.DataFrame, fecha_verdad: date, registro: list[dict]) -> str:
    lineas = [
        "# Seguimiento del desempeño",
        "",
        f"Publicaciones originales frente a las ventas conocidas al {fecha_verdad.isoformat()}, con sus correcciones.",
        "",
    ]
    completos = tabla[tabla["completo"]]
    if not completos.empty:
        costo, referencia = completos["costo"].sum(), completos["costo_baseline"].sum()
        cambio = f"{costo / referencia - 1:+.1%}" if referencia else "sin referencia"
        lineas += [
            f"Cortes con semana completa: {len(completos)} de {len(tabla)}.",
            "",
            "| Métrica | Publicado | Baseline con los mismos datos |",
            "|---|---|---|",
            f"| Costo de negocio | {costo:,.0f} ({cambio}) | {referencia:,.0f} |",
            f"| WAPE del cuantil 0.50 | {_wape_ponderado(completos, 'wape_q50'):.3f} | {_wape_ponderado(completos, 'wape_baseline'):.3f} |",
            f"| Cobertura del intervalo 0.10 a 0.90 | {completos['cobertura_q10_q90'].mean():.1%} | {completos['cobertura_baseline'].mean():.1%} |",
            "",
        ]
    lineas += [
        "## Por corte",
        "",
        "| Corte | Modelo | Días con venta | Completo | WAPE | Cobertura | Venta | Pedido | Costo | Costo del baseline |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for fila in tabla.itertuples():
        wape = "" if pd.isna(fila.wape_q50) else f"{fila.wape_q50:.3f}"
        cobertura = "" if pd.isna(fila.cobertura_q10_q90) else f"{fila.cobertura_q10_q90:.1%}"
        lineas.append(
            f"| {fila.corte} | {fila.modelo} | {fila.dias_con_venta} | {'sí' if fila.completo else 'no'} | {wape} | {cobertura} "
            f"| {fila.venta:,.0f} | {fila.pedido:,.0f} | {fila.costo:,.0f} | {fila.costo_baseline:,.0f} |"
        )
    if registro:
        lineas += ["", "## Decisiones de modelo", "", "| Fecha | Reemplaza | Vigente | Motivo |", "|---|---|---|---|"]
        for decision in registro:
            vigente = decision["vigente"] or "baseline"
            lineas.append(f"| {decision['fecha']} | {'sí' if decision['reemplaza'] else 'no'} | {vigente} | {decision['motivo']} |")
    return "\n".join(lineas) + "\n"


def _comparacion_de_modelos(registro: list[dict]) -> pd.DataFrame:
    """Una fila por modelo y fecha de entrenamiento, con los mismos cortes para todos."""
    filas = []
    for decision in registro:
        for nombre, metricas in decision.get("metricas", {}).items():
            filas.append({"fecha": decision["fecha"], "modelo": nombre, **metricas})
    return pd.DataFrame(filas)


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
    registro = decisiones(rutas)

    rutas.seguimiento.mkdir(parents=True, exist_ok=True)
    salidas = {
        "desempeno.csv": tabla.to_csv(index=False, lineterminator="\n"),
        "reporte.md": _reporte_markdown(tabla, fecha_verdad, registro),
        "comparacion_modelos.csv": _comparacion_de_modelos(registro).to_csv(index=False, lineterminator="\n"),
    }
    for nombre, contenido in salidas.items():
        temporal = rutas.seguimiento / f".{nombre}.tmp"
        temporal.write_text(contenido, encoding="utf-8", newline="\n")
        os.replace(temporal, rutas.seguimiento / nombre)
    return len(originales)
