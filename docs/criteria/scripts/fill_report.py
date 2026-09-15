#!/usr/bin/env python3
"""Llena Formato_Informe_Final_INCO.docx con el contenido real de Argus.

Parte de la plantilla oficial (formato IEEE a 2 columnas verificado) e inyecta el
contenido ya redactado (Resumen/Introducción/Trabajos relacionados/Desarrollo/
Módulos I-III/Resultados/Conclusiones/Reconocimientos/Referencias), preservando
el estilo de cada párrafo existente en vez de reconstruir el documento desde cero.

Uso: python3 fill_report.py
Genera: docs/criteria/ReporteArgusINCO_final.docx
"""
import copy

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.text.paragraph import Paragraph

SRC = "docs/criteria/Formato_Informe_Final_INCO.docx"
OUT = "docs/criteria/ReporteArgusINCO_final.docx"
DESIGNS = "docs/designs"
FIGDIR = "docs/criteria/scripts/figures"

doc = docx.Document(SRC)
P = doc.paragraphs  # snapshot of Paragraph *objects* — index-safe even after edits,
                     # because we grab references now and only ever mutate the
                     # underlying XML elements, never re-query doc.paragraphs by index.

# ---------------------------------------------------------------- helpers ---


def set_text(p, text):
    """Replace a paragraph's text, keeping the formatting of its first run."""
    runs = p.runs
    if not runs:
        p.add_run(text)
        return p
    runs[0].text = text
    for r in runs[1:]:
        r._element.getparent().remove(r._element)
    return p


def widen_paragraph(p):
    """Drop the template's narrow w:ind (calibrated for its short example text,
    e.g. 'primer.autor@correo.dom') so real, longer email addresses fit on one
    line instead of wrapping mid-word. Page margins/columns are untouched."""
    pPr = p._p.find(qn("w:pPr"))
    if pPr is not None:
        ind = pPr.find(qn("w:ind"))
        if ind is not None:
            pPr.remove(ind)


def delete_paragraph(p):
    p._element.getparent().remove(p._element)


def new_paragraph_after(anchor, text="", style=None, align=None, size=None, bold=None, italic=None):
    new_p_elm = OxmlElement("w:p")
    anchor._p.addnext(new_p_elm)
    new_p = Paragraph(new_p_elm, anchor._parent)
    if style:
        new_p.style = doc.styles[style]
    if align is not None:
        new_p.alignment = align
    if text:
        r = new_p.add_run(text)
        if size:
            r.font.size = Pt(size)
        if bold is not None:
            r.bold = bold
        if italic is not None:
            r.italic = italic
    return new_p


def add_bullets_after(anchor, items, **kw):
    """Insert one paragraph per bullet, in order, right after anchor. Returns last paragraph."""
    cur = anchor
    for text in items:
        cur = new_paragraph_after(cur, "• " + text, style=cur.style.name if cur is anchor else None, **kw)
    return cur


def delete_range(paragraphs):
    for p in paragraphs:
        delete_paragraph(p)


def insert_table_after(anchor, data, col_widths_in, header=True):
    """data: list of rows (each a list of cell strings). First row = header if header=True.
    col_widths_in is required and must sum to <= ~3.3in (the body's column width,
    5043 twips) — python-docx table widths are ignored unless autofit is disabled
    and every cell in each column gets the same explicit width."""
    n_rows, n_cols = len(data), len(data[0])
    tbl = doc.add_table(rows=n_rows, cols=n_cols)
    tbl.style = OLD_TABLE_STYLE
    tbl.autofit = False
    tbl.allow_autofit = False
    for ri, row in enumerate(data):
        for ci, val in enumerate(row):
            cell = tbl.cell(ri, ci)
            cell.text = ""
            r = cell.paragraphs[0].add_run(str(val))
            r.font.size = Pt(8)
            r.font.name = "Times New Roman"
            if header and ri == 0:
                r.bold = True
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            cell.width = Inches(col_widths_in[ci])
    for ci, w in enumerate(col_widths_in):
        tbl.columns[ci].width = Inches(w)
    # tblW defaults to type="auto" w="0" even with autofit off — python-docx 1.2 has no
    # Table.width setter (assigning one is a silent no-op), and the stale auto/0 value
    # made LibreOffice's PDF export silently drop/clip the table's last column. Set it
    # on the XML directly to the real summed width, in dxa (twentieths of a point).
    tblPr = tbl._tbl.tblPr
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.append(tblW)
    total_dxa = int(Inches(sum(col_widths_in)) / 635)  # EMU -> dxa (1 dxa = 635 EMU)
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(total_dxa))
    anchor._p.addnext(tbl._tbl)
    # Word requires a paragraph after a table at the body level, and returning it (instead
    # of the table itself) lets callers keep chaining anchor = insert_table_after(anchor, ...)
    # without accidentally inserting later content *before* this table (addnext always
    # inserts immediately after whatever anchor it's given).
    spacer = new_paragraph_after(anchor)
    tbl._tbl.addnext(spacer._p)
    return spacer


