"""Reporte ejecutivo de TRAXA: página imprimible (PDF) y descarga en Word.

Se conecta desde app.py con dos líneas:
    import reporte
    reporte.registrar(app, sys.modules[__name__])
"""
import io
import re
from collections import Counter
from datetime import datetime

from flask import jsonify, render_template, request, send_file

N = None   # módulo principal (app.py): ahí viven la lectura del Sheet y los catálogos

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_MINUSCULAS = {"de", "del", "la", "las", "los", "y", "e"}

SYSTEM_RESUMEN_REPORTE = (
    "Redactas el párrafo de resumen de un reporte ejecutivo de actividades de campo "
    "de proyectos ferroviarios (SEDATU / Registro Agrario Nacional).\n"
    "Reglas:\n"
    "- UN solo párrafo, de 70 a 110 palabras, en pasado, tono institucional, claro y directo.\n"
    "- Usa ÚNICAMENTE las cifras y nombres que se te dan; no inventes ni estimes nada.\n"
    "- Menciona el periodo, el total de actividades, los proyectos con mayor carga, "
    "las categorías principales y las dependencias con mayor participación.\n"
    "- Sin viñetas, sin encabezado, sin comillas, sin preguntas.\n"
    "- Responde SOLO con el párrafo."
)


# Pasa "SAN LUIS POTOSÍ" a "San Luis Potosí" (de, del, la... en minúscula).
def _titulo(s):
    palabras = str(s or "").strip().lower().split()
    return " ".join(p if (i and p in _MINUSCULAS) else p[:1].upper() + p[1:]
                    for i, p in enumerate(palabras))


# Fecha de la fila (acepta dd/mm/aaaa y mm/dd/aaaa); None si no es legible.
def _fecha(f):
    s = str(f.get("FECHA Y HORA", "")).split()
    if not s:
        return None
    partes = s[0].replace("-", "/").split("/")
    if len(partes) != 3:
        return None
    d, m, a = partes
    if len(a) == 2:
        a = "20" + a
    try:
        if int(m) > 12 and int(d) <= 12:
            d, m = m, d
        return datetime(int(a), int(m), int(d)).date()
    except (ValueError, TypeError):
        return None


# Hora HH:MM de la fila ("" si no trae).
def _hora(f):
    partes = str(f.get("FECHA Y HORA", "")).split(maxsplit=1)
    m = re.search(r"\d{1,2}:\d{2}", partes[1]) if len(partes) > 1 else None
    return m.group(0).zfill(5) if m else ""


def _fecha_larga(d):
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


# Rango pedido en la URL (?desde=AAAA-MM-DD&hasta=AAAA-MM-DD).
# Sin fechas: del 1 de enero del año en curso a hoy.
def _rango():
    hoy = datetime.now().date()
    d, h = request.args.get("desde"), request.args.get("hasta")
    desde = datetime.strptime(d, "%Y-%m-%d").date() if d else hoy.replace(month=1, day=1)
    hasta = datetime.strptime(h, "%Y-%m-%d").date() if h else hoy
    return (hasta, desde) if desde > hasta else (desde, hasta)


def _lista(contador, tope=None):
    return [{"nombre": k, "n": v} for k, v in contador.most_common(tope)]


