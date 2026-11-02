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

## Levantar Airflow

Necesitas Docker o Podman, con al menos 4 GB de memoria disponibles. La primera vez se construye la imagen, lo que tarda varios minutos.

```
docker compose up -d
```

Con Podman, el comando es `podman compose up -d`. El archivo es el mismo y no necesita cambios.

La interfaz queda en <http://localhost:8080>, con el usuario `airflow` y la contraseña `airflow`.

Para usar otras credenciales, define `AIRFLOW_USER` y `AIRFLOW_PASSWORD` en el entorno o en un archivo `.env` antes de levantar Airflow por primera vez. Solo se aplican al crear la base de datos; para cambiarlas después, usa `docker compose down -v`, que borra el historial de ejecuciones.

El DAG `comprobar_entorno` se dispara a mano y comprueba la instalación de punta a punta. Requiere haber preparado los datos.

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

La corrección puede traer el archivo completo o solo las filas corregidas: cada fila reemplaza a la anterior con la misma clave y las demás se conservan. Una corrección no puede eliminar una fila.

## Validar e ingerir

Este comando valida lo que había llegado en una fecha de corte y escribe un reporte en `data/reportes/ingesta/<fecha>.json`:

```
python -m inventario.ingesta --fecha-corte 2017-04-03
```

- **Datos inválidos:** el archivo completo queda en cuarentena. Se registra en el reporte con su motivo, no se usa y el proceso continúa.
- **Avisos:** el dato se conserva y queda registrado en el reporte.
- **Carga histórica faltante o inválida:** el comando termina con error, porque no hay base con la cual operar.

La lista completa de reglas está en `DECISIONES.md` (D-007).
