# Decisiones técnicas y de producto

Cada decisión registra su contexto, las alternativas consideradas y su justificación.

## D-001: carpeta local como remoto de DVC

**Fecha:** 2026-11-01

**Contexto:** el enunciado exige versionar datos y modelos con DVC y un remoto. El equipo docente clonará el repositorio, colocará los archivos de Kaggle y ejecutará el flujo sin intervención manual y sin las credenciales del estudiante.

**Decisión:** usar como remoto predeterminado la carpeta `dvc-remote/`, con ruta relativa e ignorada por Git.

**Alternativas:**

| Alternativa | A favor | En contra |
|---|---|---|
| Carpeta local (elegida) | Funciona en cualquier clon, sin cuenta ni credenciales | No hay copia fuera de la máquina, salvo la sincronización de OneDrive; el equipo docente no puede descargar las versiones del estudiante |
| DagsHub privado | Remoto externo, con copia fuera de la máquina | Requiere cuenta y token; un flujo que dependa de ese token fallaría en la evaluación |
| DagsHub público | El equipo docente podría descargar sin credenciales | Redistribuye públicamente los datos de Kaggle |

**Justificación:** el producto debe operar sin credenciales personales, y el equipo docente reconstruye desde los archivos originales de Kaggle, por lo que no necesita descargar del remoto.

**Riesgo aceptado:** el enunciado no define el tipo de remoto, y una carpeta dentro del proyecto podría no considerarse suficiente. Si se pierde la carpeta del proyecto, también se pierde el historial de versiones que no esté en otra copia.

**Cuándo revisarla:** si el volumen de datos crece demasiado para la carpeta o si el equipo docente pide un remoto externo. El cambio consiste en configurar el nuevo remoto y ejecutar `dvc push`, sin modificar los archivos `.dvc`.

## D-002: registrar la llegada en el nombre del archivo

**Fecha:** 2026-11-01

**Contexto:** para evitar la fuga de información, hay que saber cuándo llegó cada dato, y una corrección no puede borrar lo que se conocía antes.

**Decisión:** cada archivo se llama `data/bandeja/<fuente>/<fecha_dato>__<fecha_entrega>.csv`. Una corrección es un archivo nuevo con la misma fecha del dato y una entrega posterior, y solo rige desde esa entrega. Las versiones se aplican en orden de entrega, según D-010.

**Alternativas:**

| Alternativa | En contra |
|---|---|
| Fecha de modificación del archivo | Cambia al clonar, copiar o restaurar con DVC |
| Manifiesto con las fechas de cada archivo | Sería un archivo más que mantener y podría desincronizarse |
| Sobrescribir el archivo al corregirlo | Pierde lo que se conocía antes y modifica lo ya entregado |

**Justificación:** el nombre no se pierde al copiar el archivo, y permite saber qué se conocía en cualquier fecha sin abrirlo.

## D-003: disponibilidad de cada fuente

**Fecha:** 2026-11-01

**Contexto:** Kaggle no trae fechas de llegada. El enunciado pide que las fuentes lleguen con distinta frecuencia y anticipación.

**Decisión:** estos supuestos viven en `config/fuentes.yaml`.

| Fuente | Llegada | Justificación |
|---|---|---|
| `train` | Día siguiente | El cierre de ventas del día está disponible a la mañana siguiente |
| `transactions` | A los 2 días | Supone una consolidación más lenta que la de las ventas |
| `oil` | Siguiente día hábil: el viernes llega el lunes | Los 486 días faltantes son fines de semana |
| `holidays_events` | 30 días antes del evento | Los feriados se conocen por adelantado |
| `stores` | Solo en la carga histórica | Es un catálogo sin fechas |

**Alternativas:** dar a todas las fuentes el mismo retraso, que es más simple, pero no refleja la operación que describe el enunciado; o usar `oil` y `holidays_events` completos, lo que filtraría datos futuros.

**Situaciones esperadas, que no provocan falla:** fines de semana sin `oil`, precios de `oil` vacíos, días sin eventos y fuentes que aún no han llegado.

## D-004: inyección de datos

**Fecha:** 2026-11-01

**Contexto:** el enunciado no indica cómo se entregará el periodo no publicado. Se asume que llega con el mismo formato de Kaggle.

