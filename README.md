# Motor de Reposición de Inventario

Producto de ML que transforma las ventas diarias de Corporación Favorita en un pedido sugerido por tienda y familia de producto, con Git, DVC y Apache Airflow.

Las instrucciones completas para levantar y ejecutar el producto se agregarán aquí.

## Preparar los datos

1. Descarga los datos de [Kaggle](https://www.kaggle.com/competitions/store-sales-time-series-forecasting/data) y coloca en `data/kaggle/` estos archivos: `train.csv`, `stores.csv`, `holidays_events.csv`, `oil.csv` y `transactions.csv`.
2. Instala el proyecto y genera la carga histórica y la bandeja. Usa Python 3.12:

   ```
   pip install -e ".[dev]"
   python -m inventario.bandeja
   ```

El comando crea `data/historico/`, con lo que ya había llegado el 2017-03-31, y `data/bandeja/`, con lo que llega después. Volver a ejecutarlo no cambia nada.

## Cómo inyectar datos

La bandeja acepta datos nuevos de dos formas. Las fechas de llegada de cada fuente están en `config/fuentes.yaml`.

### CSV con formato de Kaggle

Coloca en una carpeta los CSV que tengas, con los mismos nombres y columnas que los originales. No hace falta incluir las cinco fuentes.

```
python -m inventario.bandeja --origen <carpeta>
```

El comando divide cada CSV por fecha y calcula su entrega según la configuración. Se detiene sin escribir nada si faltan columnas, si una fecha es inválida o si un dato ya entregado llega con otro contenido.

### Archivos sueltos

Copia el archivo directamente en la bandeja, con este nombre y las columnas de esa fuente:

```
data/bandeja/<fuente>/<fecha_dato>__<fecha_entrega>.csv
```

Las fechas usan el formato `AAAA-MM-DD`, y un archivo cuyo nombre no siga esta convención no se usa.

### Correcciones

Una corrección se inyecta como archivo suelto, con la misma fecha del dato y una entrega posterior. El archivo anterior se conserva:

```
data/bandeja/train/2017-08-15__2017-08-16.csv   dato original
data/bandeja/train/2017-08-15__2017-08-20.csv   corrección: rige desde el 20
```

Cada archivo reemplaza a las versiones anteriores de esa fecha, así que debe traer todas sus filas.
