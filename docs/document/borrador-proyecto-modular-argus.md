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
| Modelo de IA | MediaPipe **Face Detector** (BlazeFace, recorte de rostro) → **CNN** entrenada sobre el recorte, cuyo embedding de 64 dimensiones (capa penúltima, congelada) se fusiona con 10 features geométricas de MediaPipe **FaceLandmarker** (EAR/MAR + blendshapes) por cada instante → **LSTM** sobre una ventana deslizante de hasta 100 frames (20s a 5fps). Clasificación **binaria** (`Not Drowsy` / `Drowsy`), no de 6 niveles. **84.24% accuracy / 0.8375 F1-macro** en sujetos no vistos — ver Parte 6 | MediaPipe **Face Mesh** cuantizado **INT8 + XNNPACK**, en **C++**, clasificación **PERCLOS** (umbral ≥1.5s) con **regresión logística o CNN-LSTM** sobre 68 landmarks |
| Backend | **Django/Flask/FastAPI** + SQL o MongoDB (sin decidir) + contenedor **OSRM** para rutas | **100% serverless en AWS**: IoT Core, Lambda, API Gateway, **DynamoDB**, Cognito, definido con **AWS CDK** |
| Frontend | **React + react-leaflet**, 3 roles (Root/Admin, Guardian, Truck Driver) | **React** + Amazon Location Service o Google Maps API |
| Comunicación camión↔nube | Protocolo sin decidir (candidato HTTPS), vía ESP32 | MQTT a AWS IoT Core, con **RockBLOCK/Iridium** satelital de respaldo |
| Sensor adicional | Sensor de agarre en el volante (grip sensor) | Sensores **FSR** (fuerza resistiva) en el volante |
| Qué existe como código | El pipeline completo de notebooks (creación de dataset + entrenamiento de 4 familias de modelo) y, en `cv-argus/`, el modelo desplegado corriendo de punta a punta: cámara → MediaPipe → inferencia → salida (`model/` y `pipeline/` terminados, no solo andamiaje) — **nada de AWS/CDK/DynamoDB existe** | Nada de esto existe como código en el repo |

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
> microsueño. El sistema combina un módulo de inferencia en el borde (Raspberry Pi 5, MediaPipe
> + una CNN cuyo embedding se fusiona con features geométricas faciales y se alimenta a una
> LSTM sobre una ventana temporal) con un microcontrolador (ESP32) responsable de alertas,
> frenado preventivo y comunicación, y un buffer local que garantiza continuidad sin cobertura
> celular. El modelo final (fusión CNN+LSTM) alcanzó **84.24% de exactitud** y **0.8375 de F1
> macro** en sujetos de prueba nunca vistos en entrenamiento — casi el doble del F1 de una CNN
> de un solo frame (0.5273) sobre los mismos sujetos. Los hallazgos confirman que combinar
> contexto temporal con características visuales crudas supera a un enfoque de un solo frame
> para clasificar somnolencia (binaria: alerta / somnoliento) en hardware de borde de bajo costo.

*(~140 palabras con los números ya puestos.)*

✅ **Resuelto — modelo final confirmado con números reales, no placeholder.** A diferencia de
versiones anteriores de este borrador (que citaban 98.55%/95.66% de una corrida vieja del
monolito `ArgusMLModel.ipynb` sobre 6 clases, nunca reproducida en el código actual), los
números de abajo sí vienen de una corrida real, documentada y verificada del pipeline vigente
— ver `notebook/CLAUDE.md`'s "`11_cnn_lstm_training_drive_pull.ipynb`'s fixed rerun" para el
detalle completo:

| Modelo | Accuracy (test) | F1 macro | Notas |
|---|---|---|---|
| CNN de un solo frame (recorte facial → CNN) | 59.64% | 0.5273 | primer resultado binario real, referencia — no es el modelo desplegado |
| **CNN+LSTM (embedding CNN congelado + features geométricas fusionadas → LSTM)** | **84.24%** (84.38% en el umbral de decisión elegido) | **0.8375** (0.8379 en el umbral) | **modelo final, el que despliega `cv-argus`** |

