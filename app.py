"""
Creador de imágenes de producto
- Sube foto (desde el celular) -> quita fondo (rembg, local y gratis)
- Fondo nuevo desde un prompt (Pollinations.ai, gratis y sin API key) o color sólido
- Nombre, precio y descripción con texto automático legible según el fondo
- Descarga en formato historia de WhatsApp o catálogo

Ejecutar:  streamlit run app.py
"""
import io
import os
import random
import urllib.parse

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat
from rembg import new_session, remove

FORMATOS = {
    "Historia de WhatsApp (1080×1920)": (1080, 1920),
    "Catálogo cuadrado (1080×1080)": (1080, 1080),
    "Catálogo vertical (1080×1350)": (1080, 1350),
}

# Fuentes de Google Fonts (se descargan una sola vez a la carpeta ./fuentes)
FUENTES = {
    "bold": (
        "Poppins-Bold.ttf",
        "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf",
    ),
    "regular": (
        "Poppins-Regular.ttf",
        "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Regular.ttf",
    ),
}


# ----------------------------------------------------------------- utilidades
@st.cache_resource
def _ruta_fuente(clave):
    os.makedirs("fuentes", exist_ok=True)
    nombre, url = FUENTES[clave]
    ruta = os.path.join("fuentes", nombre)
    if not os.path.exists(ruta):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            with open(ruta, "wb") as f:
                f.write(r.content)
        except Exception:
            return None
    return ruta


def fuente(clave, tam):
    try:
        return ImageFont.truetype(_ruta_fuente(clave), tam)
    except Exception:
        return ImageFont.load_default(tam)  # respaldo si no hay internet


@st.cache_resource
def _sesion_rembg():
    # Modelo por defecto (u2net). Para algo más ligero: new_session("u2netp")
    return new_session()


@st.cache_data(show_spinner=False)
def quitar_fondo(datos: bytes) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(datos))).convert("RGB")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return remove(buf.getvalue(), session=_sesion_rembg())


