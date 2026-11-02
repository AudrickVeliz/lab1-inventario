import json
from datetime import date
from pathlib import Path

import pytest

from inventario.bandeja import cargar_configuracion, generar
from inventario.ingesta import ErrorHistorico, escribir_reporte, ingerir, main

CONFIG_REAL = Path(__file__).parents[1] / "config" / "fuentes.yaml"
ENCABEZADO = "id,date,store_nbr,family,sales,onpromotion\n"
CORTE = date(2017, 4, 30)


@pytest.fixture
def config():
    return cargar_configuracion(CONFIG_REAL)


@pytest.fixture
def entorno(tmp_path, config):
    """Dos tiendas y dos familias: un día completo tiene 4 filas."""
    origen = tmp_path / "origen"
    origen.mkdir()
    filas = []
    identificador = 0
    for dia in ("2017-03-29", "2017-03-30", "2017-03-31", "2017-04-01"):
        for tienda in (1, 2):
            for familia, venta in (("BEVERAGES", 10.0), ("DAIRY", 20.0)):
                identificador += 1
                filas.append(f"{identificador},{dia},{tienda},{familia},{venta},0\n")
    (origen / "train.csv").write_bytes((ENCABEZADO + "".join(filas)).encode())
    (origen / "stores.csv").write_bytes(b"store_nbr,city,state,type,cluster\n1,Quito,Pichincha,D,13\n2,Quito,Pichincha,D,13\n")
    (origen / "transactions.csv").write_bytes(b"date,store_nbr,transactions\n2017-03-20,1,100\n")
    (origen / "oil.csv").write_bytes(b"date,dcoilwtico\n2017-03-20,50.5\n")
    (origen / "holidays_events.csv").write_bytes(
        b"date,type,locale,locale_name,description,transferred\n2017-04-14,Holiday,National,Ecuador,Viernes Santo,False\n"
    )
    bandeja, historico = tmp_path / "bandeja", tmp_path / "historico"
    generar(origen, bandeja, historico, config)
    return bandeja, historico


def entregar(bandeja, fuente, nombre, contenido):
    carpeta = bandeja / fuente
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / nombre
    ruta.write_bytes(contenido if isinstance(contenido, bytes) else contenido.encode())
    return ruta


def ventas(tabla, dia, tienda=1, familia="BEVERAGES"):
    filas = tabla[(tabla["date"] == dia) & (tabla["store_nbr"] == tienda) & (tabla["family"] == familia)]
    return filas["sales"].tolist()


def en_cuarentena(reporte):
    return {a.archivo: a.motivos for a in reporte.cuarentena}


def tipos_de_aviso(reporte):
    return [a.tipo for a in reporte.avisos]


# --- Datos inválidos: se detienen ---