⚠️ **Caveats reales que hay que mantener en el reporte, no esconder:** es un solo fold
(`StratifiedGroupKFold`, sujetos nunca compartidos entre train/val/test), sin validación cruzada
todavía — la propia experiencia del proyecto con otros modelos ha mostrado variaciones de
hasta ±9 puntos de F1 macro entre folds con un número de sujetos similar. Tampoco se ha
corrido todavía de punta a punta contra hardware real de Raspberry Pi (ver Parte 9). Redactar
estos números como "el mejor resultado medido hasta ahora", no como "resultado validado en
producción".

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
>   dos ejes a la vez: clasifica *secuencias* temporales (embedding de CNN fusionado con
>   features geométricas, alimentando una LSTM sobre ventanas), no frames sueltos, y extiende
>   el problema más allá de la detección hacia un sistema distribuido
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
| 1 | Detección facial y ocular (visión artificial, % de cierre ocular)                                       | ✅ **Completado** — pipeline entrenado y evaluado (`notebook/`: CNN + features geométricas fusionadas → LSTM) y **corriendo en vivo** en `cv-argus/` (cámara → MediaPipe → inferencia → salida), no solo en el notebook                                                 |
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
- **Modelo/IA:** Python, MediaPipe (Face Detector para el recorte facial, FaceLandmarker para
  las features geométricas), TensorFlow/Keras (CNN + `GeometricRatioFeatureLayer` custom + LSTM
  sobre el embedding fusionado), scikit-learn (RandomForest, uno de los baselines comparados),
  pandas/joblib — repartido en diez notebooks encadenados por etapa (creación de dataset,
  entrenamiento, exportación), no en un solo monolito.
- **Borde (Raspberry Pi 5):** contenedor Docker (`python:3.11-slim-bookworm`, elegido sobre
  Alpine porque MediaPipe/TensorFlow solo publican wheels `manylinux`/glibc), `picamera2`
  para la cámara CSI, `gdown` para descargar los modelos entrenados desde Drive **al construir
  la imagen** (no al iniciar el contenedor) — para que el dispositivo pueda arrancar y empezar a
  monitorear aunque el camión no tenga señal en ese momento.
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

**Pruebas realizadas:** comparación sistemática de cuatro familias de modelo sobre el mismo
problema binario (`Not Drowsy` / `Drowsy`) — RandomForest y una red densa (Dense NN) sobre
features de un solo frame quedaron topadas en 33-41% de accuracy (correlación de Spearman
máxima de |r|=0.26 entre cualquier feature de un solo frame y el nivel de somnolencia — la
razón medida, no solo sospechada, de ese techo); una CNN de un solo frame sobre el recorte
facial subió a 59.64% accuracy / 0.5273 F1 macro; y el modelo final — el embedding de esa
misma CNN, ya congelado, fusionado con features geométricas y alimentado a una LSTM sobre una
ventana de hasta 100 frames — alcanzó **84.24% accuracy / 0.8375 F1 macro**, casi el doble del
F1 de la CNN de un solo frame sobre los mismos sujetos de prueba. División train/val/test
agrupada por sujeto (`StratifiedGroupKFold`) en todos los casos, para que ningún sujeto
aparezca a la vez en entrenamiento y prueba.

**Proceso de implementación:** pipeline de notebooks encadenados por etapa (Colab, con Google
Drive como almacenamiento) → extracción de recortes faciales (MediaPipe Face Detector) →
extracción de features geométricas sobre esos recortes (MediaPipe FaceLandmarker) → entrenamiento
de la CNN sobre los recortes → congelamiento de su embedding y fusión con las features
geométricas por instante → entrenamiento de la LSTM sobre esa secuencia fusionada → exportación
del modelo final y carga de ese artefacto en `cv-argus` para inferencia en vivo en el Pi, con
una ventana deslizante en memoria (no imágenes, solo los vectores ya fusionados) que se
actualiza un frame a la vez.

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
  - Ventana deslizante del modelo desplegado: un arreglo `numpy` de forma `(100, 74)` que
    actúa como cola circular de los últimos 100 frames (20s a 5fps) — no imágenes, sino la
    fila ya fusionada de cada instante (64 valores del embedding de la CNN + 10 features
    geométricas). Se descarta el frame más viejo y se agrega el nuevo en cada llamada, lo que
    mantiene el buffer en unos ~30KB en vez de reprocesar imágenes acumuladas.
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

