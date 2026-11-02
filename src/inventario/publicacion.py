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
from inventario.ingesta import escribir_reporte, ingerir
from inventario.negocio import ConfigNegocio, cargar_negocio
from inventario.pedido import calcular_pedido
from inventario.pronostico import pronosticar_baseline, ventana_reciente, ventas_conocidas

MODELO = "baseline_mediana"
_COLUMNAS_VENTAS = ["date", "store_nbr", "family", "sales"]


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


@dataclass(frozen=True)
class Rutas:
    raiz: Path

    @property
    def kaggle(self) -> Path:
        return self.raiz / "data" / "kaggle"

    @property
    def bandeja(self) -> Path:
        return self.raiz / "data" / "bandeja"

    @property
    def historico(self) -> Path:
        return self.raiz / "data" / "historico"

    @property
    def procesados(self) -> Path:
        return self.raiz / "data" / "procesados"

    @property
    def publicados(self) -> Path:
        return self.raiz / "data" / "publicados"

    @property
    def seguimiento(self) -> Path:
        return self.raiz / "data" / "seguimiento"

    @property
    def reportes(self) -> Path:
        return self.raiz / "data" / "reportes"

    @property
    def config_fuentes(self) -> Path:
        return self.raiz / "config" / "fuentes.yaml"

    @property
    def config_negocio(self) -> Path:
        return self.raiz / "config" / "negocio.yaml"


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


def _ventas_a_csv(ventas: pd.DataFrame) -> bytes:
    return ventas.to_csv(index=False, lineterminator="\n", date_format="%Y-%m-%d").encode("utf-8")


def _leer_ventas(contenido: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(contenido), parse_dates=["date"])


def _preparar(rutas: Rutas, version: Version, fuentes: Configuracion, negocio: ConfigNegocio) -> tuple[bytes, bytes, dict, object]:
    """Datos para el modelo: solo lo entregado hasta ``conocimiento`` y vendido antes del corte."""
    tablas, reporte = ingerir(rutas.bandeja, rutas.historico, version.conocimiento, fuentes)
    conocidas = ventas_conocidas(tablas["train"], version.corte)
    ventana = ventana_reciente(conocidas, negocio.semanas_baseline, negocio.horizonte_dias)
    ventas = _ventas_a_csv(ventana[_COLUMNAS_VENTAS])
    tiendas = tablas["stores"][["store_nbr"]].to_csv(index=False, lineterminator="\n").encode("utf-8")
    contexto = {
        "ultima_fecha_por_fuente": {f: e.ultima_fecha_dato for f, e in reporte.fuentes.items()},
        "archivos_en_cuarentena": [asdict(a) for a in reporte.cuarentena],
        "avisos": len(reporte.avisos),
    }
    return ventas, tiendas, contexto, reporte


def _calcular(ventas_csv: bytes, tiendas_csv: bytes, version: Version, negocio: ConfigNegocio) -> tuple[bytes, bytes]:
    ventas = _leer_ventas(ventas_csv)
    tiendas = pd.read_csv(io.BytesIO(tiendas_csv))["store_nbr"].tolist()
    pronostico = pronosticar_baseline(ventas, tiendas, version.corte, negocio)
    pedido = calcular_pedido(ventas, tiendas, version.corte, negocio)
    return _ventas_a_csv(pronostico), _ventas_a_csv(pedido)


def _escribir_atomico(ruta: Path, contenido: bytes) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(f".{ruta.name}.tmp")
    temporal.write_bytes(contenido)
    os.replace(temporal, ruta)


def preparar_version(rutas: Rutas, version: Version) -> None:
    """Valida, ingiere y deja los datos listos para el modelo. Es idempotente."""
    fuentes = cargar_configuracion(rutas.config_fuentes)
    negocio = cargar_negocio(rutas.config_negocio)
    ventas, tiendas, contexto, reporte = _preparar(rutas, version, fuentes, negocio)
    escribir_reporte(reporte, rutas.reportes / "ingesta")
    carpeta = rutas.procesados / version.relativa
    _escribir_atomico(carpeta / "ventas.csv.gz", gzip.compress(ventas, mtime=0))
    _escribir_atomico(carpeta / "tiendas.csv", tiendas)
    _escribir_atomico(carpeta / "contexto.json", _a_json(contexto))


def _a_json(datos: dict) -> bytes:
    return (json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


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
    ventas = gzip.decompress((carpeta / "ventas.csv.gz").read_bytes())
    tiendas = (carpeta / "tiendas.csv").read_bytes()
    contexto = json.loads((carpeta / "contexto.json").read_text(encoding="utf-8"))
    pronostico, pedido = _calcular(ventas, tiendas, version, negocio)

    metadatos = {
        **version.a_dict(),
        **contexto,
        "modelo": MODELO,
        "version_codigo": _version_codigo(rutas.raiz),
        "md5_datos_preparados": _md5(ventas),
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
    """Recalcula una publicación desde los datos y compara el resultado byte a byte."""
    fuentes = cargar_configuracion(rutas.config_fuentes)
    negocio = cargar_negocio(rutas.config_negocio)
    ventas, tiendas, _, _ = _preparar(rutas, version, fuentes, negocio)
    pronostico, pedido = _calcular(ventas, tiendas, version, negocio)
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
