"""Publicación inmutable: cada versión queda en una carpeta que nunca se reescribe.

Una versión se identifica con dos fechas: la del corte y la de lo que se conocía al
calcularla. La publicación original tiene ambas iguales; una corrección posterior
genera otra versión del mismo corte (DECISIONES.md, D-017).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from inventario.bandeja import Configuracion, cargar_configuracion
from inventario.entrenamiento import cargar_modelo, entradas_desde_tablas, vigente_para
from inventario.ingesta import escribir_reporte, ingerir
from inventario.modelo import Entradas, calcular_pedido_ml, pronosticar_ml
from inventario.negocio import ConfigNegocio, cargar_negocio
from inventario.pedido import calcular_pedido
from inventario.pronostico import pronosticar_baseline, ventana_reciente, ventas_conocidas
from inventario.rutas import Rutas

__all__ = ["MODELO", "MODELO_ML", "Rutas", "Version", "listar_publicaciones", "preparar_version", "publicar_version", "reproducir"]

MODELO = "baseline_mediana"
MODELO_ML = "gradient_boosting"
_DIAS_DE_PETROLEO = 60


@dataclass(frozen=True, order=True)
class Version:
    corte: date
    conocimiento: date

    def a_dict(self) -> dict[str, str]:
        """Forma serializable, para pasarla entre tareas."""
        return {"corte": self.corte.isoformat(), "conocimiento": self.conocimiento.isoformat()}

    @classmethod
    def de_dict(cls, datos: dict[str, str]) -> Version:
        return cls(date.fromisoformat(datos["corte"]), date.fromisoformat(datos["conocimiento"]))

    @property
    def relativa(self) -> Path:
        return Path(self.corte.isoformat()) / self.conocimiento.isoformat()


def _md5(contenido: bytes) -> str:
    return hashlib.md5(contenido).hexdigest()


def _version_codigo(raiz: Path) -> str:
    """Commit del código. El montaje puede tener otro dueño, de ahí ``safe.directory``."""
    try:
        resultado = subprocess.run(
            ["git", "-c", "safe.directory=*", "rev-parse", "HEAD"],
            cwd=raiz, capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "desconocida"
    return resultado.stdout.strip()


def _a_csv(tabla: pd.DataFrame) -> bytes:
    return tabla.to_csv(index=False, lineterminator="\n", date_format="%Y-%m-%d").encode("utf-8")


def _a_json(datos: dict) -> bytes:
    return (json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _preparar(rutas: Rutas, version: Version, fuentes: Configuracion, negocio: ConfigNegocio) -> tuple[dict[str, bytes], dict, object]:
    """Datos para el modelo: solo lo entregado hasta ``conocimiento`` y vendido antes del corte."""
    tablas, reporte = ingerir(rutas.bandeja, rutas.historico, version.conocimiento, fuentes)
    entradas = entradas_desde_tablas(tablas, fuentes)
    conocidas = ventas_conocidas(entradas.ventas, version.corte)
    corte = pd.Timestamp(version.corte)
    fin = corte + pd.Timedelta(days=negocio.horizonte_dias)
    archivos = {
        "ventas.csv": _a_csv(ventana_reciente(conocidas, negocio.semanas_baseline, negocio.horizonte_dias)),
        "tiendas.csv": _a_csv(entradas.tiendas),
        "feriados.csv": _a_csv(entradas.feriados[entradas.feriados["date"].between(corte, fin)]),
        "petroleo.csv": _a_csv(entradas.petroleo[entradas.petroleo["date"] >= corte - pd.Timedelta(days=_DIAS_DE_PETROLEO)]),
    }
    contexto = {
        "ultima_fecha_por_fuente": {f: e.ultima_fecha_dato for f, e in reporte.fuentes.items()},
        "archivos_en_cuarentena": [asdict(a) for a in reporte.cuarentena],
        "avisos": len(reporte.avisos),
    }
    return archivos, contexto, reporte


def _leer_entradas(archivos: dict[str, bytes]) -> Entradas:
    def leer(nombre, **opciones):
        return pd.read_csv(io.BytesIO(archivos[nombre]), **opciones)

    return Entradas(
        ventas=leer("ventas.csv", parse_dates=["date"]),
        tiendas=leer("tiendas.csv"),
        feriados=leer("feriados.csv", parse_dates=["date"]),
        petroleo=leer("petroleo.csv", parse_dates=["date", "entrega"]),
    )


def _calcular(
    rutas: Rutas,
    archivos: dict[str, bytes],
    version: Version,
    negocio: ConfigNegocio,
) -> tuple[bytes, bytes, dict]:
    """Calcula con el modelo vigente en el corte. Si el modelo falla, se publica con el baseline."""
    entradas = _leer_entradas(archivos)
    vigente = vigente_para(rutas, version.corte)
    info = {"modelo": MODELO, "modelo_entrenado": None}
    if vigente is not None:
        try:
            artefacto = cargar_modelo(rutas, vigente)
            pronostico = pronosticar_ml(artefacto, entradas, version.corte, negocio)
            pedido = calcular_pedido_ml(artefacto, entradas, version.corte, negocio)
        except Exception as error:  # noqa: BLE001 - una falla del modelo no debe dejar a la tienda sin pedido.
            info["falla_del_modelo"] = f"{vigente}: {type(error).__name__}"
        else:
            info |= {
                "modelo": MODELO_ML,
                "modelo_entrenado": vigente,
                "md5_modelo": _md5((rutas.modelos / vigente / "modelo.joblib").read_bytes()),
            }
            return _a_csv(pronostico), _a_csv(pedido), info
    tiendas = entradas.tiendas["store_nbr"].tolist()
    pronostico = pronosticar_baseline(entradas.ventas, tiendas, version.corte, negocio)
    pedido = calcular_pedido(entradas.ventas, tiendas, version.corte, negocio)
    return _a_csv(pronostico), _a_csv(pedido), info


def _escribir_atomico(ruta: Path, contenido: bytes) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_bytes(contenido)
    os.replace(temporal, ruta)


def preparar_version(rutas: Rutas, version: Version) -> None:
    """Valida, ingiere y deja los datos listos para el modelo. Es idempotente."""
    fuentes = cargar_configuracion(rutas.config_fuentes)
    negocio = cargar_negocio(rutas.config_negocio)
    archivos, contexto, reporte = _preparar(rutas, version, fuentes, negocio)
    escribir_reporte(reporte, rutas.reportes / "ingesta")
    carpeta = rutas.procesados / version.relativa
    for nombre, contenido in archivos.items():
        if nombre == "ventas.csv":
            _escribir_atomico(carpeta / "ventas.csv.gz", gzip.compress(contenido, mtime=0))
        else:
            _escribir_atomico(carpeta / nombre, contenido)
    _escribir_atomico(carpeta / "contexto.json", _a_json(contexto))


def _leer_procesados(carpeta: Path) -> dict[str, bytes]:
    return {
        "ventas.csv": gzip.decompress((carpeta / "ventas.csv.gz").read_bytes()),
        **{n: (carpeta / n).read_bytes() for n in ("tiendas.csv", "feriados.csv", "petroleo.csv")},
    }


def publicar_version(rutas: Rutas, version: Version) -> bool:
    """Publica la versión. Si ya existe no hace nada, porque lo publicado es inmutable.

    La carpeta se escribe aparte y se renombra al final, para que nunca
    exista una publicación a medias.
    """
    final = rutas.publicados / version.relativa
    if final.exists():
        return False
    negocio = cargar_negocio(rutas.config_negocio)
    carpeta = rutas.procesados / version.relativa
    archivos = _leer_procesados(carpeta)
    contexto = json.loads((carpeta / "contexto.json").read_text(encoding="utf-8"))
    pronostico, pedido, info_modelo = _calcular(rutas, archivos, version, negocio)

    metadatos = {
        **version.a_dict(),
        **contexto,
        **info_modelo,
        "version_codigo": _version_codigo(rutas.raiz),
        "md5_datos_preparados": _md5(b"".join(archivos[n] for n in sorted(archivos))),
        "md5_config_fuentes": _md5(rutas.config_fuentes.read_bytes()),
        "md5_config_negocio": _md5(rutas.config_negocio.read_bytes()),
    }
    temporal = final.with_name(f".{final.name}.tmp")
    if temporal.exists():
        shutil.rmtree(temporal)  # Resto de un intento anterior interrumpido.
    temporal.mkdir(parents=True)
    (temporal / "pronostico.csv").write_bytes(pronostico)
    (temporal / "pedido.csv").write_bytes(pedido)
    (temporal / "metadatos.json").write_bytes(_a_json(metadatos))
    os.replace(temporal, final)
    return True


def listar_publicaciones(rutas: Rutas) -> list[Version]:
    if not rutas.publicados.is_dir():
        return []
    versiones = []
    for corte in rutas.publicados.iterdir():
        if not corte.is_dir():
            continue
        for conocimiento in corte.iterdir():
            if conocimiento.is_dir() and not conocimiento.name.startswith("."):
                versiones.append(Version(date.fromisoformat(corte.name), date.fromisoformat(conocimiento.name)))
    return sorted(versiones)


def reproducir(rutas: Rutas, version: Version) -> dict[str, bool]:
    """Recalcula una publicación desde los datos y el modelo, y compara el resultado byte a byte."""
    fuentes = cargar_configuracion(rutas.config_fuentes)
    negocio = cargar_negocio(rutas.config_negocio)
    archivos, _, _ = _preparar(rutas, version, fuentes, negocio)
    pronostico, pedido, _ = _calcular(rutas, archivos, version, negocio)
    carpeta = rutas.publicados / version.relativa
    return {
        "pronostico.csv": (carpeta / "pronostico.csv").read_bytes() == pronostico,
        "pedido.csv": (carpeta / "pedido.csv").read_bytes() == pedido,
    }


def main(argumentos: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(description="Reproduce un pronóstico publicado")
    lector.add_argument("--corte", type=date.fromisoformat, required=True)
    lector.add_argument("--conocimiento", type=date.fromisoformat, help="por defecto, la fecha del corte")
    lector.add_argument("--raiz", type=Path, default=Path("."))
    args = lector.parse_args(argumentos)
    version = Version(args.corte, args.conocimiento or args.corte)
    rutas = Rutas(args.raiz)
    if not (rutas.publicados / version.relativa).is_dir():
        print(f"ERROR: no existe la publicación {version.relativa.as_posix()}", file=sys.stderr)
        return 1
    resultado = reproducir(rutas, version)
    for archivo, igual in resultado.items():
        print(f"{archivo}: {'idéntico' if igual else 'DISTINTO'}")
    return 0 if all(resultado.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
