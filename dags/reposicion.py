"""Revisor periódico: procesa lo que haya llegado a la bandeja. Solo orquesta.

Cubre las etapas del producto: ingesta y validación, preparación de datos, pronóstico
y pedido sugerido, seguimiento del desempeño y versionado con DVC.
"""

from datetime import datetime, timedelta, timezone

from airflow.sdk import dag, get_current_context, task

from inventario import operacion, publicacion, seguimiento


def _etiquetar(version: dict) -> publicacion.Version:
    get_current_context()["etiqueta"] = f"{version['corte']} / {version['conocimiento']}"
    return publicacion.Version.de_dict(version)


@dag(
    dag_id="reposicion",
    schedule="*/5 * * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=1)},
    tags=["producto"],
)
def reposicion():
    @task
    def generar_bandeja():
        return operacion.generar_bandeja(operacion.rutas_de_entorno())

    @task.short_circuit
    def hay_trabajo():
        return operacion.hay_trabajo(operacion.rutas_de_entorno())

    @task
    def detectar_versiones():
        return [v.a_dict() for v in operacion.versiones_pendientes(operacion.rutas_de_entorno())]

    @task(map_index_template="{{ etiqueta }}")
    def validar_y_preparar(version):
        publicacion.preparar_version(operacion.rutas_de_entorno(), _etiquetar(version))
        return version

    @task(map_index_template="{{ etiqueta }}")
    def publicar_pronostico_y_pedido(version):
        publicacion.publicar_version(operacion.rutas_de_entorno(), _etiquetar(version))

    # Sin versiones nuevas, las tareas anteriores se omiten y esto debe ejecutarse igual.
    @task(trigger_rule="none_failed")
    def actualizar_seguimiento():
        rutas = operacion.rutas_de_entorno()
        seguimiento.actualizar_seguimiento(rutas, operacion.ultima_entrega(rutas))

    @task(trigger_rule="none_failed")
    def versionar_con_dvc():
        operacion.versionar(operacion.rutas_de_entorno())

    @task(trigger_rule="none_failed")
    def marcar_revisado():
        operacion.marcar_revisado(operacion.rutas_de_entorno())

    versiones = detectar_versiones()
    publicadas = publicar_pronostico_y_pedido.expand(version=validar_y_preparar.expand(version=versiones))
    generar_bandeja() >> hay_trabajo() >> versiones
    publicadas >> actualizar_seguimiento() >> versionar_con_dvc() >> marcar_revisado()


reposicion()