**Decisión:** aceptar dos formas, documentadas en `README.md`: CSV con formato de Kaggle, que un comando divide por fecha, o archivos sueltos con la convención de D-002. Las correcciones solo se inyectan como archivos sueltos.

**Alternativas:** aceptar solo archivos sueltos obligaría a quien evalúa a dividir los CSV; aceptar solo CSV impediría indicar la fecha de entrega de una corrección.

**Justificación:** el comando no sobrescribe. Si un CSV trae otro contenido para algo ya entregado, se detiene sin escribir nada, porque no puede saber cuándo llegó esa corrección.

## D-005: sin promociones futuras

**Fecha:** 2026-11-01

**Contexto:** `onpromotion` llega en la misma fila que las ventas de `train`, por lo que no se conoce antes que ellas.

**Decisión:** el modelo solo usa promociones ya entregadas.

**Alternativa:** crear una fuente aparte con el plan de promociones. Mejoraría el pronóstico, pero sería una fuente inventada que no está en los datos.

**Costo aceptado:** el pronóstico puede ser algo menos preciso en fechas con promoción.

## D-006: corte de la carga histórica en 2017-03-31

**Fecha:** 2026-11-01

**Decisión:** la carga histórica contiene lo que ya había llegado el 2017-03-31, según D-003. Por eso, las ventas del 31 de marzo entran por la bandeja el 1 de abril. Lo demás se entrega por la bandeja hasta el fin de los datos.

**Justificación:** separar por fecha de llegada, y no por fecha del dato, aplica la misma regla a la carga histórica. Quedan unas 19 semanas de fechas de corte para el seguimiento y más de cuatro años para entrenar.

**Alternativas:** un corte más tardío deja menos semanas de seguimiento, y uno más temprano reduce el entrenamiento.

## D-007: frontera entre dato inválido y situación esperada

**Fecha:** 2026-11-01

**Contexto:** el enunciado exige detener los datos inválidos sin que las situaciones esperadas provoquen fallas. Las reglas se contrastaron con los datos reales, que no tienen claves duplicadas, ventas negativas ni tiendas fuera del catálogo.

**Decisión:**

| Tratamiento | Situaciones |
|---|---|
| Inválido: se detiene | CSV ilegible; columnas incorrectas; tipos que no se pueden interpretar; valores obligatorios vacíos; fecha distinta a la del nombre del archivo; entrega anterior al dato, salvo en fuentes con anticipación; valores negativos; misma clave con valores distintos; tienda fuera del catálogo; nombre fuera de la convención |
| Aviso: se conserva | Filas duplicadas exactas, de las que queda una; familia nueva; archivo incompleto; venta mayor que 100 veces la mediana de su serie |
| Esperado: sin aviso | Fuente que aún no llega; fin de semana sin `oil`; 25 de diciembre sin datos; venta 0; precio de `oil` vacío; día sin eventos |

**Justificación de los casos discutibles:**
- **Venta muy alta:** hay 33 ventas de más de 100 veces su mediana en los datos originales. Detenerlas rechazaría picos reales. El costo aceptado es que un error de captura solo genere un aviso. El umbral está en `config/fuentes.yaml`.
- **Archivo incompleto:** no se inventan ceros, porque una fila ausente es un dato desconocido y no una venta nula.
- **Tienda nueva:** es inválida hasta que llegue el catálogo actualizado, porque no se puede asociar a su ciudad, tipo ni grupo.
- **Familia nueva:** se conserva, porque no rompe ningún catálogo. El modelo deberá tratarla como desconocida.

**Alternativas:** detener también las ventas muy altas, o tratar como aviso los valores negativos. La primera rechaza datos reales; la segunda deja pasar datos sin sentido de negocio.

## D-008: cuarentena del archivo completo

**Fecha:** 2026-11-01

**Decisión:** un archivo con cualquier dato inválido no se usa, se registra con su motivo en el reporte de ingesta y el proceso continúa. El archivo permanece en la bandeja, porque esta nunca se modifica. Si la carga histórica falta o es inválida, el proceso sí falla, porque no hay base con la cual operar.

**Alternativas:** rechazar solo las filas inválidas es más fino, pero puede dejar un día a medias sin que se note. Fallar toda la ejecución dejaría sin pedido sugerido a todas las tiendas por un solo archivo.

