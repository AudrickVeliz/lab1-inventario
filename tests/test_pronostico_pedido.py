from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from inventario.negocio import GrupoCosto, cargar_negocio
from inventario.pedido import calcular_pedido, costo_del_pedido, unidades_a_pedir
from inventario.pronostico import nombre_cuantil, pronosticar_baseline

CONFIG_REAL = Path(__file__).parents[1] / "config" / "negocio.yaml"
CORTE = date(2017, 4, 3)  # lunes
BEB, PRODUCE = "BEVERAGES", "PRODUCE"


@pytest.fixture
def config():
    return cargar_negocio(CONFIG_REAL)


def historia(valor, fin=date(2017, 4, 2), dias=140, tienda=1, familia=BEB):
    """Ventas diarias que terminan en ``fin``; ``valor`` recibe la fecha."""
    fechas = [fin - timedelta(days=i) for i in range(dias)][::-1]
    return pd.DataFrame({
        "date": pd.to_datetime(fechas),
        "store_nbr": tienda,
        "family": familia,
        "sales": [float(valor(f)) for f in fechas],
    })


def fila(tabla, dia, tienda=1, familia=BEB):
    resultado = tabla[(tabla["date"] == pd.Timestamp(dia)) & (tabla["store_nbr"] == tienda) & (tabla["family"] == familia)]
    assert len(resultado) == 1
    return resultado.iloc[0]


def pedido_de(tabla, tienda=1, familia=BEB):
    resultado = tabla[(tabla["store_nbr"] == tienda) & (tabla["family"] == familia)]
    assert len(resultado) == 1
    return resultado.iloc[0]


# --- Configuración ---


def test_la_configuracion_refleja_los_costos_acordados(config):
    assert config.grupo_de(PRODUCE).cuantil_critico == pytest.approx(0.60)
    assert config.grupo_de(BEB).cuantil_critico == pytest.approx(3 / 3.5)
    assert len(config.familia_a_grupo) == 9
    assert config.grupo_de("FAMILIA NUEVA").nombre == "resto"


@pytest.mark.parametrize(
    ("original", "reemplazo", "mensaje"),
    [
        ("merma: 2", "merma: 0", "positivos"),
        ("quiebre: 3\n      merma: 0.5", "quiebre: -1\n      merma: 0.5", "positivos"),
        ("[0.10, 0.50, 0.90]", "[0.10, 1.50]", "cuantiles"),
        ("grupo_predeterminado: resto", "grupo_predeterminado: otro", "predeterminado"),
        ("horizonte_dias: 7", "horizonte_dias: 0", "horizonte"),
        ("        - DAIRY\n", "        - DAIRY\n        - DAIRY\n", "más de un grupo"),
    ],
)
def test_una_configuracion_invalida_se_rechaza(tmp_path, original, reemplazo, mensaje):
    texto = CONFIG_REAL.read_text(encoding="utf-8")
    assert original in texto
    ruta = tmp_path / "negocio.yaml"
    ruta.write_text(texto.replace(original, reemplazo), encoding="utf-8")
    with pytest.raises(ValueError, match=mensaje):
        cargar_negocio(ruta)


# --- Pronóstico ---


def test_cubre_de_martes_a_lunes(config):
    tabla = pronosticar_baseline(historia(lambda f: 1), [1], CORTE, config)
    assert tabla["date"].min() == pd.Timestamp("2017-04-04")
    assert tabla["date"].max() == pd.Timestamp("2017-04-10")
    assert tabla["date"].nunique() == 7 and len(tabla) == 7
    assert (tabla["fecha_corte"] == pd.Timestamp(CORTE)).all()


def test_usa_solo_el_mismo_dia_de_la_semana(config):
    ventas = historia(lambda f: 100 if f.weekday() == 5 else 10)
    tabla = pronosticar_baseline(ventas, [1], CORTE, config)
    assert fila(tabla, "2017-04-08")["q50"] == 100  # sábado
    assert fila(tabla, "2017-04-05")["q50"] == 10  # miércoles


