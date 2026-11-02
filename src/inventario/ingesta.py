"""Ingesta: arma lo que se conocía de cada fuente en una fecha de corte.

Parte de la carga histórica y aplica, en orden de entrega, cada archivo válido de la
bandeja. Cada fila reemplaza a la anterior con la misma clave, por lo que una corrección
puede traer el archivo completo o solo las filas corregidas.

Un archivo inválido queda en cuarentena: se registra en el reporte y no se usa, pero
permanece en la bandeja y el proceso continúa.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from inventario.bandeja import (
    Configuracion,
    ReglaFuente,
    cargar_configuracion,
    fecha_entrega,
    nombres_invalidos,
    versiones_disponibles,
)
from inventario.validacion import (
    Aviso,
    ErrorLectura,
    Referencia,
    leer_csv,
    validar_archivo,
    validar_contenido,
)


class ErrorHistorico(ValueError):
    """La carga histórica falta o es inválida: sin esa base no se puede operar."""


@dataclass(frozen=True)
class ArchivoEnCuarentena:
    fuente: str
    archivo: str
    motivos: list[str]


@dataclass(frozen=True)
class EstadoFuente:
    filas: int
    ultima_fecha_dato: str | None
    archivos_usados: int
    correcciones: int


@dataclass(frozen=True)
class Reporte:
    fecha_corte: str
    fuentes: dict[str, EstadoFuente]
    cuarentena: list[ArchivoEnCuarentena]
    avisos: list[Aviso]


def _cargar_historico(
    historico: Path,
    regla: ReglaFuente,
    fecha_corte: date,
    tiendas: frozenset[int] | None,
) -> tuple[pd.DataFrame, list[Aviso]]:
    ruta = Path(historico) / f"{regla.nombre}.csv.gz"
    if ruta.exists():
        try:
            cruda = leer_csv(ruta)
        except ErrorLectura as error:
            raise ErrorHistorico(f"{ruta.name}: {error}") from error
    elif regla.columna_fecha is None:
        raise ErrorHistorico(f"falta el catálogo {ruta.name}: ejecuta primero inventario.bandeja")
    else:
        cruda = pd.DataFrame({columna: pd.Series(dtype=object) for columna in regla.columnas})

    resultado = validar_contenido(cruda, regla, tiendas, ruta.name)
    if resultado.tabla is None:
        raise ErrorHistorico(f"{ruta.name}: {'; '.join(resultado.motivos)}")

    tabla = resultado.tabla
    if regla.columna_fecha is not None and not tabla.empty:
        # Permite un backfill anterior al corte histórico sin ver datos que no habían llegado.
        fechas = tabla[regla.columna_fecha].dt.date
        entregas = fechas.map({f: fecha_entrega(regla, f) for f in fechas.unique()})
        tabla = tabla[entregas <= fecha_corte]
    return tabla, resultado.avisos


def _aplicar_bandeja(
    base: pd.DataFrame,
    bandeja: Path,
    regla: ReglaFuente,
    fecha_corte: date,
    referencia: Referencia,
) -> tuple[pd.DataFrame, EstadoFuente, list[ArchivoEnCuarentena], list[Aviso]]:
    tiene_fechas = regla.columna_fecha is not None
    conocidas = set(base[regla.columna_fecha].dt.date) if tiene_fechas else set()
    partes = [base]
    cuarentena = []
    avisos = []
    usados = correcciones = 0

    for archivo in versiones_disponibles(bandeja, regla.nombre, fecha_corte):
        es_correccion = not tiene_fechas or archivo.fecha_dato in conocidas
        resultado = validar_archivo(archivo, regla, referencia, es_correccion)
        avisos += resultado.avisos
        if resultado.tabla is None:
            cuarentena.append(ArchivoEnCuarentena(regla.nombre, archivo.ruta.name, resultado.motivos))
            continue
        partes.append(resultado.tabla)
        usados += 1
        correcciones += es_correccion
        conocidas.add(archivo.fecha_dato)

    for ruta in nombres_invalidos(bandeja, regla.nombre):
        cuarentena.append(ArchivoEnCuarentena(regla.nombre, ruta.name, ["nombre fuera de la convención"]))

    con_filas = [parte for parte in partes if not parte.empty] or [base]
    tabla = pd.concat(con_filas, ignore_index=True)
    tabla = tabla.drop_duplicates(list(regla.clave), keep="last")
    tabla = tabla.sort_values(list(regla.clave), ignore_index=True)

    ultima = None
    if tiene_fechas and not tabla.empty:
        ultima = tabla[regla.columna_fecha].max().date().isoformat()
    estado = EstadoFuente(len(tabla), ultima, usados, int(correcciones))
    return tabla, estado, cuarentena, avisos


def _referencia(tiendas: frozenset[int], historico_train: pd.DataFrame, config: Configuracion) -> Referencia:
    """Usa solo la carga histórica, que siempre se conoce y no depende de la bandeja."""
    if historico_train.empty:
        return Referencia(tiendas=tiendas, umbral_venta_sobre_mediana=config.umbral_venta_sobre_mediana)
    con_venta = historico_train[historico_train["sales"] > 0]
    return Referencia(
        tiendas=tiendas,
        familias=frozenset(historico_train["family"]),
        medianas=con_venta.groupby(["store_nbr", "family"])["sales"].median(),
        umbral_venta_sobre_mediana=config.umbral_venta_sobre_mediana,
    )


def ingerir(
    bandeja: Path,
    historico: Path,
    fecha_corte: date,
    config: Configuracion,
) -> tuple[dict[str, pd.DataFrame], Reporte]:
    """Devuelve las tablas conocidas en ``fecha_corte`` y el reporte. Solo lee."""
    tablas: dict[str, pd.DataFrame] = {}
    estados: dict[str, EstadoFuente] = {}
    cuarentena: list[ArchivoEnCuarentena] = []
    avisos: list[Aviso] = []

    def procesar(regla: ReglaFuente, base: pd.DataFrame, referencia: Referencia) -> None:
        tablas[regla.nombre], estados[regla.nombre], malos, nuevos = _aplicar_bandeja(
            base, bandeja, regla, fecha_corte, referencia
        )
        cuarentena.extend(malos)
        avisos.extend(nuevos)

    catalogos = [r for r in config.fuentes.values() if r.columna_fecha is None]
    for regla in catalogos:
        base, avisos_base = _cargar_historico(historico, regla, fecha_corte, None)
        avisos.extend(avisos_base)
        procesar(regla, base, Referencia())
    tiendas = frozenset(int(t) for t in tablas["stores"]["store_nbr"])

    con_fechas = [r for r in config.fuentes.values() if r.columna_fecha is not None]
    bases = {}
    for regla in con_fechas:
        bases[regla.nombre], avisos_base = _cargar_historico(historico, regla, fecha_corte, tiendas)
        avisos.extend(avisos_base)
    referencia = _referencia(tiendas, bases["train"], config)
    for regla in con_fechas:
        procesar(regla, bases[regla.nombre], referencia)

    reporte = Reporte(fecha_corte.isoformat(), estados, cuarentena, avisos)
    return tablas, reporte


def escribir_reporte(reporte: Reporte, carpeta: Path) -> Path:
    """Escribe el reporte; repetir la misma fecha de corte produce el mismo archivo."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{reporte.fecha_corte}.json"
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_text(
        json.dumps(asdict(reporte), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporal, ruta)
    return ruta


def main(argumentos: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(description="Valida e ingiere lo disponible en una fecha de corte")
    lector.add_argument("--fecha-corte", type=date.fromisoformat, required=True)
    lector.add_argument("--bandeja", type=Path, default=Path("data/bandeja"))
    lector.add_argument("--historico", type=Path, default=Path("data/historico"))
    lector.add_argument("--reportes", type=Path, default=Path("data/reportes/ingesta"))
    lector.add_argument("--config", type=Path, default=Path("config/fuentes.yaml"))
    args = lector.parse_args(argumentos)
    try:
        _, reporte = ingerir(args.bandeja, args.historico, args.fecha_corte, cargar_configuracion(args.config))
    except ErrorHistorico as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    ruta = escribir_reporte(reporte, args.reportes)
    print(f"Corte {reporte.fecha_corte}: {len(reporte.cuarentena)} en cuarentena, {len(reporte.avisos)} avisos ({ruta.as_posix()})")
    for fuente, estado in reporte.fuentes.items():
        print(f"  {fuente}: {estado.filas} filas, último dato {estado.ultima_fecha_dato}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