def set_sectPr(paragraph, num_cols):
    """Mark `paragraph` as the last paragraph of a section with num_cols columns
    (continuous break), copied off the document's final section for margins/page size."""
    sectPr = copy.deepcopy(doc.sections[-1]._sectPr)
    cols_el = sectPr.find(qn("w:cols"))
    if cols_el is None:
        cols_el = OxmlElement("w:cols")
        sectPr.append(cols_el)
    for attr in ("w:num", "w:equalWidth", "w:sep"):
        if cols_el.get(qn(attr)) is not None:
            del cols_el.attrib[qn(attr)]
    cols_el.set(qn("w:num"), str(num_cols))
    if num_cols == 2:
        cols_el.set(qn("w:equalWidth"), "1")
    else:
        # drop any leftover per-column <w:col> width (copied from the 2-col
        # section) so the 1-col section actually uses the full text width
        for col_child in cols_el.findall(qn("w:col")):
            cols_el.remove(col_child)
    type_el = sectPr.find(qn("w:type"))
    if type_el is None:
        type_el = OxmlElement("w:type")
        pgSz_el = sectPr.find(qn("w:pgSz"))
        if pgSz_el is not None:
            pgSz_el.addprevious(type_el)  # w:type must precede w:pgSz per schema order
        else:
            sectPr.insert(0, type_el)
    type_el.set(qn("w:val"), "continuous")
    pPr = paragraph._p.get_or_add_pPr()
    old = pPr.find(qn("w:sectPr"))
    if old is not None:
        pPr.remove(old)
    pPr.append(sectPr)


def insert_full_width_figure(anchor, image_path, caption, width_in=6.3):
    """Open a temporary 1-column section around a figure so it can span both
    columns (explicitly allowed by the template's section D), then close back
    to 2 columns for whatever follows."""
    set_sectPr(anchor, num_cols=2)  # close the running 2-col section right before the figure
    p_img = new_paragraph_after(anchor, style="Normal", align=WD_ALIGN_PARAGRAPH.CENTER)
    run = p_img.add_run()
    run.add_picture(image_path, width=Inches(width_in))
    p_cap = new_paragraph_after(p_img, style="Normal", align=WD_ALIGN_PARAGRAPH.CENTER,
                                 text=caption, size=8, bold=True)
    set_sectPr(p_cap, num_cols=1)  # this paragraph closes the 1-col figure section
    return p_cap


def insert_column_figure_after(anchor, image_path, caption, width_in=3.15):
    p_img = new_paragraph_after(anchor, style="Normal", align=WD_ALIGN_PARAGRAPH.CENTER)
    run = p_img.add_run()
    run.add_picture(image_path, width=Inches(width_in))
    p_cap = new_paragraph_after(p_img, style="Normal", align=WD_ALIGN_PARAGRAPH.CENTER,
                                 text=caption, size=8, bold=True)
    return p_cap


def set_reference(p, number, parts):
    """parts: list of (text, italic bool) tuples building one IEEE reference paragraph.

    The template numbers its 11 example references across two different auto-list
    definitions (numId 3 and 4, restarting partway through) — reusing that numbering
    for our own 10 references produces a skipped number. Strip it and number
    explicitly instead, keeping a hanging indent so wrapped lines still align under
    the reference text rather than under "[N]"."""
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    pPr = p._p.get_or_add_pPr()
    numPr = pPr.find(qn("w:numPr"))
    if numPr is not None:
        pPr.remove(numPr)
    ind = pPr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        pPr.append(ind)
    ind.set(qn("w:left"), "245")
    ind.set(qn("w:hanging"), "245")
    p.add_run(f"[{number}] ")
    for text, italic in parts:
        r = p.add_run(text)
        r.italic = italic


# ------------------------------------------------------------- old table ---
OLD_TABLE_STYLE = doc.tables[0].style
doc.tables[0]._element.getparent().remove(doc.tables[0]._element)

