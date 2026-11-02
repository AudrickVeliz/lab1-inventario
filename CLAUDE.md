# Motor de Reposición de Inventario

Laboratorio 1 de Product Development (Universidad Galileo). Transforma ventas diarias de Corporación Favorita (Kaggle) en un pronóstico con incertidumbre y un pedido sugerido por tienda y familia, con seguimiento del desempeño.

Lo principal no es el modelo, sino operar el producto en el tiempo. La evaluación corre sobre un periodo no publicado, sin intervención manual.

## Reglas del producto

1. **Sin fuga de información.** Toda predicción usa solo lo disponible al emitirse. Distingue siempre la fecha del dato de la fecha en que llegó.
2. **La bandeja es externa.** El flujo la lee y nunca la modifica.
3. **Lo publicado es inmutable.** Una corrección de datos genera una nueva versión; no reescribe un pronóstico ya publicado.
4. **Tareas idempotentes.** Ejecutar una tarea varias veces produce el mismo resultado que ejecutarla una vez.
5. **Datos inválidos se detienen; situaciones esperadas no fallan.** Un dato inválido no debe llegar al resultado. Un feriado, una tienda cerrada o una fuente que aún no llega no deben provocar una falla.
6. **Los DAGs solo orquestan.** La lógica vive en módulos de Python con pruebas.
7. **Los costos de negocio viven en configuración versionada.** Cambiarlos modifica la recomendación sin tocar el código.
8. **Sin intervención manual.** Nada debe depender de pasos a mano, de rutas locales ni de credenciales personales.

## Flujo de Git

- `main` está protegida: todo cambio entra por Pull Request con las pruebas en verde.
- Ramas con prefijo y descripción corta: `feat/`, `fix/`, `chore/`, `docs/`, `test/`.
- Commits pequeños y de un solo propósito, en español, con el formato `tipo: descripción` (`feat: validar esquema de transacciones`).
- Cada PR cubre un solo tema y explica qué cambia y cómo se verificó.

## Datos y modelos

- Ningún dato ni modelo va en Git. Se versionan con DVC y un remoto.
- Se versionan como mínimo los datos de entrada, los datos procesados, los modelos y los pronósticos publicados.
- Un pronóstico publicado debe poder reproducirse a partir de su versión de código y de datos.
- `test.csv` y `sample_submission.csv` de Kaggle no forman parte del laboratorio.

## Entorno

- Python 3.12 en el entorno de Conda `lab1`.
- Airflow 3 con Docker Compose. Localmente se ejecuta sobre Podman, pero el `compose` debe funcionar sin cambios en Docker, que es lo que usará el equipo docente.
- Todo el texto se guarda con finales de línea LF.

## Documentación

- Cada decisión técnica o de producto se registra en `DECISIONES.md` con sus alternativas y su justificación, en particular las de disponibilidad de la información, validación de datos y manejo de correcciones.
- `README.md` debe permitir levantar el producto desde cero con los archivos originales de Kaggle.

## Cómo trabajar en este proyecto

- Avanza paso a paso y consulta con Audrick las decisiones de producto antes de implementarlas.
- Explica el porqué de cada decisión: Audrick debe poder comprender y justificar todo el código.
- No declares algo terminado sin haberlo ejecutado; reporta el comando y el resultado.
