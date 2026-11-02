import ast
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from datos_sinteticos import escribir_kaggle, venta  # noqa: E402

from inventario import operacion, publicacion  # noqa: E402
from inventario.publicacion import Rutas, Version  # noqa: E402
from inventario.seguimiento import actualizar_seguimiento  # noqa: E402

RAIZ = Path(__file__).parents[1]
PRIMER_CORTE = date(2017, 4, 3)
ENCABEZADO = "id,date,store_nbr,family,sales,onpromotion\n"


@pytest.fixture
def rutas(tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copy(RAIZ / "config" / "fuentes.yaml", tmp_path / "config")
    shutil.copy(RAIZ / "config" / "negocio.yaml", tmp_path / "config")
    return Rutas(tmp_path)


@pytest.fixture
def con_datos(rutas):
    escribir_kaggle(rutas.kaggle)
    operacion.generar_bandeja(rutas)
    return rutas


def procesar(rutas, version):
    publicacion.preparar_version(rutas, version)
    return publicacion.publicar_version(rutas, version)


def procesar_pendientes(rutas):
    pendientes = operacion.versiones_pendientes(rutas)
    for version in pendientes:
        procesar(rutas, version)
    return pendientes


def entregar_venta(rutas, dia, entrega, valor, tienda=1, familia="BEVERAGES"):
    nombre = f"{dia.isoformat()}__{entrega.isoformat()}.csv"
    ruta = rutas.bandeja / "train" / nombre
    ruta.write_bytes((ENCABEZADO + f"999,{dia.isoformat()},{tienda},{familia},{valor},0\n").encode())
    return ruta


def estado_de(carpeta):
    return {r: (r.read_bytes(), r.stat().st_mtime_ns) for r in carpeta.rglob("*") if r.is_file()}


# --- Generación de la bandeja ---


def test_sin_csv_de_origen_no_es_un_error(rutas):
    assert operacion.generar_bandeja(rutas) == {"estado": "sin_origen"}
    assert not operacion.hay_trabajo(rutas)
    assert operacion.versiones_pendientes(rutas) == []


def test_genera_la_bandeja_y_es_idempotente(rutas):
    escribir_kaggle(rutas.kaggle)
    primero = operacion.generar_bandeja(rutas)
    segundo = operacion.generar_bandeja(rutas)
    assert primero["estado"] == "generado" and primero["escritos"] > 0
    assert segundo["escritos"] == 0 and segundo["sin_cambios"] == primero["escritos"]


def test_un_origen_invalido_se_detiene_sin_fallar_ni_escribir(rutas):
    escribir_kaggle(rutas.kaggle)
    (rutas.kaggle / "oil.csv").write_bytes(b"fecha,precio\n2017-04-03,50\n")
    resultado = operacion.generar_bandeja(rutas)
    assert resultado["estado"] == "detenido" and "oil.csv" in resultado["motivo"]
    assert not rutas.bandeja.exists()
    assert json.loads((rutas.reportes / "generacion.json").read_text(encoding="utf-8"))["estado"] == "detenido"


def test_un_conflicto_detiene_y_conserva_lo_ya_entregado(con_datos):
    antes = estado_de(con_datos.bandeja)
    tabla = pd.read_csv(con_datos.kaggle / "train.csv")
    tabla.loc[tabla["date"] == "2017-05-01", "sales"] = 1.0
    tabla.to_csv(con_datos.kaggle / "train.csv", index=False)
    assert operacion.generar_bandeja(con_datos)["estado"] == "detenido"
    assert estado_de(con_datos.bandeja) == antes


# --- Qué cortes se deben procesar ---


def test_los_cortes_son_lunes_desde_el_fin_de_la_carga_historica(con_datos):
    cortes = operacion.cortes_debidos(con_datos)
    assert cortes[0] == PRIMER_CORTE
    assert all(c.weekday() == 0 for c in cortes)
    assert cortes[-1] == date(2017, 5, 15)  # hay ventas hasta su víspera


def test_un_corte_necesita_ventas_hasta_su_vispera(rutas):
    escribir_kaggle(rutas.kaggle, fin=date(2017, 5, 13))  # sábado: falta el domingo
    operacion.generar_bandeja(rutas)
    assert operacion.cortes_debidos(rutas)[-1] == date(2017, 5, 8)


def test_otras_fuentes_no_crean_cortes(con_datos):
    # Un feriado anunciado o un petróleo posteriores no deben crear cortes sin ventas.
    carpeta = con_datos.bandeja / "holidays_events"
    carpeta.mkdir(exist_ok=True)
    (carpeta / "2017-12-25__2017-11-25.csv").write_bytes(
        b"date,type,locale,locale_name,description,transferred\n2017-12-25,Holiday,National,Ecuador,Navidad,False\n"
    )
    assert operacion.cortes_debidos(con_datos)[-1] == date(2017, 5, 15)


def test_una_correccion_tardia_no_crea_cortes_nuevos(con_datos):
    antes = operacion.cortes_debidos(con_datos)
    entregar_venta(con_datos, date(2017, 5, 1), date(2017, 9, 1), 77.0)
    assert operacion.cortes_debidos(con_datos) == antes


def test_un_periodo_nuevo_se_procesa_sin_tocar_nada(con_datos, tmp_path):
    procesar_pendientes(con_datos)
    assert operacion.versiones_pendientes(con_datos) == []
    # Llega otra semana completa con el formato de Kaggle.
    tabla = pd.read_csv(con_datos.kaggle / "train.csv")
    nueva = tabla[tabla["date"] == "2017-05-15"].assign(date="2017-05-22", id=lambda t: t["id"] + 100000)
    pd.concat([tabla, nueva]).to_csv(con_datos.kaggle / "train.csv", index=False)
    operacion.generar_bandeja(con_datos)
    assert operacion.versiones_pendientes(con_datos) == [Version(date(2017, 5, 22), date(2017, 5, 22))]


# --- Publicación ---


def test_publica_los_tres_archivos(con_datos):
    version = Version(PRIMER_CORTE, PRIMER_CORTE)
    assert procesar(con_datos, version) is True
    carpeta = con_datos.publicados / version.relativa
    assert sorted(r.name for r in carpeta.iterdir()) == ["metadatos.json", "pedido.csv", "pronostico.csv"]
    assert len(pd.read_csv(carpeta / "pronostico.csv")) == 2 * 2 * 7
    assert len(pd.read_csv(carpeta / "pedido.csv")) == 2 * 2


def test_los_metadatos_permiten_rastrear_la_publicacion(con_datos):
    version = Version(PRIMER_CORTE, PRIMER_CORTE)
    procesar(con_datos, version)
    metadatos = json.loads((con_datos.publicados / version.relativa / "metadatos.json").read_text(encoding="utf-8"))
    assert metadatos["corte"] == metadatos["conocimiento"] == PRIMER_CORTE.isoformat()
    assert metadatos["modelo"] == publicacion.MODELO
    assert metadatos["ultima_fecha_por_fuente"]["train"] == "2017-04-02"
    assert metadatos["ultima_fecha_por_fuente"]["oil"] == "2017-03-31"
    assert metadatos["archivos_en_cuarentena"] == []
    assert len(metadatos["md5_datos_preparados"]) == 32
    assert len(metadatos["md5_config_negocio"]) == 32
    assert "version_codigo" in metadatos


def test_lo_publicado_no_se_reescribe(con_datos):
    version = Version(PRIMER_CORTE, PRIMER_CORTE)
    procesar(con_datos, version)
    carpeta = con_datos.publicados / version.relativa
    (carpeta / "pedido.csv").write_bytes(b"marcado\n")
    antes = estado_de(carpeta)
    assert procesar(con_datos, version) is False
    assert estado_de(carpeta) == antes


def test_un_intento_interrumpido_no_deja_una_publicacion_a_medias(con_datos):
    version = Version(PRIMER_CORTE, PRIMER_CORTE)
    final = con_datos.publicados / version.relativa
    temporal = final.with_name(f".{final.name}.tmp")
    temporal.mkdir(parents=True)
    (temporal / "pedido.csv").write_bytes(b"incompleto\n")
    assert operacion.versiones_pendientes(con_datos)[0] == version
    assert procesar(con_datos, version) is True
    assert not temporal.exists()
    assert (final / "pedido.csv").read_bytes() != b"incompleto\n"


def test_las_ventas_preparadas_no_incluyen_el_futuro(con_datos):
    version = Version(PRIMER_CORTE, PRIMER_CORTE)
    publicacion.preparar_version(con_datos, version)
    ventas = pd.read_csv(con_datos.procesados / version.relativa / "ventas.csv.gz", parse_dates=["date"])
    assert ventas["date"].max() == pd.Timestamp("2017-04-02")


def test_el_pronostico_no_cambia_si_llegan_datos_posteriores(con_datos):
    version = Version(PRIMER_CORTE, PRIMER_CORTE)
    procesar(con_datos, version)
    original = (con_datos.publicados / version.relativa / "pronostico.csv").read_bytes()
    entregar_venta(con_datos, date(2017, 3, 28), date(2017, 4, 20), 5000.0)
    assert publicacion.reproducir(con_datos, version)["pronostico.csv"]
    assert (con_datos.publicados / version.relativa / "pronostico.csv").read_bytes() == original


def test_se_puede_reproducir_una_publicacion(con_datos):
    version = Version(date(2017, 4, 10), date(2017, 4, 10))
    procesar(con_datos, version)
    assert publicacion.reproducir(con_datos, version) == {"pronostico.csv": True, "pedido.csv": True}


def test_reproducir_detecta_una_publicacion_alterada(con_datos):
    version = Version(PRIMER_CORTE, PRIMER_CORTE)
    procesar(con_datos, version)
    (con_datos.publicados / version.relativa / "pedido.csv").write_bytes(b"alterado\n")
    assert publicacion.reproducir(con_datos, version) == {"pronostico.csv": True, "pedido.csv": False}


def test_comando_de_reproduccion(con_datos, capsys):
    procesar(con_datos, Version(PRIMER_CORTE, PRIMER_CORTE))
    argumentos = ["--corte", PRIMER_CORTE.isoformat(), "--raiz", str(con_datos.raiz)]
    assert publicacion.main(argumentos) == 0
    assert "idéntico" in capsys.readouterr().out
    assert publicacion.main(["--corte", "2017-04-10", "--raiz", str(con_datos.raiz)]) == 1


# --- Correcciones ---


def test_una_correccion_genera_versiones_de_todos_los_cortes_afectados(con_datos):
    procesar_pendientes(con_datos)
    dia, entrega = date(2017, 4, 5), date(2017, 5, 2)
    entregar_venta(con_datos, dia, entrega, 500.0)
    # Afecta a los cortes posteriores al dato y anteriores a su llegada.
    assert operacion.versiones_pendientes(con_datos) == [
        Version(date(2017, 4, 10), entrega),
        Version(date(2017, 4, 17), entrega),
        Version(date(2017, 4, 24), entrega),
        Version(date(2017, 5, 1), entrega),
    ]


def test_una_correccion_no_reescribe_la_publicacion_original(con_datos):
    procesar_pendientes(con_datos)
    original = con_datos.publicados / "2017-04-10" / "2017-04-10"
    antes = estado_de(original)
    entregar_venta(con_datos, date(2017, 4, 5), date(2017, 5, 2), 500.0)
    procesar_pendientes(con_datos)
    assert estado_de(original) == antes
    assert (con_datos.publicados / "2017-04-10" / "2017-05-02" / "pedido.csv").exists()
    assert operacion.versiones_pendientes(con_datos) == []


def test_la_nueva_version_refleja_la_correccion(rutas):
    escribir_kaggle(rutas.kaggle)
    operacion.generar_bandeja(rutas)
    procesar_pendientes(rutas)
    # Tres semanas: un pico aislado no alcanza a mover el cuantil 0.86 de 12 semanas.
    entrega = date(2017, 5, 2)
    for dia in (date(2017, 3, 26), date(2017, 4, 2), date(2017, 4, 9)):
        entregar_venta(rutas, dia, entrega, 5000.0)
    procesar_pendientes(rutas)
    pedido = {
        c: pd.read_csv(rutas.publicados / "2017-04-10" / c / "pedido.csv").query("store_nbr == 1 and family == 'BEVERAGES'")["pedido"].item()
        for c in ("2017-04-10", "2017-05-02")
    }
    assert pedido["2017-05-02"] > pedido["2017-04-10"]


def test_una_correccion_fuera_de_la_ventana_no_afecta_al_corte(con_datos):
    procesar_pendientes(con_datos)
    # Dato de diciembre: fuera de las 14 semanas que usa el corte del 8 de mayo.
    entrega = date(2017, 5, 9)
    entregar_venta(con_datos, date(2016, 12, 5), entrega, 500.0)
    cortes = {v.corte for v in operacion.versiones_pendientes(con_datos)}
    assert date(2017, 5, 8) not in cortes


def test_un_dato_atrasado_se_trata_igual_que_una_correccion(rutas):
    escribir_kaggle(rutas.kaggle)
    operacion.generar_bandeja(rutas)
    ruta = rutas.bandeja / "train" / "2017-04-09__2017-04-10.csv"
    ruta.rename(rutas.bandeja / "train" / "2017-04-09__2017-04-20.csv")
    versiones = operacion.versiones_pendientes(rutas)
    assert Version(date(2017, 4, 10), date(2017, 4, 10)) in versiones
    assert Version(date(2017, 4, 10), date(2017, 4, 20)) in versiones
    assert Version(date(2017, 4, 17), date(2017, 4, 20)) in versiones


def test_una_correccion_invalida_no_cambia_el_resultado(con_datos):
    procesar_pendientes(con_datos)
    entrega = date(2017, 5, 2)
    entregar_venta(con_datos, date(2017, 4, 5), entrega, -5.0)
    version = Version(date(2017, 4, 10), entrega)
    procesar(con_datos, version)
    nueva = con_datos.publicados / version.relativa
    original = con_datos.publicados / "2017-04-10" / "2017-04-10"
    assert (nueva / "pedido.csv").read_bytes() == (original / "pedido.csv").read_bytes()
    metadatos = json.loads((nueva / "metadatos.json").read_text(encoding="utf-8"))
    assert metadatos["archivos_en_cuarentena"][0]["archivo"] == "2017-04-05__2017-05-02.csv"


# --- Revisor ---


def test_el_revisor_no_recalcula_si_no_llego_nada(con_datos):
    assert operacion.hay_trabajo(con_datos)
    procesar_pendientes(con_datos)
    assert operacion.hay_trabajo(con_datos)  # falta el seguimiento de esta revisión
    operacion.marcar_revisado(con_datos)
    assert not operacion.hay_trabajo(con_datos)


def test_un_archivo_nuevo_reactiva_al_revisor(con_datos):
    procesar_pendientes(con_datos)
    operacion.marcar_revisado(con_datos)
    entregar_venta(con_datos, date(2016, 12, 5), date(2017, 5, 9), 500.0)
    assert operacion.hay_trabajo(con_datos)


def test_un_archivo_temporal_no_reactiva_al_revisor(con_datos):
    procesar_pendientes(con_datos)
    operacion.marcar_revisado(con_datos)
    (con_datos.bandeja / "train" / ".en_progreso.tmp").write_bytes(b"x")
    assert not operacion.hay_trabajo(con_datos)


# --- Backfill ---


def test_backfill_emite_cortes_sin_publicar_como_originales(con_datos):
    versiones = operacion.versiones_para_backfill(con_datos, date(2017, 4, 3), date(2017, 4, 17))
    assert versiones == [Version(d, d) for d in (date(2017, 4, 3), date(2017, 4, 10), date(2017, 4, 17))]


def test_backfill_puede_cubrir_fechas_anteriores_a_la_bandeja(con_datos):
    corte = date(2017, 2, 6)
    assert operacion.versiones_para_backfill(con_datos, corte, corte) == [Version(corte, corte)]
    procesar(con_datos, Version(corte, corte))
    pedido = pd.read_csv(con_datos.publicados / "2017-02-06" / "2017-02-06" / "pedido.csv")
    assert (pedido["pedido"] > 0).all()


def test_backfill_reprocesa_lo_publicado_con_lo_ultimo_conocido(con_datos):
    procesar(con_datos, Version(PRIMER_CORTE, PRIMER_CORTE))
    ultima = operacion.ultima_entrega(con_datos)
    assert operacion.versiones_para_backfill(con_datos, PRIMER_CORTE, PRIMER_CORTE) == [Version(PRIMER_CORTE, ultima)]


def test_backfill_con_conocimiento_explicito_y_es_idempotente(con_datos):
    version = Version(PRIMER_CORTE, date(2017, 4, 20))
    assert operacion.versiones_para_backfill(con_datos, PRIMER_CORTE, PRIMER_CORTE, version.conocimiento) == [version]
    procesar(con_datos, version)
    assert operacion.versiones_para_backfill(con_datos, PRIMER_CORTE, PRIMER_CORTE, version.conocimiento) == []


# --- Seguimiento ---


def test_el_seguimiento_compara_con_la_venta_real(con_datos):
    procesar_pendientes(con_datos)
    assert actualizar_seguimiento(con_datos, operacion.ultima_entrega(con_datos)) == 7
    tabla = pd.read_csv(con_datos.seguimiento / "desempeno.csv")
    assert tabla["corte"].tolist()[0] == PRIMER_CORTE.isoformat()
    primero = tabla.iloc[0]
    assert primero["dias_con_venta"] == 7 and primero["completo"]
    semana = [PRIMER_CORTE + timedelta(days=d) for d in range(1, 8)]
    real = sum(venta(d, t, f) for d in semana for t in (1, 2) for f in ("BEVERAGES", "PRODUCE"))
    assert primero["venta"] == pytest.approx(real)
    assert "Seguimiento del desempeño" in (con_datos.seguimiento / "reporte.md").read_text(encoding="utf-8")


def test_el_ultimo_corte_queda_marcado_como_incompleto(con_datos):
    procesar_pendientes(con_datos)
    actualizar_seguimiento(con_datos, operacion.ultima_entrega(con_datos))
    tabla = pd.read_csv(con_datos.seguimiento / "desempeno.csv")
    assert not tabla.iloc[-1]["completo"]


def test_el_seguimiento_usa_la_publicacion_original_y_la_venta_corregida(con_datos):
    procesar_pendientes(con_datos)
    actualizar_seguimiento(con_datos, operacion.ultima_entrega(con_datos))
    antes = pd.read_csv(con_datos.seguimiento / "desempeno.csv").set_index("corte").loc["2017-04-03"]

    dia, entrega = date(2017, 4, 5), date(2017, 5, 2)
    entregar_venta(con_datos, dia, entrega, 5000.0)
    procesar_pendientes(con_datos)
    actualizar_seguimiento(con_datos, operacion.ultima_entrega(con_datos))
    tabla = pd.read_csv(con_datos.seguimiento / "desempeno.csv").set_index("corte")
    # Una fila por corte: las versiones corregidas no se evalúan.
    assert tabla.index.is_unique
    assert tabla.loc["2017-04-03", "pedido"] == antes["pedido"]
    assert tabla.loc["2017-04-03", "venta"] > antes["venta"]


def test_el_seguimiento_es_idempotente(con_datos):
    procesar_pendientes(con_datos)
    actualizar_seguimiento(con_datos, operacion.ultima_entrega(con_datos))
    primero = {r.name: r.read_bytes() for r in con_datos.seguimiento.iterdir()}
    actualizar_seguimiento(con_datos, operacion.ultima_entrega(con_datos))
    assert primero == {r.name: r.read_bytes() for r in con_datos.seguimiento.iterdir()}


def test_el_seguimiento_sin_publicaciones_no_falla(con_datos):
    assert actualizar_seguimiento(con_datos, operacion.ultima_entrega(con_datos)) == 0


# --- Versionado ---


def test_versionar_agrega_y_envia(con_datos, monkeypatch):
    llamadas = []

    class Resultado:
        returncode = 0
        stderr = ""

    procesar(con_datos, Version(PRIMER_CORTE, PRIMER_CORTE))
    monkeypatch.setenv("INVENTARIO_DVC", "dvc-falso")
    monkeypatch.setattr(operacion.subprocess, "run", lambda c, **k: llamadas.append(c) or Resultado())
    objetivos = operacion.versionar(con_datos)
    assert "data/publicados" in objetivos and "data/kaggle" not in objetivos
    assert llamadas == [["dvc-falso", "add", *objetivos], ["dvc-falso", "push"]]


def test_versionar_reporta_un_fallo(con_datos, monkeypatch):
    class Resultado:
        returncode = 1
        stderr = "sin espacio"

    monkeypatch.setattr(operacion.subprocess, "run", lambda c, **k: Resultado())
    with pytest.raises(RuntimeError, match="sin espacio"):
        operacion.versionar(con_datos)


# --- Regla del proyecto: los DAGs solo orquestan ---


@pytest.mark.parametrize("ruta", sorted((RAIZ / "dags").glob("*.py")), ids=lambda r: r.name)
def test_los_dags_no_importan_librerias_de_datos(ruta):
    permitidos = {"airflow", "datetime", "inventario", "os", "pathlib"}
    modulos = set()
    for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Import):
            modulos |= {n.name.split(".")[0] for n in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            modulos.add(nodo.module.split(".")[0])
    assert modulos <= permitidos