# ============================================================== PORTADA ===
set_text(P[0], "Argus: Sistema de Monitoreo del Conductor Basado en Visión Artificial "
               "para la Prevención de Accidentes por Fatiga en el Autotransporte de Carga")
set_text(P[1], "Erick Alejandro Carrillo López, Paulino Jacob Suárez Ortiz, "
               "Rafael Agustín Pulido Tobías, Mario Antonio Ruz Canul")
# P[2] institución ya es correcta (CUCEI, UDG) — se deja igual.
for _p in (P[3], P[4], P[5]):
    widen_paragraph(_p)
set_text(P[3], "erick.carrillo4982@alumnos.udg.mx")
set_text(P[4], "Paulino.suarez8804@alumnos.udg.mx  ·  rafael.pulido4119@alumnos.udg.mx")
set_text(P[5], "[correo del asesor — completar]")
# P[6] "FIRMA DE VISTO BUENO DEL ASESOR" se deja como etiqueta estructural.

RESUMEN = (
    "El autotransporte de carga mueve el 57% de las mercancías de México, pero la fatiga y "
    "somnolencia causan entre el 24% y 30% de los accidentes viales, agravado por el "
    "incumplimiento generalizado de la NOM-087. Argus es un sistema de monitoreo del "
    "conductor (DMS) que aumenta, sin reemplazar, al operador: una capa preventiva que "
    "detecta somnolencia en tiempo real mediante visión artificial y actúa antes de un "
    "microsueño. El sistema combina un módulo de inferencia en el borde (Raspberry Pi 5) con "
    "un microcontrolador (ESP32) responsable de alertas, frenado preventivo y comunicación, y "
    "un buffer local que garantiza continuidad sin cobertura celular. El modelo de IA es un "
    "clasificador binario (Not Drowsy / Drowsy) que fusiona, por cada instante, el embedding "
    "de una red convolucional (CNN) congelada con un subconjunto de características "
    "geométricas faciales, y clasifica esa secuencia con una red LSTM sobre una ventana "
    "temporal. Alcanzó 84.24% de exactitud y 0.8375 de F1 macro en sujetos de prueba nunca "
    "vistos en entrenamiento, casi el doble del F1 de una CNN de un solo frame (0.5273) sobre "
    "los mismos sujetos."
)
set_text(P[7], "Resumen")
p7 = P[7]
r = p7.add_run("— " + RESUMEN)
r.bold = True
r.italic = False

set_text(P[8], "Palabras clave")
r = P[8].add_run(" – monitoreo del conductor, somnolencia, visión artificial, CNN, LSTM, "
                  "MediaPipe, sistemas embebidos, sistemas distribuidos.")
r.bold = True
r.italic = False

# ============================================================ I. INTRO ===
INTRO = (
    "México depende del autotransporte federal para mover más de la mitad de su carga, pero "
    "paga un costo altísimo en vidas: cerca de 17,000 muertes al año por accidentes de "
    "transporte terrestre, de los cuales la fatiga del conductor explica hasta un 30%. El "
    "ciclo es conocido: jornadas «justo a tiempo» que exceden la NOM-087, bitácoras "
    "falsificadas, y un 81.3% de los operadores recurriendo a sustancias para mantenerse "
    "despiertos. La automatización total (Nivel 5) — el camino que ya toman flotas "
    "comerciales en EUA — no es viable en México a corto plazo: un camión detenido por su "
    "protocolo de «minimal risk condition» ante una anomalía es un blanco fácil para el robo "
    "de carga, la red carretera carece de la señalización y cobertura 5G que la conducción "
    "autónoma exige, y la brecha de costo (450,000 USD vs. 180,000 USD) es prohibitiva para "
    "el «hombre-camión» que domina el mercado. El sistema toma su nombre de Argos Panoptes, "
    "el gigante centinela de la mitología griega que nunca dormía por completo: vigilaba con "
    "unos ojos mientras otros descansaban. Argus busca esa misma vigilancia constante para el "
    "conductor — no reemplazarlo, sino ser los ojos que no se cierran cuando los suyos lo "
    "hacen. El sistema propone «Aumento Humano»: una barrera tecnológica de bajo costo que "
    "vigila el estado fisiológico del conductor y solo interviene — alertando o frenando "
    "preventivamente — cuando el humano ya no puede reaccionar a tiempo."
)
set_text(P[10], INTRO)
delete_range([P[11], P[12]])