def fondo_ia(prompt, size, seed):
    """Genera un fondo con Pollinations.ai (gratis, sin key)."""
    W, H = size
    if H > W * 1.5:
        gw, gh = 576, 1024
    elif H > W:
        gw, gh = 768, 960
    else:
        gw, gh = 768, 768
    texto = f"{prompt}, empty background, no people, no text"
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(texto)
        + f"?width={gw}&height={gh}&seed={seed}&nologo=true"
    )
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def luminancia_rgb(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def envolver(draw, texto, font, ancho_max):
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = (actual + " " + palabra).strip()
        if draw.textlength(prueba, font=font) <= ancho_max:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


# ------------------------------------------------------------- composición
def componer(producto, fondo, nombre, precio, desc, size):
    W, H = size
    lienzo = ImageOps.fit(fondo, size, Image.LANCZOS).convert("RGBA")
    historia = H > W * 1.5
    hay_texto = any([nombre.strip(), precio.strip(), desc.strip()])
    margen = int(W * 0.06)
    zona_prod = int(H * (0.55 if hay_texto else 0.92))

    # --- producto recortado, escalado y centrado
    caja = producto.getchannel("A").getbbox()
    prod = producto.crop(caja) if caja else producto
    esc = min(W * 0.84 / prod.width, (zona_prod - margen * 1.5) / prod.height)
    prod = prod.resize(
        (max(1, int(prod.width * esc)), max(1, int(prod.height * esc))), Image.LANCZOS
    )
    px = (W - prod.width) // 2
    py = max(margen, (zona_prod - prod.height) // 2 + (int(H * 0.04) if historia else 0))

    # --- sombra suave
    sombra = Image.new("RGBA", size, (0, 0, 0, 0))
    negro = Image.new("RGBA", prod.size, (0, 0, 0, 255))
    negro.putalpha(prod.getchannel("A").point(lambda a: int(a * 0.5)))
    sombra.paste(negro, (px + int(W * 0.01), py + int(W * 0.02)))
    sombra = sombra.filter(ImageFilter.GaussianBlur(W * 0.02))
    lienzo = Image.alpha_composite(lienzo, sombra)
    lienzo.alpha_composite(prod, (px, py))

    if not hay_texto:
        return lienzo.convert("RGB")

    # --- panel de texto con contraste automático
    panel = (margen, zona_prod, W - margen, H - (int(H * 0.07) if historia else margen))
    muestra = lienzo.crop(panel).convert("L").resize((100, 100))
    stat = ImageStat.Stat(muestra)
    fondo_claro = stat.mean[0] > 140
    col_texto = (25, 25, 30) if fondo_claro else (255, 255, 255)
    col_panel = (255, 255, 255) if fondo_claro else (0, 0, 0)
    alfa = 175 if stat.stddev[0] > 45 else 120  # más opaco si el fondo es muy "ruidoso"

    capa = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(capa).rounded_rectangle(
        panel, radius=int(W * 0.05), fill=col_panel + (alfa,)
    )
    lienzo = Image.alpha_composite(lienzo, capa)
    d = ImageDraw.Draw(lienzo)

    ancho_max = (panel[2] - panel[0]) - int(W * 0.08)
    alto_panel = panel[3] - panel[1]
    gap = int(W * 0.025)

    # --- nombre (se achica hasta caber en 2 líneas)
    tam = int(W * 0.075)
    while True:
        f_nom = fuente("bold", tam)
        l_nom = envolver(d, nombre.strip(), f_nom, ancho_max)
        if len(l_nom) <= 2 or tam <= int(W * 0.04):
            break
        tam -= 4
    l_nom = l_nom[:2]
    alto_linea_nom = int(tam * 1.2)

    # --- precio en "pastilla" con color tomado del producto
    precio = precio.strip()
    if precio and precio[0].isdigit():
        precio = "$" + precio
    f_pre = fuente("bold", int(W * 0.065))
    acento = (230, 60, 90)
    chico = prod.resize((40, 40))
    pix = [p[:3] for p in chico.getdata() if p[3] > 200]
    if pix:
        n = len(pix)
        prom = tuple(sum(c[i] for c in pix) // n for i in range(3))
        acento = ImageEnhance.Color(Image.new("RGB", (1, 1), prom)).enhance(1.8).getpixel((0, 0))
    if abs(luminancia_rgb(acento) - luminancia_rgb(col_panel)) < 70:
        acento = col_texto
    col_pre = (25, 25, 30) if luminancia_rgb(acento) > 140 else (255, 255, 255)
    if precio:
        bb = d.textbbox((0, 0), precio, font=f_pre)
        pill_w = (bb[2] - bb[0]) + int(W * 0.08)
        pill_h = (bb[3] - bb[1]) + int(W * 0.04)
    else:
        pill_w = pill_h = 0

    # --- descripción (máx. 4 líneas, se recorta si no cabe)
    f_desc = fuente("regular", int(W * 0.036))
    l_desc = envolver(d, desc.strip(), f_desc, ancho_max)
    alto_linea_desc = int(W * 0.036 * 1.3)
    if len(l_desc) > 4:
        l_desc = l_desc[:4]
        l_desc[-1] = l_desc[-1].rstrip(" .,;") + "…"

    def alto_total():
        bloques = []
        if l_nom:
            bloques.append(len(l_nom) * alto_linea_nom)
        if precio:
            bloques.append(pill_h)
        if l_desc:
            bloques.append(len(l_desc) * alto_linea_desc)
        return sum(bloques) + gap * max(0, len(bloques) - 1)

    while alto_total() > alto_panel - int(W * 0.06) and len(l_desc) > 1:
        l_desc.pop()
        l_desc[-1] = l_desc[-1].rstrip(" .,;") + "…"

    y = panel[1] + max(int(W * 0.03), (alto_panel - alto_total()) // 2)
    cx = W // 2

    for linea in l_nom:
        d.text((cx, y), linea, font=f_nom, fill=col_texto, anchor="ma")
        y += alto_linea_nom
    if l_nom:
        y += gap
    if precio:
        d.rounded_rectangle(
            (cx - pill_w // 2, y, cx + pill_w // 2, y + pill_h),
            radius=pill_h // 2,
            fill=acento,
        )
        d.text((cx, y + pill_h // 2), precio, font=f_pre, fill=col_pre, anchor="mm")
        y += pill_h + gap
    for linea in l_desc:
        d.text((cx, y), linea, font=f_desc, fill=col_texto, anchor="ma")
        y += alto_linea_desc

    return lienzo.convert("RGB")


def a_png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------- UI
st.set_page_config(page_title="Foto de producto", page_icon="🛍️", layout="centered")
ss = st.session_state
for k, v in {
    "paso": 1, "producto": None, "fondo": None, "final": None,
    "nombre": "", "precio": "", "desc": "",
}.items():
    ss.setdefault(k, v)

st.title("🛍️ Foto de producto")

# ---- PASO 1: foto + fondo
if ss.paso == 1:
    st.subheader("1 · Foto y fondo")
    formato = st.selectbox("Formato final", list(FORMATOS), key="formato")
    size = FORMATOS[formato]

    archivo = st.file_uploader(
        "Agrega la foto de tu producto", type=["png", "jpg", "jpeg", "webp"]
    )
    if archivo:
        with st.spinner("Quitando el fondo…"):
            ss.producto = Image.open(io.BytesIO(quitar_fondo(archivo.getvalue()))).convert("RGBA")

    if ss.producto is not None:
        st.image(ss.producto, caption="Producto sin fondo", width=200)

        modo = st.radio("Fondo nuevo", ["✨ Generar con IA", "🎨 Color sólido"], horizontal=True)
        if modo.startswith("✨"):
            prompt = st.text_area(
                "Describe el fondo",
                value="estudio fotográfico minimalista, luz suave, tonos pastel",
                height=80,
            )
            if st.button("Generar fondo"):
                with st.spinner("Generando fondo…"):
                    try:
                        ss.fondo = fondo_ia(prompt, size, random.randint(1, 10**6))
                    except Exception as e:
                        st.error(f"No se pudo generar el fondo ({e}). Intenta de nuevo.")
        else:
            color = st.color_picker("Color de fondo", "#F4E9DD")
            ss.fondo = Image.new("RGB", (64, 64), color)

        if ss.fondo is not None:
            st.image(
                componer(ss.producto, ss.fondo, "", "", "", size),
                caption="Vista previa",
                use_container_width=True,
            )
            if st.button("Siguiente →", type="primary"):
                ss.paso = 2
                st.rerun()

# ---- PASO 2: datos del producto
elif ss.paso == 2:
    st.subheader("2 · Datos del producto")
    with st.form("datos"):
        nombre = st.text_input("Nombre del producto", ss.nombre)
        precio = st.text_input("Precio", ss.precio, placeholder="350")
        desc = st.text_area("Descripción", ss.desc, max_chars=200)
        crear = st.form_submit_button("Crear imagen ✨", type="primary")
    if crear:
        ss.nombre, ss.precio, ss.desc = nombre, precio, desc
        with st.spinner("Armando la imagen…"):
            ss.final = componer(
                ss.producto, ss.fondo, nombre, precio, desc, FORMATOS[ss.formato]
            )
        ss.paso = 3
        st.rerun()
    if st.button("← Atrás"):
        ss.paso = 1
        st.rerun()

# ---- PASO 3: resultado
else:
    st.subheader("3 · ¡Listo!")
    st.image(ss.final, use_container_width=True)
    st.download_button(
        "⬇️ Descargar imagen",
        data=a_png(ss.final),
        file_name="producto.png",
        mime="image/png",
        type="primary",
    )
    c1, c2 = st.columns(2)
    if c1.button("✏️ Editar textos"):
        ss.paso = 2
        st.rerun()
    if c2.button("🆕 Nuevo producto"):
        for k in ("producto", "fondo", "final"):
            ss[k] = None
        ss.nombre = ss.precio = ss.desc = ""
        ss.paso = 1
        st.rerun()
