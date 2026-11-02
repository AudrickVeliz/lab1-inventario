"""Comprueba el entorno de punta a punta: imagen, ejecutor, montaje del proyecto y memoria.

No tiene calendario: se dispara a mano para diagnosticar una instalación.
"""

import os
from pathlib import Path

from airflow.sdk import dag, task


@dag(dag_id="comprobar_entorno", schedule=None, catchup=False, tags=["diagnostico"])
def comprobar_entorno():
    @task
    def ingerir_un_corte():
        from inventario.ingesta import main

        raiz = Path(os.environ["INVENTARIO_RAIZ"])
        codigo = main([
            "--fecha-corte", "2017-04-03",
            "--bandeja", str(raiz / "data" / "bandeja"),
            "--historico", str(raiz / "data" / "historico"),
            "--reportes", str(raiz / "data" / "reportes" / "ingesta"),
            "--config", str(raiz / "config" / "fuentes.yaml"),
        ])
        if codigo != 0:
            raise RuntimeError(f"La ingesta terminó con el código {codigo}")

    ingerir_un_corte()


comprobar_entorno()
