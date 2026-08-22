# Borrador — Reporte de Proyecto Modular Argus (formato IEEE)

> Documento de trabajo interno, no es el entregable final. Objetivo: darle a cada sección del
> `Formato_Proyecto_Modular V2.docx` un primer borrador de contenido específico de Argus, más
> las dudas que encontré al cruzarlo con `CLAUDE.md`, el notebook, `cv-argus/README.md`,
> `argus-descripción-proyecto.pdf`, `Argus_Definicion_Tecnica.docx.pdf` y `criteriosaprobacion_0.pdf`.
> Cada sección trae: qué exige el formato, una propuesta de contenido en español, y — donde
> encontré algo ambiguo o que no cuadra entre documentos — una pregunta con mi mejor respuesta
> propuesta, marcada así:
>
> ❓ **Pregunta** — duda real que hay que resolver en equipo.
> 💡 **Propuesta** — mi recomendación, a validar con ustedes y el asesor.

Antes de ir sección por sección, esto es lo más importante que encontré — condiciona varias
secciones del reporte, así que lean esto primero.

---

## 🔴 Hallazgo crítico: hay dos arquitecturas distintas para Argus, no una

> ✅ **Decisión del equipo (resuelto):** la columna izquierda (`CLAUDE.md` + notebook +
> `cv-argus/`, código real) es la arquitectura para **este** documento de registro. La
> columna derecha (AWS) queda como el "modelo ideal" a desarrollar *si* el tiempo del
> proyecto lo permite — no se detalla ni se compromete en el documento de registro, para no
> prometer de más sobre algo sin código todavía. Ver nota de alcance justo después de la
> tabla.

Encontré **dos documentos de definición técnica que no describen el mismo sistema**:

| | `CLAUDE.md` + `semantic-design` (18 jul) + notebook + `cv-argus/` (código real) | `Argus_Definicion_Tecnica.docx.pdf` (12 ago) |
|---|---|---|
| Modelo de IA | MediaPipe **FaceLandmarker** (Python) → capa `GeometricRatioFeatureLayer` (EAR/MAR/pose) → **LSTM** entrenado sobre 59 features (7 geométricas + 52 blendshapes), validado con Spearman/Kruskal-Wallis | MediaPipe **Face Mesh** cuantizado **INT8 + XNNPACK**, en **C++**, clasificación **PERCLOS** (umbral ≥1.5s) con **regresión logística o CNN-LSTM** sobre 68 landmarks |
| Backend | **Django/Flask/FastAPI** + SQL o MongoDB (sin decidir) + contenedor **OSRM** para rutas | **100% serverless en AWS**: IoT Core, Lambda, API Gateway, **DynamoDB**, Cognito, definido con **AWS CDK** |
| Frontend | **React + react-leaflet**, 3 roles (Root/Admin, Guardian, Truck Driver) | **React** + Amazon Location Service o Google Maps API |
| Comunicación camión↔nube | Protocolo sin decidir (candidato HTTPS), vía ESP32 | MQTT a AWS IoT Core, con **RockBLOCK/Iridium** satelital de respaldo |
| Sensor adicional | Sensor de agarre en el volante (grip sensor) | Sensores **FSR** (fuerza resistiva) en el volante |
| Qué existe como código | El notebook completo y el andamiaje de `cv-argus/` (Docker, packaging, entry point) — **nada de AWS/CDK/DynamoDB existe** | Nada de esto existe como código en el repo |

Esto no es un matiz de redacción: son dos sistemas distintos con distintas tecnologías en
cada uno de los tres módulos que evalúa el comité. Si el reporte mezcla frases de los dos
documentos (que es fácil que pase si cada quien redacta una sección con el PDF que tenía a
la mano), el resultado va a leerse inconsistente — y el criterio de no-aprobación **C** es
literalmente *"el proyecto no se encuentra claramente delimitado"*.

✅ **Por qué esta decisión es la correcta para un documento de *registro*, no solo la más
cómoda de escribir:**
1. Es la única de las dos que tiene código real corriendo detrás (el notebook entrenado, el
   andamiaje Docker de `cv-argus`) — un comité de titulación puede pedir ver el prototipo
   funcionando, y hoy eso es MediaPipe+LSTM en Python, no C++/INT8/XNNPACK.
2. El modelo matemático que ya está *validado estadísticamente* (Spearman/Kruskal-Wallis
   sobre 59 features, comparación RandomForest vs. LSTM) es el del notebook, no un PERCLOS
   con umbral fijo — y el criterio 2.3 pide justificar la selección del algoritmo, que aquí
   ya está hecho con evidencia, mientras que "PERCLOS ≥1.5s" es una regla fija sin ese respaldo.
3. Este documento es para *registrar* el proyecto, no un reporte final de titulación — el
   criterio de no-aprobación **A** ("objetivos no alcanzables en los tiempos establecidos") y
   **B** ("el prototipo no es viable") castigan justo lo contrario de lo que conviene aquí:
   comprometerse por escrito con AWS IoT Core/Lambda/DynamoDB/Cognito/CDK cuando nada de eso
   existe todavía es prometer de más sobre algo que ni siquiera se ha empezado.

📌 **Cómo tratar el lado AWS en el documento, entonces:** no desaparece del todo, pero baja de
"especificación técnica" a "dirección posible si el alcance lo permite" — en condicional, sin
nombrar servicios específicos como compromiso, y solo en la Parte 9 (trabajo a futuro). Ya
ajusté esa sección más abajo con ese tono.

---

## Otra nota antes de empezar: los archivos `.docx` que pediste revisar

✅ **Resuelto** — confirmado que fue solo un archivo duplicado/movido entre `docs/document/` y
`docs/criteria/`, no un problema real. Sin acción pendiente aquí.

Para referencia: `Analisis formato proyecto modular.docx` es útil pero es **tu propia
interpretación** del formato oficial, no el documento oficial en sí — lo usé como guía de
lectura, pero basé la estructura de este borrador en `Formato_Proyecto_Modular V2.docx` (la
plantilla IEEE real) y `criteriosaprobacion_0.pdf` (los criterios de aprobación reales, con
checkboxes).

---

## Parte 1 — Resumen (máx. 150 palabras)

**Qué pide el formato:** síntesis del problema + objetivo, métodos/tecnologías, resultado
obtenido, conclusión — en ese orden, dentro de 150 palabras. *(Nota: "resultado obtenido" en
formato IEEE de proyecto terminado implica tiempo pasado — ver el hallazgo sobre la Parte 8
más abajo, aplica igual aquí.)*

**Propuesta de contenido:**

> El autotransporte de carga mueve el 57% de las mercancías de México, pero la fatiga y
> somnolencia causan entre el 24% y 30% de los accidentes viales, agravado por el
> incumplimiento generalizado de la NOM-087. Argus es un sistema de monitoreo del conductor
> (DMS) que augmenta —sin reemplazar— al operador: una capa preventiva que detecta
> somnolencia en tiempo real mediante visión artificial y actúa antes de que ocurra un
> microsueño. El sistema combina un módulo de inferencia en el borde (Raspberry Pi 5,
> MediaPipe FaceLandmarker + una red LSTM entrenada sobre razones geométricas oculares/de
> boca y blendshapes faciales) con un microcontrolador (ESP32) responsable de alertas,
> frenado preventivo y comunicación, y un buffer local que garantiza continuidad sin
> cobertura celular. La **LSTM** fue el modelo elegido sobre el baseline RandomForest, con
> 98.55% de exactitud en el conjunto de prueba (F1 ponderado 0.99), frente al 95.66% del
> RandomForest (F1 ponderado 0.96). Los hallazgos confirman que un enfoque multimodal
> geométrico-secuencial es viable para clasificar 6 niveles de somnolencia en hardware de
> borde de bajo costo.

