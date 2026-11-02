"""Bandeja: simula el sistema de origen y controla qué dato estaba disponible y cuándo.

Cada archivo se llama ``<fuente>/<fecha_dato>__<fecha_entrega>.csv``. Una corrección
es un archivo nuevo con la misma fecha del dato y una entrega posterior.

La generación (``generar``) representa al sistema externo. La lectura
(``archivos_disponibles``) es lo que usa el flujo y nunca escribe en la bandeja.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

_PATRON_NOMBRE = re.compile(r"^(\d{4}-\d{2}-\d{2})__(\d{4}-\d{2}-\d{2})\.csv$")
_CALENDARIOS = {"natural", "habil"}


class ErrorOrigen(ValueError):
    """Los archivos de origen no se pueden dividir: faltan columnas o hay fechas inválidas."""


class ConflictoBandeja(ValueError):
    """Un archivo ya entregado tiene otro contenido: la corrección requiere una entrega nueva."""


@dataclass(frozen=True)
class ReglaFuente:
    nombre: str
    columnas: tuple[str, ...]
    columna_fecha: str | None
    retraso_dias: int = 0
    calendario: str = "natural"


@dataclass(frozen=True)
class Configuracion:
    corte_historico: date
    fuentes: dict[str, ReglaFuente]


@dataclass(frozen=True)
class ArchivoBandeja:
    ruta: Path
    fuente: str
    fecha_dato: date
    fecha_entrega: date


@dataclass(frozen=True)
class Resumen:
    escritos: int
    sin_cambios: int


def cargar_configuracion(ruta: Path) -> Configuracion:
    datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    fuentes = {}
    for nombre, regla in datos["fuentes"].items():
        calendario = regla.get("calendario", "natural")
        if calendario not in _CALENDARIOS:
            raise ValueError(f"{nombre}: calendario desconocido {calendario!r}")
        fuentes[nombre] = ReglaFuente(
            nombre=nombre,
            columnas=tuple(regla["columnas"]),
            columna_fecha=regla["columna_fecha"],
            retraso_dias=regla.get("retraso_dias", 0),
            calendario=calendario,
        )
    return Configuracion(corte_historico=datos["corte_historico"], fuentes=fuentes)


def fecha_entrega(regla: ReglaFuente, fecha_dato: date) -> date:
    """Fecha en que llega un dato según la regla de su fuente."""
    if regla.calendario == "habil":
        return (pd.Timestamp(fecha_dato) + pd.offsets.BDay(regla.retraso_dias)).date()
    return fecha_dato + timedelta(days=regla.retraso_dias)


def nombre_archivo(fecha_dato: date, entrega: date) -> str:
    return f"{fecha_dato.isoformat()}__{entrega.isoformat()}.csv"


def _leer_origen(ruta: Path, regla: ReglaFuente) -> pd.DataFrame:
    # Todo como texto y sin interpretar vacíos: la bandeja entrega el dato tal como llegó,
    # incluidos los precios vacíos. Validarlo le corresponde a la ingesta.
    tabla = pd.read_csv(ruta, dtype=str, keep_default_na=False)
    if set(tabla.columns) != set(regla.columnas):
        raise ErrorOrigen(
            f"{ruta.name}: se esperaban las columnas {list(regla.columnas)} "
            f"y llegaron {list(tabla.columns)}"
        )
    return tabla[list(regla.columnas)]


def _a_csv(tabla: pd.DataFrame) -> bytes:
    return tabla.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _planificar_fuente(
    tabla: pd.DataFrame,
    regla: ReglaFuente,
    corte: date,
    bandeja: Path,
    historico: Path,
) -> dict[Path, bytes]:
    plan: dict[Path, bytes] = {}
    ruta_historico = historico / f"{regla.nombre}.csv.gz"

    if regla.columna_fecha is None:
        if not tabla.empty:
            plan[ruta_historico] = gzip.compress(_a_csv(tabla), mtime=0)
        return plan

    try:
        fechas = pd.to_datetime(tabla[regla.columna_fecha], format="%Y-%m-%d").dt.date
    except ValueError as error:
        raise ErrorOrigen(f"{regla.nombre}: fecha inválida ({error})") from error

    entregas = fechas.map({f: fecha_entrega(regla, f) for f in fechas.unique()})
    en_historico = entregas <= corte
    if en_historico.any():
        plan[ruta_historico] = gzip.compress(_a_csv(tabla[en_historico]), mtime=0)

    pendientes = tabla[~en_historico]
    for fecha_dato, filas in pendientes.groupby(fechas[~en_historico], sort=True):
        nombre = nombre_archivo(fecha_dato, fecha_entrega(regla, fecha_dato))
        plan[bandeja / regla.nombre / nombre] = _a_csv(filas)
    return plan


def _contenido(ruta: Path) -> bytes:
    crudo = ruta.read_bytes()
    return gzip.decompress(crudo) if ruta.suffix == ".gz" else crudo


def _mismo_contenido(ruta: Path, nuevo: bytes) -> bool:
    return _contenido(ruta) == (gzip.decompress(nuevo) if ruta.suffix == ".gz" else nuevo)


def generar(origen: Path, bandeja: Path, historico: Path, config: Configuracion) -> Resumen:
    """Divide los CSV de origen en carga histórica y archivos de la bandeja.

    Es idempotente y no sobrescribe lo entregado. Si detecta un conflicto, no escribe
    nada, para no dejar una entrega a medias.
    """
    origen, bandeja, historico = Path(origen), Path(bandeja), Path(historico)
    plan: dict[Path, bytes] = {}
    for regla in config.fuentes.values():
        ruta = origen / f"{regla.nombre}.csv"
        if ruta.exists():
            tabla = _leer_origen(ruta, regla)
            plan |= _planificar_fuente(tabla, regla, config.corte_historico, bandeja, historico)

    existentes = [ruta for ruta in plan if ruta.exists()]
    conflictos = [ruta for ruta in existentes if not _mismo_contenido(ruta, plan[ruta])]
    if conflictos:
        lista = ", ".join(ruta.as_posix() for ruta in conflictos[:5])
        raise ConflictoBandeja(
            f"{len(conflictos)} archivo(s) ya entregado(s) con otro contenido: {lista}. "
            "Una corrección se inyecta como archivo nuevo con una fecha de entrega posterior."
        )

    nuevos = [ruta for ruta in plan if not ruta.exists()]
    for ruta in nuevos:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_name(f".{ruta.name}.tmp")
        temporal.write_bytes(plan[ruta])
        os.replace(temporal, ruta)
    return Resumen(escritos=len(nuevos), sin_cambios=len(existentes))


def parsear_nombre(ruta: Path, fuente: str) -> ArchivoBandeja | None:
    """Interpreta un nombre de archivo; devuelve None si no sigue la convención."""
    coincidencia = _PATRON_NOMBRE.match(Path(ruta).name)
    if coincidencia is None:
        return None
    try:
        dato, entrega = (date.fromisoformat(grupo) for grupo in coincidencia.groups())
    except ValueError:
        return None
    return ArchivoBandeja(Path(ruta), fuente, dato, entrega)


def _archivos(bandeja: Path, fuente: str) -> list[Path]:
    carpeta = Path(bandeja) / fuente
    if not carpeta.is_dir():
        return []  # Una fuente que aún no llega es una situación esperada.
    return sorted(r for r in carpeta.iterdir() if r.is_file() and not r.name.startswith("."))


def nombres_invalidos(bandeja: Path, fuente: str) -> list[Path]:
    """Archivos que no siguen la convención: no se usan y deben reportarse."""
    return [r for r in _archivos(bandeja, fuente) if parsear_nombre(r, fuente) is None]


def archivos_disponibles(bandeja: Path, fuente: str, fecha_corte: date) -> list[ArchivoBandeja]:
    """Versión vigente de cada fecha del dato, con lo que había llegado a ``fecha_corte``.

    Este filtro es la barrera contra la fuga de información: lo entregado después
    de la fecha de corte no existe para quien pregunta en esa fecha.
    """
    vigentes: dict[date, ArchivoBandeja] = {}
    for ruta in _archivos(bandeja, fuente):
        archivo = parsear_nombre(ruta, fuente)
        if archivo is None or archivo.fecha_entrega > fecha_corte:
            continue
        actual = vigentes.get(archivo.fecha_dato)
        if actual is None or archivo.fecha_entrega > actual.fecha_entrega:
            vigentes[archivo.fecha_dato] = archivo
    return [vigentes[fecha] for fecha in sorted(vigentes)]


def main(argumentos: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(description="Genera la carga histórica y la bandeja")
    lector.add_argument("--origen", type=Path, default=Path("data/kaggle"))
    lector.add_argument("--bandeja", type=Path, default=Path("data/bandeja"))
    lector.add_argument("--historico", type=Path, default=Path("data/historico"))
    lector.add_argument("--config", type=Path, default=Path("config/fuentes.yaml"))
    args = lector.parse_args(argumentos)
    try:
        resumen = generar(args.origen, args.bandeja, args.historico, cargar_configuracion(args.config))
    except (ErrorOrigen, ConflictoBandeja) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Archivos escritos: {resumen.escritos}; sin cambios: {resumen.sin_cambios}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