✅ **Resuelto — el modelo final es una fusión CNN+LSTM, con clasificación binaria, y con
números reales de una corrida verificada.** Versiones anteriores de este borrador describían un
LSTM puro sobre 59 features geométricas, clasificando 6 niveles de somnolencia, con
98.55%/95.66% de accuracy sacados de `ArgusMLModel.ipynb` (el monolito original, ya retirado) —
esa arquitectura nunca llegó a desplegarse ni a correr con esos números verificados. Lo que
Argus sí entrenó, comparó y terminó desplegando es distinto en tres ejes a la vez: (1) el
problema se simplificó de 6 niveles a **clasificación binaria** (`Not Drowsy` / `Drowsy`) —
la clase intermedia ("baja vigilancia") resultó ser, en cada arquitectura probada, la más
inconsistente de clasificar (0% de recall en dos corridas distintas), así que fusionarla con
la clase de alerta elimina ese modo de falla en vez de solo mover el piso de adivinar al azar;
(2) el modelo final no es un LSTM puro sobre features geométricas, sino una **fusión**: el
embedding de una CNN entrenada sobre el recorte facial, combinado por instante con un
subconjunto de features geométricas, alimentando una LSTM sobre una ventana temporal; y (3) los
números reportados abajo sí vienen de una corrida real y documentada, no de un placeholder.

**Qué pide el formato/criterios:** cubrir al menos una rama de IA de la lista (redes
neuronales / ML / visión artificial / ...); formular el modelo matemático; justificar la
selección de algoritmos.

**Propuesta de contenido:**

- **Ramas cubiertas:** redes neuronales (CNN + LSTM), aprendizaje automático (RandomForest y una
  red densa, ambos comparados como baseline), visión artificial (MediaPipe Face Detector para el
  recorte facial + FaceLandmarker para 478 landmarks/blendshapes/pose). Cubre 3 de las 9 ramas
  listadas en el criterio 2.1 — más que suficiente (solo se exige una).
- **Modelo matemático:** la capa `GeometricRatioFeatureLayer` calcula, por frame, EAR (Eye
  Aspect Ratio) y MAR (Mouth Aspect Ratio) a partir de razones geométricas entre landmarks
  oculares/de boca. El modelo final usa un subconjunto de 10 de esas features (3 razones EAR/MAR
  + 7 blendshapes de MediaPipe) por instante, concatenado con el embedding de 64 dimensiones de
  una CNN (`Conv2D`/`BatchNorm`/`GlobalAveragePooling2D` → `Dense(64, relu)`) ya entrenada sobre
  el recorte facial, cuyos pesos quedan **congelados** para esta segunda etapa — el vector
  fusionado de 74 valores por instante, sobre una ventana de hasta 100 instantes (20s a 5fps),
  alimenta una `LSTM(64)` cuya salida es un softmax binario (`Not Drowsy` / `Drowsy`).
