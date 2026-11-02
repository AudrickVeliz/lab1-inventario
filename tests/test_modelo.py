import json
import shutil
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from datos_sinteticos import escribir_kaggle  # noqa: E402

from inventario import entrenamiento, operacion, publicacion  # noqa: E402
from inventario.bandeja import cargar_configuracion  # noqa: E402
from inventario.entrenamiento import decidir, decision_de, entradas_desde_tablas, entrenar_en_fecha, vigente_para  # noqa: E402
from inventario.ingesta import ingerir  # noqa: E402
from inventario.modelo import (  # noqa: E402
    ajustar,
    calcular_pedido_ml,
    caracteristicas,
    errores_fuera_de_muestra,
    evaluar_baseline,
    evaluar_ml,
    origenes_previos,
    predecir,
    pronosticar_ml,
)
from inventario.negocio import GrupoCosto, cargar_negocio  # noqa: E402
from inventario.pedido import calcular_pedido  # noqa: E402
from inventario.pronostico import pronosticar_baseline  # noqa: E402
from inventario.publicacion import Rutas, Version  # noqa: E402
from inventario.seguimiento import actualizar_seguimiento  # noqa: E402

RAIZ = Path(__file__).parents[1]
CORTE = date(2017, 4, 3)
HORIZONTE = 7


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("modelo")
    (raiz / "config").mkdir()
    shutil.copy(RAIZ / "config" / "fuentes.yaml", raiz / "config")
    shutil.copy(RAIZ / "config" / "negocio.yaml", raiz / "config")
    rutas = Rutas(raiz)
    escribir_kaggle(rutas.kaggle)
    operacion.generar_bandeja(rutas)
    return rutas


@pytest.fixture
def rutas(base, tmp_path):
    """Copia aislada: cada prueba puede escribir modelos y publicaciones."""
    destino = tmp_path / "proyecto"
    shutil.copytree(base.raiz, destino)
    return Rutas(destino)


@pytest.fixture(scope="module")
def negocio(base):
    return cargar_negocio(base.config_negocio)


@pytest.fixture(scope="module")
def entradas(base):
    fuentes = cargar_configuracion(base.config_fuentes)
    tablas, _ = ingerir(base.bandeja, base.historico, date(2017, 5, 16), fuentes)
    return entradas_desde_tablas(tablas, fuentes)


@pytest.fixture(scope="module")
def artefacto(entradas):
    origenes = origenes_previos(date(2017, 3, 20), 30, HORIZONTE, 0)
    modelo = ajustar(entradas, origenes[:-4], HORIZONTE, {"max_iter": 30})
    modelo["errores_dia"], modelo["errores_periodo"] = errores_fuera_de_muestra(modelo, entradas, origenes[-4:])
    return modelo


# --- Sin fuga de información ---


def test_las_variables_no_cambian_si_hay_ventas_futuras(entradas):
    pasadas = replace(entradas, ventas=entradas.ventas[entradas.ventas["date"] < pd.Timestamp(CORTE)])
    pd.testing.assert_frame_equal(
        caracteristicas(entradas, CORTE, HORIZONTE),
        caracteristicas(pasadas, CORTE, HORIZONTE),
    )


def test_el_precio_del_viernes_no_se_conoce_hasta_el_lunes(entradas):
    precios = pd.DataFrame({
        "date": pd.to_datetime(["2017-03-30", "2017-03-31"]),
        "dcoilwtico": [50.0, 99.0],
        "entrega": pd.to_datetime(["2017-03-31", "2017-04-03"]),
    })
    prueba = replace(entradas, petroleo=precios)
    assert caracteristicas(prueba, date(2017, 4, 2), HORIZONTE)["petroleo"].iloc[0] == 50.0
    assert caracteristicas(prueba, date(2017, 4, 3), HORIZONTE)["petroleo"].iloc[0] == 99.0


def test_una_venta_del_corte_no_cuenta_en_las_estadisticas(entradas):
    modificadas = entradas.ventas.copy()
    modificadas.loc[modificadas["date"] == pd.Timestamp(CORTE), "sales"] = 1e6
    pd.testing.assert_frame_equal(
        caracteristicas(entradas, CORTE, HORIZONTE),
        caracteristicas(replace(entradas, ventas=modificadas), CORTE, HORIZONTE),
    )


def test_los_origenes_de_entrenamiento_tienen_el_objetivo_conocido():
    origenes = origenes_previos(date(2017, 4, 3), 5, HORIZONTE, 0)
    assert all(o.weekday() == 0 for o in origenes)
    assert origenes == sorted(origenes)
    assert origenes[-1] + timedelta(days=HORIZONTE) < date(2017, 4, 3)


