# Handoff: qué nos falta para cubrir los criterios del proyecto modular

**Fecha de la revisión:** 2026-10-17
**Revisado contra:**
- `docs/criteria/criteriosaprobacion_0.pdf`: criterios por módulo del Comité de Titulación INCO (sept. 2019).
- [Criterios de pre-evaluación 2024](https://www.cucei.udg.mx/carreras/computacion/sites/default/files/criterios_de_evaluacion_2024.pdf): rúbrica general con 11 requisitos y porcentajes. No está en el repo; solo está publicada en la web de CUCEI.
- `docs/criteria/ReporteArgusINCO_final.docx`/`.pdf` (16 oct.), generado por `docs/criteria/scripts/fill_report.py`.
- El código actual de `src/`.

## Resumen

**El código sí cubre los tres módulos.** El problema es el **reporte final**:
- Está desactualizado. Dice que el backend, el ESP32 y el frontend "siguen sin código".
- Le faltan dos cosas que el comité pide literalmente: las ecuaciones del modelo (2.2) y la justificación de protocolos (3.4).
- Tiene un probable error de números en la Tabla III.
- Mezcla nombres de módulos de dos planes de estudio distintos.

---

## 0. Bloqueante: confirmar con el asesor qué módulos aplican

Los documentos no coinciden entre sí:

| Documento | Módulos |
|---|---|
| `criteriosaprobacion_0.pdf` (2019) y `Formato_Proyecto_Modular V2.docx` | Arquitectura y Programación de Sistemas · Sistemas Inteligentes · Sistemas Distribuidos |
| `Formato_Informe_Final_INCO.docx` (la plantilla que usamos) | Módulo I **Gestión de la tecnología de información** · Módulo II **Sistemas robustos, paralelos y distribuidos** (solo dos) |
| Nuestro reporte | Los dos anteriores, más un **Módulo III "Cómputo flexible (softcomputing)"** agregado por nosotros |

El reporte usa los nombres nuevos pero los llena con la numeración 1.1–1.5 de los criterios 2019. No encontramos criterios publicados para "Gestión de TI" ni para "Cómputo flexible".

- [ ] **Preguntar al Ing. Ruz** qué plan y qué módulos nos corresponden, y si hay criterios por módulo más nuevos que los de 2019.
- [ ] Ajustar los títulos y el contenido de las secciones de módulos según su respuesta.

---

## 1. Estado por criterio (2019)

Leyenda: ✅ cubierto · ⚠️ cubierto en código pero débil o ausente en el reporte · ❌ falta

### Módulo 1: Arquitectura y Programación de Sistemas

| Criterio | Estado | Qué falta |
|---|---|---|
| 1.1 Lenguajes | ⚠️ | El reporte dice "C" para el ESP32: es **C++ (Arduino)**. Falta justificar TypeScript/React (`ui-argus`) y FastAPI (`backend-argus`). |
| 1.2 BD / estructuras de datos | ✅ | SQLite WAL, MongoDB/Beanie y la ventana circular `(100, 74)` ya están explicados. |
| 1.3 Metodología | ✅ | Scrum. Ver también la rúbrica 2024 (roles), sección 4. |
| 1.4 Ingeniería de software | ⚠️ | Mencionar las pruebas: unitarias por módulo, Playwright (`src/it-argus`), pruebas de la cadena del borde (`src/tests`) y HIL del ESP32 (`src/esp32-argus/test/hil`). |
| 1.5 Modelado del sistema | ⚠️ | El reporte solo tiene la Fig. 1 (arquitectura) y la Fig. 2 (pipeline). **Agregar el diagrama ER**, que ya existe en `docs/designs/ER-model-*.png`. Recomendado: un diagrama de secuencia de una alerta (cámara → Pi → Bluetooth → ESP32 → HTTP → backend → UI) y casos de uso por rol (`root_admin`, `admin`, `guardian`, `truck_driver`). |

### Módulo 2: Sistemas Inteligentes

| Criterio | Estado | Qué falta |
|---|---|---|
| 2.1 Ramas de IA | ✅ | Redes neuronales (CNN + LSTM), ML (RandomForest) y visión artificial (MediaPipe). |
| 2.2 Modelo matemático | ❌ | El reporte lo describe solo con palabras. **Hay que escribir las ecuaciones** (lista abajo). |
| 2.3 Justificación de algoritmos | ⚠️ | El argumento es bueno, pero se apoya en números de la Tabla III que probablemente están mal (ver abajo). |

**Ecuaciones a agregar (2.2):**
- [ ] EAR (Eye Aspect Ratio) y MAR (Mouth Aspect Ratio) con los índices de landmarks reales. Fuente: `src/cv-argus/src/model/layers.py` (`GeometricRatioFeatureLayer`).
- [ ] La fusión por instante: `x_t = [e_t ; g_t] ∈ R^74` (embedding CNN de 64-D y 10 features geométricas).
- [ ] Las compuertas de la LSTM: `f_t, i_t, o_t, c̃_t, c_t, h_t`.
- [ ] La salida softmax binaria y la pérdida de entropía cruzada (con los pesos de clase, si se usaron).
- [ ] La regla de decisión: `Drowsy si p(Drowsy|x_1..T) ≥ t*`, con `t* = 0.57`, y el criterio de selección (recall de Drowsy ≥ 0.70 y, entre esos umbrales, la máxima precisión).
- [ ] Opcional: las métricas (precisión, recall, F1 macro) definidas formalmente.

**Probable error en la Tabla III:**
La tabla pone RandomForest en 32.6% y la red densa en 38.6–40.8% "sobre el mismo problema binario". Según `CLAUDE.md` y `src/notebook/CLAUDE.md` ("Binary migration"), los notebooks `01`–`08` **no se han vuelto a correr en binario**, así que esos números parecen ser del esquema anterior de 3 clases. En un problema binario, 33% está por debajo del azar (50%), y el jurado lo va a notar.
- [ ] Opción A: volver a correr `04` (RandomForest) y `05` (red densa) con el dataset binario y actualizar la tabla.
- [ ] Opción B: marcar esas filas explícitamente como "esquema de 3 clases (azar = 33%)" y quitar la frase "mismo problema binario". También revisar el 33–41% que aparece en las Secciones III-E, IV y V.

### Módulo 3: Sistemas Distribuidos

| Criterio | Estado | Qué falta |
|---|---|---|
| 3.1 Sistema descentralizado | ✅ | Ya cubre concurrencia, procesamiento en el borde, red de sensores (GPS, grip y pánico en el ESP32) y tolerancia a fallos (buffer y reenvío). **Agregar seguridad (3.1.7):** API key por camión para el ESP32, JWT y bcrypt en el backend, y RBAC de 4 roles. Ver `src/backend-argus/CLAUDE.md`. |
| 3.2 Algoritmo propio cliente/servidor | ⚠️ | Está implementado: el protocolo **PULL/ACK** sobre Bluetooth SPP (`src/esp32-argus/src/esp32_argus/bluetooth_client.cpp` ↔ `src/cv-argus/src/sender/`). El reporte apenas lo nombra. **Describir el algoritmo:** formato de mensajes, flujo PULL → registros → `ACK id1,id2`, reintento de lo no confirmado (*at-least-once*), y cómo se evitan duplicados o pérdidas (ver el commit `adc49f6`, "fix alert loss after a failed POST"). Ideal con un diagrama de secuencia. |
| 3.3 Comunicación entre ≥ 2 dispositivos | ⚠️ | Por diseño se cumple: Pi ↔ ESP32 ↔ backend. **Pendiente de evidencia real:** la corrida HIL con el firmware real por Bluetooth todavía no se ha hecho (`src/esp32-argus/test/hil/README.md`). El GPS ya está verificado en banco. |
| 3.4 Justificación de protocolos | ❌ | No existe en el reporte. Hay que agregar un apartado (lista abajo). |

**Protocolos a justificar (3.4):**
- [ ] **Bluetooth Classic SPP/RFCOMM** (Pi ↔ ESP32): por qué no BLE, ni serial cableado, ni Wi-Fi/MQTT. Argumentos: enlace punto a punto en cabina, sin depender de una red, flujo de bytes simple para el protocolo de líneas.
- [ ] **Pull iniciado por el ESP32** en vez de push desde la Pi: el ESP32 controla su ritmo y es el dueño de la parte crítica.
- [ ] **HTTP/REST + JSON con `X-Device-Api-Key`** (ESP32 → backend): por qué API key por dispositivo y no login de usuario.
- [ ] **Wi-Fi** como transporte principal del ESP32 y **SMS (SIM800L)** solo como respaldo del botón de pánico: funciona aunque no haya Wi-Fi o backend.
- [ ] **UART/NMEA** para el GPS NEO-6M.
- [ ] **Polling cada 7 s** del frontend (`ui-argus/src/pages/LiveOps.tsx`, `POLL_MS`) en vez de WebSockets: justificarlo o mencionarlo como mejora futura (WebSockets también cubriría la opción 3.1.6).

---

## 2. Criterios generales de NO aprobación (2019)

| Criterio | Riesgo |
|---|---|
| A. Objetivos alcanzables | Bajo, siempre que el reporte refleje lo que sí está hecho (ver sección 3). |
| B. Prototipo viable | Medio: falta la corrida en una **Raspberry Pi 5 real** y la HIL completa del ESP32. |
| C. Delimitación | Bajo. |
| D. Complejidad técnica | Bajo. |
| F. Complejidad acorde a 3 integrantes | Bajo, pero hay que documentar los roles (sección 4). |
| G. Originalidad | Bajo. El reporte ya compara contra Geotab/Samsara y la tesis MobileNet. |

---

## 3. El reporte contradice el estado real del proyecto (lo más urgente)

Todo se edita en `docs/criteria/scripts/fill_report.py` (el script regenera el `.docx`) y luego se exporta el PDF con el comando de su docstring.

- [ ] **Sección IV (Resultados):** quitar "los otros cuatro (orchestrator/, buffer/, sender/, alerts/), el firmware del ESP32, el backend y el frontend siguen sin código". Todo eso existe hoy.
- [ ] **Sección V (Trabajo a futuro):** quitar "Prototipar el firmware del ESP32", "Implementar el backend/frontend" y "Completar e integrar orchestrator/…". Reemplazarlos con lo que de verdad falta: Pi 5 real, HIL completo, k-fold y verificación del checkpoint de la CNN.
- [ ] **Tabla I:** el botón de pánico ya está en el firmware (cambiar a "Completado — firmware, pendiente de prueba en hardware"). Revisar la fila 4: el GPS ya está verificado en banco.
- [ ] **Tabla II, fila 9:** todas las pantallas usan el backend real, más Access y Profile. Agregar filas para el simulador de flota (`src/simulator-argus`), el firmware ESP32 y las pruebas de la cadena del borde.
- [ ] **Sección III-C:** agregar el simulador de flota y OSRM (que solo usa el simulador).
- [ ] **Sección III-D / Fig. 1:** mencionar la fusión de somnolencia + grip en el ESP32 (`fusion_contract.py` ↔ firmware).

---

## 4. Rúbrica de pre-evaluación 2024

| Criterio (peso) | Estado | Acción |
|---|---|---|
| Funcionalidad del prototipo (15%) | ⚠️ | Preparar una demo estable: `docker compose` + simulador + cv-argus en vivo. Idealmente, la cadena real Pi → ESP32 → backend. |
| Resolución de la problemática (15%) | ✅ | — |
| Aplicación de conocimientos (10%) | ✅ | — |
| Uso de metodologías (10%) | ⚠️ | Mostrar evidencia de Scrum: backlog, sprints, fechas. |
| **Trabajo en equipo (10%)** | ❌ | El reporte **no define roles**. Agregar quién hizo qué: IA/visión, backend/frontend, firmware/hardware. |
| Viabilidad técnica (10%) | ⚠️ | Correr en una Pi 5 real. Hoy solo está medido en Docker con límites que simulan la Pi. |
| Innovación (5%) | ✅ | — |
| Presentación (5%) | ⚠️ | Depende de los arreglos de la sección 3. |
| Calidad del código (5%) | ✅ | — |
| Escalabilidad (5%) | ✅ | Ya está argumentada (edge-first, sin inferencia central). |
| Integración de tecnologías (5%) | ✅ | — |
| Resolución de problemas (5%) | ✅ | Se pueden citar bugs reales resueltos: el LR de `11`, la inferencia de 600 ms → 10 ms con `tf.function`, el muestreo a 5 fps. |
| **UI/UX (5%)** | ❌ | El reporte **no tiene ninguna captura del frontend**. Agregar Live operations (mapa) y Alert triage. |

Además, la plantilla `Formato_Proyecto_Modular V2.docx` pide **repositorio de código, versión actual y licencia**, y el reporte no los incluye.
- [ ] Agregar la URL del repo (`github.com/alecksandr26/Argus`), un tag/versión y la licencia (ya existe: **MIT**, ver `LICENSE`).

---

## 5. Pendientes técnicos (fuera del reporte)

- [ ] Corrida HIL completa: `src/esp32-argus/test/hil/esp32_hil.py` con el ESP32 real por Bluetooth, desde Windows (ver su README).
- [ ] Correr `cv-argus` en una **Raspberry Pi 5 real** con la cámara CSI y medir latencia, CPU y memoria.
- [ ] Validación cruzada k-fold del modelo final (hoy es un solo fold).
- [ ] Verificar que el checkpoint de la CNN que descarga `cv-argus` (`CNN_MODEL_DRIVE_FILE_ID`) es el mismo con el que `11` generó los embeddings.
- [ ] Volver a correr `04`/`05` en binario si se elige la Opción A de la Tabla III.

---

## Orden sugerido

1. Sección 0: confirmar los módulos con el asesor.
2. Sección 3: poner el reporte al día con el estado real.
3. Las ecuaciones (2.2) y el apartado de protocolos y algoritmo PULL/ACK (3.2/3.4).
4. Tabla III (RF/red densa).
5. El ER, los roles del equipo, capturas de la UI y repo/licencia.
6. Pendientes técnicos (HIL, Pi 5 real) antes de la demo.