- **Justificación de algoritmos, con la evidencia real detrás de cada paso, no solo el
  resultado final:**
  1. RandomForest y una red densa, entrenados sobre features de un solo frame, quedaron topados
     en 33-41% de accuracy — la correlación de Spearman entre cualquier feature de un solo frame
     y el nivel de somnolencia no supera |r|=0.26 (el propio EAR, la feature alrededor de la cual
     se diseñó todo el pipeline geométrico, tiene |r|=0.04). La instantánea de un frame no
     alcanza a capturar la señal; el indicio real está en cómo cambia a lo largo de una
     secuencia, no en un instante suelto.
  2. Una CNN sobre el recorte facial completo (no solo un resumen geométrico hecho a mano) subió
     a 59.64% accuracy / 0.5273 F1 macro sobre el mismo problema binario — evidencia de que los
     píxeles crudos cargan información que las features geométricas no capturaban.
  3. Fusionar el embedding de esa CNN (ya congelado, sin reentrenar) con las features
     geométricas y alimentarlo a una LSTM sobre una ventana temporal llevó el resultado a
     **84.24% accuracy / 0.8375 F1 macro** — casi el doble del F1 de la CNN de un solo frame
     sobre los mismos sujetos de prueba. Es la primera confirmación directa, con un número
     limpio y no un ajuste sobreajustado, de la hipótesis central del proyecto: el contexto
     temporal revela algo que un solo frame no puede, estructuralmente, ver.
  4. La clasificación final no usa `argmax` sobre el softmax, sino un **umbral de decisión**
     elegido sobre el conjunto de validación con un criterio de seguridad primero: entre los
     umbrales que garantizan un recall de `Drowsy` de al menos 70%, se elige el de mejor
     precisión (`t*=0.57` en la corrida citada) — perder a un conductor somnoliento es el error
     más costoso, así que el criterio de selección lo refleja explícitamente en vez de tratar
     los dos tipos de error como equivalentes.
  5. El desbalance de clases (más clips de "alerta" que de "somnoliento" en el dataset) se
     maneja duplicando con traslape las ventanas de la clase minoritaria antes de entrenar
     (`Drowsy`), en vez de un `class_weight` genérico — un ajuste que sí se probó y no mejoró el
     resultado en las primeras familias de modelo, así que se dejó de usar como técnica por
     defecto.
- **Justificación de MediaPipe (Face Detector + FaceLandmarker) como base de visión:**
  ambos son modelos tipo BlazeFace — arquitecturas ligeras, cuantizables, pensadas para correr
  en CPU/NPU de dispositivos móviles y embebidos sin GPU dedicada — el motivo por el que se
  eligieron sobre alternativas más pesadas (p. ej. detectores basados en transformers, o YOLO,
  evaluado y descartado por su licencia AGPL-3.0 y el costo de mantener un segundo framework de
  detección). Es la única opción de las evaluadas con inferencia en tiempo real ya validada en
  hardware de borde de bajo costo — el mismo perfil de la Raspberry Pi 5 donde corre Argus.

⚠️ **Caveats que hay que mantener explícitos en el reporte, no solo en este borrador:** el
84.24%/0.8375 es de un solo fold (`StratifiedGroupKFold`, sujetos disjuntos entre train/val/test),
sin validación cruzada todavía — el propio proyecto ha visto variaciones de hasta ±9 puntos de F1
macro entre folds con un número similar de sujetos en otras corridas. Tampoco se ha verificado
todavía que el checkpoint de la CNN que `cv-argus` descarga en producción sea exactamente el
mismo que se usó para generar los embeddings de esta corrida — un desajuste ahí degradaría la
precisión en silencio, no con un error visible. Redactar el resultado como "el mejor medido
hasta ahora", no como validado en producción.

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

✅ **Resuelto en cuanto a números — el resultado del modelo ya es real y verificado (Parte 6),
pero el alcance de "terminado" sigue siendo parcial y hay que redactarlo así.**

**Qué pide el formato:** resumir resultados en orden lógico; cubrir (1) objetivos realmente
alcanzados al término del desarrollo, y (2) su relación con la solución planteada. **Escrito
en tiempo pasado.**

🔶 **Estado real de `cv-argus` a la fecha de este borrador, para no exagerar ni quedarse
corto:** de los 6 módulos planeados, **dos ya están terminados como código, no solo diseñados**
— `model/` (carga de los modelos entrenados, extracción del embedding congelado de la CNN,
predicción sobre la ventana fusionada) y `pipeline/` (captura de cámara, las dos etapas de
MediaPipe, la etapa de inferencia, salidas de texto y de video anotado para demo) — corriendo
de punta a punta contra una cámara o un video grabado, no solo en el notebook. Los otros
cuatro (`orchestrator/`, `buffer/`, `sender/`, `alerts/`) siguen sin código, más allá de la
arquitectura ya definida (SQLite en modo WAL para el buffer, Bluetooth con polling del ESP32
para el envío — ver Parte 7). Tampoco existe el firmware del ESP32, ni el backend, ni el
frontend.