# --- Variables ---


def test_el_feriado_aplica_segun_su_alcance(entradas):
    tiendas = pd.DataFrame({
        "store_nbr": [1, 2], "city": ["Quito", "Loja"], "state": ["Pichincha", "Loja"], "type": ["D", "D"], "cluster": [1, 1],
    })
    feriados = pd.DataFrame({
        "date": pd.to_datetime(["2017-04-04", "2017-04-05", "2017-04-06", "2017-04-07", "2017-04-08"]),
        "type": ["Holiday", "Holiday", "Holiday", "Holiday", "Event"],
        "locale": ["National", "Regional", "Local", "National", "National"],
        "locale_name": ["Ecuador", "Pichincha", "Loja", "Ecuador", "Ecuador"],
        "description": ["a", "b", "c", "d", "e"],
        "transferred": [False, False, False, True, False],
    })
    tabla = caracteristicas(replace(entradas, tiendas=tiendas, feriados=feriados), CORTE, HORIZONTE)

    def valor(dia, tienda, columna="feriado"):
        filas = tabla[(tabla["date"] == dia) & (tabla["store_nbr"] == tienda)]
        return filas[columna].iloc[0]

    assert valor("2017-04-04", 1) == 1 and valor("2017-04-04", 2) == 1  # nacional
    assert valor("2017-04-05", 1) == 1 and valor("2017-04-05", 2) == 0  # regional
    assert valor("2017-04-06", 1) == 0 and valor("2017-04-06", 2) == 1  # local
    assert valor("2017-04-07", 1) == 0  # trasladado: ese día no es feriado
    assert valor("2017-04-08", 1) == 0 and valor("2017-04-08", 1, "evento") == 1


def test_sin_feriados_conocidos_no_falla(entradas):
    vacios = entradas.feriados.iloc[0:0]
    tabla = caracteristicas(replace(entradas, feriados=vacios), CORTE, HORIZONTE)
    assert (tabla["feriado"] == 0).all()


# --- Predicción ---


def test_predice_todas_las_series_y_dias_sin_valores_negativos(artefacto, entradas):
    tabla = predecir(artefacto, entradas, CORTE)
    assert len(tabla) == 2 * 2 * HORIZONTE
    assert (tabla["prediccion"] >= 0).all()
    assert tabla["date"].min() == pd.Timestamp("2017-04-04")


def test_el_ajuste_es_repetible(entradas):
    origenes = origenes_previos(date(2017, 3, 20), 12, HORIZONTE, 0)
    uno = predecir(ajustar(entradas, origenes, HORIZONTE, {"max_iter": 10}), entradas, CORTE)
    dos = predecir(ajustar(entradas, origenes, HORIZONTE, {"max_iter": 10}), entradas, CORTE)
    pd.testing.assert_frame_equal(uno, dos)


def test_una_familia_nueva_no_rompe_la_prediccion(artefacto, entradas):
    nueva = entradas.ventas[entradas.ventas["date"] == "2017-04-02"].assign(family="NUEVA")
    tabla = predecir(artefacto, replace(entradas, ventas=pd.concat([entradas.ventas, nueva], ignore_index=True)), CORTE)
    assert "NUEVA" in set(tabla["family"])
    assert tabla["prediccion"].notna().all()


def test_el_formato_es_igual_al_del_baseline(artefacto, entradas, negocio):
    tiendas = entradas.tiendas["store_nbr"].tolist()
    assert list(pronosticar_ml(artefacto, entradas, CORTE, negocio).columns) == list(
        pronosticar_baseline(entradas.ventas, tiendas, CORTE, negocio).columns
    )
    assert list(calcular_pedido_ml(artefacto, entradas, CORTE, negocio).columns) == list(
        calcular_pedido(entradas.ventas, tiendas, CORTE, negocio).columns
    )


def test_los_cuantiles_no_se_cruzan_ni_son_negativos(artefacto, entradas, negocio):
    tabla = pronosticar_ml(artefacto, entradas, CORTE, negocio)
    assert (tabla["q10"] <= tabla["q50"]).all() and (tabla["q50"] <= tabla["q90"]).all()
    assert (tabla["q10"] >= 0).all()


def _con_errores_conocidos(artefacto, errores):
    """Cada serie recibe los mismos errores, para poder calcular el resultado exacto."""
    series = artefacto["errores_dia"][["store_nbr", "family"]].drop_duplicates()
    filas = series.merge(pd.DataFrame({"error": errores}), how="cross")
    return artefacto | {"errores_dia": filas, "errores_periodo": filas}


