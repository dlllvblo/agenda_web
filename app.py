from flask import Flask, request, jsonify, render_template
import re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import requests
from shapely.geometry import shape, Point
from shapely.strtree import STRtree
import unicodedata

app = Flask(__name__)

# =========================
# MAPA PROYECTOS
# =========================
mapa_proyectos = {
        'TAP':   '1. AIFA - PACHUCA',
        'TIGDL': '5. IRAPUATO - GUADALAJARA',
        'TMLM':  '6. MAZATLÁN - LOS MOCHIS',
        'TMQ':   '2. MÉXICO - QUERÉTARO',
        'TQI':   '3. QUERÉTARO - IRAPUATO',
        'TQSLP': '7. QUERÉTARO - SAN LUIS POTOSÍ',
        'TSNL':  '4. SALTILLO - NUEVO LAREDO',
        'TSLPS': '8. SAN LUIS POTOSÍ - SALTILLO',
    }

def _norm(s):
    s = s or ""
    s = re.sub(r'^\s*\d+\.\s*', '', s)                       # quita "4. " inicial
    s = ''.join(c for c in unicodedata.normalize('NFD', s)   # quita acentos
                if unicodedata.category(c) != 'Mn')
    s = s.lower()
    s = re.sub(r'[-–—−]', ' ', s)                            # unifica guiones a espacio
    s = re.sub(r'\s+', ' ', s).strip()                       # colapsa espacios
    return s

def _sin_acentos(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or "")
                   if unicodedata.category(c) != 'Mn')

# Canónico (lo que se ve en la leyenda) -> alias a buscar
DEP_ALIASES = {
    "SEDATU":   ["SEDATU", "EDATU"],
    "RAN":      ["RAN", "REGISTRO AGRARIO NACIONAL"],
    "SEDENA":   ["SEDENA", "DEFENSA", "SDN"],   # DEFENSA == SEDENA
    "SICT":     ["SICT", "VINCULACION"],        # Vinculación es unidad de SICT
    "ATTRAPI":  ["ATTRAPI", "ATRAPI"],
    "PA":       ["PA", "P.A.", "PROCURADURIA AGRARIA"],
    "FIFONAFE": ["FIFONAFE"],
    "INDAABIN": ["INDAABIN", "INDABIN"],
    # --- cola: se agrupan en OTRAS ---
    "ARTF":     ["ARTF"],
    "SIAL":     ["SIAL"],          
    "LIDERVIC": ["LIDERVIC"],      
    "CONAVI":   ["CONAVI"],
    "CONAGUA":  ["CONAGUA"],
    "CFE":      ["CFE"],
    "INPI":     ["INPI"],
    "INAH":     ["INAH"],
    "SEMARNAT": ["SEMARNAT"],
    "SEGOB":    ["SEGOB"],
    "CENAGAS":  ["CENAGAS"],
    "CENAPRED": ["CENAPRED", "CENEPRED"],
    "SALUD":    ["SALUD"],
}

# Las 8 con rebanada propia; lo demás cae en OTRAS
DEP_NUCLEO = {"SEDATU","SEDENA","RAN","SICT","ATTRAPI","PA","FIFONAFE","INDAABIN"}

def _dep_pat(alias):
    a = _sin_acentos(alias.upper())
    cuerpo = r'\s+'.join(re.escape(w) for w in a.split()).replace(r'\.', r'\.?')
    return r'(?<![A-Z0-9])' + cuerpo + r'(?![A-Z0-9])'

_DEP_PATRONES = {canon: [re.compile(_dep_pat(al)) for al in als]
                 for canon, als in DEP_ALIASES.items()}