@pytest.mark.parametrize(
    ("contenido", "fragmento"),
    [
        (b"", "ilegible"),
        (b"\xff\xfe\x00no es texto", "ilegible"),
        (ENCABEZADO + '1,2017-04-05,1,"sin cerrar,1.0,0\n', "ilegible"),
        ("a,b\n1,2\n", "columnas incorrectas"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,abc,0\n", "sales"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,nan,0\n", "sales"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,inf,0\n", "sales"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,,0\n", "sales"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,-5.0,0\n", "negativo"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5.0,-1\n", "negativo"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5.0,1.5\n", "onpromotion"),
        (ENCABEZADO + "1,05/04/2017,1,BEVERAGES,5.0,0\n", "date"),
        (ENCABEZADO + "1,2017-04-06,1,BEVERAGES,5.0,0\n", "fecha distinta"),
        (ENCABEZADO + "1,2017-04-05,99,BEVERAGES,5.0,0\n", "fuera del catálogo"),
        (ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5.0,0\n2,2017-04-05,1,BEVERAGES,6.0,0\n", "clave"),
    ],
)
def test_detiene_datos_invalidos(entorno, config, contenido, fragmento):
    bandeja, historico = entorno
    nombre = "2017-04-05__2017-04-06.csv"
    entregar(bandeja, "train", nombre, contenido)

    tablas, reporte = ingerir(bandeja, historico, CORTE, config)

    assert fragmento in " ".join(en_cuarentena(reporte)[nombre])
    assert ventas(tablas["train"], "2017-04-05") == []
    assert ventas(tablas["train"], "2017-04-01") == [10.0]


def test_detiene_una_entrega_anterior_al_dato(entorno, config):
    bandeja, historico = entorno
    nombre = "2017-04-05__2017-04-04.csv"
    entregar(bandeja, "train", nombre, ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5.0,0\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert "anterior" in en_cuarentena(reporte)[nombre][0]
    assert ventas(tablas["train"], "2017-04-05") == []


@pytest.mark.parametrize(
    ("fuente", "nombre", "contenido"),
    [
        ("transactions", "2017-04-05__2017-04-07.csv", "date,store_nbr,transactions\n2017-04-05,1,-3\n"),
        ("oil", "2017-04-05__2017-04-06.csv", "date,dcoilwtico\n2017-04-05,-1.0\n"),
        ("holidays_events", "2017-06-01__2017-04-20.csv",
         "date,type,locale,locale_name,description,transferred\n2017-06-01,Holiday,National,Ecuador,Fiesta,Tal vez\n"),
    ],
)
def test_detiene_datos_invalidos_de_otras_fuentes(entorno, config, fuente, nombre, contenido):
    bandeja, historico = entorno
    entregar(bandeja, fuente, nombre, contenido)
    _, reporte = ingerir(bandeja, historico, CORTE, config)
    assert nombre in en_cuarentena(reporte)


def test_reporta_nombres_fuera_de_convencion_y_continua(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "ventas_finales.csv", "x\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert "ventas_finales.csv" in en_cuarentena(reporte)
    assert not tablas["train"].empty


def test_un_catalogo_de_tiendas_actualizado_acepta_la_tienda_nueva(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "stores", "2017-04-05__2017-04-05.csv", "store_nbr,city,state,type,cluster\n99,Loja,Loja,A,1\n")
    entregar(bandeja, "train", "2017-04-06__2017-04-07.csv", ENCABEZADO + "1,2017-04-06,99,BEVERAGES,5.0,0\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert reporte.cuarentena == []
    assert ventas(tablas["train"], "2017-04-06", tienda=99) == [5.0]
    assert sorted(tablas["stores"]["store_nbr"]) == [1, 2, 99]


# --- Situaciones esperadas: no fallan ---


def test_una_fuente_sin_entregas_no_falla(tmp_path, entorno, config):
    _, historico = entorno
    tablas, reporte = ingerir(tmp_path / "vacia", historico, CORTE, config)
    assert reporte.cuarentena == []
    assert reporte.fuentes["train"].archivos_usados == 0
    assert not tablas["train"].empty


def test_un_precio_vacio_no_es_invalido(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "oil", "2017-04-05__2017-04-06.csv", "date,dcoilwtico\n2017-04-05,\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert reporte.cuarentena == [] and reporte.avisos == []
    fila = tablas["oil"][tablas["oil"]["date"] == "2017-04-05"]
    assert fila["dcoilwtico"].isna().all() and len(fila) == 1


def test_una_venta_cero_no_genera_aviso(entorno, config):
    bandeja, historico = entorno
    filas = "".join(
        f"{i},2017-04-05,{t},{f},0.0,0\n"
        for i, (t, f) in enumerate([(1, "BEVERAGES"), (1, "DAIRY"), (2, "BEVERAGES"), (2, "DAIRY")])
    )
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO + filas)
    _, reporte = ingerir(bandeja, historico, CORTE, config)
    assert reporte.cuarentena == [] and reporte.avisos == []


def test_un_feriado_anunciado_se_acepta_antes_de_su_fecha(entorno, config):
    bandeja, historico = entorno
    entregar(
        bandeja, "holidays_events", "2017-05-14__2017-04-14.csv",
        "date,type,locale,locale_name,description,transferred\n2017-05-14,Event,National,Ecuador,Dia,False\n",
    )
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert reporte.cuarentena == []
    assert "2017-05-14" in tablas["holidays_events"]["date"].astype(str).tolist()


# --- Avisos: se reportan, pero el dato se conserva ---


def test_filas_duplicadas_exactas_generan_aviso_y_se_conserva_una(entorno, config):
    bandeja, historico = entorno
    fila = "1,2017-04-05,1,BEVERAGES,5.0,0\n"
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO + fila + fila)
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert reporte.cuarentena == []
    assert "filas_duplicadas" in tipos_de_aviso(reporte)
    assert ventas(tablas["train"], "2017-04-05") == [5.0]


def test_familia_nueva_genera_aviso_y_se_conserva(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO + "1,2017-04-05,1,NUEVA,5.0,0\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert "familia_nueva" in tipos_de_aviso(reporte)
    assert ventas(tablas["train"], "2017-04-05", familia="NUEVA") == [5.0]


def test_archivo_incompleto_genera_aviso_y_no_inventa_ceros(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5.0,0\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert "archivo_incompleto" in tipos_de_aviso(reporte)
    assert (tablas["train"]["date"] == "2017-04-05").sum() == 1


def test_archivo_solo_con_encabezado_genera_aviso(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO)
    _, reporte = ingerir(bandeja, historico, CORTE, config)
    assert reporte.cuarentena == []
    assert "archivo_incompleto" in tipos_de_aviso(reporte)


def test_venta_muy_alta_genera_aviso_y_se_conserva(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5000.0,0\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert "venta_muy_alta" in tipos_de_aviso(reporte)
    assert ventas(tablas["train"], "2017-04-05") == [5000.0]


def test_el_umbral_de_venta_vive_en_la_configuracion(entorno, config):
    from dataclasses import replace

    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5000.0,0\n")
    _, reporte = ingerir(bandeja, historico, CORTE, replace(config, umbral_venta_sobre_mediana=1000))
    assert "venta_muy_alta" not in tipos_de_aviso(reporte)


# --- Disponibilidad en el tiempo ---


def test_no_usa_lo_entregado_despues_del_corte(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-05__2017-04-06.csv", ENCABEZADO + "1,2017-04-05,1,BEVERAGES,5.0,0\n")

    antes, reporte_antes = ingerir(bandeja, historico, date(2017, 4, 5), config)
    despues, _ = ingerir(bandeja, historico, date(2017, 4, 6), config)

    assert ventas(antes["train"], "2017-04-05") == []
    assert reporte_antes.fuentes["train"].ultima_fecha_dato == "2017-04-01"
    assert ventas(despues["train"], "2017-04-05") == [5.0]


def test_un_archivo_invalido_futuro_no_afecta_un_corte_anterior(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-05__2017-04-20.csv", b"")
    _, reporte = ingerir(bandeja, historico, date(2017, 4, 19), config)
    assert reporte.cuarentena == []


def test_un_backfill_anterior_al_corte_historico_no_ve_datos_futuros(entorno, config):
    bandeja, historico = entorno
    tablas, _ = ingerir(bandeja, historico, date(2017, 3, 30), config)
    assert tablas["train"]["date"].max().date() == date(2017, 3, 29)


def test_los_feriados_futuros_ya_anunciados_son_visibles(entorno, config):
    bandeja, historico = entorno
    tablas, _ = ingerir(bandeja, historico, date(2017, 3, 31), config)
    assert tablas["holidays_events"]["date"].max().date() == date(2017, 4, 14)


# --- Correcciones ---


def test_una_correccion_parcial_conserva_las_demas_filas(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-01__2017-04-10.csv", ENCABEZADO + "9,2017-04-01,1,BEVERAGES,99.0,0\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert ventas(tablas["train"], "2017-04-01") == [99.0]
    assert ventas(tablas["train"], "2017-04-01", familia="DAIRY") == [20.0]
    assert (tablas["train"]["date"] == "2017-04-01").sum() == 4
    assert reporte.fuentes["train"].correcciones == 1
    assert "archivo_incompleto" not in tipos_de_aviso(reporte)


def test_una_correccion_solo_rige_desde_su_entrega(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-01__2017-04-10.csv", ENCABEZADO + "9,2017-04-01,1,BEVERAGES,99.0,0\n")
    antes, _ = ingerir(bandeja, historico, date(2017, 4, 9), config)
    despues, _ = ingerir(bandeja, historico, date(2017, 4, 10), config)
    assert ventas(antes["train"], "2017-04-01") == [10.0]
    assert ventas(despues["train"], "2017-04-01") == [99.0]


def test_rige_la_correccion_mas_reciente(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-04-01__2017-04-20.csv", ENCABEZADO + "9,2017-04-01,1,BEVERAGES,2.0,0\n")
    entregar(bandeja, "train", "2017-04-01__2017-04-10.csv", ENCABEZADO + "9,2017-04-01,1,BEVERAGES,1.0,0\n")
    tablas, _ = ingerir(bandeja, historico, CORTE, config)
    assert ventas(tablas["train"], "2017-04-01") == [2.0]


def test_una_correccion_de_la_carga_historica_prevalece(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "2017-03-29__2017-04-10.csv", ENCABEZADO + "9,2017-03-29,1,BEVERAGES,77.0,0\n")
    tablas, _ = ingerir(bandeja, historico, CORTE, config)
    assert ventas(tablas["train"], "2017-03-29") == [77.0]


def test_una_correccion_invalida_conserva_el_dato_anterior(entorno, config):
    bandeja, historico = entorno
    nombre = "2017-04-01__2017-04-10.csv"
    entregar(bandeja, "train", nombre, ENCABEZADO + "9,2017-04-01,1,BEVERAGES,-99.0,0\n")
    tablas, reporte = ingerir(bandeja, historico, CORTE, config)
    assert nombre in en_cuarentena(reporte)
    assert ventas(tablas["train"], "2017-04-01") == [10.0]


# --- Operación ---


def test_ingerir_no_modifica_la_bandeja(entorno, config):
    bandeja, historico = entorno
    entregar(bandeja, "train", "malo.csv", b"")
    def estado():
        return {r: (r.read_bytes(), r.stat().st_mtime_ns) for r in bandeja.rglob("*")if r.is_file()}
    antes = estado()
    ingerir(bandeja, historico, CORTE, config)
    assert estado() == antes


def test_el_reporte_es_idempotente(entorno, config, tmp_path):
    bandeja, historico = entorno
    entregar(bandeja, "train", "malo.csv", b"")
    carpeta = tmp_path / "reportes"
    _, reporte = ingerir(bandeja, historico, CORTE, config)
    primero = escribir_reporte(reporte, carpeta).read_bytes()
    _, reporte = ingerir(bandeja, historico, CORTE, config)
    assert escribir_reporte(reporte, carpeta).read_bytes() == primero


def test_el_reporte_no_incluye_rutas_locales(entorno, config, tmp_path):
    bandeja, historico = entorno
    entregar(bandeja, "train", "malo.csv", b"")
    _, reporte = ingerir(bandeja, historico, CORTE, config)
    texto = escribir_reporte(reporte, tmp_path / "reportes").read_text(encoding="utf-8")
    assert str(tmp_path) not in texto
    assert json.loads(texto)["cuarentena"][0]["archivo"] == "malo.csv"


def test_una_carga_historica_invalida_detiene_el_proceso(entorno, config):
    bandeja, historico = entorno
    (historico / "train.csv.gz").write_bytes(b"esto no es gzip")
    with pytest.raises(ErrorHistorico, match="train.csv.gz"):
        ingerir(bandeja, historico, CORTE, config)


def test_sin_catalogo_de_tiendas_detiene_el_proceso(entorno, config):
    bandeja, historico = entorno
    (historico / "stores.csv.gz").unlink()
    with pytest.raises(ErrorHistorico, match="stores.csv.gz"):
        ingerir(bandeja, historico, CORTE, config)


def _argumentos(entorno, tmp_path):
    bandeja, historico = entorno
    return [
        "--fecha-corte", CORTE.isoformat(),
        "--bandeja", str(bandeja),
        "--historico", str(historico),
        "--reportes", str(tmp_path / "reportes"),
        "--config", str(CONFIG_REAL),
    ]


def test_comando_termina_bien_con_archivos_en_cuarentena(entorno, tmp_path, capsys):
    entregar(entorno[0], "train", "malo.csv", b"")
    assert main(_argumentos(entorno, tmp_path)) == 0
    assert "1 en cuarentena" in capsys.readouterr().out
    assert (tmp_path / "reportes" / f"{CORTE.isoformat()}.json").exists()


def test_comando_falla_si_la_base_es_invalida(entorno, tmp_path, capsys):
    (entorno[1] / "stores.csv.gz").unlink()
    assert main(_argumentos(entorno, tmp_path)) == 1
    assert "ERROR" in capsys.readouterr().err
