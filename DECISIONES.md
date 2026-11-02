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
