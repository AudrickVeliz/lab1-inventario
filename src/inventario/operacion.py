"""Lógica del revisor: decide qué falta procesar. Los DAGs solo llaman a estas funciones.

No depende del reloj real: las fechas salen de los archivos entregados, por lo que
funciona con cualquier periodo que llegue a la bandeja.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

from inventario.bandeja import (
    ConflictoBandeja,
    ErrorOrigen,
    cargar_configuracion,
    generar,
    versiones_disponibles,
)
from inventario.negocio import cargar_negocio
from inventario.pronostico import SEMANAS_DE_MARGEN
from inventario.entrenamiento import decision_de, entrenar_en_fecha, fechas_de_entrenamiento
from inventario.publicacion import Rutas, Version, listar_publicaciones

_FUENTE_PRINCIPAL = "train"
_SIN_LIMITE = date.max
_OBJETIVOS_DVC = ("historico", "bandeja", "procesados", "modelos", "publicados", "seguimiento", "reportes")


def rutas_de_entorno() -> Rutas:
    return Rutas(Path(os.environ.get("INVENTARIO_RAIZ", ".")))


def generar_bandeja(rutas: Rutas) -> dict:
    """Representa al sistema externo. Sin CSV de origen no hay nada que hacer, y no es un error.

    Un origen inválido se detiene sin escribir, pero el flujo sigue con lo ya entregado.
    """
    if not any(rutas.kaggle.glob("*.csv")):
        return {"estado": "sin_origen"}
    try:
        resumen = generar(rutas.kaggle, rutas.bandeja, rutas.historico, cargar_configuracion(rutas.config_fuentes))
    except (ErrorOrigen, ConflictoBandeja) as error:
        resultado = {"estado": "detenido", "motivo": str(error)}
        _guardar_json(rutas.reportes / "generacion.json", resultado)
        return resultado
    resultado = {"estado": "generado", "escritos": resumen.escritos, "sin_cambios": resumen.sin_cambios}
    _guardar_json(rutas.reportes / "generacion.json", resultado)
    return resultado


def _guardar_json(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporal, ruta)


def cortes_debidos(rutas: Rutas) -> list[date]:
    """Cortes con ventas hasta su víspera. Otras fuentes no cuentan: los feriados se anuncian
    con meses de anticipación, y crearían cortes sin ventas que los respalden.
    """
    entregas = versiones_disponibles(rutas.bandeja, _FUENTE_PRINCIPAL, _SIN_LIMITE)
    if not entregas:
        return []
    fuentes = cargar_configuracion(rutas.config_fuentes)
    negocio = cargar_negocio(rutas.config_negocio)
    ultima_venta = max(a.fecha_dato for a in entregas)

    corte = fuentes.corte_historico + timedelta(days=1)
    while corte.weekday() != negocio.dia_semana_corte:
        corte += timedelta(days=1)
    cortes = []
    while corte - timedelta(days=1) <= ultima_venta:
        cortes.append(corte)
        corte += timedelta(days=7)
    return cortes


def versiones_pendientes(rutas: Rutas) -> list[Version]:
    """Publicaciones originales que faltan, y versiones nuevas por datos que llegaron después.

    Una entrega afecta a un corte si su fecha cae en la ventana que ese corte usa y llegó
    después de emitirlo: es una corrección o un dato atrasado. Todos los cortes afectados
    generan una versión, porque el proceso no debe requerir intervención manual.
    """
    cortes = cortes_debidos(rutas)
    negocio = cargar_negocio(rutas.config_negocio)
    dias_ventana = negocio.horizonte_dias * (negocio.semanas_baseline + SEMANAS_DE_MARGEN)
    pendientes = {Version(corte, corte) for corte in cortes}
    for archivo in versiones_disponibles(rutas.bandeja, _FUENTE_PRINCIPAL, _SIN_LIMITE):
        for corte in cortes:
            inicio_ventana = corte - timedelta(days=dias_ventana)
            if inicio_ventana < archivo.fecha_dato < corte < archivo.fecha_entrega:
                pendientes.add(Version(corte, archivo.fecha_entrega))
    return sorted(pendientes - set(listar_publicaciones(rutas)))


def entrenar_pendientes(rutas: Rutas) -> list[date]:
    """Entrena en orden: cada decisión compara contra el modelo vigente de la anterior.

    Una fecha ya decidida no se repite, así que una corrección posterior no cambia
    el modelo con el que se publicó un corte.
    """
    negocio = cargar_negocio(rutas.config_negocio)
    fechas = fechas_de_entrenamiento(cortes_debidos(rutas), negocio.modelo.reentrenar_cada_cortes)
    pendientes = [fecha for fecha in fechas if decision_de(rutas, fecha) is None]
    for fecha in pendientes:
        entrenar_en_fecha(rutas, fecha)
    return pendientes


def ultima_entrega(rutas: Rutas) -> date | None:
    """Hasta dónde llega lo conocido de ventas, incluidas las correcciones."""
    entregas = versiones_disponibles(rutas.bandeja, _FUENTE_PRINCIPAL, _SIN_LIMITE)
    return max((a.fecha_entrega for a in entregas), default=None)


def versiones_para_backfill(rutas: Rutas, desde: date, hasta: date, conocimiento: date | None = None) -> list[Version]:
    """Cortes de un rango. Sin ``conocimiento``, un corte sin publicar se emite como original
    y uno ya publicado se reprocesa con todo lo que se conoce hoy.
    """
    negocio = cargar_negocio(rutas.config_negocio)
    publicadas = set(listar_publicaciones(rutas))
    cortes_publicados = {v.corte for v in publicadas}
    actual = ultima_entrega(rutas)
    versiones = []
    dia = desde
    while dia <= hasta:
        if dia.weekday() == negocio.dia_semana_corte:
            if conocimiento is not None:
                versiones.append(Version(dia, conocimiento))
            elif dia not in cortes_publicados:
                versiones.append(Version(dia, dia))
            elif actual is not None and actual > dia:
                versiones.append(Version(dia, actual))
        dia += timedelta(days=1)
    return sorted(set(versiones) - publicadas)


def _huella_bandeja(rutas: Rutas) -> str:
    """Los archivos entregados no cambian de contenido, así que basta con sus nombres."""
    nombres = sorted(
        ruta.relative_to(rutas.bandeja).as_posix()
        for ruta in rutas.bandeja.rglob("*") if ruta.is_file() and not ruta.name.startswith(".")
    ) if rutas.bandeja.is_dir() else []
    return hashlib.md5("\n".join(nombres).encode("utf-8")).hexdigest()


def _ruta_revision(rutas: Rutas) -> Path:
    """Estado operativo: va fuera de lo versionado, porque se escribe después de versionar."""
    return rutas.raiz / "data" / ".estado" / "ultima_revision.json"


def hay_trabajo(rutas: Rutas) -> bool:
    """Permite que el revisor corra seguido sin recalcular nada si no llegó nada nuevo."""
    if versiones_pendientes(rutas):
        return True
    ruta = _ruta_revision(rutas)
    if not ruta.exists():
        return ultima_entrega(rutas) is not None
    return json.loads(ruta.read_text(encoding="utf-8"))["huella_bandeja"] != _huella_bandeja(rutas)


def marcar_revisado(rutas: Rutas) -> None:
    _guardar_json(_ruta_revision(rutas), {"huella_bandeja": _huella_bandeja(rutas)})


def versionar(rutas: Rutas) -> list[str]:
    """Versiona con DVC lo generado y lo envía al remoto."""
    objetivos = [f"data/{nombre}" for nombre in _OBJETIVOS_DVC if (rutas.raiz / "data" / nombre).is_dir()]
    if not objetivos:
        return []
    dvc = os.environ.get("INVENTARIO_DVC", "dvc")
    for comando in ([dvc, "add", *objetivos], [dvc, "push"]):
        resultado = subprocess.run(comando, cwd=rutas.raiz, capture_output=True, text=True)
        if resultado.returncode != 0:
            raise RuntimeError(f"{' '.join(comando[1:2])} falló: {resultado.stderr[-500:]}")
    return objetivos