*(~150 palabras con los números ya puestos — ajustar solo si vuelven a entrenar con un split
distinto, ver nota roja abajo.)*

✅ **Resuelto (LSTM confirmado como modelo final — ver Parte 6), pero 🔴 los números de abajo
siguen pendientes de re-verificar.** Los venía de leer `ArgusMLModel.ipynb` (el monolito viejo,
celdas 125–126 y 138) antes de que se dividiera en los tres notebooks actuales y se corrigiera
el split train/test (ver el hallazgo detallado en Parte 6). Ese notebook corregido
(`02_model_training.ipynb`) todavía no se ha ejecutado ni una vez, así que estos números son
**placeholder**, no resultado verificado sobre el código que corre hoy:

| Modelo | Accuracy (test) | F1 ponderado | Notas |
|---|---|---|---|
| RandomForest (7 features: `eyeBlinkLeft`, `eyeBlinkRight`, `EAR_mean`, `eyeSquintLeft`, `eyeSquintRight`, `jawOpen`, `Pitch`) | 95.66% ⚠️ | 0.96 ⚠️ | baseline, pendiente re-run |
| **LSTM** (60 timesteps × 58 features, `LSTM(128)→Dropout(0.3)→LSTM(64)→Dropout(0.3)→Dense(6, softmax)`, 145,542 parámetros) | **98.55%** ⚠️ (test loss 0.0536) | 0.99 ⚠️ | modelo final elegido, pendiente re-run |

📎 **Nota técnica, actualizada:** el split ya no es un tema de "aceptar tal cual" — el código
de `02_model_training.ipynb` **ya corrigió** el train/test a `GroupShuffleSplit` agrupado por
sujeto (arregla la fuga de ventanas casi-duplicadas del mismo sujeto entre train y test que
tenía el `train_test_split` estratificado anterior). Lo único pendiente es correr ese notebook
una vez con el fix aplicado y traer los números reales de vuelta a esta tabla y a las Partes 4,
6 y 8, que citan los mismos.

---

## Parte 2 — Introducción

**Qué pide el formato:** introducción concisa que prepare al lector para lo que sigue (no es
el lugar para resultados ni metodología a detalle).

**Propuesta de contenido:**

> México depende del autotransporte federal para mover más de la mitad de su carga, pero
> paga un costo altísimo en vidas: ~16,500 muertes y 150 mil millones de pesos anuales en
> accidentes viales, de los cuales la fatiga del conductor explica hasta un 30%. El ciclo es
> conocido: jornadas "justo a tiempo" que exceden la NOM-087, bitácoras falsificadas, y un
> 81.3% de los operadores recurriendo a sustancias para mantenerse despiertos. La
> automatización total (Nivel 5) —el camino que ya toman flotas comerciales en EUA— no es
> viable en México a corto plazo: un camión detenido por su protocolo de "minimal risk
> condition" ante una anomalía es un blanco fácil para el robo de carga, la red carretera
> carece de la señalización y cobertura 5G que la conducción autónoma exige, y la brecha de
> costo (450,000 USD vs. 180,000 USD) es prohibitiva para el "hombre-camión" que domina el
> mercado. El sistema toma su nombre de **Argos Panoptes**, el gigante centinela de la
> mitología griega que nunca dormía por completo: vigilaba con unos ojos mientras otros
> descansaban, sin dejar nunca de observar. Argus busca esa misma vigilancia constante para
> el conductor — no reemplazarlo, sino ser los ojos que no se cierran cuando los suyos lo
> hacen. El sistema propone "Aumento Humano": una barrera tecnológica de bajo costo que
> vigila el estado fisiológico del conductor y solo interviene —alertando o frenando
> preventivamente— cuando el humano ya no puede reaccionar a tiempo. Este documento describe
> el diseño, la justificación técnica y los resultados del prototipo desarrollado.

✅ **Resuelto** — sí, mencionar "Argus" desde la introducción. Añadí la referencia a Argos
Panoptes (el gigante de los cien ojos que vigilaba por turnos, nunca dormido del todo) porque
conecta directo y de forma no forzada con la tesis central del proyecto — un sistema que
"vigila" la somnolencia del conductor sin reemplazarlo. Es el tipo de dato que un comité
académico valora en una introducción (ancla el nombre del proyecto a su propósito), y no
compite con la Parte 3/4 porque ahí se habla de trabajo técnico, no del porqué del nombre.

---

## Parte 3 — Trabajos relacionados