def test_calcula_los_cuantiles_de_las_ultimas_12_semanas(config):
    # Domingos: la semana más reciente vale 12, la anterior 11, y así hasta 1.
    fin = date(2017, 4, 2)
    ventas = historia(lambda f: max(12 - (fin - f).days // 7, 1))
    fila_domingo = fila(pronosticar_baseline(ventas, [1], CORTE, config), "2017-04-09")
    valores = np.arange(1, 13)
    assert fila_domingo["q50"] == pytest.approx(np.quantile(valores, 0.5))
    assert fila_domingo["q10"] == pytest.approx(np.quantile(valores, 0.1))
    assert fila_domingo["q90"] == pytest.approx(np.quantile(valores, 0.9))
    assert fila_domingo["observaciones"] == 12


def test_ignora_lo_anterior_a_las_12_semanas(config):
    fin = date(2017, 4, 2)
    ventas = historia(lambda f: 1000 if (fin - f).days >= 7 * 12 else 5)
    tabla = pronosticar_baseline(ventas, [1], CORTE, config)
    assert (tabla["q90"] == 5).all()


def test_las_semanas_vienen_de_la_configuracion(config):
    fin = date(2017, 4, 2)
    ventas = historia(lambda f: 100 if (fin - f).days < 7 else 5)
    tabla = pronosticar_baseline(ventas, [1], CORTE, replace(config, semanas_baseline=1))
    assert (tabla["q50"] == 100).all()
    assert (tabla["observaciones"] == 1).all()


def test_los_cuantiles_vienen_de_la_configuracion(config):
    tabla = pronosticar_baseline(historia(lambda f: 1), [1], CORTE, replace(config, cuantiles=(0.25, 0.75)))
    assert {nombre_cuantil(0.25), nombre_cuantil(0.75)} <= set(tabla.columns)
    assert "q50" not in tabla.columns


def test_los_cuantiles_no_se_cruzan(config):
    aleatorio = np.random.default_rng(1)
    ventas = historia(lambda f: aleatorio.integers(0, 100))
    tabla = pronosticar_baseline(ventas, [1], CORTE, config)
    assert (tabla["q10"] <= tabla["q50"]).all() and (tabla["q50"] <= tabla["q90"]).all()


def test_no_usa_ventas_del_corte_ni_posteriores(config):
    base = historia(lambda f: 10)
    futuro = historia(lambda f: 9999, fin=date(2017, 4, 20), dias=18)
    limpio = pronosticar_baseline(base, [1], CORTE, config)
    contaminado = pronosticar_baseline(pd.concat([base, futuro]), [1], CORTE, config)
    pd.testing.assert_frame_equal(limpio, contaminado)


def test_con_ventas_atrasadas_usa_lo_mas_reciente(config):
    ventas = historia(lambda f: 7, fin=date(2017, 3, 20))
    tabla = pronosticar_baseline(ventas, [1], CORTE, config)
    assert (tabla["q50"] == 7).all()
    assert (tabla["observaciones"] == 12).all()


def test_una_tienda_sin_ventas_se_marca_sin_observaciones(config):
    tabla = pronosticar_baseline(historia(lambda f: 7), [1, 2], CORTE, config)
    assert len(tabla) == 14
    sin_datos = fila(tabla, "2017-04-04", tienda=2)
    assert sin_datos["q50"] == 0 and sin_datos["observaciones"] == 0


def test_un_dia_faltante_no_falla(config):
    ventas = historia(lambda f: 7)
    ventas = ventas[ventas["date"] != pd.Timestamp("2017-04-02")]
    tabla = pronosticar_baseline(ventas, [1], CORTE, config)
    assert fila(tabla, "2017-04-09")["q50"] == 7


def test_sin_ventas_conocidas_devuelve_una_tabla_vacia(config):
    vacia = historia(lambda f: 1).iloc[0:0]
    tabla = pronosticar_baseline(pd.concat([vacia, historia(lambda f: 1, fin=date(2017, 5, 1), dias=2)]), [1], CORTE, config)
    assert tabla.empty


def test_pronosticar_es_determinista_y_no_altera_la_entrada(config):
    ventas = historia(lambda f: f.day)
    copia = ventas.copy()
    primero = pronosticar_baseline(ventas, [1], CORTE, config)
    pd.testing.assert_frame_equal(primero, pronosticar_baseline(ventas, [1], CORTE, config))
    pd.testing.assert_frame_equal(ventas, copia)


# --- Pedido ---


def test_el_pedido_cubre_el_horizonte_y_usa_el_cuantil_de_su_grupo(config):
    ventas = pd.concat([historia(lambda f: 10), historia(lambda f: 10, familia=PRODUCE)])
    tabla = calcular_pedido(ventas, [1], CORTE, config)
    assert pedido_de(tabla)["cuantil_critico"] == pytest.approx(3 / 3.5)
    assert pedido_de(tabla, familia=PRODUCE)["grupo"] == "perecederos"
    assert pedido_de(tabla)["desde"] == pd.Timestamp("2017-04-04")
    assert pedido_de(tabla)["hasta"] == pd.Timestamp("2017-04-10")
    assert pedido_de(tabla)["pedido"] == 70


def test_usa_el_cuantil_del_total_y_no_la_suma_de_cuantiles_diarios(config):
    # Semanas alternas de 7 y 21 unidades, con el día alto cambiando cada semana.
    fin = date(2017, 4, 2)
    def valor(f):
        distancia = (fin - f).days
        semana = distancia // 7
        return 21 if distancia % 7 == semana % 7 else 0
    ventas = historia(valor)
    tabla = calcular_pedido(ventas, [1], CORTE, replace(config, grupos={**config.grupos, "resto": GrupoCosto("resto", 9, 1)}))
    assert pedido_de(tabla)["pedido"] == 21
    assert pedido_de(tabla)["bloques_observados"] == 12


def test_cambiar_los_costos_cambia_el_pedido_sin_tocar_el_codigo(tmp_path, config):
    fin = date(2017, 4, 2)
    ventas = historia(lambda f: 1 + (fin - f).days // 7)  # totales semanales distintos
    bajo = calcular_pedido(ventas, [1], CORTE, config)

    texto = CONFIG_REAL.read_text(encoding="utf-8")
    ruta = tmp_path / "negocio.yaml"
    ruta.write_text(texto.replace("quiebre: 3\n      merma: 0.5", "quiebre: 1\n      merma: 9"), encoding="utf-8")
    alto = calcular_pedido(ventas, [1], CORTE, cargar_negocio(ruta))

    assert pedido_de(alto)["cuantil_critico"] == pytest.approx(0.1)
    assert pedido_de(alto)["pedido"] < pedido_de(bajo)["pedido"]


def test_redondea_al_entero_superior(config):
    ventas = historia(lambda f: 1.01)
    assert pedido_de(calcular_pedido(ventas, [1], CORTE, config))["pedido"] == 8


def test_el_ruido_de_punto_flotante_no_suma_una_unidad():
    totales = pd.Series([10 + 2e-15, 10.2, 0.0])
    assert unidades_a_pedir(totales).tolist() == [10, 11, 0]


def test_descarta_bloques_con_pocos_dias(config):
    ventas = historia(lambda f: 10)
    # Se quitan días intermedios para que la última fecha conocida no cambie:
    # el bloque más reciente queda con 5 días y no debe usarse.
    quitar = pd.to_datetime(["2017-03-29", "2017-03-30"])
    ventas = ventas[~ventas["date"].isin(quitar)]
    assert pedido_de(calcular_pedido(ventas, [1], CORTE, config))["bloques_observados"] == 11


def test_conserva_bloques_a_los_que_les_falta_un_dia(config):
    # Como el 25 de diciembre: el bloque sigue siendo representativo.
    ventas = historia(lambda f: 10)
    ventas = ventas[ventas["date"] != pd.Timestamp("2017-03-29")]
    assert pedido_de(calcular_pedido(ventas, [1], CORTE, config))["bloques_observados"] == 12


def test_una_serie_sin_historia_pide_cero_y_queda_identificada(config):
    tabla = calcular_pedido(historia(lambda f: 7), [1, 2], CORTE, config)
    sin_datos = pedido_de(tabla, tienda=2)
    assert sin_datos["pedido"] == 0 and sin_datos["bloques_observados"] == 0


def test_el_pedido_no_usa_ventas_futuras(config):
    base = historia(lambda f: 10)
    futuro = historia(lambda f: 9999, fin=date(2017, 4, 20), dias=18)
    pd.testing.assert_frame_equal(
        calcular_pedido(base, [1], CORTE, config),
        calcular_pedido(pd.concat([base, futuro]), [1], CORTE, config),
    )


def test_el_pedido_es_entero_y_no_negativo(config):
    aleatorio = np.random.default_rng(2)
    tabla = calcular_pedido(historia(lambda f: aleatorio.random() * 50), [1], CORTE, config)
    assert tabla["pedido"].dtype == "int64" and (tabla["pedido"] >= 0).all()


# --- Costo del pedido ---


def _pedido_fijo(unidades, familia=BEB):
    return pd.DataFrame({
        "desde": [pd.Timestamp("2017-04-04")],
        "hasta": [pd.Timestamp("2017-04-10")],
        "store_nbr": [1],
        "family": [familia],
        "pedido": [unidades],
    })


def _semana_real(total, familia=BEB):
    return pd.DataFrame({
        "date": pd.date_range("2017-04-04", "2017-04-10"),
        "store_nbr": 1,
        "family": familia,
        "sales": total / 7,
    })


@pytest.mark.parametrize(
    ("pedido", "real", "familia", "costo_esperado"),
    [
        (10, 14, BEB, 4 * 3),  # faltan 4: quiebre 3
        (14, 10, BEB, 4 * 0.5),  # sobran 4: merma 0.5
        (14, 10, PRODUCE, 4 * 2),  # sobran 4: merma 2
        (10, 10, BEB, 0),
    ],
)
def test_el_costo_usa_los_costos_de_su_grupo(config, pedido, real, familia, costo_esperado):
    resultado = costo_del_pedido(_pedido_fijo(pedido, familia), _semana_real(real, familia), config).iloc[0]
    assert resultado["costo"] == pytest.approx(costo_esperado)
    assert resultado["dias_observados"] == 7


def test_el_costo_indica_los_dias_observados(config):
    real = _semana_real(7).iloc[:3]
    resultado = costo_del_pedido(_pedido_fijo(3), real, config).iloc[0]
    assert resultado["dias_observados"] == 3
    assert resultado["venta_real"] == pytest.approx(3)

