import gzip
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from inventario.bandeja import (
    ConflictoBandeja,
    ErrorOrigen,
    archivos_disponibles,
    cargar_configuracion,
    fecha_entrega,
    generar,
    main,
    nombres_invalidos,
)

CONFIG_REAL = Path(__file__).parents[1] / "config" / "fuentes.yaml"


@pytest.fixture
def config():
    return cargar_configuracion(CONFIG_REAL)


@pytest.fixture
def rutas(tmp_path):
    """CSV pequeños con el formato de Kaggle, alrededor del corte del 2017-03-31 (viernes)."""
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "train.csv").write_text(
        "id,date,store_nbr,family,sales,onpromotion\n"
        "1,2017-03-30,1,BEVERAGES,10.0,0\n"
        "2,2017-03-31,1,BEVERAGES,20.0,1\n"
        "3,2017-04-01,1,BEVERAGES,30.0,0\n"
        "4,2017-04-01,2,BEVERAGES,40.0,0\n"
    )
    (origen / "transactions.csv").write_text(
        "date,store_nbr,transactions\n"
        "2017-03-29,1,100\n"
        "2017-03-30,1,110\n"
    )
    (origen / "oil.csv").write_text(
        "date,dcoilwtico\n"
        "2017-03-30,50.1\n"
        "2017-03-31,\n"
    )
    (origen / "holidays_events.csv").write_text(
        "date,type,locale,locale_name,description,transferred\n"
        "2017-04-14,Holiday,National,Ecuador,Viernes Santo,False\n"
        "2017-05-14,Event,National,Ecuador,Dia de la Madre,False\n"
        "2017-05-14,Event,National,Ecuador,Otro evento,False\n"
    )
    (origen / "stores.csv").write_text(
        "store_nbr,city,state,type,cluster\n"
        "1,Quito,Pichincha,D,13\n"
    )
    return origen, tmp_path / "bandeja", tmp_path / "historico"


def nombres(bandeja, fuente):
    return sorted(ruta.name for ruta in (bandeja / fuente).iterdir())


def historico_csv(historico, fuente):
    return pd.read_csv(historico / f"{fuente}.csv.gz", dtype=str, keep_default_na=False)


@pytest.mark.parametrize(
    ("fuente", "dato", "esperada"),
    [
        ("train", date(2017, 3, 31), date(2017, 4, 1)),
        ("transactions", date(2017, 3, 31), date(2017, 4, 2)),
        ("oil", date(2017, 3, 30), date(2017, 3, 31)),
        ("oil", date(2017, 3, 31), date(2017, 4, 3)),  # viernes: llega el lunes
        ("holidays_events", date(2017, 4, 30), date(2017, 3, 31)),
    ],
)
def test_fecha_entrega_segun_la_fuente(config, fuente, dato, esperada):
    assert fecha_entrega(config.fuentes[fuente], dato) == esperada


def test_cambiar_la_configuracion_cambia_la_entrega(config):
    regla = replace(config.fuentes["train"], retraso_dias=5)
    assert fecha_entrega(regla, date(2017, 3, 31)) == date(2017, 4, 5)


def test_divide_segun_la_fecha_de_entrega(rutas, config):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)

    # El dato del 31 no había llegado el 31: va a la bandeja aunque su fecha sea del corte.
    assert historico_csv(historico, "train")["date"].tolist() == ["2017-03-30"]
    assert nombres(bandeja, "train") == [
        "2017-03-31__2017-04-01.csv",
        "2017-04-01__2017-04-02.csv",
    ]
    assert historico_csv(historico, "transactions")["date"].tolist() == ["2017-03-29"]
    assert nombres(bandeja, "transactions") == ["2017-03-30__2017-04-01.csv"]
    assert nombres(bandeja, "oil") == ["2017-03-31__2017-04-03.csv"]

    # Un feriado futuro ya anunciado es parte de la carga histórica.
    assert historico_csv(historico, "holidays_events")["date"].tolist() == ["2017-04-14"]
    assert nombres(bandeja, "holidays_events") == ["2017-05-14__2017-04-14.csv"]


