"""Validación: separa los datos inválidos, que se detienen, de las situaciones esperadas.

Un motivo invalida el archivo completo. Un aviso se reporta, pero el dato se conserva.
La frontera entre ambos está justificada en DECISIONES.md (D-007).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from inventario.bandeja import ArchivoBandeja, ReglaFuente


class ErrorLectura(ValueError):
    """El archivo no se puede interpretar como CSV."""


@dataclass(frozen=True)
class Aviso:
    fuente: str
    archivo: str
    tipo: str
    detalle: str


@dataclass(frozen=True)
class Referencia:
    """Lo que ya se conoce y sirve para evaluar un archivo nuevo."""

    tiendas: frozenset[int] | None = None
    familias: frozenset[str] | None = None
    medianas: pd.Series | None = None
    umbral_venta_sobre_mediana: float | None = None


@dataclass
class Resultado:
    tabla: pd.DataFrame | None
    motivos: list[str] = field(default_factory=list)
    avisos: list[Aviso] = field(default_factory=list)


def leer_csv(ruta: Path) -> pd.DataFrame:
    """Lee todo como texto: interpretar los tipos es parte de validar."""
    try:
        return pd.read_csv(ruta, dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError, OSError, EOFError) as error:
        raise ErrorLectura(f"archivo ilegible: {type(error).__name__}") from error


def _convertir(serie: pd.Series, tipo: str, anulable: bool) -> tuple[pd.Series, str | None]:
    nombre = serie.name
    vacios = serie.str.strip() == ""
    if vacios.any() and not anulable:
        return serie, f"{nombre}: {int(vacios.sum())} valor(es) vacío(s)"

    if tipo == "texto":
        return serie, None
    if tipo == "fecha":
        convertida = pd.to_datetime(serie.where(~vacios), format="%Y-%m-%d", errors="coerce")
        invalidos = convertida.isna() & ~vacios
    elif tipo == "booleano":
        convertida = serie.map({"True": True, "False": False})
        invalidos = convertida.isna() & ~vacios
        if not invalidos.any():
            convertida = convertida.astype("boolean" if anulable else "bool")
    elif tipo in ("decimal", "entero"):
        convertida = pd.to_numeric(serie.where(~vacios), errors="coerce")
        no_finitos = ~np.isfinite(convertida.astype("float64"))
        invalidos = no_finitos & ~vacios
        if tipo == "entero":
            invalidos |= ~no_finitos & (convertida % 1 != 0)
            if not invalidos.any():
                convertida = convertida.astype("Int64" if anulable else "int64")
        else:
            convertida = convertida.astype("float64")
    else:
        raise ValueError(f"{nombre}: tipo desconocido {tipo!r}")

    if invalidos.any():
        return serie, f"{nombre}: {int(invalidos.sum())} valor(es) con formato inválido (se esperaba {tipo})"
    return convertida, None


def validar_contenido(
    cruda: pd.DataFrame,
    regla: ReglaFuente,
    tiendas: frozenset[int] | None,
    archivo: str,
) -> Resultado:
    """Reglas que no dependen del nombre del archivo; también se aplican a la carga histórica."""
    if set(cruda.columns) != set(regla.columnas):
        return Resultado(None, [f"columnas incorrectas: {list(cruda.columns)}"])

    columnas = {}
    motivos = []
    for columna, tipo in regla.tipos.items():
        columnas[columna], motivo = _convertir(cruda[columna], tipo, columna in regla.anulables)
        if motivo:
            motivos.append(motivo)
    if motivos:
        return Resultado(None, motivos)
    tabla = pd.DataFrame(columnas)

    for columna in regla.no_negativas:
        negativos = int((tabla[columna] < 0).sum())
        if negativos:
            motivos.append(f"{columna}: {negativos} valor(es) negativo(s)")

    avisos = []
    repetidas = int(tabla.duplicated().sum())
    if repetidas:
        tabla = tabla.drop_duplicates()
        avisos.append(Aviso(regla.nombre, archivo, "filas_duplicadas", f"{repetidas} fila(s) idéntica(s) descartada(s)"))
    conflictos = int(tabla.duplicated(list(regla.clave)).sum())
    if conflictos:
        motivos.append(f"{conflictos} clave(s) repetida(s) con valores distintos")

    if tiendas is not None and "store_nbr" in tabla.columns:
        desconocidas = sorted(set(tabla["store_nbr"]) - tiendas)
        if desconocidas:
            motivos.append(f"tienda(s) fuera del catálogo: {desconocidas[:10]}")

    return Resultado(None, motivos, avisos) if motivos else Resultado(tabla, [], avisos)


def _avisos_de_ventas(tabla: pd.DataFrame, regla: ReglaFuente, referencia: Referencia, archivo: ArchivoBandeja, es_correccion: bool) -> list[Aviso]:
    if not {"family", "sales"} <= set(tabla.columns):
        return []
    avisos = []
    nombre = archivo.ruta.name

    if referencia.familias is not None:
        nuevas = sorted(set(tabla["family"]) - referencia.familias)
        if nuevas:
            avisos.append(Aviso(regla.nombre, nombre, "familia_nueva", f"familias nuevas: {nuevas[:10]}"))

    if not es_correccion and referencia.tiendas and referencia.familias:
        esperadas = len(referencia.tiendas) * len(referencia.familias)
        if len(tabla) < esperadas:
            avisos.append(Aviso(regla.nombre, nombre, "archivo_incompleto", f"{len(tabla)} de {esperadas} filas"))

    if referencia.medianas is not None and referencia.umbral_venta_sobre_mediana is not None:
        pares = pd.MultiIndex.from_frame(tabla[["store_nbr", "family"]])
        medianas = referencia.medianas.reindex(pares).to_numpy()
        altas = int((tabla["sales"].to_numpy() > referencia.umbral_venta_sobre_mediana * medianas).sum())
        if altas:
            avisos.append(
                Aviso(regla.nombre, nombre, "venta_muy_alta", f"{altas} venta(s) sobre {referencia.umbral_venta_sobre_mediana:g} veces su mediana")
            )
    return avisos


def validar_archivo(
    archivo: ArchivoBandeja,
    regla: ReglaFuente,
    referencia: Referencia,
    es_correccion: bool,
) -> Resultado:
    """Valida un archivo de la bandeja. Nunca lanza una excepción por un dato inválido."""
    if regla.retraso_dias >= 0 and archivo.fecha_entrega < archivo.fecha_dato:
        return Resultado(None, ["la entrega es anterior a la fecha del dato"])
    try:
        cruda = leer_csv(archivo.ruta)
    except ErrorLectura as error:
        return Resultado(None, [str(error)])

    resultado = validar_contenido(cruda, regla, referencia.tiendas, archivo.ruta.name)
    if resultado.tabla is None:
        return resultado

    if regla.columna_fecha is not None:
        fechas = resultado.tabla[regla.columna_fecha].dt.date
        distintas = int((fechas != archivo.fecha_dato).sum())
        if distintas:
            return Resultado(None, [f"{distintas} fila(s) con una fecha distinta a la del nombre del archivo"], resultado.avisos)

    resultado.avisos += _avisos_de_ventas(resultado.tabla, regla, referencia, archivo, es_correccion)
    return resultado