# ============================================== II. TRABAJOS RELACIONADOS ===
delete_paragraph(P[14])
set_text(P[15], "A. Datasets y estudios sobre microsueños")
set_text(P[16], "RLDD — dataset público de 60 sujetos (~30 h de video) etiquetado en tres "
                 "niveles de vigilancia, con un modelo temporal (LSTM jerárquico) sobre "
                 "features de parpadeo [1]. Valida el enfoque temporal usado en este proyecto "
                 "y es candidato para ampliar el entrenamiento.")
b = add_bullets_after(P[16], [
    "UL-DD — dataset multimodal de 19 sujetos con video, señales biométricas y sensor de "
    "presión en el volante [2] — el mismo tipo de sensor que Argus contempla. Referencia "
    "natural para un futuro módulo de fusión biométrica.",
    "Un estudio en simulador con 144 sujetos muestra que rasgos de personalidad e IQ "
    "influyen en cuándo aparece el microsueño de cada persona [3], evidencia de que un "
    "umbral fijo único (tipo PERCLOS) no generaliza bien entre individuos — lo que refuerza "
    "preferir un modelo estadísticamente validado por característica.",
])
set_text(P[17], "B. Proyectos con el mismo problema, enfoque distinto")
set_text(P[18], "Un trabajo de grado que detecta somnolencia diurna y nocturna con una CNN "
                 "MobileNetV3 sobre 12 datasets públicos reporta 97% de precisión detectando "
                 "ojos cerrados [4]. Es un clasificador de visión acotado, sin backend, "
                 "comunicación entre dispositivos ni sistema de alertas/actuación. Argus se "
                 "diferencia en dos ejes: clasifica secuencias (embedding de CNN fusionado con "
                 "features geométricas, LSTM sobre ventanas), no frames sueltos, y extiende el "
                 "problema hacia un sistema distribuido completo (borde + alerta + frenado + "
                 "nube).")
b = add_bullets_after(P[18], [
    "Soluciones comerciales de video-telemática (Geotab [5], Samsara [6]) ya despliegan "
    "monitoreo del conductor a nivel flota; Argus se diferencia por ser edge-first, pensado "
    "para conectividad intermitente y el riesgo de robo de carga en México.",
    "La automatización comercial de Nivel 4 en EUA (Aurora [7], Kodiak [8]) sirve de "
    "contraste directo con la inviabilidad de la automatización total en el contexto "
    "mexicano descrita en la Sección I.",
])
delete_range([P[19], P[20], P[21], P[22], P[23], P[24]])

# ==================================== III. DESCRIPCIÓN DEL DESARROLLO ===
delete_paragraph(P[26])

set_text(P[27], "A. Metodología de trabajo")
set_text(P[28], "El equipo, integrado por tres personas, trabajó bajo Scrum: un backlog "
                 "priorizado de requerimientos llevaba el registro de las piezas pendientes, "
                 "en progreso y terminadas, y cada semana se realizó una reunión de avance "
                 "para revisar lo completado y asignar las siguientes tareas.")
delete_paragraph(P[29])

set_text(P[30], "B. Requerimientos principales y backlog")
set_text(P[31], "La Tabla I resume el backlog priorizado de requerimientos del MVP y su "
                 "estado de avance a la fecha de este documento.")
set_text(P[33], "ESTADO DEL BACKLOG DEL MVP")
insert_table_after(P[33], [
    ["#", "Requerimiento", "Estado"],
    ["1", "Detección facial y ocular (visión artificial)", "Completado — corriendo en vivo en cv-argus"],
    ["2", "Alertas sonoras en cabina ante microsueño", "Pendiente — depende del firmware ESP32"],
    ["3", "Frenado autónomo preventivo (CAN/AEB)", "Pendiente — interfaz no implementada"],
    ["4", "Transmisión de alertas/ubicación por red celular", "Pendiente — protocolo Pi↔ESP32↔nube"],
    ["5", "Memoria local (buffer), reenvío automático", "En diseño — SQLite/WAL definido, sin código aún"],
    ["6", "Botón de pánico", "Pendiente — depende del firmware ESP32"],
], col_widths_in=[0.3, 1.6, 1.3])
delete_range([P[34], P[35], P[36]])

set_text(P[37], "C. Tecnologías utilizadas")
set_text(P[38], "Modelo/IA: Python, MediaPipe (Face Detector para el recorte, FaceLandmarker "
                 "para las features geométricas), TensorFlow/Keras (CNN + "
                 "GeometricRatioFeatureLayer propia + LSTM sobre el embedding fusionado), "
                 "scikit-learn (RandomForest como baseline).")
