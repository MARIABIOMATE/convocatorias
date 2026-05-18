#!/usr/bin/env python3
"""
Verificación diaria de convocatorias educativas.

Consulta BOE y BOCM (RSS oficiales) y busca coincidencias con las convocatorias
del catálogo. Cuando encuentra una publicación oficial, extrae la fecha de cierre
del plazo y la guarda en `fechas.json` para que la aplicación la marque como
"verificada" automáticamente.

Se ejecuta cada día a las 7:00 UTC vía GitHub Actions.
"""

import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import re
from datetime import datetime
from pathlib import Path

# ============================================================
# FUENTES OFICIALES (RSS) — añadir o quitar según necesidad
# ============================================================
FUENTES = [
    {
        'nombre': 'BOE · Últimas disposiciones',
        'url': 'https://www.boe.es/rss/canal.php?c=10',
        'codigo': 'BOE'
    },
    {
        'nombre': 'BOE · Disposiciones generales',
        'url': 'https://www.boe.es/rss/canal.php?c=1A',
        'codigo': 'BOE'
    },
    {
        'nombre': 'BOCM · Sumarios',
        'url': 'https://www.bocm.es/rss/sumarios',
        'codigo': 'BOCM'
    },
]

# ============================================================
# PALABRAS CLAVE POR CONVOCATORIA
# Para cada id del catálogo, qué buscar en BOE/BOCM
# ============================================================
PALABRAS_CLAVE = {
    # === Comunidad de Madrid ===
    'cm-transporte': ['transporte escolar', 'ayudas individualizadas transporte'],
    'cm-comedor': ['comedor escolar', 'ayudas comedor'],
    'cm-accede': ['programa accede', 'préstamo de libros'],
    'cm-neae': ['necesidad específica de apoyo educativo', 'NEAE'],
    'cm-infantil': ['escolarización infantil', 'primer ciclo infantil', 'educación infantil'],
    'cm-proa': ['proa+', 'éxito escolar', 'cooperación territorial'],
    'cm-pie': ['proyectos de innovación educativa', 'PIE'],
    'cm-seminarios': ['seminarios y proyectos de formación', 'formación en centros CTIF'],
    'cm-programas-inst': ['huertos escolares', 'hábitos de vida saludable'],
    'refuerza': ['programa refuerza', 'refuerzo educativo extraescolar'],
    'convivencia-madrid': ['educar para la convivencia', 'plan de convivencia'],
    'bibliotecas-madrid': ['bibliotecas escolares'],
    'aulas-crecimiento': ['aulas de crecimiento'],
    # === Estatales ===
    'meed-becas-general': ['becas generales', 'becas estudios postobligatorios'],
    'meed-convivencia': ['premios convivencia y bienestar', 'proyectos de convivencia'],
    'meed-irene': ['premios irene', 'aulas por la igualdad'],
    'steam-ninas': ['niñas en pie de ciencia', 'alianza steam'],
    'meed-vidasaludable': ['sello vida saludable'],
    'meed-lectura': ['premio nacional promoción de la lectura', 'bibliotecas escolares'],
    'apsred': ['aprendizaje-servicio', 'aprendizaje servicio'],
    'intef': ['intef', 'instituto nacional de tecnologías educativas'],
    'consumopolis': ['consumópolis', 'consumo responsable'],
    'incibe': ['incibe', 'ciberseguridad escolar'],
    'defensor-pueblo': ['concurso dibujos derechos humanos'],
    'min-agricultura-colores': ['colores que conciencian'],
    # === Europeas y NextGen ===
    'erasmus-ka120': ['acreditación erasmus', 'erasmus ka120', 'ka120'],
    'erasmus-ka122-sch': ['erasmus ka122', 'movilidad escolar', 'ka122-sch'],
    'erasmus-ka210': ['asociaciones a pequeña escala', 'erasmus ka210'],
    'erasmus-ka220': ['asociaciones de cooperación', 'erasmus ka220'],
    'compdigedu': ['#compdigedu', 'plan de digitalización', 'competencia digital docente'],
    'uaoe': ['unidades de acompañamiento', 'acompañamiento orientación personal y familiar'],
    'proa-avanza': ['proa+ avanza'],
    'plan-refuerzo': ['plan de refuerzo educativo'],
    'esero': ['esero', 'astro pi'],
    # === Fundaciones (publicadas a veces en BOE) ===
    'fund-mapfre': ['fundación mapfre innovación educativa'],
    'fund-onceapoyo': ['concurso escolar once'],
    'fund-aepd': ['protección de datos centros educativos'],
}

