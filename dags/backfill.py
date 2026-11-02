"""Backfill manual: emite o reprocesa los cortes de un rango. Solo orquesta."""

from datetime import date

from airflow.sdk import Param, dag, get_current_context, task

from inventario import operacion, publicacion, seguimiento


@dag(
    dag_id="backfill",
    schedule=None,
    catchup=False,
    params={
        "desde": Param("2017-04-03", type="string", format="date"),
        "hasta": Param("2017-04-03", type="string", format="date"),
        "conocimiento": Param(None, type=["null", "string"], description="Por defecto, lo más reciente conocido"),
    },
    tags=["producto"],
)
def backfill():
    @task
    def calcular_versiones():
        parametros = get_current_context()["params"]
        conocimiento = parametros["conocimiento"]
        versiones = operacion.versiones_para_backfill(
            operacion.rutas_de_entorno(),
            date.fromisoformat(parametros["desde"]),
            date.fromisoformat(parametros["hasta"]),
            date.fromisoformat(conocimiento) if conocimiento else None,
        )
        return [v.a_dict() for v in versiones]

    @task
    def validar_y_preparar(version):
        publicacion.preparar_version(operacion.rutas_de_entorno(), publicacion.Version.de_dict(version))
        return version

    @task
    def publicar_pronostico_y_pedido(version):
        publicacion.publicar_version(operacion.rutas_de_entorno(), publicacion.Version.de_dict(version))

    @task(trigger_rule="none_failed")
    def actualizar_seguimiento():
        rutas = operacion.rutas_de_entorno()
        seguimiento.actualizar_seguimiento(rutas, operacion.ultima_entrega(rutas))

    @task(trigger_rule="none_failed")
    def versionar_con_dvc():
        operacion.versionar(operacion.rutas_de_entorno())

    publicadas = publicar_pronostico_y_pedido.expand(version=validar_y_preparar.expand(version=calcular_versiones()))
    publicadas >> actualizar_seguimiento() >> versionar_con_dvc()


backfill()