add_bullets_after(P[38], [
    "Borde (Raspberry Pi 5): contenedor Docker, picamera2 para la cámara CSI, descarga de "
    "los modelos entrenados desde almacenamiento en la nube al construir la imagen.",
    "Microcontrolador (ESP32): C (Arduino/ESP-IDF), con parseo GPS/NMEA por UART.",
    "Persistencia local: SQLite en modo WAL, para lectura/escritura concurrente entre el "
    "proceso que encola alertas y el que las despacha.",
    "Backend/nube: FastAPI con MongoDB (vía Beanie); OSRM para cálculo de rutas queda como "
    "mejora futura.",
    "Frontend: React con react-leaflet, mostrando geolocalización y estado de flota/alertas.",
])
delete_range([P[39], P[40], P[41], P[42], P[43]])

set_text(P[44], "D. Arquitectura y diseño del sistema")
set_text(P[45], "La Fig. 1 muestra la arquitectura completa del sistema: en el borde, la "
                 "Raspberry Pi 5 ejecuta MediaPipe y el modelo fusionado CNN+LSTM y decide el "
                 "estado del conductor, comunicándolo por Bluetooth al ESP32, que actúa "
                 "(alarma, freno preventivo, botón de pánico) y habla por HTTP con el backend; "
                 "en la nube, FastAPI + MongoDB sirven a un frontend React con mapa en tiempo "
                 "real.")
insert_full_width_figure(
    P[45], f"{DESIGNS}/semantic-design-overview.png",
    "Fig. 1 — Arquitectura del sistema Argus: borde (Raspberry Pi 5 + ESP32) y nube "
    "(FastAPI + MongoDB + React).",
)
delete_range([P[46], P[47], P[48], P[49], P[50], P[51], P[52], P[53], P[54], P[55], P[56],
              P[57], P[58], P[59], P[60], P[61], P[62], P[63]])

set_text(P[64], "E. Pruebas realizadas")
set_text(P[65], "Se comparó un conjunto de arquitecturas sobre el mismo problema binario "
                 "(Not Drowsy / Drowsy), resumido en la Tabla II: RandomForest y una red "
                 "densa sobre features de un solo frame quedaron topadas en 33–41% de "
                 "accuracy (correlación de Spearman máxima |r|=0.26 entre cualquier feature "
                 "de un solo frame y el nivel de somnolencia); una CNN sobre el recorte "
                 "facial completo subió a 59.64% / 0.5273 F1 macro; y el modelo final — el "
                 "embedding congelado de esa CNN fusionado con features geométricas y "
                 "alimentado a una LSTM sobre una ventana de hasta 100 frames — alcanzó "
                 "84.24% accuracy / 0.8375 F1 macro. División train/val/test agrupada por "
                 "sujeto (StratifiedGroupKFold) en todos los casos.")
insert_table_after(P[65], [
    ["Modelo", "Accuracy", "F1 macro", "Recall Drowsy"],
    ["RandomForest (7 feat. geométricas)", "32.6%", "—", "0.13"],
    ["Red densa (58 features)", "38.6–40.8%", "—", "—"],
    ["CNN de un solo frame", "59.64%", "0.5273", "—"],
    ["CNN+LSTM (backbone de cero)", "62.29%", "0.6078", "0.47"],
    ["CNN+LSTM (embedding congelado)*", "84.24%", "0.8375", "0.73"],
    ["Ensemble (0.5 cero + 0.5 congelado)", "82.99%", "0.8255", "0.73"],
], col_widths_in=[1.2, 0.75, 0.62, 0.6])

set_text(P[66], "F. Proceso de implementación")
P[66].style = doc.styles["Heading 2"]
P[67].style = doc.styles["Normal"]
set_text(P[67], "Pipeline de notebooks encadenados por etapa (Colab + Drive): extracción de "
                 "recortes faciales (Face Detector) → extracción de features geométricas "
                 "sobre esos recortes (FaceLandmarker) → entrenamiento de la CNN → "
                 "congelamiento de su embedding y fusión con las features geométricas por "
                 "instante → entrenamiento de la LSTM sobre esa secuencia fusionada → "
                 "exportación del modelo final y carga en cv-argus para inferencia en vivo en "
                 "el Pi, con una ventana deslizante en memoria (vectores ya fusionados, no "
                 "imágenes) que se actualiza un frame a la vez.")
delete_range([P[68], P[69]])
delete_range(P[70:91])  # instructivo "H. Referencias bibliográficas" + 12 ejemplos de la plantilla

