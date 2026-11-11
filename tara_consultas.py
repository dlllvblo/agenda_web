"""Tara responde preguntas sobre la agenda por Telegram.

Las cuentas las hace el código sobre el Sheet, nunca la IA. Cada respuesta dice
qué periodo y qué filtros entendió, para que se pueda verificar de un vistazo.

Se conecta desde app.py con:
    import tara_consultas
    tara_consultas.registrar(app, sys.modules[__name__])
y una línea dentro de _procesar_buffer:
    if tara_consultas.atender(chat_id, texto): return
"""
import hmac
import os
import re
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from flask import jsonify, request

from reporte import MESES, _fecha, _hora, _titulo

N = None                 # módulo principal (app.py)
MAX_LISTA = 25           # actividades que caben en un mensaje de lista
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_RE_MES = "|".join(MESES)

ALIAS_EDO = {
    "coahuila": "coahuila de zaragoza", "veracruz": "veracruz de ignacio de la llave",
    "michoacan": "michoacan de ocampo", "edomex": "mexico", "estado de mexico": "mexico",
    "cdmx": "ciudad de mexico",
}

AYUDA = (
    "Soy Tara. Reenvíame una agenda y la subo al Sheet, o pregúntame por lo que ya está registrado:\n\n"
    "• ¿Qué hay mañana en TSNL?\n"
    "• ¿Cuántas firmas hubo en TMQ en junio?\n"
    "• Lista de actividades del 3 al 7 de noviembre\n"
    "• Resumen de esta semana\n"
    "• ¿Cuántas reuniones con SEDENA este mes?\n\n"
    "Atajos: /hoy  /manana  /semana  /mes  /ayuda\n\n"
    "Entiendo: hoy, ayer, mañana, esta semana, semana pasada, este mes, mes pasado, "
    "un mes por su nombre, fechas como 12/11/2026 o «del 3 al 7 de junio»; la clave del "
    "proyecto (TMQ, TSNL…), estados, municipios, dependencias y el tipo de actividad."
)

SYSTEM_REESCRIBIR = (
    "Reescribes una pregunta sobre una agenda de actividades de campo en UNA sola línea, "
    "usando solo este vocabulario:\n"
    "- Verbo: 'cuantas', 'lista' o 'resumen'.\n"
    "- Periodo: 'hoy', 'ayer', 'mañana', 'esta semana', 'semana pasada', 'este mes', "
    "'mes pasado', el nombre de un mes, o una fecha dd/mm/aaaa.\n"
    "- Proyecto: su clave (TAP, TMQ, TQI, TSNL, TIG, TMLM, TQSLP, TSLPS, TIX).\n"
    "Conserva tal cual los nombres de estados, municipios, dependencias y el tipo de actividad.\n"
    "No agregues datos que no estén en la pregunta. Si la pregunta no trata de la agenda, "
    "responde solo: NO"
)


# Hoy en horario del centro de México (el servidor corre en UTC).
def hoy_mx():
    return (datetime.now(timezone.utc) - timedelta(hours=6)).date()