# Lee el Sheet y arma todos los números del reporte para el rango dado.
def datos(desde, hasta):
    inv = {v: k for k, v in N.mapa_proyectos.items()}
    proy, edo, dep, cat, prop, mun, por_dia = (Counter() for _ in range(7))
    actividades, sin_fecha, sin_categoria = [], 0, 0

    for f in N._filas_hoja_principal():
        if not any((v or "").strip() for v in f.values()):
            continue                                    # renglón vacío
        d = _fecha(f)
        if d is None:
            sin_fecha += 1
            continue
        if d < desde or d > hasta:
            continue

        clave = inv.get((f.get("PROYECTO FERROVIARIO") or "").strip(), "OTRO")
        proy[clave] += 1

        estado = _titulo(f.get("ESTADO"))
        if estado:
            edo[estado] += 1
        municipio = _titulo(f.get("MUNICIPIO"))
        if municipio:
            mun[f"{municipio}, {estado}" if estado else municipio] += 1

        celda = N._sin_acentos(str(f.get("DEPENDENCIAS PARTICIPANTES", "")).upper())
        for canon, patrones in N._DEP_PATRONES.items():
            if any(p.search(celda) for p in patrones):
                dep[canon] += 1

        # Mismo criterio que la pantalla de Conteo, para que los números coincidan.
        hits = N.clasificar_actividad(f.get("TIPO DE SOLICITUD", ""),
                                      f.get("ACTIVIDADES DESARROLLADAS", ""))
        if not hits:
            sin_categoria += 1
        for nombre_cat, _sub in hits:
            cat[re.sub(r"^\d+\.\s*", "", nombre_cat)] += 1

        tipo = re.sub(r"\s+", " ", f.get("TIPO DE PROPIEDAD", "") or "").strip()
        prop[_titulo(tipo) if tipo and tipo.upper() != "N/A" else "Sin dato"] += 1

        por_dia[d] += 1
        actividades.append((d, _hora(f), {
            "fecha": d.strftime("%d/%m/%Y"),
            "hora": _hora(f),
            "proyecto": clave,
            "municipio": municipio,
            "estado": estado,
            "actividad": (f.get("TIPO DE SOLICITUD") or "").strip(),
            "dependencias": (f.get("DEPENDENCIAS PARTICIPANTES") or "").strip(),
        }))

    # Evolución: por día si el rango es de hasta un mes; si no, por mes.
    if (hasta - desde).days <= 31:
        unidad = "día"
        puntos = [{"etiqueta": d.strftime("%d/%m"), "n": n} for d, n in sorted(por_dia.items())]
    else:
        unidad = "mes"
        por_mes = Counter()
        for d, n in por_dia.items():
            por_mes[(d.year, d.month)] += n
        puntos = [{"etiqueta": f"{MESES[m - 1][:3]} {a}", "n": n}
                  for (a, m), n in sorted(por_mes.items())]

    proyectos = []
    for clave, n in proy.most_common():
        nombre = (re.sub(r"^\d+\.\s*", "", N.mapa_proyectos[clave])
                  if clave in N.mapa_proyectos else "Sin proyecto reconocido")
        proyectos.append({"clave": clave, "nombre": nombre, "n": n})

    actividades.sort(key=lambda t: (t[0], t[1] or "99:99"))
    total = sum(proy.values())
    return {
        "ventana": {
            "desde": desde.strftime("%d/%m/%Y"), "hasta": hasta.strftime("%d/%m/%Y"),
            "desde_iso": desde.isoformat(), "hasta_iso": hasta.isoformat(),
            "desde_txt": _fecha_larga(desde), "hasta_txt": _fecha_larga(hasta),
        },
        "generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total": total,
        "dias_con_actividad": len(por_dia),
        "proyectos": proyectos,
        "estados": _lista(edo),
        "dependencias": _lista(dep),
        "categorias": _lista(cat),
        "sin_categoria": sin_categoria,
        "propiedad": _lista(prop),
        "municipios": _lista(mun, 10),
        "serie": {"unidad": unidad, "puntos": puntos},
        "actividades": [a for _, _, a in actividades],
        "sin_fecha": sin_fecha,
    }


def _enumerar(nombres):
    nombres = list(nombres)
    if len(nombres) <= 1:
        return "".join(nombres)
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


# Párrafo armado solo con las cifras; se usa si Haiku no responde.
def _resumen_base(d):
    v = d["ventana"]
    if not d["total"]:
        return f"Del {v['desde_txt']} al {v['hasta_txt']} no se registraron actividades."
    partes = [
        f"Del {v['desde_txt']} al {v['hasta_txt']} se registraron {d['total']} actividades "
        f"en {len([p for p in d['proyectos'] if p['clave'] != 'OTRO'])} proyectos ferroviarios "
        f"y {len(d['estados'])} estados, distribuidas en {d['dias_con_actividad']} días con actividad."
    ]
    if d["proyectos"]:
        p = d["proyectos"][0]
        partes.append(f"El proyecto con mayor carga fue {p['nombre']} ({p['clave']}) con {p['n']} "
                      f"actividades ({round(p['n'] / d['total'] * 100)}% del total).")
    if d["categorias"]:
        c = d["categorias"][0]
        partes.append(f"La categoría más frecuente fue {c['nombre']} ({c['n']}).")
    if d["dependencias"]:
        partes.append("Las dependencias con mayor participación fueron "
                      + _enumerar(f"{x['nombre']} ({x['n']})" for x in d["dependencias"][:3]) + ".")
    return " ".join(partes)