# ===================================== MÓDULO I — Gestión de la Tecnología de IT ===
# (contenido de Arquitectura y Programación de Sistemas)
set_text(P[92], "1.1 Lenguajes: Python en todo el pipeline de IA (ecosistema maduro de "
                 "MediaPipe/TensorFlow/OpenCV). C en el firmware ESP32 (estándar para ese "
                 "microcontrolador; suficiente para GPS por UART y control de GPIO).")
add_bullets_after(P[92], [
    "1.2 Bases de datos / estructuras de datos: SQLite como cola de buffer local, sin "
    "servidor, modo WAL para concurrencia productor/consumidor; una ventana circular numpy "
    "de forma (100, 74) como buffer del modelo desplegado (64 del embedding CNN + 10 "
    "geométricas por instante, ~30 KB, sin reprocesar imágenes); MongoDB como base de datos "
    "del backend, esquema flexible por documento para users/trucks/drivers/alerts/routes.",
    "1.3 Metodología: Scrum (ver Sección III-A).",
    "1.4 Ingeniería de software: separación de responsabilidades explícita en cv-argus "
    "(model/, pipeline/, orchestrator/, buffer/, sender/, alerts/) con dependencias en una "
    "sola dirección. Docker como mecanismo de reproducibilidad.",
    "1.5 Modelado del sistema: la Fig. 1 traza una frontera explícita entre lo que decide "
    "(Raspberry Pi, IA) y lo que actúa (ESP32, hardware de seguridad), y entre el borde "
    "(tiempo real, crítico) y la nube (gestión, no crítica en tiempo real).",
])

# ===================================== MÓDULO II — Sistemas Distribuidos ===
set_text(P[94], "El mérito distribuido del proyecto no se apoya únicamente en el tramo hacia "
                 "la nube — un backend centralizado consumido por clientes vía HTTP no "
                 "constituye, por sí solo, un sistema distribuido — sino en elementos "
                 "concretos de cómputo y comunicación entre dispositivos del propio equipo.")
add_bullets_after(P[94], [
    "Procesamiento distribuido: la inferencia del modelo CNN+LSTM sobre video ocurre en el "
    "borde, dentro de cada camión, en vez de enviar video crudo a un servidor central. La "
    "carga de cómputo está descentralizada por diseño, no solo por la escala de la flota.",
    "Comunicación directa entre dos dispositivos: la Raspberry Pi 5 (decide) y el ESP32 "
    "(actúa) se comunican por Bluetooth, con el ESP32 haciendo pull periódico sobre la cola "
    "SQLite de la Pi — un protocolo propio implementado en ambos extremos, no el consumo de "
    "un servicio cliente/servidor ya construido por terceros.",
    "Tolerancia a fallos con buffer local: la cola SQLite persiste las alertas cuando no hay "
    "conectividad y las reenvía automáticamente al recuperar la señal.",
    "Concurrencia real: el proceso que encola y el que despacha alertas operan de forma "
    "concurrente sobre el mismo archivo SQLite en modo WAL.",
])

# ===================================== MÓDULO III — Softcomputing ===
set_text(P[96], "Ramas de IA cubiertas: redes neuronales (CNN + LSTM), aprendizaje "
                 "automático (RandomForest como baseline) y visión artificial (MediaPipe "
                 "Face Detector + FaceLandmarker).")
mod3 = add_bullets_after(P[96], [
    "Modelo matemático: la capa GeometricRatioFeatureLayer calcula, por frame, EAR "
    "(Eye Aspect Ratio) y MAR (Mouth Aspect Ratio) a partir de razones geométricas entre "
    "landmarks. El modelo final usa 10 de esas features por instante, concatenadas con el "
    "embedding de 64-D de una CNN ya entrenada sobre el recorte facial y con pesos "
    "congelados — el vector fusionado de 74 valores por instante, sobre una ventana de hasta "
    "100 instantes, alimenta una LSTM(64) con salida softmax binaria.",
    "Justificación de algoritmos: RandomForest y la red densa sobre features de un solo "
    "frame quedaron topadas en 33–41% de accuracy — el techo lo explica la correlación de "
    "Spearman máxima de |r|=0.26 entre cualquier feature de un solo frame y el nivel de "
    "somnolencia. Fusionar el embedding congelado con las features geométricas y alimentarlo "
    "a una LSTM sobre una ventana llevó el resultado a 84.24% / 0.8375 F1 macro (Tabla II), "
    "confirmando que el contexto temporal revela algo que un solo frame no puede, "
    "estructuralmente, ver.",
    "La clasificación final no usa argmax sino un umbral de decisión (t*=0.57) elegido en "
    "validación con criterio de seguridad: entre los umbrales con recall de Drowsy ≥ 0.70, "
    "el de mejor precisión — perder a un conductor somnoliento es el error más costoso.",
    "Justificación de MediaPipe: Face Detector y FaceLandmarker son arquitecturas tipo "
    "BlazeFace, ligeras y cuantizables para CPU/NPU sin GPU dedicada — la única opción "
    "evaluada con inferencia en tiempo real ya validada en hardware de borde de bajo costo.",
])
mod3 = new_paragraph_after(mod3, "La Tabla III y la Fig. 2 muestran el desempeño medido del "
                                  "modelo final sobre 1,440 ventanas de prueba de 10 sujetos "
                                  "nunca vistos en entrenamiento.")