def test_cada_fecha_conserva_todas_sus_filas(rutas, config):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    assert len(pd.read_csv(bandeja / "train" / "2017-04-01__2017-04-02.csv")) == 2
    assert len(pd.read_csv(bandeja / "holidays_events" / "2017-05-14__2017-04-14.csv")) == 2


def test_no_se_pierde_ninguna_fila(rutas, config):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    original = pd.read_csv(origen / "train.csv", dtype=str)
    partes = [historico_csv(historico, "train")]
    partes += [pd.read_csv(r, dtype=str) for r in sorted((bandeja / "train").iterdir())]
    pd.testing.assert_frame_equal(
        pd.concat(partes).sort_values("id").reset_index(drop=True),
        original.sort_values("id").reset_index(drop=True),
    )


def test_conserva_el_dato_tal_como_llego(rutas, config):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    assert (bandeja / "oil" / "2017-03-31__2017-04-03.csv").read_bytes() == (
        b"date,dcoilwtico\n2017-03-31,\n"
    )
    assert historico_csv(historico, "train")["sales"].tolist() == ["10.0"]


def test_el_catalogo_solo_va_al_historico(rutas, config):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    assert not (bandeja / "stores").exists()
    assert historico_csv(historico, "stores")["city"].tolist() == ["Quito"]


def test_generar_es_idempotente(rutas, config):
    origen, bandeja, historico = rutas
    primero = generar(origen, bandeja, historico, config)
    archivos = sorted(p for raiz in (bandeja, historico) for p in raiz.rglob("*") if p.is_file())
    estado = {ruta: (ruta.read_bytes(), ruta.stat().st_mtime_ns) for ruta in archivos}

    segundo = generar(origen, bandeja, historico, config)

    assert primero.escritos == len(archivos) and primero.sin_cambios == 0
    assert segundo.escritos == 0 and segundo.sin_cambios == len(archivos)
    assert estado == {r: (r.read_bytes(), r.stat().st_mtime_ns) for r in archivos}


def test_el_historico_es_determinista(rutas, config, tmp_path):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    otro = tmp_path / "otro"
    generar(origen, bandeja, otro, config)
    assert (historico / "train.csv.gz").read_bytes() == (otro / "train.csv.gz").read_bytes()


def test_no_toca_los_archivos_de_origen(rutas, config):
    origen, bandeja, historico = rutas
    antes = {r: r.read_bytes() for r in origen.iterdir()}
    generar(origen, bandeja, historico, config)
    assert antes == {r: r.read_bytes() for r in origen.iterdir()}


def test_un_conflicto_detiene_todo_sin_escribir(rutas, config):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    ruta = origen / "train.csv"
    ruta.write_text(
        ruta.read_text().replace("3,2017-04-01,1,BEVERAGES,30.0", "3,2017-04-01,1,BEVERAGES,99.0")
        + "5,2017-04-02,1,BEVERAGES,50.0,0\n"
    )
    entregado = bandeja / "train" / "2017-04-01__2017-04-02.csv"
    antes = entregado.read_bytes()

    with pytest.raises(ConflictoBandeja, match="entrega posterior"):
        generar(origen, bandeja, historico, config)

    assert entregado.read_bytes() == antes
    assert not (bandeja / "train" / "2017-04-02__2017-04-03.csv").exists()


def test_acepta_un_periodo_nuevo_con_el_formato_de_kaggle(rutas, config, tmp_path):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    entrante = tmp_path / "entrante"
    entrante.mkdir()
    (entrante / "train.csv").write_text(
        "id,date,store_nbr,family,sales,onpromotion\n"
        "9,2017-08-16,1,BEVERAGES,70.0,0\n"
    )
    resumen = generar(entrante, bandeja, historico, config)
    assert resumen.escritos == 1
    assert "2017-08-16__2017-08-17.csv" in nombres(bandeja, "train")
    assert historico_csv(historico, "train")["date"].tolist() == ["2017-03-30"]


def test_detiene_columnas_incorrectas(rutas, config):
    origen, bandeja, historico = rutas
    (origen / "oil.csv").write_text("fecha,precio\n2017-04-03,50\n")
    with pytest.raises(ErrorOrigen, match="oil.csv"):
        generar(origen, bandeja, historico, config)
    assert not bandeja.exists()