# Resumen redactado por Haiku a partir de las cifras; si falla, el párrafo base.
def resumen(d):
    base = _resumen_base(d)
    if not d["total"]:
        return base
    v = d["ventana"]
    hechos = "\n".join([
        f"Periodo: del {v['desde_txt']} al {v['hasta_txt']}",
        f"Total de actividades: {d['total']}",
        f"Días con actividad: {d['dias_con_actividad']}",
        "Proyectos (actividades): " + "; ".join(f"{p['nombre']} [{p['clave']}]: {p['n']}" for p in d["proyectos"][:6]),
        "Estados: " + "; ".join(f"{x['nombre']}: {x['n']}" for x in d["estados"][:6]),
        "Categorías: " + "; ".join(f"{x['nombre']}: {x['n']}" for x in d["categorias"][:5]),
        "Dependencias participantes: " + "; ".join(f"{x['nombre']}: {x['n']}" for x in d["dependencias"][:6]),
    ])
    texto = N._llamar_haiku(SYSTEM_RESUMEN_REPORTE, hechos, 400, 0.4, "reporte")
    malo = (not texto or "?" in texto or len(texto) > 1100 or len(texto.split()) < 25
            or re.search(r"(?i)(necesito que|proporci[oó]n|por favor|no puedo|no cuento con)", texto))
    return base if malo else " ".join(texto.split())