# ============================================================
# CONFIGURACIÓN — meses en español para extraer fechas
# ============================================================
MESES_ES = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12'
}

# ============================================================
# FUNCIONES
# ============================================================
def fetch(url, timeout=15):
    """Descarga una URL devolviendo el contenido como texto."""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; VerificadorConvocatorias/1.0; +https://github.com)'
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"  [Error de red] {url}: {e}")
        return None


def extraer_fechas(texto):
    """Detecta fechas en español dentro de un texto.

    Devuelve una lista de fechas en formato ISO (YYYY-MM-DD) en el orden en que aparecen.
    """
    fechas = []
    # "3 de octubre de 2025" o "3 octubre 2025"
    patron1 = r'(\d{1,2})\s+(?:de\s+)?(' + '|'.join(MESES_ES.keys()) + r')\s+(?:de\s+)?(\d{4})'
    for m in re.finditer(patron1, texto, re.IGNORECASE):
        dia, mes_nombre, anio = m.groups()
        mes_num = MESES_ES[mes_nombre.lower()]
        fechas.append(f"{anio}-{mes_num}-{dia.zfill(2)}")

    # "01/10/2025"
    for m in re.finditer(r'(\d{1,2})/(\d{1,2})/(\d{4})', texto):
        dia, mes, anio = m.groups()
        fechas.append(f"{anio}-{mes.zfill(2)}-{dia.zfill(2)}")

    # "2025-10-03"
    for m in re.finditer(r'(\d{4})-(\d{2})-(\d{2})', texto):
        fechas.append(m.group(0))

    return fechas


def buscar_fecha_cierre(texto):
    """Intenta identificar la fecha de CIERRE del plazo dentro del texto.

    Busca fragmentos como "finalizará el ...", "hasta el ...", "antes de las ... del día ..."
    """
    fechas = extraer_fechas(texto)
    if not fechas:
        return None

    # Si encuentra "finalizará", "hasta el", priorizar fecha cercana
    patrones_cierre = [
        r'finalizar[áa]?\s+(?:a\s+)?(?:las\s+\d+:\d+\s+(?:horas?\s+)?)?(?:del\s+)?(?:d[íi]a\s+)?([^.]{1,80})',
        r'plazo[^.]{0,30}(?:hasta|finalizar[áa]?)([^.]{1,80})',
        r'hasta\s+(?:las\s+\d+:\d+\s+(?:horas?\s+)?)?(?:del\s+)?(?:d[íi]a\s+)?([^.]{1,80})'
    ]
    for patron in patrones_cierre:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            fragmento = m.group(1)
            fechas_cierre = extraer_fechas(fragmento)
            if fechas_cierre:
                return fechas_cierre[0]

    # Si no hay marcador claro, devolver la última fecha encontrada
    # (suele ser la fecha de cierre)
    return fechas[-1]


def buscar_en_rss(rss_url, palabras_clave_list):
    """Busca en un RSS items que contengan alguna de las palabras clave."""
    xml = fetch(rss_url)
    if not xml:
        return []

    coincidencias = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        print(f"  [Error parseando XML] {rss_url}: {e}")
        return []

    for item in root.iter('item'):
        titulo = (item.findtext('title') or '').strip()
        descripcion = (item.findtext('description') or '').strip()
        enlace = (item.findtext('link') or '').strip()
        fecha_pub = (item.findtext('pubDate') or '').strip()

        # Quitar etiquetas HTML del description
        descripcion_limpia = re.sub(r'<[^>]*>', ' ', descripcion)
        texto_completo = (titulo + ' ' + descripcion_limpia).lower()

        for kw in palabras_clave_list:
            if kw.lower() in texto_completo:
                coincidencias.append({
                    'titulo': titulo,
                    'descripcion': descripcion_limpia.strip(),
                    'enlace': enlace,
                    'fecha_publicacion': fecha_pub,
                    'palabra_clave': kw
                })
                break

    return coincidencias