def test_los_cuantiles_son_la_prediccion_mas_el_cuantil_de_los_errores(artefacto, entradas, negocio):
    modelo = _con_errores_conocidos(artefacto, [-10.0, 0.0, 10.0])
    central = predecir(modelo, entradas, CORTE).rename(columns={"prediccion": "central"})
    tabla = pronosticar_ml(modelo, entradas, CORTE, negocio).merge(central, on=["date", "store_nbr", "family"])
    assert tabla["q50"].to_numpy() == pytest.approx(tabla["central"].to_numpy())
    assert tabla["q90"].to_numpy() == pytest.approx(tabla["central"].to_numpy() + 8.0)
    assert tabla["q10"].to_numpy() == pytest.approx((tabla["central"] - 8.0).clip(lower=0).to_numpy())


def test_el_pedido_es_el_total_mas_el_cuantil_critico_de_los_errores(artefacto, entradas, negocio):
    modelo = _con_errores_conocidos(artefacto, [0.0, 100.0])
    total = predecir(modelo, entradas, CORTE).groupby(["store_nbr", "family"], as_index=False)["prediccion"].sum()
    tabla = calcular_pedido_ml(modelo, entradas, CORTE, negocio).merge(total, on=["store_nbr", "family"])
    esperado = np.ceil((tabla["prediccion"] + 100.0 * tabla["cuantil_critico"]).round(6))
    assert tabla["pedido"].to_numpy() == pytest.approx(esperado.to_numpy())


def test_cambiar_los_costos_cambia_el_pedido_sin_reentrenar(artefacto, entradas, negocio):
    original = calcular_pedido_ml(artefacto, entradas, CORTE, negocio)
    grupos = {**negocio.grupos, "resto": GrupoCosto("resto", 1, 99), "perecederos": GrupoCosto("perecederos", 1, 99)}
    barato = calcular_pedido_ml(artefacto, entradas, CORTE, replace(negocio, grupos=grupos))
    assert barato["pedido"].sum() < original["pedido"].sum()


def test_un_pedido_siempre_es_entero_y_no_negativo(artefacto, entradas, negocio):
    tabla = calcular_pedido_ml(artefacto, entradas, CORTE, negocio)
    assert tabla["pedido"].dtype == "int64" and (tabla["pedido"] >= 0).all()


def test_las_metricas_son_las_mismas_para_ambos_modelos(artefacto, entradas, negocio):
    origenes = [date(2017, 4, 3), date(2017, 4, 10)]
    assert set(evaluar_baseline(entradas, origenes, negocio)) == set(evaluar_ml(artefacto, entradas, origenes, negocio))


# --- Criterio de reemplazo ---


@pytest.mark.parametrize(
    ("candidato", "vigente", "reemplaza"),
    [
        ({"costo": 90, "cobertura": 0.80}, {"costo": 100, "cobertura": 0.70}, True),
        ({"costo": 98, "cobertura": 0.80}, {"costo": 100, "cobertura": 0.80}, True),  # justo el mínimo
        ({"costo": 99, "cobertura": 0.80}, {"costo": 100, "cobertura": 0.70}, False),  # mejora insuficiente
        ({"costo": 110, "cobertura": 0.80}, {"costo": 100, "cobertura": 0.70}, False),  # empeora el costo
        ({"costo": 90, "cobertura": 0.60}, {"costo": 100, "cobertura": 0.75}, False),  # empeora la cobertura
        ({"costo": 90, "cobertura": 0.95}, {"costo": 100, "cobertura": 0.82}, False),  # demasiado ancha
    ],
)
def test_criterio_de_reemplazo(candidato, vigente, reemplaza):
    assert decidir(candidato, vigente, 0.02, 0.80)[0] is reemplaza


# --- Entrenamiento y modelo vigente ---


def _con_decision(rutas, fecha, vigente, artefacto=None):
    registro = {"fecha": fecha.isoformat(), "modelo_anterior": None, "reemplaza": vigente is not None, "vigente": vigente, "motivo": "prueba"}
    return entrenamiento._guardar(rutas, fecha, registro, artefacto)


def test_sin_entrenamiento_el_vigente_es_el_baseline(rutas):
    assert vigente_para(rutas, CORTE) is None