# Arma el archivo de Word con el mismo contenido que la página.
def _word(d):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    GUINDA, DORADO, GRIS = RGBColor(0xA4, 0x21, 0x45), RGBColor(0xA8, 0x80, 0x3F), RGBColor(0x5A, 0x46, 0x36)
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2)
        s.top_margin = s.bottom_margin = Cm(1.8)
    base = doc.styles["Normal"]
    base.font.name = "Noto Sans"
    base.font.size = Pt(10)

    def parrafo(texto, tam=10, negrita=False, color=None, centro=False, espacio=4):
        p = doc.add_paragraph()
        if centro:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(espacio)
        r = p.add_run(texto)
        r.font.size, r.bold = Pt(tam), negrita
        if color is not None:
            r.font.color.rgb = color
        return p

    def sombrear(celda, hexa):
        sh = OxmlElement("w:shd")
        sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), hexa)
        celda._tc.get_or_add_tcPr().append(sh)

    def tabla(titulo, encabezados, filas, anchos):
        parrafo(titulo, 12, True, GUINDA, espacio=3).paragraph_format.space_before = Pt(10)
        if not filas:
            parrafo("Sin registros en el periodo.", 9, color=GRIS)
            return
        t = doc.add_table(rows=1, cols=len(encabezados))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(encabezados):
            c = t.rows[0].cells[i]
            c.text = ""
            r = c.paragraphs[0].add_run(h)
            r.bold, r.font.size, r.font.color.rgb = True, Pt(9), RGBColor(0xFF, 0xFF, 0xFF)
            sombrear(c, "A42145")
        for fila in filas:
            celdas = t.add_row().cells
            for i, valor in enumerate(fila):
                celdas[i].text = ""
                r = celdas[i].paragraphs[0].add_run(str(valor))
                r.font.size = Pt(9)
                if i and isinstance(valor, (int, float)) or (i and str(valor).endswith("%")):
                    celdas[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for fila in t.rows:
            for i, ancho in enumerate(anchos):
                fila.cells[i].width = Cm(ancho)

    def pct(n):
        return f"{round(n / d['total'] * 100)}%" if d["total"] else "0%"

    v = d["ventana"]
    parrafo("TRAXA", 22, True, GUINDA, centro=True, espacio=0)
    parrafo("REPORTE EJECUTIVO DE ACTIVIDADES", 11, True, DORADO, centro=True, espacio=2)
    parrafo(f"Del {v['desde_txt']} al {v['hasta_txt']}", 11, centro=True, espacio=10)

    kpi = doc.add_table(rows=2, cols=4)
    kpi.alignment = WD_TABLE_ALIGNMENT.CENTER
    valores = [(d["total"], "Actividades"),
               (len([p for p in d["proyectos"] if p["clave"] != "OTRO"]), "Proyectos ferroviarios"),
               (len(d["estados"]), "Estados"),
               (len(d["dependencias"]), "Dependencias")]
    for i, (n, etiqueta) in enumerate(valores):
        a, b = kpi.rows[0].cells[i], kpi.rows[1].cells[i]
        a.text = b.text = ""
        r = a.paragraphs[0].add_run(f"{n:,}")
        r.bold, r.font.size, r.font.color.rgb = True, Pt(20), GUINDA
        r2 = b.paragraphs[0].add_run(etiqueta)
        r2.font.size, r2.font.color.rgb = Pt(8), GRIS
        a.paragraphs[0].alignment = b.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    parrafo("Resumen ejecutivo", 12, True, GUINDA, espacio=3).paragraph_format.space_before = Pt(12)
    p = parrafo(d.get("resumen") or _resumen_base(d), 10, espacio=6)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    tabla("Actividades por proyecto ferroviario", ["Clave", "Proyecto", "Actividades", "%"],
          [(x["clave"], x["nombre"], x["n"], pct(x["n"])) for x in d["proyectos"]], [2, 9.5, 3, 2])
    tabla("Actividades por categoría", ["Categoría", "Conceptos"],
          [(x["nombre"], x["n"]) for x in d["categorias"]]
          + ([("Sin categoría asignada", d["sin_categoria"])] if d["sin_categoria"] else []), [13.5, 3])
    tabla("Distribución por estado", ["Estado", "Actividades", "%"],
          [(x["nombre"], x["n"], pct(x["n"])) for x in d["estados"]], [11.5, 3, 2])
    tabla("Dependencias participantes", ["Dependencia", "Actividades"],
          [(x["nombre"], x["n"]) for x in d["dependencias"]], [13.5, 3])
    tabla("Tipo de propiedad", ["Tipo", "Actividades", "%"],
          [(x["nombre"], x["n"], pct(x["n"])) for x in d["propiedad"]], [11.5, 3, 2])
    tabla("Municipios con más actividades", ["Municipio", "Actividades"],
          [(x["nombre"], x["n"]) for x in d["municipios"]], [13.5, 3])
    tabla(f"Actividades por {d['serie']['unidad']}", [d["serie"]["unidad"].capitalize(), "Actividades"],
          [(x["etiqueta"], x["n"]) for x in d["serie"]["puntos"]], [13.5, 3])

    if request.args.get("listado") == "1":
        doc.add_page_break()
        tabla("Listado de actividades", ["Fecha", "Hora", "Proy.", "Municipio", "Actividad"],
              [(a["fecha"], a["hora"], a["proyecto"], a["municipio"], a["actividad"])
               for a in d["actividades"]], [2.2, 1.4, 1.6, 3.3, 8])

    parrafo(f"Registro Agrario Nacional · Dirección General Territorial — Generado el {d['generado']}",
            8, color=GRIS, centro=True).paragraph_format.space_before = Pt(14)

    salida = io.BytesIO()
    doc.save(salida)
    salida.seek(0)
    return salida


# Conecta las rutas del reporte a la app principal.
def registrar(app, nucleo):
    global N
    N = nucleo

    def _armar(con_resumen=True):
        d = datos(*_rango())
        d["resumen"] = resumen(d) if con_resumen else _resumen_base(d)
        return d

    # Página del reporte (se guarda como PDF desde el navegador).
    @app.route("/reporte")
    def reporte_pagina():
        return render_template("reporte.html")

    # API: todos los números del reporte para el rango pedido.
    @app.route("/api/reporte")
    def api_reporte():
        try:
            return jsonify(_armar(request.args.get("resumen") != "0"))
        except ValueError:
            return jsonify({"error": "Fecha inválida en el filtro"}), 400
        except Exception:
            return N._error_api()

    # Descarga del reporte en Word.
    @app.route("/reporte.docx")
    def reporte_word():
        try:
            d = _armar()
            nombre = f"TRAXA_reporte_{d['ventana']['desde_iso']}_a_{d['ventana']['hasta_iso']}.docx"
            return send_file(
                _word(d), as_attachment=True, download_name=nombre,
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except ValueError:
            return "Fecha inválida en el filtro", 400
        except Exception:
            import traceback
            print(traceback.format_exc(), flush=True)
            return "No se pudo generar el Word; revisa el log del servidor.", 500