# Texto comparable: sin acentos ni mayúsculas, puntuación como espacio.
def _n(s):
    s = N._sin_acentos(str(s or "")).lower()
    s = re.sub(r"[¿?¡!.,;:()\"'“”«»]", " ", s)
    s = re.sub(r"[-–—]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Estado en forma comparable y única ("Coahuila" = "Coahuila de Zaragoza").
def _edo(s):
    e = _n(s)
    return ALIAS_EDO.get(e, e)


def _palabra(frase, texto):
    return re.search(r"(?<!\w)" + re.escape(frase) + r"(?!\w)", texto) is not None


# True si el mensaje parece una pregunta o un comando y no una agenda.
def es_consulta(texto):
    t = (texto or "").strip()
    if not t or len(t) > 300 or t.count("\n") > 3:
        return False
    if re.search(r"(?im)^\s*\d+[\.\-]\s|\bHora\s*:|https?://", t):
        return False
    if t.startswith("/"):
        return True
    # Una hora (10:00) o un encabezado "Agenda ..." / "Proyecto ..." son de agenda, no de pregunta.
    if re.search(r"\b\d{1,2}:\d{2}\b", t) or re.match(r"(proyecto\b|agenda\s+(?!de\s+(hoy|manana|ayer)\b))", _n(t)):
        return False
    if "?" in t or "¿" in t:
        return True
    return bool(re.match(
        r"(cuant[oa]s?|que hay|que tenemos|que se tiene|cuales|dime|dame|lista|listado|listame|"
        r"muestrame|muestra|resumen|ayuda|hola|buenos dias|buenas tardes|buenas noches|"
        r"agenda de (hoy|manana|ayer)|actividades (de|del|en|para|hoy|manana))\b", _n(t)))


def _anio(s, hoy):
    if not s:
        return hoy.year
    return int(s) + 2000 if len(s) <= 2 else int(s)


class FechaInvalida(ValueError):
    """La pregunta trae una fecha que no existe (ej. 31/02)."""


def _dia(a, m, d):
    try:
        return date(a, m, d)
    except ValueError:
        raise FechaInvalida(f"{d}/{m}/{a}")


def _fin_de_mes(a, m):
    return (date(a + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)


# Periodo que pide la pregunta: (desde, hasta) o None si no menciona ninguno.
def periodo(n, hoy):
    n = re.sub(r"\b(por|en|a|de|durante) la manana\b", " ", n)   # "en la mañana" es parte del día

    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*(?:al|a|hasta el|hasta)\s*"
                  r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", n)
    if m:
        a2 = _anio(m.group(6), hoy)
        d1 = _dia(_anio(m.group(3), hoy) if m.group(3) else a2, int(m.group(2)), int(m.group(1)))
        d2 = _dia(a2, int(m.group(5)), int(m.group(4)))
        if d1 and d2:
            return min(d1, d2), max(d1, d2)

    m = re.search(rf"\b(\d{{1,2}})\s*(?:al|a)\s*(\d{{1,2}})\s+de\s+({_RE_MES})(?:\s+(?:de|del)\s+(\d{{4}}))?", n)
    if m:
        a, mes = _anio(m.group(4), hoy), MESES.index(m.group(3)) + 1
        d1, d2 = _dia(a, mes, int(m.group(1))), _dia(a, mes, int(m.group(2)))
        if d1 and d2:
            return min(d1, d2), max(d1, d2)

    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", n)
    if m:
        d = _dia(_anio(m.group(3), hoy), int(m.group(2)), int(m.group(1)))
        if d:
            return d, d

    m = re.search(rf"\b(\d{{1,2}})\s+de\s+({_RE_MES})(?:\s+(?:de|del)\s+(\d{{4}}))?", n)
    if m:
        d = _dia(_anio(m.group(3), hoy), MESES.index(m.group(2)) + 1, int(m.group(1)))
        if d:
            return d, d

    if _palabra("pasado manana", n):
        return hoy + timedelta(days=2), hoy + timedelta(days=2)
    if _palabra("hoy", n) and _palabra("manana", n):
        return hoy, hoy + timedelta(days=1)
    if _palabra("manana", n):
        return hoy + timedelta(days=1), hoy + timedelta(days=1)
    if re.search(r"\b(antier|anteayer|antes de ayer)\b", n):
        return hoy - timedelta(days=2), hoy - timedelta(days=2)
    if _palabra("ayer", n):
        return hoy - timedelta(days=1), hoy - timedelta(days=1)
    if _palabra("hoy", n):
        return hoy, hoy

    m = re.search(r"\bultimos (\d{1,3}) dias\b", n)
    if m:
        return hoy - timedelta(days=int(m.group(1)) - 1), hoy
    lunes = hoy - timedelta(days=hoy.weekday())
    if re.search(r"\bsemana (pasada|anterior)\b", n):
        return lunes - timedelta(days=7), lunes - timedelta(days=1)
    if re.search(r"\b(proxima semana|siguiente semana|semana que viene|semana proxima|semana entrante)\b", n):
        return lunes + timedelta(days=7), lunes + timedelta(days=13)
    if _palabra("ultima semana", n):
        return hoy - timedelta(days=6), hoy
    if _palabra("semana", n):
        return lunes, lunes + timedelta(days=6)

    if re.search(r"\bmes (pasado|anterior)\b", n):
        fin = hoy.replace(day=1) - timedelta(days=1)
        return fin.replace(day=1), fin
    if re.search(r"\b(proximo mes|siguiente mes|mes que viene|mes proximo|mes entrante)\b", n):
        ini = _fin_de_mes(hoy.year, hoy.month) + timedelta(days=1)
        return ini, _fin_de_mes(ini.year, ini.month)
    if _palabra("ultimo mes", n):
        return hoy - timedelta(days=30), hoy
    m = re.search(rf"\b({_RE_MES})(?:\s+(?:de|del)\s+(\d{{4}}))?\b", n)
    if m:
        a, mes = _anio(m.group(2), hoy), MESES.index(m.group(1)) + 1
        return date(a, mes, 1), _fin_de_mes(a, mes)
    if _palabra("mes", n):
        return hoy.replace(day=1), _fin_de_mes(hoy.year, hoy.month)

    if re.search(r"\bano (pasado|anterior)\b", n):
        return date(hoy.year - 1, 1, 1), date(hoy.year - 1, 12, 31)
    m = re.search(r"\b(20\d\d)\b", n)
    if m:
        return date(int(m.group(1)), 1, 1), date(int(m.group(1)), 12, 31)
    if _palabra("ano", n):
        return date(hoy.year, 1, 1), hoy
    return None


def _fmt(d):
    return f"{DIAS[d.weekday()]} {d.strftime('%d/%m/%Y')}"


def _etiqueta_periodo(desde, hasta):
    if desde == hasta:
        return _fmt(desde)
    return f"{desde.strftime('%d/%m/%Y')} – {hasta.strftime('%d/%m/%Y')}"


# Proyectos mencionados (por clave o por nombre) y el texto sin las palabras de ese nombre.
def _proyectos(texto, n):
    claves = []
    for c in N._CLAVES_RE.findall(texto):
        if c.upper() not in claves:
            claves.append(c.upper())
    for alias, real in getattr(N, "_ALIAS_CLAVE", {}).items():
        if _palabra(alias, n) and real.upper() not in claves:
            claves.append(real.upper())
    inv = {v: k for k, v in N.mapa_proyectos.items()}
    palabras = set(n.split())
    resto = n
    for clave_nombre, nombre in N._PROY_POR_NOMBRE.items():
        if clave_nombre <= palabras:
            if inv[nombre] not in claves:
                claves.append(inv[nombre])
            for w in clave_nombre:
                resto = re.sub(r"(?<!\w)" + re.escape(w) + r"(?!\w)", " ", resto)
    return claves, resto


# Estado y municipio mencionados, tomados de los que existen en el Sheet.
# Devuelve (estado, municipio) comparables y sus nombres tal como se muestran.
def _lugar(n, filas):
    nombres_edo, nombres_mun = {}, {}
    for f in filas:
        if (f.get("ESTADO") or "").strip():
            nombres_edo.setdefault(_edo(f.get("ESTADO")), _titulo(f.get("ESTADO")))
        if (f.get("MUNICIPIO") or "").strip():
            nombres_mun.setdefault(_n(f.get("MUNICIPIO")), _titulo(f.get("MUNICIPIO")))
    edos = sorted(nombres_edo, key=len, reverse=True)
    estado = next((e for e in edos if _palabra(e, n)), "")
    if not estado:
        estado = next((real for alias, real in ALIAS_EDO.items()
                       if _palabra(alias, n) and real in nombres_edo), "")
    resto = n
    if estado:
        for frase in [estado] + [al for al, r in ALIAS_EDO.items() if r == estado]:
            resto = resto.replace(frase, " ")
    muns = sorted(nombres_mun, key=len, reverse=True)
    municipio = next((m for m in muns if len(m) >= 5 and _palabra(m, resto)), "")
    return estado, municipio, nombres_edo.get(estado, ""), nombres_mun.get(municipio, "")


# Interpreta la pregunta. Devuelve un diccionario con la intención y los filtros.
def interpretar(texto, filas, hoy):
    n = _n(texto)
    claves, sin_proy = _proyectos(texto, n)
    rango = periodo(n, hoy)
    estado, municipio, estado_txt, municipio_txt = _lugar(sin_proy, filas)
    categorias = sorted({re.sub(r"^\d+\.\s*", "", c) for c, _ in N.clasificar_actividad(texto)})
    celda = N._sin_acentos(texto.upper())
    deps = [canon for canon, pats in N._DEP_PATRONES.items() if any(p.search(celda) for p in pats)]

    if _palabra("resumen", n):
        intencion = "resumen"
    elif re.search(r"\b(cuant[oa]s?|total|numero de)\b", n):
        intencion = "contar"
    elif re.search(r"\b(que hay|que tenemos|que se tiene|cuales|lista|listado|listame|muestrame|muestra|detalle|agenda)\b", n):
        intencion = "listar"
    else:
        intencion = ""

    entendido = bool(rango or claves or estado or municipio or categorias or deps or intencion)
    if not intencion:
        intencion = "listar" if rango and (rango[1] - rango[0]).days <= 7 else "contar"
    if rango:
        desde, hasta, por_defecto = rango[0], rango[1], ""
    elif intencion == "listar":
        desde, hasta, por_defecto = hoy, hoy, " (no indicaste periodo; tomé hoy)"
    else:
        desde, hasta, por_defecto = date(hoy.year, 1, 1), hoy, " (no indicaste periodo; tomé lo que va del año)"
    return {"intencion": intencion, "desde": desde, "hasta": hasta, "nota_periodo": por_defecto,
            "proyectos": claves, "estado": estado, "municipio": municipio,
            "estado_txt": estado_txt, "municipio_txt": municipio_txt,
            "categorias": categorias, "dependencias": deps, "entendido": entendido}


# Filas del Sheet que cumplen el periodo y los filtros.
def filtrar(q, filas):
    inv = {v: k for k, v in N.mapa_proyectos.items()}
    salida = []
    for f in filas:
        d = _fecha(f)
        if d is None or d < q["desde"] or d > q["hasta"]:
            continue
        clave = inv.get((f.get("PROYECTO FERROVIARIO") or "").strip(), "OTRO")
        if q["proyectos"] and clave not in q["proyectos"]:
            continue
        if q["estado"] and _edo(f.get("ESTADO")) != q["estado"]:
            continue
        if q["municipio"] and not _palabra(q["municipio"], _n(f.get("MUNICIPIO"))):
            continue
        cats = {re.sub(r"^\d+\.\s*", "", c) for c, _ in N.clasificar_actividad(
            f.get("TIPO DE SOLICITUD", ""), f.get("ACTIVIDADES DESARROLLADAS", ""))}
        if q["categorias"] and not (cats & set(q["categorias"])):
            continue
        if q["dependencias"]:
            celda = N._sin_acentos(str(f.get("DEPENDENCIAS PARTICIPANTES", "")).upper())
            if not all(any(p.search(celda) for p in N._DEP_PATRONES[c]) for c in q["dependencias"]):
                continue
        salida.append({"fecha": d, "hora": _hora(f), "proyecto": clave, "cats": cats,
                       "estado": (f.get("ESTADO") or "").strip(),
                       "municipio": (f.get("MUNICIPIO") or "").strip(),
                       "actividad": (f.get("TIPO DE SOLICITUD") or "").strip()})
    salida.sort(key=lambda a: (a["fecha"], a["hora"] or "99:99"))
    return salida


def _plural(n):
    return f"{n} actividad" if n == 1 else f"{n:,} actividades"


def _encabezado(q):
    lineas = [f"Periodo: {_etiqueta_periodo(q['desde'], q['hasta'])}{q['nota_periodo']}"]
    filtros = []
    if q["proyectos"]:
        filtros.append("proyecto " + ", ".join(q["proyectos"]))
    if q["estado"]:
        filtros.append("estado " + q["estado_txt"])
    if q["municipio"]:
        filtros.append("municipio " + q["municipio_txt"])
    if q["categorias"]:
        filtros.append("categoría " + " / ".join(q["categorias"]))
    if q["dependencias"]:
        filtros.append("con " + " y ".join(q["dependencias"]))
    if filtros:
        lineas.append("Filtros: " + " · ".join(filtros))
    return "\n".join(lineas)


def _por_proyecto(acts):
    c = Counter(a["proyecto"] for a in acts)
    return " · ".join(f"{k} {v}" for k, v in c.most_common())


# Arma el texto de respuesta a partir de la pregunta interpretada.
def redactar(q, acts):
    cab = _encabezado(q)
    if not acts:
        return f"No encontré actividades registradas.\n{cab}"

    if q["intencion"] == "contar":
        texto = f"📊 {_plural(len(acts))}\n{cab}"
        if not q["proyectos"] and len({a['proyecto'] for a in acts}) > 1:
            texto += f"\nPor proyecto: {_por_proyecto(acts)}"
        return texto

    if q["intencion"] == "resumen":
        cats = Counter(c for a in acts for c in a["cats"])
        edos = Counter(_titulo(a["estado"]) for a in acts if a["estado"])
        partes = [f"📋 Resumen: {_plural(len(acts))}", cab, f"Por proyecto: {_por_proyecto(acts)}"]
        if cats:
            partes.append("Categorías: " + " · ".join(f"{k} {v}" for k, v in cats.most_common(5)))
        if edos:
            partes.append("Estados: " + " · ".join(f"{k} {v}" for k, v in edos.most_common(5)))
        return "\n".join(partes)

    lineas = [f"🗓 {_plural(len(acts))}", cab]
    dia = None
    for a in acts[:MAX_LISTA]:
        if a["fecha"] != dia:
            dia = a["fecha"]
            lineas.append(f"\n— {_fmt(dia)} —")
        lugar = _titulo(a["municipio"]) or _titulo(a["estado"])
        actividad = a["actividad"] if len(a["actividad"]) <= 90 else a["actividad"][:87] + "…"
        lineas.append(f"• {a['hora'] or '--:--'} [{a['proyecto']}] " + (f"{lugar} · " if lugar else "") + actividad)
    if len(acts) > MAX_LISTA:
        lineas.append(f"\n… y {len(acts) - MAX_LISTA} más. Acota con un proyecto o un periodo más corto.")
    return "\n".join(lineas)


_COMANDOS = {
    "/hoy": "que hay hoy", "/manana": "que hay manana", "/mañana": "que hay manana",
    "/semana": "resumen de esta semana", "/mes": "resumen de este mes",
}


# Respuesta completa a un mensaje de consulta.
def responder(texto, hoy=None):
    hoy = hoy or hoy_mx()
    t = (texto or "").strip()
    if t.startswith("/"):
        comando = t.split()[0].split("@")[0].lower()
        if comando in ("/start", "/ayuda", "/help"):
            return AYUDA
        if comando not in _COMANDOS:
            return "No conozco ese comando.\n\n" + AYUDA
        t = _COMANDOS[comando] + " " + " ".join(t.split()[1:])
    if re.fullmatch(r"(hola|buenos dias|buenas tardes|buenas noches|ayuda)", _n(t)):
        return AYUDA

    filas = N._filas_hoja_principal()
    try:
        q = interpretar(t, filas, hoy)
    except FechaInvalida:
        return "Esa fecha no existe. Escríbela como dd/mm/aaaa, por ejemplo 12/11/2026."
    if not q["entendido"]:
        # Último recurso: Haiku reescribe la pregunta con palabras que sí entiendo; las cuentas siguen siendo del código.
        nueva = N._llamar_haiku(SYSTEM_REESCRIBIR, t, 80, 0, "tara")
        if nueva and nueva.strip().upper() != "NO" and len(nueva) < 200 and "\n" not in nueva.strip():
            try:
                q = interpretar(nueva.strip(), filas, hoy)
            except FechaInvalida:
                pass
        if not q["entendido"]:
            return "No entendí la pregunta.\n\n" + AYUDA
    return redactar(q, filtrar(q, filas))


# Punto de entrada desde el webhook: True si el mensaje era una consulta y ya se respondió.
def atender(chat_id, texto):
    if not es_consulta(texto):
        return False
    try:
        respuesta = responder(texto)
    except Exception:
        print(traceback.format_exc(), flush=True)
        respuesta = "❌ No pude consultar el Sheet en este momento. Intenta de nuevo en un minuto."
    N.telegram_enviar(chat_id, respuesta)
    return True


# Conecta Tara a la app principal y agrega la ruta del resumen diario.
def registrar(app, nucleo):
    global N
    N = nucleo
    N._RUTAS_PUBLICAS.add("tara_resumen_diario")     # no pide contraseña: trae su propio secreto

    # La llama un programador externo cada mañana; manda la agenda del día a los chats autorizados.
    @app.route("/tara/resumen-diario", methods=["GET", "POST"])
    def tara_resumen_diario():
        secreto = os.environ.get("CRON_SECRET", "")
        if not secreto or not hmac.compare_digest(request.headers.get("X-Cron-Secret", ""), secreto):
            return "", 403
        try:
            texto = "☀️ Buenos días. Agenda de hoy:\n\n" + responder("que hay hoy")
        except Exception:
            print(traceback.format_exc(), flush=True)
            return jsonify({"error": "No se pudo leer el Sheet"}), 500
        for chat_id in N.TELEGRAM_CHATS_OK:
            N.telegram_enviar(chat_id, texto)
        return jsonify({"enviados": len(N.TELEGRAM_CHATS_OK)})
