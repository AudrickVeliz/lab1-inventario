"""Entrena candidatos, los compara con el modelo vigente y registra cada decisión.

Cada fecha de entrenamiento deja una carpeta inmutable en ``data/modelos/<fecha>/`` con
la decisión y, si hubo reemplazo, el modelo. Sin modelo vigente se publica con el baseline.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta

import joblib
import pandas as pd

from inventario.bandeja import Configuracion, cargar_configuracion, fecha_entrega
from inventario.ingesta import ingerir
from inventario.modelo import (
    Entradas,
    ajustar,
    errores_fuera_de_muestra,
    evaluar_baseline,
    evaluar_ml,
    origenes_previos,
)
from inventario.negocio import ConfigNegocio, cargar_negocio
from inventario.rutas import Rutas

_DIAS_DE_HISTORIA_REQUERIDOS = 84


def entradas_desde_tablas(tablas: dict[str, pd.DataFrame], fuentes: Configuracion) -> Entradas:
    petroleo = tablas["oil"]
    regla = fuentes.fuentes["oil"]
    entregas = {f: fecha_entrega(regla, f) for f in petroleo["date"].dt.date.unique()}
    return Entradas(
        ventas=tablas["train"][["date", "store_nbr", "family", "sales", "onpromotion"]],
        tiendas=tablas["stores"],
        feriados=tablas["holidays_events"],
        petroleo=petroleo.assign(entrega=pd.to_datetime(petroleo["date"].dt.date.map(entregas))),
    )


def fechas_de_entrenamiento(cortes: list[date], cada: int) -> list[date]:
    return cortes[::cada]


def _carpeta(rutas: Rutas, fecha: date):
    return rutas.modelos / fecha.isoformat()


def decision_de(rutas: Rutas, fecha: date) -> dict | None:
    ruta = _carpeta(rutas, fecha) / "decision.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def decisiones(rutas: Rutas) -> list[dict]:
    """Todas las decisiones, de la más antigua a la más nueva."""
    if not rutas.modelos.is_dir():
        return []
    fechas = sorted(c.name for c in rutas.modelos.iterdir() if c.is_dir() and not c.name.startswith("."))
    return [d for d in (decision_de(rutas, date.fromisoformat(f)) for f in fechas) if d is not None]


def vigente_para(rutas: Rutas, corte: date) -> str | None:
    """Fecha del modelo vigente en un corte; ``None`` significa baseline."""
    vigente = None
    for decision in decisiones(rutas):
        if date.fromisoformat(decision["fecha"]) <= corte:
            vigente = decision["vigente"]
    return vigente


def cargar_modelo(rutas: Rutas, fecha: str) -> dict:
    return joblib.load(rutas.modelos / fecha / "modelo.joblib")


def decidir(candidato: dict, vigente: dict, mejora_minima: float, cobertura_nominal: float) -> tuple[bool, str]:
    """El criterio de reemplazo: menos costo por un margen claro, sin empeorar la cobertura."""
    mejora = 1 - candidato["costo"] / vigente["costo"] if vigente["costo"] else 0.0
    distancia_candidato = abs(candidato["cobertura"] - cobertura_nominal)
    distancia_vigente = abs(vigente["cobertura"] - cobertura_nominal)
    if mejora < mejora_minima:
        return False, f"el costo mejora {mejora:.1%}, menos que el mínimo de {mejora_minima:.1%}"
    if distancia_candidato > distancia_vigente:
        return False, f"el costo mejora {mejora:.1%}, pero la cobertura empeora"
    return True, f"el costo mejora {mejora:.1%} y la cobertura no empeora"


def _guardar(rutas: Rutas, fecha: date, decision: dict, artefacto: dict | None) -> dict:
    """La carpeta se escribe aparte y se renombra al final: nunca queda a medias."""
    final = _carpeta(rutas, fecha)
    temporal = final.with_name(f".{final.name}.tmp")
    if temporal.exists():
        shutil.rmtree(temporal)
    temporal.mkdir(parents=True)
    if artefacto is not None:
        joblib.dump(artefacto, temporal / "modelo.joblib", compress=3)
    texto = json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (temporal / "decision.json").write_text(texto, encoding="utf-8", newline="\n")
    os.replace(temporal, final)
    return decision


def _evaluar_y_decidir(entradas: Entradas, fecha: date, negocio: ConfigNegocio, rutas: Rutas, anterior: str | None) -> tuple[dict, dict | None]:
    config = negocio.modelo
    horizonte = negocio.horizonte_dias
    inicio_datos = entradas.ventas["date"].min().date()
    primer_origen = inicio_datos + timedelta(days=_DIAS_DE_HISTORIA_REQUERIDOS)
    origenes = [
        o for o in origenes_previos(fecha, config.semanas_totales, horizonte, negocio.dia_semana_corte)
        if o >= primer_origen
    ]
    fuera = config.semanas_calibracion + config.semanas_evaluacion
    entrenamiento, calibracion, evaluacion = (
        origenes[:-fuera], origenes[-fuera:-config.semanas_evaluacion], origenes[-config.semanas_evaluacion:],
    )
    registro = {"fecha": fecha.isoformat(), "modelo_anterior": anterior}
    if len(entrenamiento) < config.semanas_minimas_entrenamiento:
        return registro | {
            "reemplaza": False,
            "vigente": anterior,
            "motivo": f"datos insuficientes: {len(entrenamiento)} semanas para entrenar",
        }, None

    # El candidato no ve los cortes de calibración ni de evaluación.
    candidato = ajustar(entradas, entrenamiento, horizonte)
    candidato["errores_dia"], candidato["errores_periodo"] = errores_fuera_de_muestra(candidato, entradas, calibracion)
    metricas_candidato = evaluar_ml(candidato, entradas, evaluacion, negocio)
    metricas_baseline = evaluar_baseline(entradas, evaluacion, negocio)
    metricas_vigente = (
        metricas_baseline if anterior is None
        else evaluar_ml(cargar_modelo(rutas, anterior), entradas, evaluacion, negocio)
    )
    nominal = max(negocio.cuantiles) - min(negocio.cuantiles)
    reemplaza, motivo = decidir(metricas_candidato, metricas_vigente, config.mejora_minima_costo, nominal)
    registro |= {
        "reemplaza": reemplaza,
        "vigente": fecha.isoformat() if reemplaza else anterior,
        "motivo": motivo,
        "cortes_evaluados": [o.isoformat() for o in evaluacion],
        "metricas": {"candidato": metricas_candidato, "vigente": metricas_vigente, "baseline": metricas_baseline},
    }
    if not reemplaza:
        return registro, None

    # El modelo final aprende también de los cortes más recientes.
    errores_dia, errores_periodo = errores_fuera_de_muestra(
        candidato, entradas, origenes[-config.semanas_calibracion:]
    )
    final = ajustar(entradas, origenes, horizonte)
    final["errores_dia"], final["errores_periodo"] = errores_dia, errores_periodo
    return registro, final


def entrenar_en_fecha(rutas: Rutas, fecha: date) -> dict:
    """Es idempotente. Un fallo del entrenamiento no detiene la operación: sigue el vigente."""
    existente = decision_de(rutas, fecha)
    if existente is not None:
        return existente
    fuentes = cargar_configuracion(rutas.config_fuentes)
    negocio = cargar_negocio(rutas.config_negocio)
    anterior = vigente_para(rutas, fecha)
    tablas, _ = ingerir(rutas.bandeja, rutas.historico, fecha, fuentes)
    entradas = entradas_desde_tablas(tablas, fuentes)
    try:
        decision, artefacto = _evaluar_y_decidir(entradas, fecha, negocio, rutas, anterior)
    except Exception as error:  # noqa: BLE001 - cualquier fallo debe dejar al vigente en operación.
        decision, artefacto = {
            "fecha": fecha.isoformat(),
            "modelo_anterior": anterior,
            "reemplaza": False,
            "vigente": anterior,
            "motivo": f"el entrenamiento falló: {type(error).__name__}: {error}",
        }, None
    return _guardar(rutas, fecha, decision, artefacto)