**Costo aceptado:** un error en una fila descarta las filas correctas de ese archivo hasta que llegue una corrección.

## D-009: publicar con lo disponible

**Fecha:** 2026-11-01

**Decisión:** si una fuente se atrasa, el producto publica igual e informa la última fecha conocida de cada fuente. La ingesta ya entrega esa fecha en su reporte.

**Alternativa:** no publicar si las ventas tienen demasiados días de atraso. Evita pedidos con datos viejos, pero una fuente retrasada dejaría a la tienda sin recomendación.

**Pendiente:** evaluar un límite de antigüedad cuando exista el pronóstico.

## D-010: correcciones por clave

**Fecha:** 2026-11-01

**Contexto:** no se sabe si una corrección llegará con el archivo completo o solo con las filas corregidas.

**Decisión:** la ingesta parte de la carga histórica y aplica en orden de entrega cada archivo válido. Cada fila reemplaza a la anterior con la misma clave. También se pueden corregir fechas de la carga histórica.

**Alternativa:** reemplazar toda la fecha. Es más simple, pero una corrección parcial borraría el resto del día.

**Costo aceptado:** una corrección no puede eliminar una fila, solo cambiar sus valores.

## D-011: corte semanal con cobertura de martes a lunes

**Fecha:** 2026-11-01

**Decisión:** el producto emite cada lunes por la mañana un pronóstico diario de 7 días y un pedido que cubre de martes a lunes. El lunes se conocen las ventas hasta el domingo, las transacciones hasta el sábado y el petróleo hasta el viernes.

**Justificación:** la operación pide una sugerencia semanal. El pedido no se entrega el mismo día, y esta cobertura enlaza una semana con la siguiente sin huecos. Quedan 20 cortes de seguimiento, del 2017-04-03 al 2017-08-14; el último no se puede evaluar completo, porque las ventas terminan el 15 de agosto.

**Alternativa:** un horizonte de 14 días daría más anticipación, pero el pedido solo necesita la semana que cubre.

## D-012: baseline con la mediana de 12 semanas

**Fecha:** 2026-11-01

**Decisión:** el pronóstico diario usa los cuantiles 0.10, 0.50 y 0.90 de las últimas 12 observaciones del mismo día de la semana, por tienda y familia. Si las ventas se atrasan, parte de la última fecha conocida. Una serie sin observaciones se pronostica en 0 y se identifica con `observaciones` = 0.

**Evidencia:** 25 cortes de octubre de 2016 a marzo de 2017, solo con la carga histórica.

| Candidato | WAPE |
|---|---|
| Mismo día de la semana pasada | 0.214 |
| Media de 4 semanas | 0.197 |
| Mediana de 4 semanas | 0.184 |
| Mediana de 12 semanas (elegida) | 0.181 |
| Mediana de 8 semanas | 0.178 |

**Justificación:** la mediana de 8 semanas gana por 0.003, una diferencia mínima. Se eligen 12 porque los cuantiles se estiman con más observaciones y, en esa medición, el cuantil 0.85 del total semanal cubrió mejor la demanda real (77% frente a 76%).

**Limitación:** el baseline no considera feriados ni promociones. En los 20 cortes de seguimiento, el intervalo entre los cuantiles 0.10 y 0.90 cubre el 71% de las ventas, por debajo del 80% esperado. Es lo que el modelo de ML debe mejorar.

## D-013: costos de quiebre y merma

**Fecha:** 2026-11-01

**Contexto:** Kaggle no trae precios ni márgenes, así que estos costos relativos por unidad son supuestos del producto. Viven en `config/negocio.yaml`.

**Decisión:**

| Grupo | Quiebre | Merma | Cuantil del pedido |
|---|---|---|---|
| Perecederos: `BREAD/BAKERY`, `DAIRY`, `DELI`, `EGGS`, `MEATS`, `POULTRY`, `PREPARED FOODS`, `PRODUCE`, `SEAFOOD` | 3 | 2 | 0.60 |
| Resto, incluida cualquier familia nueva | 3 | 0.5 | 0.86 |

**Justificación:** el cuantil se calcula como `quiebre / (quiebre + merma)`, que minimiza el costo esperado cuando se compara lo que falta con lo que sobra. Un producto no perecedero que sobra se puede vender después; uno perecedero se pierde.

