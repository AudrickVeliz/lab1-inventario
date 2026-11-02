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

**Decisión:** cada archivo se llama `data/bandeja/<fuente>/<fecha_dato>__<fecha_entrega>.csv`. Una corrección es un archivo nuevo con la misma fecha del dato y una entrega posterior. Para cada fecha del dato rige la última versión entregada hasta la fecha de corte, que reemplaza a las anteriores.

**Alternativas:**

| Alternativa | En contra |
|---|---|
| Fecha de modificación del archivo | Cambia al clonar, copiar o restaurar con DVC |
| Manifiesto con las fechas de cada archivo | Sería un archivo más que mantener y podría desincronizarse |
| Sobrescribir el archivo al corregirlo | Pierde lo que se conocía antes y modifica lo ya entregado |

**Justificación:** el nombre no se pierde al copiar el archivo, y permite saber qué se conocía en cualquier fecha sin abrirlo.

**Pendiente:** definir en la ingesta si una corrección puede traer solo las filas corregidas.

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