mod3 = insert_table_after(mod3, [
    ["Clase", "Prec.", "Rec.", "F1", "Sop."],
    ["Not Drowsy", "0.80", "0.94", "0.87", "780"],
    ["Drowsy", "0.91", "0.73", "0.81", "660"],
    ["accuracy", "", "", "0.8424", "1440"],
    ["macro avg", "0.86", "0.83", "0.8375", "1440"],
], col_widths_in=[0.85, 0.5, 0.5, 0.65, 0.45])
mod3 = insert_full_width_figure(
    mod3, f"{DESIGNS}/cnn-lstm-mediapipe-pipeline.png",
    "Fig. 2 — Pipeline del modelo desplegado: MediaPipe (Face Detector + FaceLandmarker) → "
    "CNN congelada + GeometricRatioFeatureLayer → fusión → LSTM (t*=0.57).",
)

# ================================= IV. RESULTADOS OBTENIDOS DEL PROYECTO ===
RESULTADOS = (
    "Se comparó un conjunto de arquitecturas sobre el mismo problema binario: RandomForest y "
    "una red densa sobre features geométricas de un solo frame (33–41% de accuracy); una CNN "
    "sobre el recorte facial completo (59.64% accuracy, 0.5273 F1 macro); y el modelo final "
    "— el embedding congelado de esa CNN fusionado con features geométricas y alimentado a "
    "una LSTM sobre una ventana de hasta 20 segundos — que alcanzó 84.24% accuracy, 0.8375 F1 "
    "macro sobre sujetos nunca vistos, casi el doble del F1 de la CNN de un solo frame "
    "(Tabla II, Tabla III). El modelo final se empaquetó como un par de artefactos Keras "
    "reproducibles (la CNN congelada + la LSTM de fusión) y se integró en cv-argus: corrió de "
    "punta a punta contra una cámara en vivo y contra video grabado, con una ventana "
    "deslizante ligera manteniendo el estado entre frames. De los seis módulos planeados en "
    "el borde, dos quedaron terminados como código — model/ y pipeline/ — corriendo de punta "
    "a punta; los otros cuatro (orchestrator/, buffer/, sender/, alerts/), el firmware del "
    "ESP32, el backend y el frontend siguen sin código. El número reportado es de un solo "
    "fold (StratifiedGroupKFold, sujetos disjuntos), sin validación cruzada todavía; no se ha "
    "corrido de punta a punta contra hardware real de Raspberry Pi (solo contra un contenedor "
    "Docker en una laptop); y la identidad exacta del checkpoint de CNN usado en producción "
    "frente al usado para generar los embeddings de entrenamiento no está verificada."
)
set_text(P[98], RESULTADOS)
delete_range([P[99], P[100], P[101], P[102], P[103], P[104]])

