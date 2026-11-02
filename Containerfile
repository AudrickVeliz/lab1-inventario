# Imagen de Airflow con las dependencias del producto.
# Se construye una vez, en lugar de instalar paquetes en cada arranque
# con _PIP_ADDITIONAL_REQUIREMENTS, que la documentación oficial reserva para pruebas rápidas.
FROM docker.io/apache/airflow:3.3.2-python3.12

COPY --chown=airflow:root pyproject.toml /tmp/inventario/pyproject.toml
COPY --chown=airflow:root src /tmp/inventario/src

# El archivo de restricciones de Airflow fija versiones compatibles entre sí.
RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt" \
    /tmp/inventario

# DVC va en un entorno aparte, para que sus dependencias no choquen con las de Airflow.
RUN python -m venv /home/airflow/dvc \
    && /home/airflow/dvc/bin/pip install --no-cache-dir "dvc==3.67.1"