**Tu pregunta — ¿son trabajos que hicimos nosotros o que hizo alguien más?** Es de otros, no
de ustedes. El texto de la plantilla aquí es genérico (solo explica que "a partir de esta
sección se desarrollan los contenidos... organizada usando títulos... con subtítulos") porque
ese documento es sobre todo una guía de *formato visual* (márgenes, tipografía, niveles de
título) — reutiliza "TRABAJOS RELACIONADOS" nada más como ejemplo de encabezado para enseñar
esas reglas, no explica el contenido esperado sección por sección. Pero "Trabajos
relacionados"/"Related Work" es una sección estándar en cualquier paper estilo IEEE, y su
significado ahí es fijo: es el **estado del arte** — qué ha hecho *otra gente* (papers,
productos comerciales, proyectos similares) sobre el mismo problema, antes de que ustedes
expliquen qué hicieron. Lo que el equipo desarrolló va en la Parte 4
("Descripción del desarrollo del proyecto modular"), no aquí.

Vale la pena tomarse en serio esta sección más allá del trámite: es también donde defienden
que Argus no es una copia de algo que ya existe — el criterio de no-aprobación **G** es
justo "el proyecto carece de originalidad o no implementa una solución novedosa". Mostrar que
conocen Geotab/Samsara (los DMS comerciales que ya existen) y en qué se diferencia Argus
(edge-first, pensado para conectividad intermitente y robo de carga en México) es lo que
sustenta esa originalidad, no solo declararla.

**Qué pide el formato:** contenidos del proyecto desarrollados de forma ordenada, con
títulos/subtítulos por tema (aquí específicamente: qué otros estudios/soluciones existen).

**Ajuste según tu indicación:** ya leí a fondo los 5 archivos de `docs/references/` (antes
solo los tenía identificados, no leídos) y reorganicé esta sección en dos bloques, como
pediste: (A) papers/datasets que evalúan *opciones* para el propio proyecto —
específicamente de dónde podría salir más data de entrenamiento para microsueños — y (B)
proyectos parecidos que terminaron en algo distinto. Mantuve cada punto en 2-3 líneas nada
más, sin describir su implementación a detalle, para que se lea como panorama del estado del
arte y no como que están parafraseando su trabajo.

**A. Papers y datasets sobre microsueños — posibles fuentes de datos/entrenamiento a futuro:**

> - **RLDD — "A Realistic Dataset and Baseline Temporal Model for Early Drowsiness
>   Detection"** (Ghoddoosian, Galib & Athitsos, UT Arlington, 2019): dataset público de 60
>   sujetos (~30h de video) etiquetado en 3 niveles (alerta/baja vigilancia/somnoliento), con
>   un modelo temporal (LSTM jerárquico) alimentado por *features* de parpadeo. Relevante para
>   Argus en dos sentidos: valida el enfoque de "LSTM sobre features de comportamiento" que ya
>   se usó en el notebook, y es un dataset público candidato para ampliar el entrenamiento más
>   allá de los sujetos propios grabados.
> - **UL-DD — "A Multimodal Drowsiness Dataset"** (Bodaghi et al., 2025): dataset multimodal
>   de 19 sujetos con video, señales biométricas (ritmo cardiaco, actividad electrodérmica,
>   SpO2, temperatura de piel) **y sensor de presión en el volante** — el mismo tipo de sensor
>   de agarre que Argus ya contempla. Candidato natural de referencia si más adelante se
>   desarrolla el módulo opcional de fusión biométrica.
> - **"Towards better microsleep predictions in fatigued drivers"** (Hidalgo-Gadea et al.,
>   *Ergonomics*, 2021): estudio en simulador real con 144 sujetos que muestra que rasgos de
>   personalidad e IQ influyen en cuándo aparece el microsueño de cada persona — evidencia
>   externa de que un umbral fijo único (tipo PERCLOS ≥1.5s para todos) no generaliza bien
>   entre individuos, lo cual refuerza por qué Argus prefiere un modelo estadísticamente
>   validado por feature en vez de una regla fija (ver Parte 6).

**B. Proyectos con el mismo problema, pero enfocados en algo distinto:**

> - **"Detección de somnolencia en conducción diurna y nocturna utilizando CNN MobileNet"**
>   (Enríquez Gallegos, trabajo de grado, Universidad Técnica del Norte, Ecuador, 2025): mismo
>   tipo de proyecto académico y mismo problema, pero resuelto distinto — clasifica
>   *frame por frame* con una CNN (MobileNetV3) entrenada sobre 12 datasets públicos,
>   reportando 97% de precisión detectando ojos cerrados (94% con lentes, 92% con lentes de
>   sol). Es un proyecto acotado al clasificador de visión en sí, sin backend, sin
>   comunicación entre dispositivos, ni sistema de alertas/actuación. Argus se diferencia en
>   dos ejes a la vez: clasifica *secuencias* temporales (LSTM sobre ventanas), no frames
>   sueltos, y extiende el problema más allá de la detección hacia un sistema distribuido
>   completo (borde + alerta + frenado + nube) — el mismo problema de fondo, alcance distinto.
> - Soluciones comerciales existentes (Geotab, Samsara): DMS basados en video-telemática ya
>   desplegados a nivel flota — referencia de qué "ya existe" en el mercado, y en qué se
>   diferencia Argus (edge-first, pensado para conectividad intermitente y robo de carga en
>   México, no solo detección).
> - Automatización comercial Nivel 4 en EUA (Aurora, Kodiak) — contraste directo con la
>   sección de "Inviabilidad de automatización total en México".

✅ **Sobre la pregunta anterior de si revisaba `docs/references/` a fondo:** ya lo hice — un
aviso aparte: `349054716_Towards_better_microsleep_predictio.pdf` **no es un PDF real**, es
una página de error de un servicio de descargas (HotDebrid) guardada con extensión `.pdf`. El
mismo paper sí está completo en `TowardsbettermicrosleeppredictionsinfatigueddriversexploringbenefitsofpersonalitytraitsandIQ.pdf`
(el que usé arriba), así que no se pierde nada — pero vale la pena borrar el archivo roto para
que no quede confundiendo la carpeta de referencias.

---

## Parte 4 — Descripción del desarrollo del proyecto modular

**Qué pide el formato:** responde "cómo se hizo" — metodología de equipo, requerimientos
principales, tecnologías, repositorio público, pruebas realizadas, proceso de implementación.

**Propuesta de contenido:**

**Metodología de trabajo.** Equipo de 3 integrantes que siguió **Scrum**: un **backlog de
requerimientos** (lista priorizada, no un tablero/herramienta específica) llevaba el registro
de qué piezas estaban pendientes, en progreso y terminadas, y cada semana se hacía una reunión
Scrum para revisar qué se había completado y asignar las siguientes tareas, buscando llegar a
cada reunión con avances concretos hacia un prototipo funcional.

*(Nota: confirmado que ni el formato ni los criterios de aprobación exigen una herramienta de
backlog específica — revisé `criteriosaprobacion_0.pdf`, `Formato_Proyecto_Modular V2.docx` y
el análisis propio del equipo sin encontrar el requisito — así que un backlog simple, sin
necesidad de nombrar una herramienta tipo Trello/GitHub Projects, es suficiente. Es evidencia
concreta de proceso de ingeniería de software, que sí pesa para el criterio 1.4.)*

**Requerimientos principales y backlog** (de `argus-descripción-proyecto.pdf`, Sección 4 —
MVP, con el estado de avance que se puede sustentar con lo que ya existe en el repo):

| # | Requerimiento                                                                                           | Estado                                                                                                                                          |
|---|---------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| 1 | Detección facial y ocular (visión artificial, % de cierre ocular)                                       | ✅ **Completado** — pipeline MediaPipe FaceLandmarker + LSTM entrenado y evaluado (`notebook/`)                                                 |
| 2 | Alertas sonoras en cabina ante microsueño (ojos cerrados >1.5s)                                         | ⏳ Pendiente — depende del firmware ESP32, aún no existe                                                                                        |
| 3 | Frenado autónomo preventivo si el conductor no reacciona                                                | ⏳ Pendiente — interfaz con CAN bus/AEB no implementada                                                                                         |
| 4 | Transmisión de alertas/ubicación por red celular                                                        | ⏳ Pendiente — protocolo Pi↔ESP32↔nube sin decidir (ver Parte 7)                                                                                |
| 5 | **Memoria local (buffer):** persistencia de alertas sin internet, reenvío automático al recuperar señal | 🔶 **En diseño** — arquitectura definida (SQLite, modo WAL) y andamiaje Docker/volumen ya en `cv-argus/`, módulo `buffer/` en sí aún sin código |
| 6 | Botón de pánico para incidentes de seguridad                                                            | ⏳ Pendiente — depende del firmware ESP32                                                                                                       |

✅ **Quité "Plataforma de gestión cloud" de la lista** — confirmado, no es un requisito explícito
de `criteriosaprobacion_0.pdf` (ese documento no exige una plataforma de gestión en sí; los
puntos de Módulo 3 piden mecanismos de sistema distribuido — cliente-servidor, comunicación
entre dispositivos, tolerancia a fallos, etc., no un dashboard). Ese ítem venía de la lista de
producto en `argus-descripción-proyecto.pdf`, no de los criterios del comité, así que sacarlo
del backlog no les resta nada de cara a la evaluación. Lo que sí es real y ya usan — **Docker**
— queda representado en "Tecnologías utilizadas" más abajo y sostiene directamente el criterio
1.4 (Ingeniería de Software: reproducibilidad, mismo contenedor en laptop y Pi) sin necesitar
prometer una plataforma cloud que no existe todavía.

\[COMPLETAR/CONFIRMAR: este estado lo reconstruí a partir de `cv-argus/README.md` y del
notebook — si el equipo ya avanzó algo de los pendientes (2, 3, 4, 6) que yo no vea
reflejado en el repo todavía, avísenme y lo actualizo antes de que esto pase al reporte
final.\]

**Tecnologías utilizadas** (de `CLAUDE.md` + `cv-argus/README.md` — ver también el hallazgo
crítico arriba sobre cuál arquitectura es la vigente):
- **Modelo/IA:** Python, MediaPipe FaceLandmarker, TensorFlow/Keras (LSTM +
  `GeometricRatioFeatureLayer` custom), scikit-learn (RandomForest baseline), pandas/joblib —
  todo dentro de `notebook/ArgusMLModel.ipynb`.
- **Borde (Raspberry Pi 5):** contenedor Docker (`python:3.11-slim-bookworm`, elegido sobre
  Alpine porque MediaPipe/TensorFlow solo publican wheels `manylinux`/glibc), `picamera2`
  para la cámara CSI, `gdown` para descargar el modelo entrenado desde Drive al iniciar el
  contenedor (sin hornear el modelo en la imagen).
- **Microcontrolador (ESP32):** **C** (Arduino framework o ESP-IDF) con una librería de
  parseo GPS/NMEA para leer el módulo de geolocalización por UART (p. ej. TinyGPS++ es la
  opción estándar en ese ecosistema — \[confirmar si ya eligieron una librería/módulo GPS
  específico, o si sigue abierto\]).
- **Persistencia local:** SQLite (modo WAL recomendado, por lectura/escritura concurrente
  entre el orquestador que encola alertas y el sender que las despacha).
- **Backend/nube (planeado, sin implementar aún):** Django/Flask/FastAPI, SQL o MongoDB (sin
  decidir). Alcance del MVP: captura de geolocalización y dirección de ida/vuelta del viaje
  (origen-destino); **OSRM queda como mejora futura**, no como parte del backend comprometido
  en este documento — ver Parte 9.
- **Frontend (planeado, sin implementar aún):** React + react-leaflet (mapa mostrando
  geolocalización y origen/destino capturados; sin ruteo calculado por OSRM en el MVP).

**Repositorio:** [`github.com/alecksandr26/Argus`](https://github.com/alecksandr26/Argus/tree/main)
— repositorio público. \[Falta solo: licencia y versión/commit puntual a citar en la portada,
que el formato pide explícitamente ("Repositorio de código", "Versión actual del código",
"Licencia legal código") — si no han elegido una licencia todavía, es la única pieza que
sigue pendiente aquí.\]

**Pruebas realizadas:** del notebook — validación estadística por feature contra nivel de
somnolencia (Kruskal-Wallis: EAR H=1985.27 p≈0, MAR H=476.41 p≈9.9e-101, eyeBlinkRight
H=1823.48 p≈0, mouthFunnel H=709.80 p≈3.7e-151 — las 4 features probadas resultaron
significativas) y comparación de dos clasificadores: RandomForest (95.66% accuracy ⚠️) vs. LSTM
(98.55% accuracy ⚠️, elegido como modelo final — ver Parte 6), split 80/20 agrupado por sujeto
(`GroupShuffleSplit`). *(⚠️ Los dos accuracy de arriba son del split viejo, no del
`GroupShuffleSplit` ya corregido en el código — ver el hallazgo en Parte 6/1: falta correr
`02_model_training.ipynb` una vez para tener números reales.)*

**Proceso de implementación:** pipeline lineal de notebook (Colab, con Google Drive como
almacenamiento) → extracción de features en ventanas deslizantes (10 fps, ventanas de 1-6s,
`stride` de 1s, umbral de validez de pose de 20°) → modelo de despliegue
`LstmGeometricFeatureModel` que empaqueta todo el preprocesamiento dentro de un solo
artefacto Keras → carga de ese artefacto en `cv-argus` vía `gdown` para inferencia en vivo en
el Pi.

✅ **Resuelto** — repo confirmado y público: `github.com/alecksandr26/Argus`. Nota aparte, no
bloqueante: ese mismo repo trae `docs/` con PDFs de criterios y borradores de titulación
mezclados con el código — no es un problema para el formato (no pide que el repo sea *solo*
código), pero si prefieren que el material interno de titulación no quede público junto al
código, es su decisión, no algo que el formato exija resolver.

---

## Parte 5 — Justificación de Arquitectura y Programación de Sistemas (Módulo 1)

**Qué pide el formato/criterios:** decidir lenguajes de programación; emplear bases de
datos y/o estructuras de datos; decidir metodología de programación; argumentar con
elementos de Ingeniería de Software; estructurar el modelado del sistema.

**Propuesta de contenido:**

- **1.1 Lenguajes de programación:** Python en todo el pipeline de IA (notebook y
  `cv-argus`) — justificado por el ecosistema maduro de MediaPipe/TensorFlow/OpenCV, que son
  librerías con las que el equipo ya construyó y validó el modelo. **C** en el firmware del
  ESP32 (Arduino framework/ESP-IDF) — estándar para ese microcontrolador y suficiente para
  lectura de GPS por UART, control de GPIO (alarma) y el resto de tareas de baja complejidad
  que le tocan.
- **1.2 Bases de datos / estructuras de datos:**
  - SQLite como cola de buffer local (justificar: sin servidor/daemon, es una librería que
    abre un archivo directo desde el proceso — encaja con un dispositivo de borde que puede
    perder conectividad; modo WAL para lectura/escritura concurrente entre el productor
    —orquestador— y el consumidor —sender— de la cola).
  - Estructura de ventana deslizante (`sliding window`) de tamaño variable (1-6s) sobre la
    secuencia de frames, con descarte total de ventanas que contienen algún frame inválido
    (cara no detectada) en vez de imputar/rellenar — decisión de diseño a justificar
    (prioriza calidad de la señal de entrenamiento sobre cantidad de datos).
  - Buffer interno del modelo desplegado: un `tf.Variable` de forma `(1, 60, 59)` que actúa
    como cola circular de los últimos 60 frames — el modelo mismo mantiene el estado, así que
    el código de inferencia no necesita implementar su propio buffer de secuencia.
  - **MongoDB** como base de datos del backend/nube (planeada, sin implementar aún — ver
    Parte 4): esquema flexible por documento, natural para los recursos `users`, `trucks`,
    `drivers`, `alerts`, `routes` que no comparten una forma rígida entre sí (p. ej. un
    `alert` de pánico vs. uno de somnolencia traen payloads distintos). Acceso vía un
    **ORM/ODM** en FastAPI (p. ej. Beanie o PyMongo con modelos Pydantic) en vez de queries
    crudas, para mantener validación de esquema y tipado consistente con el resto del backend
    Python.
- **1.3 Metodología de programación:** Scrum, con reuniones semanales/quincenales de avance y
  asignación de tareas (ver Parte 4).
- **1.4 Ingeniería de software:** separación de responsabilidades explícita en `cv-argus`:
  `model/` (inferencia), `pipeline/` (captura), `orchestrator/` (decisión), `buffer/`
  (persistencia), `sender/` (comunicación), `alerts/` (modelo de datos) — cada módulo con una
  única responsabilidad y dependencias en una sola dirección (todos dependen de `alerts/`,
  nunca al revés). Docker como mecanismo de reproducibilidad (misma imagen en laptop de
  desarrollo y en el Pi 5, sin "funciona en mi máquina").
- **1.5 Modelado del sistema:** el diagrama `docs/designs/semantic-design` — frontera
  explícita entre lo que decide (Pi, IA) y lo que actúa (ESP32, hardware de seguridad), y
  entre el borde (tiempo real, crítico) y la nube (gestión, no crítico en tiempo real).

✅ Con la arquitectura del backend ya confirmada como **FastAPI + MongoDB** (ver Parte 1.2), y
el resto del stack de borde sin cambios, `semantic-design.drawio` sigue siendo el diagrama
vigente para "modelado del sistema" en la Parte 5 — la frontera Pi/ESP32 y borde/nube que
representa no depende de si el backend específico es Django o FastAPI, solo de que sea el
lado izquierdo de la tabla del hallazgo crítico y no el AWS-serverless. Nota aparte: existe un
`argus_diagrama_v2.png` más reciente en la misma carpeta que no revisé a fondo — vale la pena
que alguien del equipo confirme que no es ya la versión AWS antes de usarlo, para no meter por
accidente el diagrama equivocado en el reporte.

---

## Parte 6 — Justificación de Sistemas Inteligentes (Módulo 2)

✅ **Resuelto — el modelo secuencial final se queda como LSTM, no cambia a feedforward.**
Revisé a fondo `02_model_training.ipynb` (la celda markdown "Design Decision: Persistent
(`stateful=True`) Hidden State", justo antes de la definición del modelo) para confirmarlo con
el código real, no solo de palabra: un LSTM plano (`stateful=False`, que es lo que ya está
implementado) ya calcula recurrencia real — compuertas `h_t`/`c_t` genuinas — *dentro* de cada
llamada, sobre la ventana completa de 60 frames. Eso es exactamente lo que permite capturar
"los ojos llevan cerrándose los últimos segundos" (duración/velocidad del cierre ocular a lo
largo de la ventana) — una señal que una capa `Dense`/feedforward, al no tener ese mecanismo de
recurrencia, no puede replicar estructuralmente por más ancha o profunda que sea. Esa es la
justificación técnica real, no solo "porque ya lo teníamos hecho".

🔴 **Pero surgió un hallazgo nuevo, más importante para esta sección que LSTM-vs-feedforward:
los números de accuracy de abajo (98.55%/95.66%) todavía no están re-verificados contra el
código que corre hoy.** Al revisar `02_model_training.ipynb` directamente, ninguna de sus
celdas tiene `execution_count`/`outputs` — es decir, **este notebook nunca se ha corrido** desde
que se dividió del monolito original y se corrigió el bug de fuga de datos entre train/test
(el split pasó de `train_test_split` estratificado, que mezclaba sujetos, a `GroupShuffleSplit`
agrupado por sujeto — ver la celda "Design Decision" citada arriba, que documenta el fix). Los
números 98.55%/95.66% que siguen citados abajo y en las Partes 1, 4 y 8 vienen de la corrida
*anterior*, con el split que sí tenía fuga entre train/test — no hay garantía de que se
sostengan una vez que alguien corra `02_model_training.ipynb` con el split corregido. **Hay que
volver a correr el notebook y actualizar estos números en las cuatro secciones antes de dar el
reporte por final** — mientras tanto, tratar 98.55%/95.66% como placeholder, no como resultado
verificado.

**Qué pide el formato/criterios:** cubrir al menos una rama de IA de la lista (redes
neuronales / ML / visión artificial / ...); formular el modelo matemático; justificar la
selección de algoritmos.

**Propuesta de contenido:**

- **Ramas cubiertas:** redes neuronales (LSTM), aprendizaje automático (RandomForest como
  baseline comparativo), visión artificial (MediaPipe FaceLandmarker: detección de 478
  landmarks faciales + blendshapes + matriz de rotación de cabeza). Cubre 3 de las 9 ramas
  listadas en el criterio 2.1 — más que suficiente (solo se exige una).
- **Modelo matemático:** la capa `GeometricRatioFeatureLayer` calcula, por frame, EAR (Eye
  Aspect Ratio) y MAR (Mouth Aspect Ratio) a partir de razones geométricas entre landmarks
  oculares/de boca, más pitch/yaw/roll de cabeza desde la matriz de rotación — produciendo un
  vector de 59 features (7 geométricas + 52 blendshapes de MediaPipe) por frame \[nota menor:
  el modelo entrenado que corrió en el notebook terminó usando 58 features de entrada, no 59
  — vale la pena confirmar a qué se debe esa diferencia de una antes de citar el número en el
  reporte final\]. Estas secuencias, en ventanas de hasta 60 timesteps (6s a 10fps), alimentan
  la arquitectura `LSTM(128, return_sequences=True) → Dropout(0.3) → LSTM(64) → Dropout(0.3) →
  Dense(6, softmax)` (145,542 parámetros entrenables), cuya salida es un softmax sobre 6
  clases (niveles de somnolencia 1-6).
- **Justificación de algoritmos:** Spearman (por ser variables ordinales — los niveles de
  somnolencia no son una escala continua arbitraria) y Kruskal-Wallis por feature (EAR, MAR,
  eyeBlinkRight y mouthFunnel probados, los 4 con p≈0 o prácticamente cero — significativos),
  usados para decidir qué features realmente correlacionan con el nivel de somnolencia antes
  de entrenar; comparación LSTM (98.55% accuracy \[⚠️ pendiente de re-verificar, ver nota roja
  arriba\]) vs. RandomForest (95.66% accuracy \[ídem\]) como baseline, seleccionando LSTM como
  modelo final por capturar dependencia temporal entre frames en vez de solo un frame promedio
  por ventana. El entrenamiento del LSTM también usa `class_weight` (calculado desde la
  distribución real de clases del set de entrenamiento, igual que ya hacía el RandomForest con
  `class_weight='balanced'`) — relevante de mencionar porque el nivel 6 ("entrando en
  microsueño") es, según la nota de metodología de recolección de `01_dataset_creation.ipynb`,
  el único nivel actuado en vez de auto-grabado bajo fatiga real, lo que lo hace plausiblemente
  el más escaso en el dataset y a la vez el más crítico de no descuidar.
- **Justificación de MediaPipe FaceLandmarker como detector base:** internamente es una CNN
  entrenada por *regresión* — no clasificación — sobre dos tareas encadenadas: localizar la
  cara en el frame (bounding box) y luego regresar las coordenadas continuas de cada uno de
  los 478 landmarks faciales (en vez de clasificar el frame en categorías discretas). Ese
  diseño (arquitectura ligera tipo BlazeFace/MediaPipe, cuantizable, pensada para correr en
  CPU/NPU de dispositivos móviles y embebidos sin GPU dedicada) es precisamente el motivo por
  el que se eligió sobre alternativas más pesadas (p. ej. face-mesh models de mayor cómputo o
  detectores basados en transformers): es la única opción de las evaluadas con inferencia en
  tiempo real ya validada en hardware de borde de bajo costo — el mismo perfil de la Raspberry
  Pi 5 donde corre Argus.

📎 **Nota de precisión sobre esta sección, actualizada** — versiones anteriores de esta nota
decían que el split real era `train_test_split` estratificado por clase (80/20, mezclando
sujetos) y que el equipo había decidido aceptarlo tal cual. Eso ya no describe el código
actual: `02_model_training.ipynb` corrigió el split a `GroupShuffleSplit` agrupado por sujeto
(elimina la fuga de ventanas casi-duplicadas del mismo sujeto entre train y test) — pero, como
dice la nota roja de arriba, ese notebook corregido todavía no se ha ejecutado ni una vez. Así
que el split *como código* ya es el correcto (agrupado por sujeto), pero los *números*
reportados en esta sección siguen siendo del split viejo. En el reporte final, una vez que se
corra el notebook: describir el split como "80/20 agrupado por sujeto (`GroupShuffleSplit`)",
ya no como "estratificado por clase".

✅ **Resuelto** — el criterio 2.3 pide *justificar la selección* del algoritmo, no solo
nombrarlo. `Argus_Definicion_Tecnica.docx.pdf` justificaba MediaPipe Face Mesh diciendo que
tiene "alta precisión y baja tasa de falsos positivos ante variaciones de iluminación", pero
esa prueba de robustez a iluminación no existe en el notebook, así que no se usa en el
reporte. La justificación que sí se sostiene con evidencia real, y que ya quedó arriba: (1)
arquitectura — CNN de regresión para detección de cara + landmarks, diseñada para edge/móvil,
que es la razón técnica de por qué corre viable en la Pi 5; y (2) desempeño del clasificador
de somnolencia aguas abajo — Spearman/Kruskal-Wallis por feature y la comparación LSTM vs.
RandomForest (con la limitación del split declarada, no escondida).

---

## Parte 7 — Justificación de Sistemas Distribuidos (Módulo 3)

**Qué pide el formato/criterios (leer con cuidado, tiene dos notas que descalifican
soluciones "fáciles"):**
- Sistema descentralizado que comparte recursos, cubriendo al menos una opción de la lista
  (concurrencia, BD dividida, red de sensores descentralizada, procesamiento distribuido,
  tolerancia a fallos con algoritmo descentralizado, tiempo real por sockets, seguridad en
  múltiples arquitecturas).
- Modelo cliente-servidor o punto a punto — **"No es válido solo utilizar un servicio basado
  en el modelo cliente/servidor ya creado"**.
- Comunicación entre al menos dos dispositivos — **"No se considera distribuido utilizar
  diferentes interfaces que consultan a un sistema centralizado"**.
- Justificar los protocolos de comunicación.

🔴 **Este módulo sigue siendo el de más riesgo si el reporte se apoya solo en el tramo REST —
pero ya hay piezas reales para no depender de eso.** Las dos notas en negritas descalifican
explícitamente el patrón "cliente que le pega a un API REST centralizado, sin más" — que es
lo que describe tal cual el tramo ESP32↔backend (hoy **FastAPI**, no Django — ver Parte 1.2)
si se queda solo. Un backend FastAPI + clientes (ESP32, frontend React) consumidos por HTTP
**no cuenta por sí solo** como sistema distribuido bajo estas reglas, sin importar cuántos
clientes lo consulten — son clientes de *un mismo* sistema centralizado. Eso no descalifica
todo el proyecto: lo que sí cuenta (abajo) está en la relación Pi↔ESP32 y en dónde ocurre el
cómputo pesado, no en el tramo hacia la nube.

💡 **Dónde sí está el mérito distribuido real del proyecto** (esto es lo que hay que resaltar
en el reporte, no la parte REST):
- **Ojo con un argumento tentador pero débil — "muchos camiones, cada uno con su propia IA
  corriendo en paralelo" no es, por sí solo, el tipo de distribución que pide el criterio.**
  Replicar la misma arquitectura estrella (cada Pi+ESP32 hablándole solo a un backend
  central) en N camiones sigue siendo N clientes de *un mismo* sistema centralizado —
  exactamente el patrón que la segunda nota en negritas descalifica, solo que multiplicado
  por flota. Lo que sí cuenta como cómputo distribuido real es que **el procesamiento pesado
  (inferencia del modelo LSTM sobre video) ocurre en el borde, dentro de cada camión, en vez
  de mandar video crudo a un servidor central para procesarlo ahí** — eso sí es
  "procesamiento distribuido" (una de las opciones del criterio 3.1.4): la carga de cómputo
  está descentralizada por diseño, no por escala. Ese punto, combinado con la comunicación
  directa Pi↔ESP32 de abajo, es donde se sostiene el módulo — no en "hay muchas copias
  corriendo".
- **Dos dispositivos físicos reales comunicándose directamente:** Raspberry Pi 5 (decide) ↔
  ESP32 (actúa) — esto sí cumple la nota del criterio 3.3, porque son dos dispositivos
  distintos hablando entre sí, no "interfaces" contra un servidor.
- **Tolerancia a fallos con buffer local (criterio 3.1.5):** la cola SQLite que persiste
  alertas cuando no hay conectividad y las reenvía automáticamente al recuperar señal — esto
  es una justificación real de "sistema tolerante a fallos", siempre que se documente el
  *algoritmo* de resync (qué pasa si dos alertas llegan fuera de orden, cómo se marca
  sent/unsent, qué pasa si el reenvío falla a medias), no solo mencionar que "hay un buffer".
- **Concurrencia real (criterio 3.1.1):** `orchestrator/` escribiendo nuevas alertas y
  `sender/` leyéndolas/marcándolas enviadas desde hilos distintos sobre el mismo archivo
  SQLite (de ahí la recomendación de modo WAL en `cv-argus/README.md`) — esto sí es
  concurrencia real justificable con un algoritmo, no un framework preconstruido.
- **Protocolo Pi↔ESP32 decidido: Bluetooth, con el ESP32 haciendo *pull* periódico sobre la
  cola SQLite del Pi** — ver detalle abajo.

✅ **Resuelto (protocolo Pi↔ESP32): Bluetooth, con el ESP32 como el lado que hace polling.**
Diseño acordado: el ESP32 se conecta por Bluetooth (a definir Classic SPP vs. BLE — SPP es
más simple si lo tratan como un socket serie virtual) y periódicamente hace *pull* de la cola
SQLite del Pi (filtrando registros `unsent`), en vez de que el Pi empuje (*push*) las alertas
activamente. Esto responde directo la duda de si "cliente-servidor" descalifica el diseño: sí
es un patrón cliente-servidor en el sentido de que alguien inicia la conexión y alguien más
responde, pero **no es el patrón que la nota del criterio excluye** — esa nota descalifica
*usar un servicio cliente/servidor ya construido por alguien más* (p. ej. pegarle con un
cliente HTTP genérico a la API REST de FastAPI/Django); aquí Pi y ESP32 son dos dispositivos
que ustedes mismos programan en ambos extremos de un protocolo propio sobre Bluetooth, así
que sigue calificando como comunicación directa entre dos dispositivos (criterio 3.3) con
protocolo propio, no de fábrica.

Punto a documentar con cuidado en el reporte (esto es lo que realmente sostiene 3.1.5/3.4, no
solo nombrar "Bluetooth"): el *algoritmo* de polling — cada cuánto hace pull el ESP32, qué
pasa si el Bluetooth se desconecta a medio pull (¿se reintenta el mismo registro o se
descarta?), cómo se marca un registro como `sent` (¿lo marca el Pi cuando el ESP32 confirma
recepción, o lo marca el ESP32 de forma remota?), y qué pasa si el ESP32 hace pull pero
después falla al reenviar por HTTP al backend (para no perder ni duplicar la alerta). Ese es
el nivel de detalle que el criterio 3.1.5 espera de "tolerancia a fallos con algoritmo
descentralizado" — no basta con decir "usamos Bluetooth con polling".

💡 **Nota sobre serial vs. Bluetooth** (mi recomendación previa había sido serial/UART por ser
más determinístico): Bluetooth es una elección igual de válida y más práctica si Pi y ESP32
no van montados pegados uno al otro en la cabina, sin cable de por medio — pero trae dos
riesgos a mencionar y mitigar en el reporte: (1) interferencia/EMI dentro de una cabina
metálica de camión, y (2) latencia de descubrimiento/reconexión si se cae el enlace. Ambos se
cubren con el mismo buffer SQLite que ya tienen (si el Bluetooth se cae, las alertas
simplemente se acumulan sin perderse hasta que el ESP32 vuelve a hacer pull), así que el
cambio de serial a Bluetooth no debilita el argumento de tolerancia a fallos — al contrario,
lo pone a prueba de verdad.

❓ **Pregunta secundaria (parcialmente resuelta)** — el protocolo propio de sincronización que
pedía esta pregunta ya existe, pero está del lado Pi↔ESP32 (el polling Bluetooth con
ack/marcado sent-unsent), no del lado ESP32↔backend, que sigue siendo HTTP plano a FastAPI.
Eso está bien: el criterio no exige que *todos* los tramos sean protocolos propios, solo que
el proyecto no dependa *únicamente* del tramo cliente/servidor de fábrica, y ya no depende de
eso. Si más adelante quieren reforzar también el tramo cloud (opcional, no bloqueante), la
sugerencia de WebSocket para posición en tiempo real sigue en pie como mejora, no como
requisito.

---

## Parte 8 — Resultados obtenidos del proyecto

🔶 **En definición (no cerrado) — pero por una razón distinta a antes.** La arquitectura ya no
es lo abierto: LSTM quedó confirmado como modelo final (Parte 6) y Pi↔ESP32↔nube ya tiene
diseño (Parte 7). Lo que sigue bloqueando esta sección son los **números**: el accuracy/F1
citado abajo viene de una corrida vieja, previa al fix del split (`GroupShuffleSplit`) que ya
está en el código pero no se ha vuelto a ejecutar (ver Parte 6) — así que "resultados obtenidos"
no se puede redactar en definitivo hasta correr `02_model_training.ipynb` una vez y traer
números reales. Contenido de abajo dejado tal cual por ahora como referencia de estructura, no
como texto final.

**Qué pide el formato:** resumir resultados en orden lógico; cubrir (1) objetivos realmente
alcanzados al término del desarrollo, y (2) su relación con la solución planteada. **Escrito
en tiempo pasado.**

🔴 **Posible inconsistencia de alcance:** el formato asume un proyecto *terminado*, pero
`cv-argus/README.md` dice explícitamente que de los 6 módulos planeados (`model/`,
`pipeline/`, `orchestrator/`, `buffer/`, `sender/`, `alerts/`) **ninguno existe como código
todavía** — solo el andamiaje (Docker, packaging, entry point que solo verifica el entorno).
Tampoco existe el firmware del ESP32, ni el backend, ni el frontend.

❓ **Pregunta** — para la fecha de entrega, ¿qué parte del sistema sí van a tener corriendo de
punta a punta? ¿Solo el notebook (modelo entrenado + validación), o también inferencia en
vivo en el Pi (aunque sea sin ESP32/nube todavía)?

💡 **Propuesta** — delimitar "resultados" honestamente a lo que sí esté terminado y
verificado al momento de escribir esta sección, en vez de redactar en pasado algo que sigue
en planeación (el propio criterio de no-aprobación **A** es "objetivos no alcanzables en los
tiempos establecidos", y de no-aprobación general existe el riesgo de que el comité note la
brecha entre lo narrado y lo demostrable). Ejemplo de qué sí se puede reportar como resultado
real hoy: "se extrajo y validó estadísticamente un conjunto de features geométricas y de
blendshapes por frame (Kruskal-Wallis significativo en las 4 features probadas); se
entrenaron y compararon dos clasificadores — RandomForest (95.66% accuracy, F1 ponderado 0.96)
y LSTM (98.55% accuracy, F1 ponderado 0.99), split 80/20 estratificado por clase — seleccionando
**LSTM** como modelo final; el modelo final se empaquetó en un artefacto único reproducible
(`LstmGeometricFeatureModel`) listo para inferencia en vivo." Dejar tanto la validación de
desempeño en hardware real (Raspberry Pi: latencia, uso de CPU/memoria) como la integración de
borde completa (Pi+ESP32+buffer+nube) explícitamente en la Parte 9 como "trabajo a futuro", no
disfrazadas de resultado ya alcanzado.

---

## Parte 9 — Conclusiones y trabajo a futuro

🔶 **En definición (no cerrado)** — misma razón que la Parte 8: el texto de abajo es
estructura de referencia, no conclusión final, hasta que se corra `02_model_training.ipynb`
con el split ya corregido y se traigan números reales (ver Parte 6).

**Qué pide el formato:** generalizaciones del proceso completo, sin repetir el resumen
literalmente; evitar afirmaciones sobre partes no terminadas; recomendaciones/mejoras al
final.

**Propuesta de contenido:**

> El desarrollo confirmó que un enfoque geométrico-secuencial (EAR/MAR + pose + blendshapes
> sobre ventanas temporales cortas) es una base viable y estadísticamente respaldada para
> clasificar niveles de somnolencia, sin depender de reglas fijas de umbral (como PERCLOS a
> secas) que no capturan la variabilidad entre sujetos. \[Completar con la conclusión real una
> vez que exista comparación LSTM vs. RandomForest con números.\] El diseño edge-first —
> procesamiento en el borde por camión, sincronización tolerante a fallos hacia la nube — es
> además pensado desde el inicio para escalar por flota: sumar camiones no implica escalar un
> servidor central de inferencia, porque cada unidad ya trae su propio cómputo. Esa
> escalabilidad, junto con el costo humano y económico de los accidentes por fatiga descrito
> en la Parte 2, es la motivación de fondo del proyecto: una barrera preventiva de bajo costo
> tiene margen para reducir una fracción de esas pérdidas si se despliega a nivel flota.
>
> **Trabajo a futuro:**
> - Validar el desempeño del modelo LSTM ya entrenado corriendo en hardware real (Raspberry
>   Pi 5): latencia de inferencia por frame, uso de CPU/memoria en ARM — el accuracy/F1
>   reportado en la Parte 8 viene del entrenamiento en notebook, no de una corrida en el Pi.
> - Completar e integrar los módulos `orchestrator/`, `buffer/`, `sender/` de `cv-argus` con
>   hardware real (Pi 5 + cámara CSI) para validar la inferencia en vivo fuera de la
>   simulación del notebook.
> - Prototipar el firmware del ESP32 sobre el diseño ya decidido en la Parte 7 (Bluetooth,
>   polling de la cola SQLite del Pi, HTTP hacia el backend FastAPI).
> - Implementar el backend/frontend cloud sobre **FastAPI + MongoDB** (ver Parte 1.2),
>   capturando geolocalización y dirección de ida/vuelta del viaje; **ruteo calculado vía
>   OSRM queda como mejora futura**, no como parte del MVP. Si el avance del proyecto lo
>   permite, explorar además una evolución hacia un backend *cloud-native*/serverless (p. ej.
>   AWS) como mejora de escalabilidad — sin comprometer aquí servicios o proveedor específico,
>   dado que hoy es una dirección posible, no una decisión tomada ni código existente.
> - Módulos opcionales fuera del MVP: segmentación de carriles (Canny/Hough), monitoreo
>   biométrico multimodal (ritmo cardiaco/respiración, hasta ~84% de precisión reportada en
>   literatura), detección de obstáculos (ADAS), respaldo satelital.
> - **No opcional, bloqueante para cerrar Partes 1/4/6/8:** correr `02_model_training.ipynb`
>   una vez — el split ya está corregido en el código (`GroupShuffleSplit` agrupado por
>   sujeto), pero el notebook nunca se ha ejecutado con ese fix, así que los accuracy/F1 de
>   RandomForest y LSTM citados hoy en el documento son de la corrida vieja (split con fuga
>   entre sujetos) y hay que reemplazarlos por los números reales antes de dar el reporte por
>   final.

❓ **Pregunta** — el formato pide explícitamente *no* hacer afirmaciones sobre costos/beneficios
económicos salvo que haya datos y análisis reales. `argus-descripción-proyecto.pdf` sí cita
cifras económicas (450k vs 180k USD, 150 mil millones de pesos en accidentes) — ¿esas cifras
son solo contexto del planteamiento del problema (Parte 2), o esperan que el reporte también
haga un análisis costo-beneficio propio de Argus? Si es lo segundo, es trabajo adicional no
mencionado en ningún documento que revisé.

---

## Parte 10 — Reconocimientos

**Qué pide el formato:** agradecer apoyo externo al equipo (instituciones o personas fuera
del equipo de trabajo).

❓ **Pregunta** — tu nota en `Analisis formato proyecto modular.docx` menciona un posible
contacto de apoyo (arcastechnologies.com) si el equipo se atora en algún módulo. Si
efectivamente terminan recibiendo ayuda de ahí, esta es la sección donde va — pero es una
decisión de relación/crédito que les toca a ustedes, no algo que yo pueda proponer contenido
para todavía.

✅ **Resuelto — sí califican compañeros de otros equipos/módulos que ayudaron como
consultores informales, no solo instituciones.** El formato pide "apoyo externo... personas
fuera del equipo de trabajo" en términos generales, sin restringir a empresas o asesores
formales — un compañero de otro módulo que aportó conocimiento puntual (p. ej. de
electrónica, fuera de las 3 personas del equipo) encaja igual de bien que un contacto
institucional.

**Propuesta de contenido:**

> El equipo agradece el apoyo de:
> - **Prof. Mario [apellido — confirmar]**, asesor del proyecto, por su guía a lo largo del
>   desarrollo.
> - **Esmeralda [apellido — confirmar]**, compañera de electrónica, por su apoyo en
>   \[confirmar en qué parte específica ayudó — p. ej. diseño/selección de sensores, revisión
>   del circuito del ESP32 — para no dejarlo genérico\].

\[COMPLETAR: apellidos y el detalle concreto de en qué ayudó cada quien, para que el
agradecimiento sea específico y no un nombre suelto sin contexto — y confirmar si hay alguien
más del equipo que quieran incluir aquí.\]

---

## Parte 11 — Referencias

Ya hay una lista sólida en `argus-descripción-proyecto.pdf` (NOM-087, estudios de fatiga en
transporte de carga mexicano, UL-DD, artículos de industria), más los 4 papers de
`docs/references/` ya identificados y leídos en la Parte 3 (RLDD/Ghoddoosian 2019, UL-DD/Bodaghi
2025, Hidalgo-Gadea 2021, y el trabajo de grado de Enríquez Gallegos 2025). Falta:
- Convertir todo eso al formato numerado IEEE (`[1]`, `[2]`...) que exige la plantilla, con
  cursivas en los campos correspondientes según el tipo de fuente (libro, artículo, sitio web,
  tesis, etc. — la plantilla trae un ejemplo de cada tipo, incluyendo el de tesis que aplica
  directo al trabajo de grado de UTN).
- Sumar las referencias técnicas específicas de MediaPipe/TensorFlow/el paper de LSTM que
  justifique la arquitectura del modelo, si están citando alguno en el notebook.

---

## Resumen de todas las preguntas abiertas (para decidir en equipo/con el asesor)

1. ~~🔴 Arquitectura vigente~~ — ✅ **Resuelto:** FastAPI + MongoDB + MediaPipe
   FaceLandmarker/LSTM (lo que hay en código, ver Parte 1.2 y Parte 6) es la base del documento
   de registro. AWS-serverless queda como dirección futura posible, mencionada en condicional
   en la Parte 9, sin detalle técnico comprometido en las Partes 4, 5, 6 o 7.
2. ~~¿LSTM o red feedforward?~~ — ✅ **Resuelto:** se queda **LSTM** — confirmado releyendo
   `02_model_training.ipynb`, no solo de palabra (ver Parte 6 para el porqué técnico:
   recurrencia real dentro de cada llamada, que una capa `Dense` no puede replicar).
3. 🔴 **Nuevo, bloqueante:** los accuracy/F1 citados en Partes 1, 4, 6 y 8 (LSTM 98.55%,
   RandomForest 95.66%) son de una corrida **vieja**, con un split train/test que sí tenía fuga
   de datos entre sujetos. El código ya corrigió ese split (`GroupShuffleSplit` agrupado por
   sujeto, en `02_model_training.ipynb`), pero **ese notebook nunca se ha ejecutado** — hay que
   correrlo una vez y reemplazar los números en las cuatro secciones antes de dar el reporte
   por final (ver Parte 6 para el detalle completo).
4. ~~¿Cuál es la metodología de trabajo del equipo?~~ — ✅ **Resuelto:** Scrum, reuniones
   semanales/quincenales de avance y asignación de tareas.
5. ~~¿Repo público listo?~~ — ✅ **Resuelto:** `github.com/alecksandr26/Argus`. Falta solo
   licencia y el commit/versión puntual a citar en la portada.
6. ~~¿Protocolo Pi↔ESP32 decidido (serial vs. red)?~~ — ✅ **Resuelto:** Bluetooth, con el
   ESP32 haciendo *pull* periódico sobre la cola SQLite del Pi (ver Parte 7).
7. ~~Para Módulo 3: ¿qué parte del lado cloud implementan "a mano"?~~ — ✅ **Resuelto, del lado
   Pi↔ESP32:** el protocolo propio de polling+ack por Bluetooth ya cubre esto (ver Parte 7). El
   tramo ESP32↔backend sigue siendo HTTP plano a FastAPI — aceptado, no bloqueante (el criterio
   no exige que *todos* los tramos sean protocolo propio).
8. ¿Qué va a estar realmente terminado end-to-end para la fecha de entrega? — define qué se
   puede escribir en pasado en la Parte 8 sin exagerar el alcance.
9. ¿El reporte necesita un análisis costo-beneficio propio de Argus, o las cifras económicas
   son solo contexto del problema?
10. ~~¿Quieren que revise `docs/references/` a fondo?~~ — ✅ **Resuelto:** ya se leyeron los 5
    archivos (uno resultó ser un PDF roto — ver aviso en Parte 3). Falta solo el paso final:
    formatearlos junto con los de `argus-descripción-proyecto.pdf` como lista IEEE numerada
    para la Parte 11 — avisen cuando lo necesiten y la armo.

---

*Generado a partir de: `CLAUDE.md`, `README.md`/`src/cv-argus/README.md`,
`notebook/01_dataset_creation.ipynb`, `notebook/02_model_training.ipynb` (celdas leídas
directamente — sin `execution_count`/outputs, ver Parte 6 — estadística, `class_weight`,
diseño LSTM y el fix de split), `notebook/03_deployment_export.ipynb` (armado del modelo de
despliegue `LstmGeometricFeatureModel`), `docs/document/Analisis formato proyecto
modular.docx`, `docs/criteria/Formato_Proyecto_Modular V2.docx`,
`docs/criteria/criteriosaprobacion_0.pdf`, `docs/argus-descripción-proyecto.pdf`,
`docs/Argus_Definicion_Tecnica.docx.pdf`, `docs/references/*` (5 archivos, 1 resultó ser un
PDF roto — ver Parte 3). `notebook/ArgusMLModel.ipynb` (el monolito original, retirado) fue la
fuente de los números de accuracy citados en varias secciones — ver el hallazgo de la Parte 6
sobre por qué esos números necesitan reemplazarse.*