# ============================== V. CONCLUSIONES Y TRABAJO A FUTURO ===
CONCLUSION = (
    "El desarrollo confirmó que un enfoque puramente geométrico (EAR/MAR + pose + "
    "blendshapes de un solo frame) tiene un techo real y medido — no solo sospechado — "
    "alrededor del 33–41% de accuracy, y que superarlo requirió incorporar información "
    "visual cruda (una CNN sobre el recorte facial) y, sobre todo, dar contexto temporal a "
    "esa información (fusionar el embedding de esa CNN con features geométricas y clasificar "
    "sobre una ventana, no un frame suelto). El resultado final — 84.24% de accuracy, 0.8375 "
    "de F1 macro — casi duplica el F1 de la misma CNN juzgando frame por frame, confirmando "
    "de forma directa la hipótesis central del proyecto: la duración y velocidad del cierre "
    "ocular a lo largo de una ventana es una señal que un solo instante no puede capturar "
    "estructuralmente. El diseño edge-first está además pensado para escalar por flota: "
    "sumar camiones no implica escalar un servidor central de inferencia, porque cada unidad "
    "ya trae su propio cómputo."
)
set_text(P[106], CONCLUSION)
set_text(P[107], "Trabajo a futuro:")
add_bullets_after(P[107], [
    "Validar el desempeño del modelo final corriendo en hardware real (Raspberry Pi 5): "
    "latencia de inferencia por frame, uso de CPU/memoria en ARM.",
    "Correr una validación cruzada (k-fold) sobre el resultado — hoy es un solo fold "
    "agrupado por sujeto.",
    "Confirmar que el checkpoint de la CNN que cv-argus descarga en producción es "
    "exactamente el mismo usado para generar los embeddings con los que se entrenó la LSTM "
    "final.",
    "Completar e integrar orchestrator/, buffer/, sender/ y alerts/ con hardware real (Pi 5 "
    "+ cámara CSI) para validar la cadena completa de alerta.",
    "Prototipar el firmware del ESP32 sobre el diseño ya decidido (Bluetooth, polling de la "
    "cola SQLite, HTTP hacia el backend FastAPI).",
    "Implementar el backend/frontend en la nube sobre FastAPI + MongoDB; OSRM como mejora "
    "futura.",
    "Módulos opcionales fuera del MVP: segmentación de carriles, fusión biométrica "
    "multimodal, respaldo satelital, y ampliar el conjunto de entrenamiento con más sujetos.",
])

# ==================================================== RECONOCIMIENTOS ===
set_text(P[109], "Ing. Mario Antonio Ruz Canul, asesor del proyecto, por su guía a lo largo "
                  "del desarrollo.")
delete_paragraph(P[110])

# ========================================================= REFERENCIAS ===
REFS = [
    [("R. Ghoddoosian, M. Galib, y V. Athitsos, “A realistic dataset and baseline temporal "
      "model for early drowsiness detection,” en ", False),
     ("Proc. IEEE/CVF Conf. Computer Vision and Pattern Recognition Workshops (CVPRW)", True),
     (", 2019.", False)],
    [("H. Bodaghi et al., “UL-DD: A multimodal drowsiness dataset,” 2025.", False)],
    [("M. Hidalgo-Gadea et al., “Towards better microsleep predictions in fatigued drivers: "
      "exploring benefits of personality traits and IQ,” ", False),
     ("Ergonomics", True), (", 2021.", False)],
    [("E. D. Enríquez Gallegos, “Detección de somnolencia en conducción diurna y nocturna "
      "utilizando CNN MobileNet,” Trabajo de grado, Universidad Técnica del Norte, Ecuador, "
      "2025.", False)],
    [("Geotab Inc., “Soluciones de monitoreo del conductor.” [Online]. Available: "
      "https://www.geotab.com/es-latam/soluciones-de-gestion-de-flotas/rastreo-conductor/",
      False)],
    [("Samsara Inc., “Soluciones de monitoreo del conductor.” [Online]. Available: "
      "https://www.samsara.com/mx/products/platform/ai-samsara-intelligence", False)],
    [("Aurora Innovation, “Automatización comercial de Nivel 4.” [Online]. Available: "
      "https://www.dufrei.com/blog/noticias-2/aurora-triplica-flota-autonoma-en-eu-y-mexico-161",
      False)],
    [("Kodiak Robotics, “Automatización comercial de Nivel 4.” [Online]. Available: "
      "https://kodiak.ai/news/kodiak-delivers-customer-owned-autonomous-robotrucks-to-atlas",
      False)],
    [("Norma Oficial Mexicana NOM-087-SCT-2, Transporte terrestre — Condiciones "
      "físico-mecánicas y de seguridad para la operación, Secretaría de Comunicaciones y "
      "Transportes, México.", False)],
    [("Google, “MediaPipe Face Landmarker,” documentación técnica. [Online]. Available: "
      "https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker",
      False)],
]
ref_paras = [P[112], P[113], P[114], P[115], P[116], P[117], P[118], P[119], P[120], P[121]]
for n, (rp, parts) in enumerate(zip(ref_paras, REFS), start=1):
    set_reference(rp, n, parts)
delete_paragraph(P[122])

doc.save(OUT)
print("wrote", OUT)