def test_detiene_fechas_invalidas(rutas, config):
    origen, bandeja, historico = rutas
    (origen / "oil.csv").write_text("date,dcoilwtico\n2017-13-45,50\n")
    with pytest.raises(ErrorOrigen, match="fecha inválida"):
        generar(origen, bandeja, historico, config)
    assert not bandeja.exists()


def _entregar(bandeja, fuente, *nombres_archivo):
    carpeta = bandeja / fuente
    carpeta.mkdir(parents=True)
    for nombre in nombres_archivo:
        (carpeta / nombre).write_text("contenido\n")


def test_no_ve_lo_que_llega_despues_del_corte(tmp_path):
    _entregar(tmp_path, "train", "2017-04-01__2017-04-02.csv", "2017-04-02__2017-04-03.csv")
    disponibles = archivos_disponibles(tmp_path, "train", date(2017, 4, 2))
    assert [a.fecha_dato for a in disponibles] == [date(2017, 4, 1)]


def test_una_correccion_solo_rige_desde_su_entrega(tmp_path):
    _entregar(tmp_path, "train", "2017-04-01__2017-04-02.csv", "2017-04-01__2017-04-10.csv")

    antes = archivos_disponibles(tmp_path, "train", date(2017, 4, 9))
    despues = archivos_disponibles(tmp_path, "train", date(2017, 4, 10))

    assert [a.fecha_entrega for a in antes] == [date(2017, 4, 2)]
    assert [a.fecha_entrega for a in despues] == [date(2017, 4, 10)]


def test_un_feriado_anunciado_se_conoce_antes_de_su_fecha(tmp_path):
    _entregar(tmp_path, "holidays_events", "2017-05-14__2017-04-14.csv")
    assert archivos_disponibles(tmp_path, "holidays_events", date(2017, 4, 13)) == []
    disponibles = archivos_disponibles(tmp_path, "holidays_events", date(2017, 4, 14))
    assert [a.fecha_dato for a in disponibles] == [date(2017, 5, 14)]


def test_una_fuente_que_no_llega_no_falla(tmp_path):
    assert archivos_disponibles(tmp_path, "oil", date(2017, 4, 1)) == []
    assert nombres_invalidos(tmp_path, "oil") == []


@pytest.mark.parametrize(
    "nombre",
    ["ventas.csv", "2017-04-01.csv", "2017-13-45__2017-04-02.csv", "2017-04-01__2017-04-02.txt"],
)
def test_reporta_nombres_invalidos_sin_usarlos(tmp_path, nombre):
    _entregar(tmp_path, "train", nombre)
    assert archivos_disponibles(tmp_path, "train", date(2030, 1, 1)) == []
    assert [r.name for r in nombres_invalidos(tmp_path, "train")] == [nombre]


def test_leer_no_modifica_la_bandeja(tmp_path):
    _entregar(tmp_path, "train", "2017-04-01__2017-04-02.csv", "2017-04-01__2017-04-10.csv")
    antes = {r: (r.read_bytes(), r.stat().st_mtime_ns) for r in tmp_path.rglob("*") if r.is_file()}
    archivos_disponibles(tmp_path, "train", date(2017, 4, 5))
    nombres_invalidos(tmp_path, "train")
    assert antes == {r: (r.read_bytes(), r.stat().st_mtime_ns) for r in tmp_path.rglob("*") if r.is_file()}


def test_comando_reporta_error_sin_traza(rutas, capsys):
    origen, bandeja, historico = rutas
    (origen / "oil.csv").write_text("fecha,precio\n2017-04-03,50\n")
    codigo = main([
        "--origen", str(origen),
        "--bandeja", str(bandeja),
        "--historico", str(historico),
        "--config", str(CONFIG_REAL),
    ])
    assert codigo == 1
    assert "ERROR" in capsys.readouterr().err


def test_el_historico_esta_comprimido(rutas, config):
    origen, bandeja, historico = rutas
    generar(origen, bandeja, historico, config)
    assert gzip.decompress((historico / "stores.csv.gz").read_bytes()).startswith(b"store_nbr,")