**Alternativa:** un solo costo para todas las familias, que ignoraría la merma de los perecederos que describe el enunciado.

## D-014: pedido sin inventario inicial

**Fecha:** 2026-11-01

**Decisión:** el pedido es el cuantil crítico del total del periodo, redondeado al entero superior. Supone que el periodo empieza sin existencias, porque no hay datos de inventario. El total se estima con 12 bloques de 7 días; un bloque con menos de 6 días observados no se usa, de modo que el 25 de diciembre no invalida su semana.

**Alternativas:**
- **Sumar los cuantiles diarios:** exagera la incertidumbre, porque supone que todos los días son altos o bajos a la vez.
- **Simular el inventario:** agrega supuestos que no se pueden comprobar sobre lo que realmente sobró en cada tienda.

**Costo aceptado:** en productos no perecederos, el pedido puede ser mayor de lo necesario, porque ignora lo que sobró de la semana anterior.

## D-015: compose de Airflow adaptado a una laptop

**Fecha:** 2026-11-01

**Contexto:** se parte del `docker-compose.yaml` oficial de Airflow 3.3.2. Debe correr en una laptop con Podman y, sin cambios, en Docker, sin archivos ni pasos adicionales.

**Decisión:** usar `compose.yaml` y `Containerfile`, con estos cambios frente al original.

| Cambio | Justificación |
|---|---|
| `LocalExecutor`; sin `redis`, `airflow-worker` ni `flower` | Una sola máquina no necesita Celery |
| Sin `airflow-triggerer` | Es opcional y ningún DAG usa tareas diferidas |
| Límites: `PARALLELISM` 2, una ejecución activa por DAG y un worker de API | Cada corte carga millones de filas en memoria |
| Logs en un volumen, no en `./logs` | OneDrive intentaría sincronizar miles de archivos |
| Credenciales con `${AIRFLOW_USER:-airflow}` y `${AIRFLOW_PASSWORD:-airflow}` | Funciona sin configurar nada y permite cambiarlas con variables de entorno |
| Sin `env_file: .env` | En el original es obligatorio, y un clon nuevo no tiene `.env`. Compose ya lee `.env` para las variables |
| Sin montar `./config` en Airflow | Ahí están los YAML del producto, y Airflow escribiría su `airflow.cfg` junto a ellos |
| DAGs activos al crearse y sin ejemplos | La evaluación no tiene intervención manual |
| Imágenes con `docker.io/` | Con Podman, un nombre sin registro puede pedir confirmación o fallar; en Docker es equivalente |
| Imagen propia, no `_PIP_ADDITIONAL_REQUIREMENTS` | La documentación oficial reserva esa opción para pruebas rápidas |
| El proyecto se monta completo | El código y los datos corresponden al commit, sin reconstruir la imagen al cambiar el código |

**Compatibilidad:** `Containerfile` se indica en `build.dockerfile`, porque Docker no lo detecta por sí solo. El contexto de construcción se filtra con `.dockerignore`, porque el cliente de Compose lo aplica también al trabajar sobre Podman. El workflow `Compose en Docker` levanta este mismo archivo con Docker en Linux.

**Alternativas:** `CeleryExecutor` como en el original, que agrega servicios sin ningún beneficio en una sola máquina; o instalar Airflow sin contenedores, que el enunciado no permite.

**Limitación:** cambiar `AIRFLOW_USER` o `AIRFLOW_PASSWORD` solo surte efecto al crear la base de datos; después hay que recrearla con `compose down -v`.

## D-016: versiones exactas iguales a las de Airflow

**Fecha:** 2026-11-01

**Contexto:** las restricciones oficiales de Airflow 3.3.2 fijan `pandas==3.0.5`, y el proyecto pedía `pandas<3`.

**Decisión:** fijar en `pyproject.toml` las versiones exactas de esas restricciones: `pandas==3.0.5`, `numpy==2.5.3` y `pyyaml==6.0.3`.

**Justificación:** el entorno local, el CI y el contenedor usan las mismas librerías, lo que permite reproducir un pronóstico. Se comprobó el cambio: con pandas 3 pasan todas las pruebas y los resultados del baseline son idénticos.

**Alternativa:** mantener pandas 2 sin usar las restricciones. Es más simple a corto plazo, pero la instalación se aparta de las versiones que Airflow publica como compatibles.