**Propuesta de contenido — lo que sí se puede reportar como resultado real hoy:**

> Se comparó un conjunto de arquitecturas sobre el mismo problema binario (`Not Drowsy` /
> `Drowsy`): RandomForest y una red densa sobre features geométricas de un solo frame (33-41%
> de accuracy, techo explicado por una correlación de Spearman máxima de |r|=0.26 entre
> cualquier feature de un solo frame y el nivel de somnolencia); una CNN sobre el recorte
> facial completo (59.64% accuracy, 0.5273 F1 macro); y el modelo final — el embedding
> congelado de esa CNN fusionado con features geométricas y alimentado a una LSTM sobre una
> ventana temporal de hasta 20 segundos — que alcanzó **84.24% accuracy, 0.8375 F1 macro** sobre
> sujetos nunca vistos en entrenamiento, casi el doble del F1 de la CNN de un solo frame. El
> modelo final se empaquetó como un par de artefactos Keras reproducibles (la CNN congelada +
> la LSTM de fusión) y se integró en `cv-argus`, el módulo de borde: hoy corre de punta a punta
> contra una cámara en vivo o un video grabado, con una ventana deslizante ligera (vectores
> numéricos, no imágenes acumuladas) manteniendo el estado entre frames.

⚠️ **Caveats que van en esta sección junto con el resultado, no escondidos en una nota aparte:**
el número es de un solo fold, sin validación cruzada todavía; no se ha corrido de punta a punta
contra hardware real de Raspberry Pi (solo contra un contenedor Docker en una laptop); y la
identidad exacta del checkpoint de CNN usado en producción frente al usado para generar los
embeddings de entrenamiento no está verificada (ver Parte 6). Dejar tanto esa validación en
hardware real como la integración completa de borde (Pi+ESP32+buffer+nube) explícitamente en la
Parte 9 como "trabajo a futuro", no disfrazadas de resultado ya alcanzado.

---

## Parte 9 — Conclusiones y trabajo a futuro

✅ **Resuelto en cuanto a base técnica** — a diferencia de versiones anteriores de este
borrador, la conclusión de abajo ya se apoya en una comparación real de modelos con números
verificados (Parte 6/8), no en un resultado pendiente de correr.

**Qué pide el formato:** generalizaciones del proceso completo, sin repetir el resumen
literalmente; evitar afirmaciones sobre partes no terminadas; recomendaciones/mejoras al
final.

**Propuesta de contenido:**

