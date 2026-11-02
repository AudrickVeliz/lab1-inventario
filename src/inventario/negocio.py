"""Parámetros de negocio leídos de la configuración versionada."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class GrupoCosto:
    nombre: str
    quiebre: float
    merma: float

    @property
    def cuantil_critico(self) -> float:
        """Cuantil que minimiza el costo esperado cuando se compara lo que falta con lo que sobra."""
        return self.quiebre / (self.quiebre + self.merma)


@dataclass(frozen=True)
class ConfigNegocio:
    dia_semana_corte: int
    horizonte_dias: int
    semanas_baseline: int
    dias_minimos_por_bloque: int
    cuantiles: tuple[float, ...]
    grupos: dict[str, GrupoCosto]
    familia_a_grupo: dict[str, str]
    grupo_predeterminado: str

    def grupo_de(self, familia: str) -> GrupoCosto:
        """Una familia no listada, incluida una nueva, usa el grupo predeterminado."""
        return self.grupos[self.familia_a_grupo.get(familia, self.grupo_predeterminado)]


def cargar_negocio(ruta: Path) -> ConfigNegocio:
    datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    costos = datos["costos"]

    grupos: dict[str, GrupoCosto] = {}
    familia_a_grupo: dict[str, str] = {}
    for nombre, grupo in costos["grupos"].items():
        if grupo["quiebre"] <= 0 or grupo["merma"] <= 0:
            raise ValueError(f"{nombre}: los costos deben ser positivos")
        grupos[nombre] = GrupoCosto(nombre, grupo["quiebre"], grupo["merma"])
        for familia in grupo.get("familias", ()):
            if familia in familia_a_grupo:
                raise ValueError(f"{familia}: aparece en más de un grupo de costos")
            familia_a_grupo[familia] = nombre

    predeterminado = costos["grupo_predeterminado"]
    if predeterminado not in grupos:
        raise ValueError(f"grupo predeterminado desconocido: {predeterminado}")

    cuantiles = tuple(datos["cuantiles"])
    if not all(0 < q < 1 for q in cuantiles):
        raise ValueError("los cuantiles deben estar entre 0 y 1")
    if datos["horizonte_dias"] < 1 or datos["baseline"]["semanas"] < 1:
        raise ValueError("el horizonte y las semanas del baseline deben ser positivos")
    if datos["dia_semana_corte"] not in range(7):
        raise ValueError("el día de la semana del corte debe estar entre 0 y 6")

    return ConfigNegocio(
        dia_semana_corte=datos["dia_semana_corte"],
        horizonte_dias=datos["horizonte_dias"],
        semanas_baseline=datos["baseline"]["semanas"],
        dias_minimos_por_bloque=datos["baseline"]["dias_minimos_por_bloque"],
        cuantiles=cuantiles,
        grupos=grupos,
        familia_a_grupo=familia_a_grupo,
        grupo_predeterminado=predeterminado,
    )
