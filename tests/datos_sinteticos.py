"""Genera CSV pequeños con el formato de Kaggle, para probar el flujo sin los datos reales.

Solo usa la biblioteca estándar: el CI lo ejecuta antes de instalar nada.
Uso: python tests/datos_sinteticos.py data/kaggle
"""

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

TIENDAS = (1, 2)
FAMILIAS = ("BEVERAGES", "PRODUCE")
INICIO = date(2016, 12, 1)
FIN = date(2017, 5, 15)


def _dias(inicio: date, fin: date):
    while inicio <= fin:
        yield inicio
        inicio += timedelta(days=1)


def _escribir(ruta: Path, encabezado: list[str], filas) -> None:
    with ruta.open("w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo, lineterminator="\n")
        escritor.writerow(encabezado)
        escritor.writerows(filas)


def venta(dia: date, tienda: int, familia: str) -> float:
    """Determinista, con variación por día de la semana, tienda y familia."""
    return float(10 * tienda + 5 * FAMILIAS.index(familia) + dia.weekday() + dia.day % 3)


def escribir_kaggle(carpeta: Path, fin: date = FIN) -> None:
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    dias = list(_dias(INICIO, fin))
    filas_venta = (
        (dia, tienda, familia, venta(dia, tienda, familia), dia.day % 2)
        for dia in dias for tienda in TIENDAS for familia in FAMILIAS
    )
    _escribir(
        carpeta / "train.csv",
        ["id", "date", "store_nbr", "family", "sales", "onpromotion"],
        ((i, *fila) for i, fila in enumerate(filas_venta)),
    )
    _escribir(
        carpeta / "transactions.csv",
        ["date", "store_nbr", "transactions"],
        ((dia, tienda, 100 + tienda) for dia in dias for tienda in TIENDAS),
    )
    _escribir(
        carpeta / "oil.csv",
        ["date", "dcoilwtico"],
        ((dia, 50.5 if dia.day % 7 else "") for dia in dias if dia.weekday() < 5),
    )
    _escribir(
        carpeta / "holidays_events.csv",
        ["date", "type", "locale", "locale_name", "description", "transferred"],
        [
            (date(2017, 1, 1), "Holiday", "National", "Ecuador", "Primer dia", "False"),
            (date(2017, 5, 1), "Holiday", "National", "Ecuador", "Dia del trabajo", "False"),
        ],
    )
    _escribir(
        carpeta / "stores.csv",
        ["store_nbr", "city", "state", "type", "cluster"],
        ((tienda, "Quito", "Pichincha", "D", 13) for tienda in TIENDAS),
    )


if __name__ == "__main__":
    escribir_kaggle(Path(sys.argv[1]))