def cargar_estado_anterior():
    """Carga el fechas.json anterior si existe, para preservar verificaciones antiguas."""
    archivo = Path('fechas.json')
    if archivo.exists():
        try:
            with open(archivo, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('convocatorias_verificadas', {})
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def main():
    print(f"=" * 60)
    print(f"Verificación de convocatorias")
    print(f"Inicio: {datetime.now().isoformat()}")
    print(f"=" * 60)

    # Cargar estado anterior (preservar verificaciones previas)
    verificadas = cargar_estado_anterior()
    print(f"\nVerificaciones previas cargadas: {len(verificadas)}")

    # Descargar cada fuente una sola vez
    items_por_fuente = {}
    for fuente in FUENTES:
        print(f"\nDescargando {fuente['nombre']}...")
        xml = fetch(fuente['url'])
        if xml:
            try:
                root = ET.fromstring(xml)
                items = list(root.iter('item'))
                items_por_fuente[fuente['codigo']] = (items, fuente)
                print(f"  Items recibidos: {len(items)}")
            except ET.ParseError:
                print(f"  Error parseando XML")

    # Para cada convocatoria, buscar coincidencias
    nuevas_verificaciones = 0
    print(f"\nBuscando coincidencias para {len(PALABRAS_CLAVE)} convocatorias...")

    for conv_id, palabras in PALABRAS_CLAVE.items():
        mejor_coincidencia = None

        for codigo, (items, fuente) in items_por_fuente.items():
            for item in items:
                titulo = (item.findtext('title') or '').strip()
                descripcion = (item.findtext('description') or '').strip()
                descripcion_limpia = re.sub(r'<[^>]*>', ' ', descripcion)
                texto = (titulo + ' ' + descripcion_limpia).lower()

                for kw in palabras:
                    if kw.lower() in texto:
                        enlace = (item.findtext('link') or '').strip()
                        fecha_pub = (item.findtext('pubDate') or '').strip()
                        fecha_cierre = buscar_fecha_cierre(descripcion_limpia + ' ' + titulo)

                        if fecha_cierre:
                            mejor_coincidencia = {
                                'fechaFin': fecha_cierre,
                                'fuente_url': enlace,
                                'fuente_titulo': titulo[:200],
                                'fuente_organismo': codigo,
                                'publicado_el': fecha_pub,
                                'verificado_el': datetime.now().isoformat() + 'Z',
                                'palabra_clave': kw
                            }
                            break
                if mejor_coincidencia:
                    break
            if mejor_coincidencia:
                break

        if mejor_coincidencia:
            # ¿Es nueva o actualización?
            previa = verificadas.get(conv_id)
            if not previa or previa.get('fechaFin') != mejor_coincidencia['fechaFin']:
                nuevas_verificaciones += 1
                print(f"  [{conv_id}] Verificada: {mejor_coincidencia['fechaFin']} ({codigo})")
            verificadas[conv_id] = mejor_coincidencia

    # Limpiar verificaciones expiradas (más de 60 días pasadas)
    hoy = datetime.now().date()
    expiradas = []
    for cid, info in list(verificadas.items()):
        try:
            fecha_fin = datetime.strptime(info['fechaFin'], '%Y-%m-%d').date()
            if (hoy - fecha_fin).days > 60:
                expiradas.append(cid)
        except (ValueError, KeyError):
            continue
    for cid in expiradas:
        del verificadas[cid]
    if expiradas:
        print(f"\nVerificaciones expiradas eliminadas: {len(expiradas)}")

    # Guardar
    salida = {
        'updated_at': datetime.now().isoformat() + 'Z',
        'version': 1,
        'convocatorias_verificadas': verificadas,
        'estadisticas': {
            'total_verificadas': len(verificadas),
            'nuevas_hoy': nuevas_verificaciones,
            'consultas_realizadas': len(PALABRAS_CLAVE),
            'fuentes_consultadas': [f['nombre'] for f in FUENTES]
        }
    }

    with open('fechas.json', 'w', encoding='utf-8') as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"\n" + "=" * 60)
    print(f"Resumen:")
    print(f"  Total verificadas: {len(verificadas)}")
    print(f"  Nuevas/cambiadas hoy: {nuevas_verificaciones}")
    print(f"Archivo fechas.json actualizado.")
    print(f"=" * 60)


if __name__ == '__main__':
    main()