def test_el_entrenamiento_registra_la_decision_y_las_metricas(rutas):
    decision = entrenar_en_fecha(rutas, CORTE)
    assert decision["fecha"] == CORTE.isoformat()
    assert set(decision["metricas"]) == {"candidato", "vigente", "baseline"}
    assert len(decision["cortes_evaluados"]) == 8
    assert decision["cortes_evaluados"][-1] < CORTE.isoformat()
    assert decision_de(rutas, CORTE) == decision
    assert (rutas.modelos / CORTE.isoformat() / "modelo.joblib").exists() == decision["reemplaza"]


def test_entrenar_es_idempotente(rutas):
    primero = entrenar_en_fecha(rutas, CORTE)
    carpeta = rutas.modelos / CORTE.isoformat()
    antes = {r.name: r.stat().st_mtime_ns for r in carpeta.iterdir()}
    assert entrenar_en_fecha(rutas, CORTE) == primero
    assert antes == {r.name: r.stat().st_mtime_ns for r in carpeta.iterdir()}


def test_con_pocos_datos_sigue_el_vigente(rutas):
    decision = entrenar_en_fecha(rutas, date(2016, 8, 1))
    assert decision["reemplaza"] is False and decision["vigente"] is None
    assert "insuficientes" in decision["motivo"]


def test_un_fallo_de_entrenamiento_no_detiene_la_operacion(rutas, monkeypatch):
    def falla(*_, **__):
        raise RuntimeError("sin memoria")

    monkeypatch.setattr(entrenamiento, "ajustar", falla)
    decision = entrenar_en_fecha(rutas, CORTE)
    assert decision["vigente"] is None and "sin memoria" in decision["motivo"]


def test_vigente_segun_la_fecha_del_corte(rutas, artefacto):
    _con_decision(rutas, date(2017, 4, 3), "2017-04-03", artefacto)
    _con_decision(rutas, date(2017, 5, 1), "2017-04-03")
    _con_decision(rutas, date(2017, 5, 29), "2017-05-29", artefacto)
    assert vigente_para(rutas, date(2017, 3, 27)) is None
    assert vigente_para(rutas, date(2017, 4, 3)) == "2017-04-03"
    assert vigente_para(rutas, date(2017, 5, 8)) == "2017-04-03"
    assert vigente_para(rutas, date(2017, 5, 29)) == "2017-05-29"


def test_se_entrena_cada_cuatro_cortes_y_en_orden(rutas, monkeypatch):
    fechas = []
    monkeypatch.setattr(operacion, "entrenar_en_fecha", lambda r, f: fechas.append(f))
    assert operacion.entrenar_pendientes(rutas) == [date(2017, 4, 3), date(2017, 5, 1)]
    assert fechas == [date(2017, 4, 3), date(2017, 5, 1)]


def test_una_fecha_decidida_no_se_vuelve_a_entrenar(rutas, monkeypatch):
    _con_decision(rutas, date(2017, 4, 3), None)
    fechas = []
    monkeypatch.setattr(operacion, "entrenar_en_fecha", lambda r, f: fechas.append(f))
    operacion.entrenar_pendientes(rutas)
    assert fechas == [date(2017, 5, 1)]


# --- Publicación con el modelo vigente ---


def _publicar(rutas, version):
    publicacion.preparar_version(rutas, version)
    publicacion.publicar_version(rutas, version)
    return rutas.publicados / version.relativa


def _metadatos(carpeta):
    return json.loads((carpeta / "metadatos.json").read_text(encoding="utf-8"))


def test_publica_con_el_modelo_vigente_y_lo_registra(rutas, artefacto):
    _con_decision(rutas, CORTE, CORTE.isoformat(), artefacto)
    carpeta = _publicar(rutas, Version(CORTE, CORTE))
    metadatos = _metadatos(carpeta)
    assert metadatos["modelo"] == publicacion.MODELO_ML
    assert metadatos["modelo_entrenado"] == CORTE.isoformat()
    assert len(metadatos["md5_modelo"]) == 32


def test_sin_modelo_vigente_publica_con_el_baseline(rutas):
    _con_decision(rutas, CORTE, None)
    metadatos = _metadatos(_publicar(rutas, Version(CORTE, CORTE)))
    assert metadatos["modelo"] == publicacion.MODELO
    assert metadatos["modelo_entrenado"] is None


def test_un_modelo_dañado_publica_con_el_baseline_y_lo_registra(rutas, artefacto):
    _con_decision(rutas, CORTE, CORTE.isoformat(), artefacto)
    (rutas.modelos / CORTE.isoformat() / "modelo.joblib").write_bytes(b"no es un modelo")
    carpeta = _publicar(rutas, Version(CORTE, CORTE))
    metadatos = _metadatos(carpeta)
    assert metadatos["modelo"] == publicacion.MODELO
    assert "falla_del_modelo" in metadatos
    assert (carpeta / "pedido.csv").exists()