# Lookups precalculados: por clave (TMQ) y por nombre (Saltillo–Nuevo Laredo)
_PROY_POR_CLAVE  = {_norm(k): v for k, v in mapa_proyectos.items()}
_PROY_POR_NOMBRE = {frozenset(_norm(v).split()): v for v in mapa_proyectos.values()}
_CLAVES_RE = re.compile(
    r'\b(' + '|'.join(sorted(mapa_proyectos, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

meses = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
}

# CAPA MUNICIPAL INEGI
def cargar_capas():
    base = os.path.join(os.path.dirname(__file__), "data")
    with open(os.path.join(base, "municipios_mx.geojson"), encoding="utf-8") as f:
        mun_gj = json.load(f)

    geoms = [shape(feat["geometry"]) for feat in mun_gj["features"]]
    props = [feat["properties"] for feat in mun_gj["features"]]
    return STRtree(geoms), geoms, props

ARBOL_MUN, MUN_GEOMS, MUN_PROPS = cargar_capas()


from urllib.parse import unquote
_COORDS_CACHE = {}

def extraer_coordenadas(url_maps):
    """Caché: evita re-expandir el mismo link corto (10s de timeout cada uno)."""
    if url_maps in _COORDS_CACHE:
        return _COORDS_CACHE[url_maps]
    res = _extraer_coordenadas_raw(url_maps)
    if res is None:
        res = (None, None)
    _COORDS_CACHE[url_maps] = res
    return res


def _extraer_coordenadas_raw(url_maps):
    """Extrae lat, lng de un link de Google Maps (corto o expandido)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }

    def buscar_coords(url):
        # Decodificar URL-encoding (doble, por el parámetro continue= de Google)
        url = unquote(unquote(url))
        # 1. Pin real del lugar: !3d{lat}!4d{lng}
        match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        # 2. Formato ?q=lat,lng
        match = re.search(r'[?&]q=(-?\d+\.\d+),\s*(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        # 3. Búsqueda por coordenadas: /maps/search/lat,+lng
        match = re.search(r'/maps/search/(-?\d+\.\d+),\s*\+?(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        # 4. Links de ruta: destino como !1d{lng}!2d{lat}
        if "/dir/" in url:
            match = re.search(r'!1d(-?\d+\.\d+)!2d(-?\d+\.\d+)', url)
            if match:
                return float(match.group(2)), float(match.group(1))
        # 5. Último recurso: @lat,lng (centro del encuadre, puede diferir del punto)
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None

    # Intentar sobre la URL original
    coords = buscar_coords(url_maps)
    if coords:
        return coords

    # Expandir el link corto y buscar en la URL final 
    try:
        resp = requests.get(url_maps, allow_redirects=True, timeout=10, headers=headers)
        coords = buscar_coords(resp.url)
        if coords:
            return coords
    except requests.RequestException:
        pass
    return None, None


def obtener_estado_municipio(lat, lng):
    """Point-in-polygon contra capa municipal INEGI local"""
    punto = Point(lng, lat)
    for idx in ARBOL_MUN.query(punto):
        if MUN_GEOMS[idx].contains(punto):
            p = MUN_PROPS[idx]
            return p["NOM_ENT"], p["NOM_MUN"]
    # Fallback: punto en hueco de la simplificación → municipio más cercano (<~1 km)
    idx = ARBOL_MUN.nearest(punto)
    if idx is not None and MUN_GEOMS[idx].distance(punto) < 0.01:
        p = MUN_PROPS[idx]
        return p["NOM_ENT"], p["NOM_MUN"]
    return "", ""

def resumir_actividad(linea, detalle=""):
    """Resume la actividad en UNA línea técnica y concisa, combinando línea principal
       y campo Actividad si existe. Fallback: texto original."""
    base = (linea or "").strip()
    extra = (detalle or "").strip()
    if extra and extra != base:
        base = f"{base}. {extra}" if base else extra

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not base:
        return base
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 120,
                "temperature": 0.7,
                "system": SYSTEM_RESUMEN,
                "messages": [{"role": "user", "content": f"Actividad: {base}"}],
            },
            timeout=8,
        )
        resp.raise_for_status()
        resumen = resp.json()["content"][0]["text"].strip()
        # Red de seguridad: si Haiku pidió datos en vez de redactar, usa el texto base.
        malo = (
            not resumen
            or "\n" in resumen
            or len(resumen) > 220
            or resumen.rstrip().endswith(":")
            or "?" in resumen
            or re.search(r'(?i)(necesito que|proporci|por favor|no puedo redactar|no cuento con|datos espec[ií]ficos)', resumen)
        )
        return base if malo else corregir_siglas(resumen)
    except Exception:
        return base

CORRECCIONES = {
    "palacio municipal": "Palacio Municipal",
    "presidencia municipal": "Presidencia Municipal",
    "comisariado ejidal": "Comisariado Ejidal",
    "comisariado de bienes comunales": "Comisariado de Bienes Comunales",
    "nucleo agrario": "Núcleo Agrario",
    "núcleo agrario": "Núcleo Agrario",
    "registro agrario nacional": "Registro Agrario Nacional",
    "municipios": "Municipios",
    "municipios de": "Municipios de",
    "municipio de": "Municipio de",
}

# =========================
# CORRECCIÓN DE SIGLAS
# =========================
CORRECCIONES_SIGLAS = {
    r'Bienes\s+Dominiales\s+del\s+Tren': 'Bienes Distintos a la Tierra',
}

def corregir_siglas(texto):
    for patron, correcto in CORRECCIONES_SIGLAS.items():
        texto = re.sub(patron, correcto, texto, flags=re.IGNORECASE)
    return texto

SYSTEM_RESUMEN = (
    "Redactas UNA sola línea de reporte institucional para actividades de campo "
    "de proyectos ferroviarios de SEDATU.\n"
    "Reglas:\n"
    "- En pasado, formal, técnica pero clara y directa; concisa (máx. 25 palabras).\n"
    "- Elige el verbo según la acción REAL, no uses siempre la misma fórmula. Guía: "
    "firma→'Se firmó'; reunión→'Se celebró reunión'; recorrido→'Se realizó recorrido'; "
    "acercamiento→'Se efectuó acercamiento'; entrega→'Se entregó'; notificación→'Se notificó'; "
    "verificación→'Se verificó'; levantamiento→'Se levantó'.\n"
    "- Conserva TEXTUAL: números de parcela (ej. MQ-SBA-P109), PKs (ej. pk 44+900), "
    "nombres de personas, ejidos/núcleos agrarios y dependencias.\n"
    "- No inventes datos que no estén. No agregues introducción ni explicación.\n"
    "- Responde SOLO con la línea, sin comillas.\n"
    "- El ejecutor SIEMPRE es SEDATU y las dependencias participantes; NUNCA un particular.\n"
    "- Las personas nombradas (entre paréntesis, como 'propietario:' o con Sr./Sra.) son los "
    "propietarios o afectados, no quien ejecuta. Si hay propietario, refiérelo como "
    "'del Sr. X' o 'de la Sra. X' según el nombre.\n"
    "- NUNCA pidas más datos, NUNCA hagas preguntas, NUNCA pidas aclaraciones.\n"
    "- Aunque la actividad sea muy escueta (ej. 'Caminamiento Ejidal'), redáctala "
    "igual en pasado con lo que haya (ej. 'Se realizó caminamiento ejidal'). "
    "SIEMPRE devuelves UNA línea de reporte, jamás una solicitud de información.\n"
    "GLOSARIO DE SIGLAS (usa estos significados EXACTOS):\n"
    "- BDT / BDTs / BDT's: Bienes Distintos a la Tierra (construcciones, cultivos, árboles, cosechas u otros bienes sobre el terreno, distintos de la tierra misma)\n"
    "- COP / COPs / COP's / cop / cops (en cualquier combinación de mayúsculas y minúsculas): Convenio de Ocupación Previa. En plural: Convenios de Ocupación Previa.\n"
    "- IFREM / ifrem: Instituto de la Función Registral del Estado de México\n"
    "- Regla: si encuentras una sigla que NO está en este glosario, consérvala tal cual aparece. NUNCA inventes ni deduzcas el significado de una sigla."
)

def normalizar_capitalizacion(texto):
    if not texto:
        return texto
    # frases institucionales conocidas (case-insensitive)
    for mal, bien in CORRECCIONES.items():
        texto = re.sub(r'\b' + re.escape(mal) + r'\b', bien, texto, flags=re.IGNORECASE)
    # capitalizar minúsculas
    texto = re.sub(
        r'\b(Palacio Municipal|Presidencia Municipal|Municipio(?: de)?|Ejido(?: de)?)\s+(?!(?:de|del)\b)([a-záéíóúñ]+)',
        lambda m: f"{m.group(1)} {m.group(2).capitalize()}",
        texto
    )
    return texto

# ============================================================
# CAMPOS DE AGENDA — fuente única de etiquetas (fragmento regex, SIN ':')
# Si agregas un campo nuevo, se agrega AQUÍ y todos los regex lo respetan.
# ============================================================
CAMPOS = {
    "hora":         r'Hora',
    "descripcion":  r'Descripci[oó]n',
    "frente":       r'Frente|F',
    "bdts":         r'BDTs?',
    "nomenclatura": r'Nomenclaturas?',
    "poligono":     r'Pol[ií]gonos?',
    "asistentes":   r'Asistentes?|Asisten?|Participa(?:n|ntes)?',
    "ubicacion":    r'Ubicaci[oó]n(?:es)?|Punto de reuni[oó]n|Punto de encuentro',
    "ejido":        r'Ejido',
    "municipio":    r'Municipio',
    "parcelas":     r'Parcelas',
}

def frontera_campos(excluir=()):
    """Lookahead que cae justo antes de la etiqueta ':' de cualquier campo,
       excluyendo opcionalmente el campo que estás capturando."""
    pats = [p for k, p in CAMPOS.items() if k not in excluir]
    return r'(?:' + '|'.join(f'(?:{p})' for p in pats) + r'):'

# Precalculadas una sola vez (evita rearmar la cadena en cada bloque)
_FRONT_ALL    = frontera_campos()
_FRONT_BDTS   = frontera_campos(excluir=('bdts',))
_FRONT_FRENTE = frontera_campos(excluir=('frente',))
_FRONT_ASIS   = frontera_campos(excluir=('asistentes',))

# =========================
# NOMENCLATURAS
# =========================
NOMEN_RE = re.compile(
    r'(?im)^\s*(?:Nomenclaturas?|Pol[ií]gonos?)\s*:\s*(.+?)\s*$'
)

def extraer_nomenclaturas(bloque):
    """Extrae lista de códigos de líneas Nomenclatura(s):/Polígono(s):"""
    codigos = []
    for m in NOMEN_RE.finditer(bloque):
        valor = m.group(1).strip()
        if valor.upper() == "N/A":
            continue
        partes = re.split(r'\s*,\s*|\s+y\s+|\s+e\s+', valor)
        codigos.extend(p.strip(' .') for p in partes if p.strip(' .'))
    # dedup conservando orden
    vistos = set()
    return [c for c in codigos if not (c in vistos or vistos.add(c))]

PARCELA_SUELTA_RE = re.compile(r'(?m)^\s*(P-\d+[A-Z]?)\s*(?:\(([^)]+)\))?\s*$')

def extraer_parcelas_sueltas(bloque):
    """Extrae líneas sueltas tipo 'P-117' o 'P-116 (Minuta)'."""
    out = []
    for m in PARCELA_SUELTA_RE.finditer(bloque):
        cod = m.group(1)
        if m.group(2):
            cod += f" ({m.group(2)})"
        out.append(cod)
    # dedup conservando orden
    vistos = set()
    return [c for c in out if not (c in vistos or vistos.add(c))]

def _integrar_codigos(resumen, codigos, singular, plural):
    """Pega códigos determinísticamente al resumen de Haiku.
       singular/plural = ('la nomenclatura','las nomenclaturas') | ('la parcela','las parcelas')."""
    if not codigos:
        return resumen
    faltantes = [c for c in codigos if c not in resumen]
    if not faltantes:
        return resumen
    resumen = resumen.rstrip(' .')
    lista = (faltantes[0] if len(faltantes) == 1
             else ", ".join(faltantes[:-1]) + " y " + faltantes[-1])
    if re.search(r'(?i)(predios?|pol[ií]gonos?|parcelas?|nomenclaturas?|inmuebles?)$', resumen):
        return f"{resumen}: {lista}"
    sufijo = singular if len(faltantes) == 1 else plural
    return f"{resumen} en {sufijo} {lista}."

def integrar_nomenclaturas(resumen, noms):
    return _integrar_codigos(resumen, noms, "la nomenclatura", "las nomenclaturas")

def integrar_parcelas(resumen, parcelas):
    return _integrar_codigos(resumen, parcelas, "la parcela", "las parcelas")

# =========================
# CONTEO DE ACTIVIDADES POR CATEGORÍA (abril → hoy)
# =========================
CATEGORIAS_CONTEO = [
    ("1. Atención a Reuniones/Asambleas/Asesorías",
     [r'reunion(?:es)?', r'asambleas?', r'asesorias?', r'mesas?\s+de\s+trabajo']),
    ("2. Caminamientos/Inspecciones",
     [r'caminamientos?', r'inspecci(?:on|ones)', r'recorridos?', r'visitas?\s+de\s+campo']),
    ("4. Infografía", [r'infografias?']),
    ("5. Cartografía", [r'cartografias?', r'planos?', r'mapas?']),
    ("6. Firmas de convenios y actas",
     [r'firmas?']),
    ("7. Acercamientos/Sensibilización/Negociación",
     [r'a[cs]ercamientos?', r'sensibilizaci(?:on|ones)', r'negociaci(?:on|ones)',
      r'atenci(?:on|ones)', r'citas?\s+con']),
    ("8. Visitas/Revisiones/Verificaciones",
     [r'visitas?', r'revisi(?:on|ones)', r'revisar', r'verificaci(?:on|ones)',
      r'identificaci(?:on|ones)', r'ubicaci(?:on|ones)\s+de']),
    ("9. Consultas y búsquedas registrales",
     [r'consultas?', r'busquedas?', r'antecedentes', r'catastro', r'notarias?',
      r'rpp', r'expedientes?', r'recopilaci(?:on|ones)']),
    ("10. Peritajes/Avalúos/Acompañamientos",
     [r'peritajes?', r'peritos?', r'avaluos?', r'acompanamientos?']),
    ("11. Marcaje/Delimitación",
     [r'marcajes?', r'delimitaci(?:on|ones)']),
    ("12. Mesas sociales/Convocatorias/Videoconferencias",
     [r'mesas?\s+(?:de\s+atencion|social(?:es)?)', r'videoconferencias?',
      r'convocatorias?', r'conciliaci(?:on|ones)']),
    ("13. Censos/Reubicaciones",
     [r'censos?', r'reubicaci(?:on|ones)']),
    ("14. Presentaciones/Entregas",
     [r'presentaci(?:on|ones)', r'entregas?\s+de', r'recepci(?:on|ones)',
      r'notificaci(?:on|ones)']),
]

CAT3_NOMBRE = "3. Levantamientos Agenda (Topográficos/BDTs Agroforestales y Construcción)"
CAT3_SUB = [
    ("BDTs Construcción",  [r'construcci(?:on|ones)']),
    ("BDTs Agroforestal",  [r'agroforestal(?:es)?', r'agricolas?']),
]
CAT3_GENERAL = ("Topográficos/Mediciones",
                [r'medici(?:on|ones)', r'levantamientos?', r'topografic[oa]s?', r'bdts?'])

def _compilar_cat(pats):
    return [re.compile(r'(?<!\w)' + p + r'(?!\w)') for p in pats]

_CAT_PATRONES  = [(n, _compilar_cat(p)) for n, p in CATEGORIAS_CONTEO]
_CAT3_SUB_PAT  = [(n, _compilar_cat(p)) for n, p in CAT3_SUB]
_CAT3_GEN_PAT  = _compilar_cat(CAT3_GENERAL[1])

def clasificar_actividad(tipo_solicitud, detalle=""):
    """Devuelve lista de (categoria, subcategoria) detectadas.
       Todas las categorías y el disparo de la 3: SOLO con TIPO DE SOLICITUD.
       Subtipo de la 3 (construcción/agroforestal): también busca en el detalle."""
    txt = _sin_acentos(str(tipo_solicitud or "")).lower()
    hits = []

    for nombre, patrones in _CAT_PATRONES:
        if any(p.search(txt) for p in patrones):
            hits.append((nombre, None))

    es_cat3 = (any(p.search(txt) for p in _CAT3_GEN_PAT)
               or any(p.search(txt) for _, pats in _CAT3_SUB_PAT for p in pats))

    # "Firma de BDT's" es firma de documento, no levantamiento en campo
    if es_cat3 and re.search(r'(?<!\w)firmas?(?!\w)', txt) \
       and not re.search(r'(?<!\w)(?:medici(?:on|ones)|levantamientos?|topografic)', txt):
        es_cat3 = False

    if es_cat3:
        txt_sub = txt + " " + _sin_acentos(str(detalle or "")).lower()
        sub_hits = [n for n, pats in _CAT3_SUB_PAT if any(p.search(txt_sub) for p in pats)]
        if sub_hits:
            for s in sub_hits:
                hits.append((CAT3_NOMBRE, s))
        else:
            hits.append((CAT3_NOMBRE, CAT3_GENERAL[0]))

    return hits

def _hora_24(hh, mm, mer):
    """Convierte hora a formato 24h. mer = 'A'/'P'/None."""
    h = int(hh)
    if mer:
        mer = mer.lower()
        if mer.startswith('p') and h != 12:
            h += 12
        elif mer.startswith('a') and h == 12:
            h = 0
    return f"{h:02d}:{mm}"

def procesar_agenda(texto):
    texto = re.sub(r'(?m)^([ \t]*)\*[ \t]+', r'\1- ', texto)
    texto = texto.replace('*', '')
    texto = re.sub(r'(?m)^\s*_+|_+\s*$', '', texto)

    # --- PROYECTO ---
    proyecto_match = re.search(r"(?:Agenda|Proyecto)(?:\s+Ferroviario)?\s*:?\s*(.*)",
                               texto, re.IGNORECASE)
    proyecto_raw = proyecto_match.group(1).strip() if proyecto_match else ""
    if not proyecto_raw:
        proyecto_final = "SIN PROYECTO"
    else:
        toks = proyecto_raw.split()
        ultimo = _norm(toks[-1]) if toks else ""
        if ultimo in _PROY_POR_CLAVE:                          # 1) por clave (TMQ)
            proyecto_final = _PROY_POR_CLAVE[ultimo]
        elif frozenset(_norm(proyecto_raw).split()) in _PROY_POR_NOMBRE:   # 2) por nombre
            proyecto_final = _PROY_POR_NOMBRE[frozenset(_norm(proyecto_raw).split())]
        else:
            proyecto_final = proyecto_raw                      # 3) último recurso
    if proyecto_final not in mapa_proyectos.values():
        m_clave = _CLAVES_RE.search(texto[:200])
        if m_clave:
            proyecto_final = mapa_proyectos[m_clave.group(1).upper()]
    # --- FECHA ---
    fecha_num_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", texto)
    if fecha_num_match and 1 <= int(fecha_num_match.group(2)) <= 12:
        dia = fecha_num_match.group(1).zfill(2)
        mes = fecha_num_match.group(2).zfill(2)
        anio = fecha_num_match.group(3)
        fecha = f"{dia}/{mes}/{anio}"
        fecha_dt = datetime.strptime(fecha, "%d/%m/%Y")
        fecha_solicitud = (fecha_dt - timedelta(days=1)).strftime("%d/%m/%Y")
    else:
        fecha = ""
        fecha_solicitud = ""
        for m in re.finditer(r"(\d{1,2})\s+de\s+(\w+)(?:\s+de\s+(\d{4}))?", texto, re.IGNORECASE):
            mes_texto = m.group(2).lower()
            if mes_texto in meses:
                dia = m.group(1).zfill(2)
                anio = m.group(3) if m.group(3) else str(datetime.now().year)
                fecha = f"{dia}/{meses[mes_texto]}/{anio}"
                fecha_dt = datetime.strptime(fecha, "%d/%m/%Y")
                fecha_solicitud = (fecha_dt - timedelta(days=1)).strftime("%d/%m/%Y")
                break

    # --- BLOQUES ---
    partes_texto = re.split(r"\n\s*\d+[\.-]\s*", texto)
    encabezado = partes_texto[0]
    bloques = partes_texto[1:]
    if not bloques:
        cuerpo = "\n".join(
            l for l in texto.splitlines()
            if not re.match(r"\s*Proyecto\b", l, re.IGNORECASE)
            and not re.search(r"\b\d{1,2}\s+de\s+(?:%s)\b" % "|".join(meses), l, re.IGNORECASE)
        ).strip()
        if cuerpo:
            bloques = [cuerpo]
    filas = []

    for bloque in bloques:
        bloque = bloque.replace('\xa0', ' ').strip()

        
        # HORA DOBLE (dos zonas horarias): "08:00 hrs (Hora Centro) / 09:00 hrs (Hora Nuevo Laredo)"
        hora_doble_match = re.search(
            r"(\d{1,2}):(\d{2})\s*hrs?\.?\s*\(\s*Hora\s+([^)]+?)\s*\)\s*/\s*"
            r"(\d{1,2}):(\d{2})\s*hrs?\.?\s*\(\s*Hora\s+([^)]+?)\s*\)",
            bloque, re.IGNORECASE
        )
        # HORA  
        match_hora = re.search(
            r"(\d{1,2}):(\d{2})\s*([AaPp])?\.?\s*[Mm]?\.?\s*(?:hrs?)?\s*[-–—]",
            bloque, re.IGNORECASE
        )
        hora_label_match = re.search(
            r"Hora:\s*(\d{1,2}):(\d{2})\s*([AaPp])?\.?\s*[Mm]?\.?",
            bloque, re.IGNORECASE
        )
        hora_sola_match = re.search(
            r"(\d{1,2}):(\d{2})\s*(?:([AaPp])\.?\s*[Mm]\.?|h(?:oras?|rs?))\b",
            bloque, re.IGNORECASE
        )
        if hora_doble_match:
            h1 = _hora_24(hora_doble_match.group(1), hora_doble_match.group(2), None)
            z1 = hora_doble_match.group(3).strip()
            h2 = _hora_24(hora_doble_match.group(4), hora_doble_match.group(5), None)
            z2 = hora_doble_match.group(6).strip()
            hora_txt = f"{h1} hrs {z1}\n{h2} hrs {z2}"
        elif match_hora:
            hora_txt = _hora_24(match_hora.group(1), match_hora.group(2), match_hora.group(3))
        elif hora_label_match:
            hora_txt = _hora_24(hora_label_match.group(1), hora_label_match.group(2), hora_label_match.group(3))
        elif hora_sola_match:
            hora_txt = _hora_24(hora_sola_match.group(1), hora_sola_match.group(2), hora_sola_match.group(3))
        else:
            hora_txt = ""

        # LÍNEA PRINCIPAL
        match_desc = re.search(r"Descripci[oó]n:\s*([^\n]+)", bloque, re.IGNORECASE)
        match_inline = re.search(r"\d{1,2}:\d{2}\s*(?:hrs?)?\s*[-–—]\s*(.*)", bloque, re.IGNORECASE)
        if match_desc:
            linea_principal = match_desc.group(1).strip()
        elif match_inline:
            linea_principal = match_inline.group(1).strip()
        else:
            linea_principal = bloque.splitlines()[0].strip()

        # linea principal
        linea_principal = re.split(
            rf'\s+(?={_FRONT_ALL})',
            linea_principal, maxsplit=1, flags=re.IGNORECASE
        )[0].strip()

        linea_principal = re.sub(r'^\d{1,2}:\d{2}\s*(?:hrs?)?\.?\s*[-–—]\s*', '', linea_principal, flags=re.IGNORECASE).strip()
        linea_principal = re.sub(r'^[-–—\s]+', '', linea_principal).strip()
        linea_principal = re.sub(r'\.$', '', linea_principal).strip()
        linea_principal = normalizar_capitalizacion(linea_principal)

        actividad_txt = linea_principal

        # CAMPO Actividad:
        actividad_field = re.search(r"Actividad:\s*(.*?)(?=\n\s*\w+:|$)", bloque, re.IGNORECASE | re.DOTALL)
        actividad_detalle = ""
        if actividad_field and actividad_field.group(1).strip():
            actividad_raw = actividad_field.group(1).strip()
            actividad_detalle = re.sub(r'\s*\n\s*[•\-]\s*', ' | ', actividad_raw)
            actividad_detalle = re.sub(r'^[•\-]\s*', '', actividad_detalle).strip()

        # FRENTE
        frente = re.search(
            rf"(?<!\w)(?:Frente|F):\s*([^\n]+?)(?=\s+{_FRONT_FRENTE}|[\r\n]|$)",
            bloque, re.IGNORECASE
        )
        # Fallback inline plural: "Frentes 8, 9, 10, 11 y 12" / "Frentes 3 y 4"
        if not frente:
            frentes_multi = re.search(
                r'\bFrentes?\s+(\d+(?:\s*[,y]\s*\d+)*)', linea_principal, re.IGNORECASE
            )
            if frentes_multi and re.search(r'[,y]', frentes_multi.group(1)):
                nums = re.findall(r'\d+', frentes_multi.group(1))
                lista = (", ".join(nums[:-1]) + " y " + nums[-1]) if len(nums) > 1 else nums[0]
                frente = type('_', (), {'group': lambda self, n: lista})()
        # Fallback inline singular: "F2", "Frente 3"
        if not frente:
            frente_inline = re.search(r'\bF(?:rente)?\.?\s*(\d+)\b', linea_principal, re.IGNORECASE)
            if frente_inline:
                num = frente_inline.group(1)
                frente = type('_', (), {'group': lambda self, n: num})()
        # Fallback encabezado
        if not frente:
            frente_enc = re.search(r'\bFrente\s+(\d+)\b', encabezado, re.IGNORECASE)
            if frente_enc:
                num = frente_enc.group(1)
                frente = type('_', (), {'group': lambda self, n: num})()

        # POLÍGONO
        poligono = re.search(r"Pol[ií]gono:\s*(.*)", bloque, re.IGNORECASE)

        # MUNICIPIO
        municipio_match = re.search(r"Municipio:\s*(.*)", bloque, re.IGNORECASE)
        if not municipio_match:
            municipio_match = re.search(r"Municipio:\s*(.*)", encabezado, re.IGNORECASE)
        municipio = municipio_match.group(1).split(',')[0].strip() if municipio_match else ""

        # ASISTENTES
        asistentes = re.search(
            rf"(?:Asistentes?|Asisten?|Participa(?:n|ntes)?):\s*([^\n]+?)(?=\s+{_FRONT_ASIS}|$)",
            bloque, re.IGNORECASE
        )
        # Fallback: frente pegado a la dependencia en Asistentes ("SEDATU F7")
        if not frente and asistentes:
            f_asis = re.search(r'\bF\.?\s*(\d+)\b', asistentes.group(1))
            if f_asis:
                num = f_asis.group(1)
                frente = type('_', (), {'group': lambda self, n: num})()

        # UBICACIÓN
        ubicacion = re.search(
            r"(?:Ubicaci[oó]n|Punto de reuni[oó]n|Punto de encuentro):\s*[\n\r]*\s*(?:\[.*?\]\((https?://[^\)\s]+)\)|(https?://[^\s\]]+)|(.+))",
            bloque,
            re.IGNORECASE
        )
        url = ""
        texto_ubic = ""
        if ubicacion:
            url = (ubicacion.group(1) or ubicacion.group(2) or "").strip()
            texto_ubic = (ubicacion.group(3) or "").strip()
        if not url:
            url_suelta = re.search(r'(?m)^\s*[-•]?\s*(https?://\S+)\s*$', bloque)
            if url_suelta:
                url = url_suelta.group(1).strip()
        if not url:
            url = texto_ubic          # sólo si de plano no hubo link 

        estado_geo = ""
        municipio_geo = ""
        if url and any(d in url for d in ("goo.gl", "google.com", "share.google")):
            lat, lng = extraer_coordenadas(url)
            if lat is not None:
                estado_geo, municipio_geo = obtener_estado_municipio(lat, lng)

        # UBICACIONES MÚLTIPLES
        ubicaciones_multi = re.findall(
            r'^\s*(?:[a-zA-Z][\)\.]|[-•])\s*(.+?):\s*(https?://\S+)',
            bloque, re.MULTILINE
        )

        # BDTs
        bdts = re.search(
            rf"BDTs?:\s*([^\n]+?)(?=\s+{_FRONT_BDTS}|$)",
            bloque, re.IGNORECASE
        )

       # ACTIVIDADES DESARROLLADAS
        bdts_val = bdts.group(1).strip().rstrip('.').strip() if bdts else ""
        if bdts_val.upper() == "N/A":
            bdts_val = ""

        partes = []
        if bdts_val:
            partes.append(bdts_val)
        linea_resumen = re.sub(
            r'\((?!\s*(?:Frente|F)\b)([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})\)',
            r'(propietario: \1)',
            linea_principal
        )
        resumen_final = resumir_actividad(linea_resumen, actividad_detalle)
        resumen_final = integrar_nomenclaturas(resumen_final, extraer_nomenclaturas(bloque))
        resumen_final = integrar_parcelas(resumen_final, extraer_parcelas_sueltas(bloque))
        partes.append(resumen_final)
        actividades_desarrolladas = " | ".join(partes) if partes else ""

        # EJIDO
        ejido_match = re.search(r"(?m)^\s*Ejido:?\s*(.*)", bloque, re.IGNORECASE)
        ejido = ejido_match.group(1).strip() if ejido_match else ""

        # NÚCLEO AGRARIO
        nucleo = re.search(r"Núcleo Agrario:\s*(.*)", bloque, re.IGNORECASE)
        # Fallback 0: lista de ejidos en plural -> "X, Y y Z"
        if not nucleo:
            lista_ej = re.search(r'\bejidos\s+de\s+(.+?)(?:\.|\n|$)', linea_principal, re.IGNORECASE)
            if lista_ej:
                partes_nuc = [p.strip(' .') for p in
                              re.split(r'\s*,\s*|\s+y\s+|\s+e\s+', lista_ej.group(1))
                              if p.strip(' .')]
                if len(partes_nuc) >= 2:
                    nucleo_txt = ", ".join(partes_nuc[:-1]) + " y " + partes_nuc[-1]
                    nucleo = type('_', (), {'group': lambda self, n: nucleo_txt})()
        if not nucleo:
            ejido_inline = re.search(
                r'(?i:\b(?:Comisariado\s+Ejidal\s+de|Ejidos?\s+de|Ejidos?))\s+'
                r'["\u2018\u2019\u201c\u201d\']?'
                r'([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\s]+?)'
                r'(?:["\u2018\u2019\u201c\u201d\']|\s*\(|\)|,|\.|\n|$)',
                linea_principal
            )
            if ejido_inline:
                nucleo_txt = ejido_inline.group(1).strip()
                nucleo = type('_', (), {'group': lambda self, n: nucleo_txt})()
        if not nucleo and ejido_match:
            nucleo_txt = ejido_match.group(1).strip()
            if nucleo_txt:
                nucleo = type('_', (), {'group': lambda self, n: nucleo_txt})()

        # PROPIETARIOS
        particular = re.search(r"Propietarios?:\s*(.*)|Particular:\s*(.*)", bloque, re.IGNORECASE)
        if not particular:
            propietario_inline = re.search(
                r'(?i:\b(?:se[ñn]or[a]?|propietari[ao]))\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)',
                linea_principal
            )
            if propietario_inline:
                prop_txt = propietario_inline.group(1).strip()
                particular = type('_', (), {'group': lambda self, n: prop_txt if n in (1, 2) else ''})()
        if not particular:
            titulo_inline = re.search(
                r'\b([Ss][Rr][Aa]|[Ss][Rr])\.?,?\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)',
                linea_principal
            )
            if titulo_inline:
                tit_norm = "Sra." if titulo_inline.group(1).lower().startswith("sra") else "Sr."
                prop_txt = f"{tit_norm} {titulo_inline.group(2).strip()}"
                particular = type('_', (), {'group': lambda self, n: prop_txt if n in (1, 2) else ''})()
        if not particular:
            ciudadano_inline = re.search(
                r'\bC\.\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)',
                linea_principal
            )
            if ciudadano_inline:
                prop_txt = f"Sr. {ciudadano_inline.group(1).strip()}"
                particular = type('_', (), {'group': lambda self, n: prop_txt if n in (1, 2) else ''})()
        if not particular:
            paren_inline = re.search(
                r'\((?!\s*(?:Frente|F)\b)([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})\)',
                linea_principal
            )
            if paren_inline:
                prop_txt = paren_inline.group(1).strip()
                nucleo_txt = nucleo.group(1).strip() if nucleo else ""
                ya_es_lugar = {nucleo_txt.lower(), ejido.lower(), municipio.lower()}
                if prop_txt.lower() not in ya_es_lugar:        # guardia: no es lugar ya detectado
                    particular = type('_', (), {'group': lambda self, n: prop_txt if n in (1, 2) else ''})()

        def armar_fila(ubic_url, est_geo, mun_g, acts_desarrolladas):
            return {
                "FECHA DE SOLICITUD": fecha_solicitud,
                "SOLICITANTE": "SEDATU",
                "MEDIO DE SOLICITUD": "WhatsApp",
                "TIPO DE SOLICITUD": actividad_txt,
                "PROYECTO FERROVIARIO": proyecto_final,
                "UBICACIÓN": ubic_url,
                "TIPO DE PROPIEDAD": "",
                "FRENTE": (
                    re.sub(r'\b(\d+)\b', r'F\1', frente.group(1).strip())
                    if frente and frente.group(1).strip().upper() != "N/A" else ""
                ),
                "POLÍGONO": (
                    poligono.group(1).strip()
                    if poligono and poligono.group(1).strip().upper() != "N/A" else ""
                ),
                "ESTADO": est_geo.upper(),
                "MUNICIPIO": municipio if municipio else mun_g,
                "EJIDO": ejido,
                "NÚCLEO AGRARIO": (nucleo.group(1).strip() if nucleo else ""),
                "PROPIETARIOS PROPIEDAD PRIVADA": (
                    (particular.group(1) or particular.group(2) or "").strip()
                    if particular else ""
                ),
                "FECHA Y HORA": f"{fecha} {hora_txt}",
                "DEPENDENCIAS PARTICIPANTES": (
                    asistentes.group(1).strip().rstrip('.') if asistentes else ""
                ),
                "ACTIVIDADES DESARROLLADAS": acts_desarrolladas,
            }

        if ubicaciones_multi:
            for nombre_punto, url_punto in ubicaciones_multi:
                nombre_punto = nombre_punto.strip()
                url_punto = url_punto.strip()
                est_p, mun_p = "", ""
                if any(d in url_punto for d in ("goo.gl", "google.com", "share.google")):
                    lat, lng = extraer_coordenadas(url_punto)
                    if lat is not None:
                        est_p, mun_p = obtener_estado_municipio(lat, lng)
                acts_punto = (
                    f"{nombre_punto} — {actividades_desarrolladas}"
                    if actividades_desarrolladas else nombre_punto
                )
                filas.append(armar_fila(url_punto, est_p, mun_p, acts_punto))
        else:
            filas.append(armar_fila(url, estado_geo, municipio_geo, actividades_desarrolladas))

    return filas

def subir_a_sheets(filas):
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credenciales.json", scopes=SCOPES)

    client = gspread.Client(auth=creds)
    SPREADSHEET_ID = "1xwnS8DiEB4rzs7I8BRGgUbtzDF8M_zn58qYUJS-Mvmk"
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.get_worksheet(0)
    headers = worksheet.row_values(1)
    columna_a = worksheet.col_values(1)

    ultimo = 0
    if len(columna_a) > 1:
        try:
            ultimo = int(columna_a[-1])
        except:
            ultimo = 0

    inicio = ultimo + 1
    datos = []
    for i, fila in enumerate(filas):
        nueva_fila = [inicio + i]
        for header in headers[1:]:
            nueva_fila.append(fila.get(header, ""))
        datos.append(nueva_fila)

    worksheet.append_rows(datos, value_input_option="USER_ENTERED")
    return len(filas)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/procesar", methods=["POST"])
def procesar():
    try:
        texto = request.json.get("texto", "")
        if not texto.strip():
            return jsonify({"error": "El texto está vacío"}), 400
        filas = procesar_agenda(texto)
        if not filas:
            return jsonify({"error": "No se encontraron actividades en el texto"}), 400
        total = subir_a_sheets(filas)
        return jsonify({"success": True, "filas": filas, "total": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/sw.js")
def service_worker():
    # Servido desde raíz para que el scope del SW cubra toda la app
    return app.send_static_file("sw.js")


@app.route("/share", methods=["GET", "POST"])
def share():
    if request.method == "POST":
        texto = (request.form.get("text") or request.form.get("title") or "").strip()
    else:
        texto = (request.args.get("text") or "").strip()

    if not texto:
        return "No llegó texto para procesar", 400

    try:
        filas = procesar_agenda(texto)
        if not filas:
            return "<h2>⚠️ No se encontraron actividades en el texto compartido</h2>", 400
        total = subir_a_sheets(filas)
        resumen_html = "".join(
            f"<li>{f['FECHA Y HORA']} — {f['TIPO DE SOLICITUD'][:80]}</li>" for f in filas
        )
        return f"""
        <html><head><meta name="viewport" content="width=device-width, initial-scale=1">
        <style>body{{font-family:sans-serif;padding:24px;background:#691B4F;color:white}}
        .card{{background:white;color:#333;border-radius:16px;padding:20px}}</style></head>
        <body><div class="card">
        <h2>✅ {total} actividad(es) subida(s) a Sheets</h2>
        <ul>{resumen_html}</ul>
        </div></body></html>
        """
    except Exception as e:
        return f"<h2>❌ Error: {e}</h2>", 500

# =========================
# BOT DE TELEGRAM (Tara)
# =========================
TELEGRAM_CHATS_OK = set(
    c.strip() for c in os.environ.get("TELEGRAM_CHATS_OK", "").split(",") if c.strip()
)
_telegram_vistos = set()   # update_id ya procesados (anti-duplicados en frío de Render)
_agendas_recientes = {}

import threading

_buffer_lock = threading.Lock()
_mensajes_buffer = {}  # chat_id -> {"texto": str, "timer": Timer}
BUFFER_DELAY = 4  # segundos de espera para juntar fragmentos partidos por Telegram

def telegram_enviar(chat_id, texto):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto},
            timeout=10,
        )
    except requests.RequestException:
        pass

@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    # 1) Seguridad: el secreto que Telegram manda en cada llamada.
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""):
        return "", 403

    update = request.get_json(silent=True) or {}

    # 2) Anti-duplicados: si Telegram reintenta el mismo update, lo ignoramos.
    update_id = update.get("update_id")
    if update_id is not None:
        if update_id in _telegram_vistos:
            return "", 200
        _telegram_vistos.add(update_id)
        if len(_telegram_vistos) > 1000:        # no crece sin límite
            _telegram_vistos.clear()

    msg = update.get("message") or update.get("channel_post") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    texto = msg.get("text", "") or ""
    if not chat_id:
        return "", 200

    # 3) Control de acceso. Sin lista configurada = modo setup: te dice tu id.
    if not TELEGRAM_CHATS_OK:
        telegram_enviar(chat_id, f"[setup] chat_id = {chat_id} — ponlo en TELEGRAM_CHATS_OK")
        return "", 200
    if chat_id not in TELEGRAM_CHATS_OK:
        return "", 200   # chat no autorizado: se ignora en silencio

    # 4) Acumula en buffer por si Telegram partió el mensaje en fragmentos
    with _buffer_lock:
        entry = _mensajes_buffer.get(chat_id)
        if entry:
            entry["timer"].cancel()
            entry["texto"] += texto
        else:
            entry = {"texto": texto}
            _mensajes_buffer[chat_id] = entry
        entry["timer"] = threading.Timer(BUFFER_DELAY, _procesar_buffer, args=(chat_id,))
        entry["timer"].start()

    return "", 200


def _procesar_buffer(chat_id):
    with _buffer_lock:
        entry = _mensajes_buffer.pop(chat_id, None)
    if not entry:
        return
    texto = entry["texto"]

    if not (re.search(r'(?i)agenda', texto)
            or "Fecha:" in texto
            or _CLAVES_RE.search(texto[:200])):
        telegram_enviar(chat_id, "Eso no parece agenda. Reenvíame el mensaje del grupo tal cual.")
        return

    import hashlib
    firma = hashlib.sha256(texto.strip().encode("utf-8")).hexdigest()
    ahora = datetime.now()
    for h in [h for h, t in _agendas_recientes.items() if (ahora - t).total_seconds() > 600]:
        del _agendas_recientes[h]
    if firma in _agendas_recientes:
        telegram_enviar(chat_id, "⚠️ Esa misma agenda ya la subí hace un momento; no la dupliqué.")
        return
    _agendas_recientes[firma] = ahora

    try:
        filas = procesar_agenda(texto)
        if not filas:
            telegram_enviar(chat_id, "No encontré actividades en ese texto.")
            return
        total = subir_a_sheets(filas)
        proyecto = filas[0].get("PROYECTO FERROVIARIO", "")
        telegram_enviar(chat_id, f"✅ Subí {total} actividad(es) a Sheets ({proyecto}).")
    except Exception as e:
        telegram_enviar(chat_id, f"❌ Error: {e}")

#=========================
# API AGENDA
#=========================

@app.route("/api/agenda")
def api_agenda():
    from collections import defaultdict, Counter
    import traceback
    try:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        if creds_json:
            creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file("credenciales.json", scopes=SCOPES)
        client = gspread.Client(auth=creds)
        ws = client.open_by_key("1xwnS8DiEB4rzs7I8BRGgUbtzDF8M_zn58qYUJS-Mvmk").get_worksheet(0)

        valores = ws.get_all_values()
        if len(valores) < 2:
            return jsonify({"carga": {}, "edo": {}, "dep": {}, "serie": {}, "agenda": []})
        headers = [h.replace("\n", " ").strip() for h in valores[0]]
        filas = [dict(zip(headers, row)) for row in valores[1:]]

        inv = {v: k for k, v in mapa_proyectos.items()}
        carga, edo, dep, serie = Counter(), Counter(), Counter(), defaultdict(int)

        

        def fecha_norm(fh):
            """Normaliza a dd/mm/yyyy. Devuelve None si no es fecha válida."""
            s = str(fh).split()[0].replace("-", "/") if str(fh).split() else ""
            partes = s.split("/")
            if len(partes) != 3:
                return None
            d, m, a = partes
            if len(a) == 2:
                a = "20" + a
            try:
                if int(m) > 12 and int(d) <= 12:
                    d, m = m, d
                dt = datetime.strptime(f"{int(d):02d}/{int(m):02d}/{a}", "%d/%m/%Y")
                if dt.year < 2025 or dt.year > 2026:
                    return None
                return dt.strftime("%d/%m/%Y")
            except (ValueError, TypeError):
                return None

        def fecha_dt(fh):
            """Igual que fecha_norm pero devuelve un objeto date (o None)."""
            fn = fecha_norm(fh)
            if not fn:
                return None
            try:
                return datetime.strptime(fn, "%d/%m/%Y").date()
            except ValueError:
                return None

        # Ventana de 5 días desde la fecha MÁS RECIENTE del Sheet
        fechas_validas = [d for d in (fecha_dt(f.get("FECHA Y HORA", "")) for f in filas) if d]
        if fechas_validas:
            fecha_tope = max(fechas_validas)
            fecha_piso = fecha_tope - timedelta(days=4)
        else:
            fecha_tope = fecha_piso = None

        for f in filas:
            d = fecha_dt(f.get("FECHA Y HORA", ""))
            if fecha_piso is not None and (d is None or d < fecha_piso or d > fecha_tope):
                continue

            proy = inv.get(f.get("PROYECTO FERROVIARIO", ""), "OTRO")
            carga[proy] += 1
            if f.get("ESTADO"):    edo[f["ESTADO"]] += 1

            celda = _sin_acentos(str(f.get("DEPENDENCIAS PARTICIPANTES", "")).upper())
            for canon, patrones in _DEP_PATRONES.items():
                if any(p.search(celda) for p in patrones):
                    dep[canon] += 1

            fn = fecha_norm(f.get("FECHA Y HORA", ""))
            if fn:
                serie[fn] += 1

        def hora_de(fh):
            partes = str(fh).split(maxsplit=1)
            if len(partes) < 2:
                return ""
            m = re.search(r'\d{1,2}:\d{2}', partes[1])
            return m.group(0) if m else ""

        def construir_agenda(filas, piso, tope, fdt, hde, inv):
            items = []
            for f in filas:
                d = fdt(f.get("FECHA Y HORA", ""))
                # solo las de la ventana de 3 días
                if piso is not None and (d is None or d < piso or d > tope):
                    continue
                hora = hde(f.get("FECHA Y HORA", ""))
                # clave de orden: fecha + hora (las que no tienen hora van al final del día)
                clave = (d, hora if hora else "00:00")
                items.append((clave, {
                    "hora":  hora if hora else "--:--",
                    "code":  inv.get(f.get("PROYECTO FERROVIARIO", ""), "—"),
                    "mun":   f.get("MUNICIPIO", ""),
                    "act":   f.get("TIPO DE SOLICITUD", ""),
                }))
            # ordena por (fecha, hora) descendente = más reciente primero
            items.sort(key=lambda x: x[0], reverse=True)
            return [it[1] for it in items[:10]]

        dep_colapsado = Counter()
        for canon, n in dep.items():
            dep_colapsado["OTRAS" if canon not in DEP_NUCLEO else canon] += n

        return jsonify({
            "carga": dict(carga),
            "edo": dict(edo),
            "dep": dict(dep_colapsado),
            "serie": dict(serie),
            "agenda": construir_agenda(filas, fecha_piso, fecha_tope, fecha_dt, hora_de, inv),
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

#=========================
# API CONTEO
#=========================

@app.route("/api/conteo")
def api_conteo():
    from collections import Counter
    import traceback
    try:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        if creds_json:
            creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file("credenciales.json", scopes=SCOPES)
        client = gspread.Client(auth=creds)
        ws = client.open_by_key("1xwnS8DiEB4rzs7I8BRGgUbtzDF8M_zn58qYUJS-Mvmk").get_worksheet(0)

        valores = ws.get_all_values()
        if len(valores) < 2:
            return jsonify({"conteo": {}, "cat3_desglose": {}, "otras": 0,
                            "sin_clasificar": [], "total": 0})
        headers = [h.replace("\n", " ").strip() for h in valores[0]]
        filas = [dict(zip(headers, row)) for row in valores[1:]]

        PISO = datetime(2026, 4, 1).date()
        HOY = datetime.now().date()

        def fecha_actividad(f):
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

        conteo = Counter()
        for n, _ in CATEGORIAS_CONTEO:
            conteo[n] = 0
        conteo[CAT3_NOMBRE] = 0
        cat3_desglose = Counter()
        for n, _ in CAT3_SUB:
            cat3_desglose[n] = 0
        cat3_desglose[CAT3_GENERAL[0]] = 0

        otras = 0
        sin_clasificar = []
        total = 0
        for f in filas:
            d = fecha_actividad(f)
            if d is None or d < PISO or d > HOY:
                continue
            total += 1
            hits = clasificar_actividad(
                f.get("TIPO DE SOLICITUD", ""),
                f.get("ACTIVIDADES DESARROLLADAS", ""),
            )
            if not hits:
                otras += 1
                sin_clasificar.append(f.get("TIPO DE SOLICITUD", ""))
                continue
            for cat, sub in hits:
                conteo[cat] += 1
                if sub:
                    cat3_desglose[sub] += 1

        return jsonify({
            "conteo": dict(conteo),
            "cat3_desglose": dict(cat3_desglose),
            "otras": otras,
            "sin_clasificar": sin_clasificar,
            "total": total,
            "ventana": {"desde": PISO.strftime("%d/%m/%Y"),
                        "hasta": HOY.strftime("%d/%m/%Y")},
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

#=========================
# RUTA A DASHBOARD
#=========================

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

#=========================
# RUTA A CONTEO
#=========================

@app.route("/conteo")
def conteo_dashboard():
    return render_template("conteo.html")

if __name__ == "__main__":
    app.run(debug=False)