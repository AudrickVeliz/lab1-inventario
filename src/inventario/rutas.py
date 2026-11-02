"""Ubicación de los datos del producto, siempre relativa a la raíz del proyecto."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    def modelos(self) -> Path:
        return self.raiz / "data" / "modelos"

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