def test_se_reproduce_una_publicacion_del_modelo(rutas, artefacto):
    _con_decision(rutas, CORTE, CORTE.isoformat(), artefacto)
    version = Version(CORTE, CORTE)
    _publicar(rutas, version)
    assert publicacion.reproducir(rutas, version) == {"modelo": True, "pronostico.csv": True, "pedido.csv": True}


def test_reproducir_avisa_si_el_modelo_no_se_puede_cargar(rutas, artefacto, capsys):
    _con_decision(rutas, CORTE, CORTE.isoformat(), artefacto)
    version = Version(CORTE, CORTE)
    _publicar(rutas, version)
    (rutas.modelos / CORTE.isoformat() / "modelo.joblib").write_bytes(b"no es un modelo")
    assert publicacion.reproducir(rutas, version)["modelo"] is False
    assert publicacion.main(["--corte", CORTE.isoformat(), "--raiz", str(rutas.raiz)]) == 1
    assert "instala las dependencias" in capsys.readouterr().out


def test_con_datos_preparados_predice_igual_que_con_toda_la_historia(rutas, artefacto, entradas):
    """La carpeta de procesados contiene todo lo que el modelo necesita."""
    _con_decision(rutas, CORTE, CORTE.isoformat(), artefacto)
    carpeta = _publicar(rutas, Version(CORTE, CORTE))
    negocio = cargar_negocio(rutas.config_negocio)
    fuentes = cargar_configuracion(rutas.config_fuentes)
    tablas, _ = ingerir(rutas.bandeja, rutas.historico, CORTE, fuentes)
    completas = entradas_desde_tablas(tablas, fuentes)
    esperado = calcular_pedido_ml(artefacto, completas, CORTE, negocio)
    publicado = pd.read_csv(carpeta / "pedido.csv")
    assert np.array_equal(esperado["pedido"].to_numpy(), publicado["pedido"].to_numpy())


def test_una_correccion_usa_el_modelo_vigente_en_su_corte(rutas, artefacto):
    _con_decision(rutas, CORTE, CORTE.isoformat(), artefacto)
    corte = date(2017, 4, 10)
    _publicar(rutas, Version(corte, corte))
    # Un modelo entrenado después de esa fecha no debe usarse para ese corte.
    _con_decision(rutas, date(2017, 5, 1), None)
    metadatos = _metadatos(_publicar(rutas, Version(corte, date(2017, 5, 2))))
    assert metadatos["modelo_entrenado"] == CORTE.isoformat()


# --- Comparación en el seguimiento ---


def test_el_seguimiento_compara_con_el_baseline(rutas, artefacto):
    _con_decision(rutas, CORTE, CORTE.isoformat(), artefacto)
    _publicar(rutas, Version(CORTE, CORTE))
    actualizar_seguimiento(rutas, operacion.ultima_entrega(rutas))
    tabla = pd.read_csv(rutas.seguimiento / "desempeno.csv").iloc[0]
    assert tabla["modelo"] == publicacion.MODELO_ML
    assert tabla["costo_baseline"] >= 0 and not np.isnan(tabla["wape_baseline"])
    reporte = (rutas.seguimiento / "reporte.md").read_text(encoding="utf-8")
    assert "Decisiones de modelo" in reporte and "Baseline con los mismos datos" in reporte


def test_el_seguimiento_del_baseline_coincide_con_su_referencia(rutas):
    _publicar(rutas, Version(CORTE, CORTE))
    actualizar_seguimiento(rutas, operacion.ultima_entrega(rutas))
    tabla = pd.read_csv(rutas.seguimiento / "desempeno.csv").iloc[0]
    assert tabla["costo"] == pytest.approx(tabla["costo_baseline"])
    assert tabla["wape_q50"] == pytest.approx(tabla["wape_baseline"])


def test_la_comparacion_de_modelos_se_escribe(rutas):
    entrenar_en_fecha(rutas, CORTE)
    _publicar(rutas, Version(CORTE, CORTE))
    actualizar_seguimiento(rutas, operacion.ultima_entrega(rutas))
    tabla = pd.read_csv(rutas.seguimiento / "comparacion_modelos.csv")
    assert set(tabla["modelo"]) == {"candidato", "vigente", "baseline"}
    assert {"costo", "wape", "cobertura"} <= set(tabla.columns)