> El desarrollo confirmó que un enfoque puramente geométrico (EAR/MAR + pose + blendshapes de
> un solo frame) tiene un techo real y medido — no solo sospechado — alrededor del 33-41% de
> accuracy, y que superarlo requirió dos cambios a la vez: incorporar información visual cruda
> (una CNN sobre el recorte facial, no solo un resumen geométrico hecho a mano) y, sobre todo,
> dar contexto temporal a esa información (fusionar el embedding de esa CNN con features
> geométricas y clasificar sobre una ventana, no un frame suelto). El resultado final —84.24%
> de accuracy, 0.8375 de F1 macro— casi duplica el F1 de la misma CNN juzgando frame por frame,
> confirmando de forma directa la hipótesis central del proyecto: la duración y velocidad del
> cierre ocular a lo largo de una ventana es una señal que un solo instante no puede capturar
> estructuralmente. El diseño edge-first —procesamiento en el borde por camión, sincronización
> tolerante a fallos hacia la nube— es además pensado desde el inicio para escalar por flota:
> sumar camiones no implica escalar un servidor central de inferencia, porque cada unidad ya
> trae su propio cómputo. Esa escalabilidad, junto con el costo humano y económico de los
> accidentes por fatiga descrito en la Parte 2, es la motivación de fondo del proyecto: una
> barrera preventiva de bajo costo tiene margen para reducir una fracción de esas pérdidas si
> se despliega a nivel flota.
>
> **Trabajo a futuro:**
> - Validar el desempeño del modelo final (CNN+LSTM fusionado) corriendo en hardware real
>   (Raspberry Pi 5): latencia de inferencia por frame, uso de CPU/memoria en ARM — el 84.24%
>   accuracy / 0.8375 F1 macro reportado en la Parte 8 viene del entrenamiento y evaluación en
>   notebook, no de una corrida en el Pi. Es también, de los modelos comparados, el más pesado
>   por frame muestreado (dos tareas de MediaPipe más dos modelos de Keras), así que esta
>   validación importa más aquí que para un modelo más simple.
> - Correr una validación cruzada (k-fold) sobre el resultado del 84.24%/0.8375 — hoy es un
>   solo fold agrupado por sujeto, y el propio proyecto ha visto variaciones de hasta ±9 puntos
>   de F1 macro entre folds en otros modelos con un número similar de sujetos.
> - Confirmar que el checkpoint de la CNN que `cv-argus` descarga en producción es exactamente
>   el mismo que se usó para generar los embeddings con los que se entrenó la LSTM final — un
>   riesgo abierto y no trivial: un checkpoint distinto degradaría la precisión de forma
>   silenciosa, no con un error visible.
> - Completar e integrar los módulos `orchestrator/`, `buffer/`, `sender/`, `alerts/` de
>   `cv-argus` (el módulo de inferencia y de captura de cámara ya están terminados y corriendo)
>   con hardware real (Pi 5 + cámara CSI) para validar la cadena completa de alerta, no solo la
>   predicción.
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
>   literatura), detección de obstáculos (ADAS), respaldo satelital, y ampliar el conjunto de
>   entrenamiento con más sujetos (el modelo final está entrenado sobre un número de sujetos
>   todavía modesto para una arquitectura de este tamaño).

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
2. ~~¿LSTM o red feedforward?~~ — ✅ **Resuelto, y el modelo final tampoco es un LSTM puro sobre
   features geométricas**: es una fusión del embedding de una CNN (sobre el recorte facial) con
   features geométricas, alimentando una LSTM sobre una ventana temporal — ver Parte 6 para el
   porqué técnico completo (recurrencia real dentro de cada llamada, que una capa `Dense` no
   puede replicar, más el aporte medido de los píxeles crudos frente a un resumen geométrico).
3. ~~🔴 Números de accuracy/F1 pendientes de re-verificar~~ — ✅ **Resuelto:** los números
   citados hoy en Partes 1, 4, 6 y 8 (84.24% accuracy / 0.8375 F1 macro para el modelo final)
   vienen de una corrida real y documentada del pipeline vigente, no de un placeholder del
   monolito retirado. Lo que sigue abierto no es re-verificar estos números, sino tres cosas
   distintas (ver Parte 9): validación cruzada (un solo fold hoy), correr contra hardware real
   de Raspberry Pi, y confirmar que el checkpoint de CNN en producción coincide con el usado
   para entrenar la LSTM final.
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

*Generado/actualizado a partir de: `CLAUDE.md` (raíz), `src/notebook/CLAUDE.md`,
`src/cv-argus/CLAUDE.md`, `src/cv-argus/README.md`, `src/notebook/07_cnn_training.ipynb`,
`src/notebook/11_cnn_lstm_training_drive_pull.ipynb` (fuente de los números 84.24%/0.8375 del
modelo final — ver Parte 6/8), `docs/document/Analisis formato proyecto modular.docx`,
`docs/criteria/Formato_Proyecto_Modular V2.docx`, `docs/criteria/criteriosaprobacion_0.pdf`,
`docs/argus-descripción-proyecto.pdf`, `docs/Argus_Definicion_Tecnica.docx.pdf`,
`docs/references/*` (5 archivos, 1 resultó ser un PDF roto — ver Parte 3).
`notebook/ArgusMLModel.ipynb` (el monolito original) y los notebooks `01_dataset_creation`/
`02_model_training`/`03_deployment_export` citados en versiones anteriores de este borrador ya
no existen con esos nombres — el pipeline se dividió en diez notebooks por etapa (ver
`src/notebook/CLAUDE.md`'s "Pipeline map"); las cifras 98.55%/95.66% de 6 clases que este
documento citaba antes nunca se reprodujeron con ese esquema y quedan descartadas, no
pendientes, a favor de los números binarios reales de la Parte 6.*
