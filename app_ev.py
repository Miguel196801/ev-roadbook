"""
EV Roadbook Italia — Interfaccia Mobile-First Dark Card UI.
Pianificatore guidato di viaggi in auto elettrica con due modalità principali
(Pianifica Viaggio e Cerca Colonnine in Zona / Arrivo), database locale OSM/Geofabrik,
curve di ricarica DC reali, filtri rete/Autogrill/Supercharger e garanzia energetica
su Arrivo (B) e Rientro a Casa (A).
"""

import base64
from datetime import date, datetime, time
import hmac
import html
import json
import math
import os
import re
import struct
from urllib.parse import quote, quote_plus
import zlib
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from geopy.geocoders import Nominatim, Photon
import polyline
import requests
import streamlit as st
import streamlit.components.v1 as components


def _crea_chunk_png(tipo_chunk, dati):
    """Costruisce un chunk PNG standard (lunghezza + tipo + dati + CRC32)."""
    crc = zlib.crc32(tipo_chunk + dati) & 0xFFFFFFFF
    return struct.pack(">I", len(dati)) + tipo_chunk + dati + struct.pack(">I", crc)


def _punto_in_poligono_2d(px, py, poligono):
    """Test ray-casting veloce per verificare se (px, py) è interno al poligono."""
    dentro = False
    n = len(poligono)
    x_prev, y_prev = poligono[n - 1]
    for x_curr, y_curr in poligono:
        if (y_curr > py) != (y_prev > py):
            x_int = (x_prev - x_curr) * (py - y_curr) / (y_prev - y_curr) + x_curr
            if px < x_int:
                dentro = not dentro
        x_prev, y_prev = x_curr, y_curr
    return dentro


def genera_icona_app_png_180():
    """
    Genera in memoria (solo libreria standard Python zlib/struct) un'icona PNG 180x180
    per smartphone iOS/Android: sfondo scuro arrotondato (#0E1117 / #1E222D),
    bordo sottile verde smeraldo (#10B981) e saetta luminosa smeraldo/bianca al centro.
    Restituisce (png_bytes, png_base64).
    """
    w = h = 180
    cx = cy = 89.5
    radius = 34.0
    half_inner = 81.5 - radius
    # Poligono saetta elettrica centrato in 180x180
    saetta = (
        (103.0, 26.0),
        (46.0, 98.0),
        (86.0, 98.0),
        (73.0, 154.0),
        (134.0, 80.0),
        (94.0, 80.0),
    )

    righe = bytearray()
    for y in range(h):
        righe.append(0)  # Filter type 0 (None)
        dy_abs = abs(y - cy)
        qy = dy_abs - half_inner
        qy_pos = qy if qy > 0.0 else 0.0
        # Gradiente verticale sfondo interno (#1E222D -> #131722)
        t_y = y / 179.0
        bg_r = 30.0 - 11.0 * t_y
        bg_g = 34.0 - 11.0 * t_y
        bg_b = 45.0 - 11.0 * t_y
        in_y_saetta = 25 <= y <= 155
        dy_c = y - cy

        for x in range(w):
            dx_abs = abs(x - cx)
            qx = dx_abs - half_inner
            qx_pos = qx if qx > 0.0 else 0.0
            sd = math.hypot(qx_pos, qy_pos) + min(max(qx, qy), 0.0) - radius

            if sd > 1.0:
                # Sfondo esterno scuro (#0E1117)
                r, g, b = 14, 17, 23
            elif sd >= -3.2:
                # Bordo sottile verde smeraldo (#10B981) con leggero anti-aliasing sul margine
                if sd > 0.0:
                    f = 1.0 - sd
                    r = int(14 + (16 - 14) * f)
                    g = int(17 + (185 - 17) * f)
                    b = int(23 + (129 - 23) * f)
                else:
                    r, g, b = 16, 185, 129
            else:
                # Interno scuro con morbido bagliore radiale smeraldo al centro
                dist_c = math.hypot(x - cx, dy_c)
                if dist_c < 74.0:
                    glow = (1.0 - dist_c / 74.0) ** 2
                    r = min(255, int(bg_r + 16.0 * glow))
                    g = min(255, int(bg_g + 92.0 * glow))
                    b = min(255, int(bg_b + 66.0 * glow))
                else:
                    r, g, b = int(bg_r), int(bg_g), int(bg_b)

                # Saetta elettrica luminosa verde smeraldo / bianca al centro
                if in_y_saetta and 45 <= x <= 135 and _punto_in_poligono_2d(float(x), float(y), saetta):
                    # Sfumatura luminosa dal bianco-menta in alto/centro allo smeraldo brillante in basso
                    f_bolt = max(0.0, min(1.0, (y - 26.0) / 128.0))
                    r = int(240 - (240 - 34) * f_bolt * 0.72)
                    g = int(255 - (255 - 211) * f_bolt * 0.45)
                    b = int(248 - (248 - 153) * f_bolt * 0.65)

            righe.append(r)
            righe.append(g)
            righe.append(b)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(righe), level=6)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _crea_chunk_png(b"IHDR", ihdr)
        + _crea_chunk_png(b"IDAT", idat)
        + _crea_chunk_png(b"IEND", b"")
    )
    return png_bytes, base64.b64encode(png_bytes).decode("ascii")


def carica_icona_app_b64():
    """Legge logo.png dalla cartella dell'app (o la genera in memoria come fallback) in Base64."""
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    _, icon_b64 = genera_icona_app_png_180()
    return icon_b64


APP_ICON_B64 = carica_icona_app_b64()

# Configurazione pagina (layout centrato ideale per Mobile-First Dark Card UI)
st.set_page_config(
    page_title="EV Roadbook Italia",
    page_icon="logo.png",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# Costanti applicative e profili veicolo
URL_OSRM_BASE = "https://router.project-osrm.org/route/v1/driving/"
BRENNERO_TESLA_ADS = ("paganella", "nogaredo", "plose", "brennero", "lavis")
KEYWORDS_ADS_ESPLICITE = (
    "free to x",
    "freetox",
    "area di servizio",
    "autogrill",
    "sarni",
    "chef express",
    "mychef",
    "my chef",
    "paganella",
    "nogaredo",
    "plose",
    "autostrada del brennero",
)
BRAND_HPC_NOTI = ("supercharger", "ionity", "free to x", "freetox", "ewiva", "electra", "fastned", "atlante")
OPERATORI_FAST_IT_NOTI = (
    "enel x",
    "be charge",
    "plenitude",
    "neogy",
    "a2a",
    "powy",
    "duferco",
    "acea",
)

AUTO_KIA_ENIRO = "🚙 Kia e-Niro Evolution 2020 (64 kWh)"
AUTO_TESLA_MY = "🏎️ Tesla Model Y Performance 2023 (75 kWh)"
AUTO_TESLA_M3_LR = "🚗 Tesla Model 3 Long Range RWD (79 kWh • 250 kW)"
AUTO_MAZDA_MX30 = "🚗 Mazda MX-30 Versione Lancio (35.5 kWh)"
AUTO_BMW_IX40 = "🚙 BMW iX xDrive40 08/2023 (76.6 kWh)"
AUTO_BMW_IX3_50 = "🚙 BMW iX3 50 xDrive (108.7 kWh • 400 kW)"
AUTO_BMW_IX5_60 = "🚐 BMW iX5 60 xDrive (141 kWh • 460 kW)"
AUTO_LEAPMOTOR_B05_STD = "🚗 Leapmotor B05 — 56.2 kWh (Standard)"
AUTO_LEAPMOTOR_B05_LR = "🚙 Leapmotor B05 — 67.1 kWh (Long Range)"
AUTO_LEAPMOTOR_B10_STD = "🚙 Leapmotor B10 Standard (56.2 kWh netti • 140 kW)"
AUTO_LEAPMOTOR_B10_LR = "🚙 Leapmotor B10 Long Range (67.1 kWh netti • 168 kW)"
AUTO_PERSONALIZZATA = "⚙️ Altra Auto (Personalizzata)"

AUTO_OPZIONI = [
    AUTO_KIA_ENIRO,
    AUTO_TESLA_MY,
    AUTO_TESLA_M3_LR,
    AUTO_MAZDA_MX30,
    AUTO_BMW_IX40,
    AUTO_BMW_IX3_50,
    AUTO_BMW_IX5_60,
    AUTO_LEAPMOTOR_B05_STD,
    AUTO_LEAPMOTOR_B05_LR,
    AUTO_LEAPMOTOR_B10_STD,
    AUTO_LEAPMOTOR_B10_LR,
    AUTO_PERSONALIZZATA,
]

STAGIONI = {
    "❄️ Inverno (+18%)": 1.18,
    "🌤️ Mezza stagione (+5%)": 1.05,
    "☀️ Estate (0%)": 1.00,
}

STILI_GUIDA = {
    "🌿 Guida Dolcissima / Eco": {"autostrada": 0.88, "statale": 0.92},
    "🚗 Guida Regolare": {"autostrada": 1.00, "statale": 1.00},
    "⚡ Guida Brillante / Piede Pesante": {"autostrada": 1.20, "statale": 1.12},
}

OPERATORI_OPZIONI = [
    "🌐 Tutti gli operatori (Consigliato)",
    "⚡ Atlante",
    "🔌 Be Charge / Plenitude",
    "🔌 Enel X",
    "⚡ Ewiva",
    "⚡ Free To X",
    "⚡ Ionity",
    "🏔️ Neogy",
    "🔴 Tesla Supercharger",
    "✍️ Altro (scrivi nome marchio)",
]

OPERATORI_ZONA_OPZIONI = [
    "🌐 Tutti gli operatori",
    "⚡ Atlante",
    "🔌 Be Charge / Plenitude",
    "🔌 Enel X",
    "⚡ Ewiva",
    "⚡ Free To X",
    "⚡ Ionity",
    "🏔️ Neogy",
    "🔴 Tesla Supercharger",
    "✍️ Altro (scrivi nome marchio)",
]

OPERATORI_KEYWORDS = {
    "⚡ Atlante": ("Atlante", ("atlante",)),
    "🔌 Be Charge / Plenitude": ("Be Charge / Plenitude", ("be charge", "becharge", "plenitude")),
    "🔌 Enel X": ("Enel X", ("enel x", "enelx", "enel drive", "enel")),
    "⚡ Ewiva": ("Ewiva", ("ewiva",)),
    "⚡ Free To X": ("Free To X", ("free to x", "freetox", "free-to-x")),
    "⚡ Ionity": ("Ionity", ("ionity",)),
    "🏔️ Neogy": ("Neogy", ("neogy",)),
    "🔴 Tesla Supercharger": ("Tesla Supercharger", ("tesla", "supercharger")),
}

FALLBACK_FOOD_NAME = "Ristorazione vicina"
FALLBACK_MALL_NAME = "Supermercato / Centro Comm."

RETI_PREFERITE = {
    "⚡ Tutte le colonnine veloci (Precedenza Autostrada)": "tutte",
    "🛣️ Solo in Autostrada (Aree di Servizio / Autogrill)": "ads",
    "🔴 Solo Tesla Supercharger": "tesla",
}

STILI_SOSTA = {
    "⚡ Viaggio Veloce / Diretto (solo soste strettamente necessarie)": "veloce",
    "☕ Pausa Caffè / Pranzo in Autogrill (resta in Autostrada)": "caffe",
    "🍽️ Sosta Pranzo Fuori Casello (Centro Comm. / Ristorante)": "pranzo",
}

OPZIONI_PRANZO = {
    "🛣️ Solo Autogrill / Area di Servizio (senza uscire dall'autostrada)": "pranzo_ads",
    "🛒 Anche fuori casello (Centro Commerciale o Ristorante)": "pranzo_anche_fuori",
}

PREFERENZE_STRADE = {
    "🛣️ Più veloce (con Autostrada)": "veloce_auto",
    "🚗 Evita Autostrade (Solo Statali / Extraurbano)": "no_auto",
    "⚖️ Confronta Autostrada vs Senza Autostrada": "confronta",
}

FATTORE_CONSUMO_EXTRAURBANO = 0.84  # Riduzione consumo -16% su statali/extraurbano rispetto all'autostrada
COSTO_PEDAGGIO_KM = 0.078  # Stima media pedaggio autostradale italiano (€/km)
TETTO_RICARICA_INTERMEDIA = 80  # Soglia base ricarica veloce (prima del rallentamento 80%-100%)
TETTO_SOSTA_UNICA_OTTIMALE = 90  # Massimo assoluto per sosta unica quando conviene sul tempo totale
MINUTI_FISSI_SOSTA_EXTRA = 7  # Minuti fissi di manovra/ingresso/uscita per una seconda sosta
SOGLIA_COLONNINA_LENTA_KW = 65.0  # Potenza massima (kW) per considerare una colonnina lenta nel Rabbocco Ponte
SOGLIA_AUTO_VELOCE_KW = 100.0  # Picco DC minimo (kW) dell'auto per attivare il Rabbocco Ponte verso HPC
SOGLIA_HPC_VELOCE_KW = 150.0  # Potenza minima (kW) della stazione Ultra-Fast/HPC successiva
SOGLIA_ARRIVO_HPC_PONTE = 10.0  # Batteria residua minima (%) richiesta all'arrivo sulla stazione HPC
RISPARMIO_MINIMO_PONTE_MIN = 5  # Risparmio netto minimo (minuti) richiesto per preferire Rabbocco Ponte + HPC
SOGLIA_EMERGENZA_JOLLY = 4.0  # Soglia ordinaria di sicurezza (%) per Piano B Livello 1 e Livello 2
SOGLIA_EMERGENZA_ESTREMA_JOLLY = 1.0  # Soglia estrema (%) per Piano B Livello 3 con avviso esplicito sui rischi
KEYWORDS_EBIKE = ("biciclett", "bici", "ebike", "e-bike", "bike")
UTENTI_TESLA = ("fabio", "elizabeth", "alessia", "federico")
UTENTI_FALLBACK_LOCALE = {
    "Fabio": "ev2026",
    "Alessia": "ev2026",
    "Elizabeth": "ev2026",
    "Luciano": "ev2026",
    "Chiara": "ev2026",
    "Arianna": "ev2026",
    "Stefano": "ev2026",
    "Massimo": "ev2026",
    "Federico": "ev2026",
}


def opzioni_auto_per_utente(current_user):
    """Restituisce le auto dedicate all'utente collegato (confronto case-insensitive)."""
    utente_low = str(current_user or "").strip().lower()
    if utente_low in ("fabio", "alessia", "elizabeth"):
        return [AUTO_KIA_ENIRO, AUTO_TESLA_MY, AUTO_PERSONALIZZATA]
    if utente_low == "luciano":
        return [AUTO_MAZDA_MX30, AUTO_PERSONALIZZATA]
    if utente_low == "chiara":
        return [AUTO_BMW_IX40, AUTO_PERSONALIZZATA]
    if utente_low == "arianna":
        return [AUTO_LEAPMOTOR_B05_STD, AUTO_LEAPMOTOR_B05_LR, AUTO_PERSONALIZZATA]
    if utente_low == "stefano":
        return [AUTO_BMW_IX3_50, AUTO_BMW_IX5_60, AUTO_PERSONALIZZATA]
    if utente_low == "massimo":
        return [AUTO_LEAPMOTOR_B10_STD, AUTO_LEAPMOTOR_B10_LR, AUTO_PERSONALIZZATA]
    if utente_low == "federico":
        return [AUTO_TESLA_M3_LR, AUTO_PERSONALIZZATA]
    return list(AUTO_OPZIONI)


def carica_utenti_autorizzati():
    """
    Carica gli utenti autorizzati da st.secrets['users'].
    In assenza di secrets configurati (esecuzione in ambiente locale), utilizza
    il dizionario di fallback locale e segnala la modalità test locale.
    """
    try:
        if "users" in st.secrets:
            raw_users = dict(st.secrets["users"])
            utenti = {
                str(k).strip(): str(v)
                for k, v in raw_users.items()
                if str(k).strip()
            }
            if utenti:
                return utenti, False
    except Exception:
        pass
    return dict(UTENTI_FALLBACK_LOCALE), True


def inietta_stile_dark():
    """Applica il tema Mobile-First Dark Card UI ispirato alle card finanziarie scure."""
    st.markdown(
        """
<style>
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #131722 !important;
    color: #E6EDF3 !important;
    overflow-y: auto !important;
    height: auto !important;
}
[data-testid="stHeader"] {
    background-color: #131722 !important;
}
.block-container {
    padding-top: 2.5rem !important;
    padding-bottom: 4rem !important;
    max-width: 760px !important;
}
h1, h2, h3, h4, p, label, span, .stMarkdown {
    color: #E6EDF3;
}
/* Tab principali compatti stile dark */
[data-testid="stTabs"] [role="tablist"] {
    background-color: #1E222D;
    border: 1px solid #2A2E39;
    border-radius: 12px;
    padding: 4px;
    gap: 6px;
    margin-bottom: 12px;
}
[data-testid="stTabs"] button[role="tab"] {
    flex: 1;
    border-radius: 9px !important;
    padding: 0.55rem 0.75rem !important;
    color: #94A3B8 !important;
    font-weight: 700 !important;
    font-size: 0.92rem !important;
    border: none !important;
    background: transparent !important;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    background: rgba(16, 185, 129, 0.18) !important;
    color: #34D399 !important;
    border: 1px solid rgba(16, 185, 129, 0.35) !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    display: none !important;
}
/* Input, selectbox e campi numerici su sfondo scuro ad alto contrasto (compatibile anche con tema chiaro Cloud) */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stSelectbox > div > div {
    background-color: #131722 !important;
    color: #F8FAFC !important;
    border: 1px solid #2F3545 !important;
    border-radius: 10px !important;
}
div[data-baseweb="base-input"] {
    background-color: #131722 !important;
    border-color: #2F3545 !important;
}
div[data-baseweb="select"] > div,
div[data-baseweb="select"] span,
div[data-baseweb="input"] input {
    background-color: #131722 !important;
    color: #F8FAFC !important;
    -webkit-text-fill-color: #F8FAFC !important;
}
div[data-baseweb="input"] input::placeholder {
    color: #94A3B8 !important;
    -webkit-text-fill-color: #94A3B8 !important;
    opacity: 1 !important;
}
/* Tendina aperta dei menu selectbox scura con testo chiaro */
ul[role="listbox"],
ul[role="listbox"] li,
div[data-baseweb="popover"] > div {
    background-color: #1E222D !important;
    color: #F8FAFC !important;
}
ul[role="listbox"] li:hover,
ul[role="listbox"] li[aria-selected="true"] {
    background-color: #2D3548 !important;
    color: #10B981 !important;
}
/* Occhio mostra/nascondi password, pulsanti +/- number_input e icone input scuri */
[data-baseweb="input"] button,
[data-baseweb="base-input"] button,
[data-testid="stNumberInput"] button {
    background-color: #131722 !important;
    color: #F8FAFC !important;
    border: none !important;
}
[data-testid="stNumberInput"] button:hover {
    background-color: #2D3548 !important;
    color: #10B981 !important;
}
div[data-baseweb="select"] svg,
[data-baseweb="input"] svg,
[data-baseweb="base-input"] svg {
    fill: #94A3B8 !important;
    color: #94A3B8 !important;
}
/* Riquadri di errore, avviso e info (stAlert) scuri e nitidi */
[data-testid="stAlert"] {
    background-color: #1E293B !important;
    color: #F8FAFC !important;
    border-left: 4px solid #EF4444 !important;
}
[data-testid="stAlert"] p,
[data-testid="stAlert"] span {
    color: #F8FAFC !important;
}
/* Pulsante secondario scuro (es. Esci) */
div.stButton > button[kind="secondary"] {
    background-color: #1E222D !important;
    color: #E2E8F0 !important;
    border: 1px solid #2F3545 !important;
    border-radius: 10px !important;
}
div.stButton > button[kind="secondary"]:hover {
    background-color: #2D3548 !important;
    color: #FFFFFF !important;
}
/* Pulsante primario ad alto contrasto (inclusi form di accesso) */
div.stButton > button[kind="primary"],
div[data-testid="stFormSubmitButton"] > button[kind="primary"] {
    background: linear-gradient(90deg, #10B981 0%, #059669 100%) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 12px !important;
    padding: 0.75rem 1.2rem !important;
    font-size: 1.03rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.2px !important;
    box-shadow: 0 4px 14px rgba(16, 185, 129, 0.28) !important;
}
div.stButton > button[kind="primary"]:hover,
div[data-testid="stFormSubmitButton"] > button[kind="primary"]:hover {
    filter: brightness(1.08);
}
/* Form scuro stile Dark Card (Login) */
[data-testid="stForm"] {
    background-color: #1E222D !important;
    border: 1px solid #2A2E39 !important;
    border-left: 4px solid #10B981 !important;
    border-radius: 12px !important;
    padding: 18px 20px !important;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.28) !important;
}
/* Ancora scroll risultati con margine superiore per evitare tagli dell'header */
#ancora-risultati {
    scroll-margin-top: 2.5rem;
}
/* Expander stile dark card (senza overflow:hidden per non bloccare scroll e tendine su Cloud/mobile) */
[data-testid="stExpander"] {
    background-color: #1E222D !important;
    border: 1px solid #2A2E39 !important;
    border-radius: 12px !important;
    margin-bottom: 0.85rem !important;
    overflow: visible !important;
}
[data-testid="stExpander"] summary {
    font-weight: 700 !important;
    font-size: 0.96rem !important;
    color: #F8FAFC !important;
    padding: 0.8rem 1rem !important;
}
/* Box degli step guidati */
.ev-step-header {
    background-color: #131722;
    border: 1px solid #2A2E39;
    border-left: 4px solid #3B82F6;
    border-radius: 10px;
    padding: 9px 13px;
    margin: 12px 0 10px 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.ev-step-title {
    font-size: 0.94rem;
    font-weight: 700;
    color: #F8FAFC;
}
.ev-step-tag {
    font-size: 0.74rem;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 999px;
    background: rgba(59, 130, 246, 0.16);
    color: #60A5FA;
}
/* Dark Card principale stile finanziario */
.ev-card {
    background-color: #1E222D;
    border: 1px solid #2A2E39;
    border-left: 4px solid #10B981;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 12px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.22);
}
.ev-card-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 10px;
    flex-wrap: wrap;
}
.ev-card-title {
    font-size: 1.01rem;
    font-weight: 700;
    color: #F8FAFC;
    line-height: 1.3;
}
.ev-card-sub {
    font-size: 0.82rem;
    color: #94A3B8;
    margin-top: 2px;
}
.ev-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    justify-content: flex-end;
}
.ev-badge {
    font-size: 0.74rem;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 999px;
    white-space: nowrap;
}
.ev-b-green { background: rgba(16, 185, 129, 0.16); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.3); }
.ev-b-blue { background: rgba(59, 130, 246, 0.16); color: #60A5FA; border: 1px solid rgba(59, 130, 246, 0.3); }
.ev-b-amber { background: rgba(245, 158, 11, 0.16); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.3); }
.ev-b-purple { background: rgba(168, 85, 247, 0.16); color: #C084FC; border: 1px solid rgba(168, 85, 247, 0.3); }
.ev-b-red { background: rgba(239, 68, 68, 0.16); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.3); }
.ev-b-slate { background: rgba(148, 163, 184, 0.15); color: #CBD5E1; border: 1px solid rgba(148, 163, 184, 0.25); }
.ev-badge-link {
    text-decoration: none !important;
    display: inline-flex;
    align-items: center;
    cursor: pointer;
    transition: filter 0.15s ease;
}
.ev-badge-link:hover {
    filter: brightness(1.2);
    text-decoration: underline !important;
}
.ev-kv {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    padding: 6px 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    font-size: 0.9rem;
}
.ev-kv:last-of-type {
    border-bottom: none;
}
.ev-k {
    color: #94A3B8;
}
.ev-v {
    color: #F8FAFC;
    font-weight: 600;
    text-align: right;
}
.ev-warn-box {
    background: rgba(245, 158, 11, 0.12);
    border: 1px solid rgba(245, 158, 11, 0.35);
    color: #FCD34D;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 0.84rem;
    margin-top: 8px;
}
.ev-info-box {
    background: rgba(59, 130, 246, 0.12);
    border: 1px solid rgba(59, 130, 246, 0.35);
    color: #93C5FD;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 0.84rem;
    margin-top: 8px;
}
.ev-maps-btn {
    display: block;
    width: 100%;
    text-align: center;
    background: #2563EB;
    color: #FFFFFF !important;
    text-decoration: none !important;
    font-weight: 700;
    font-size: 0.94rem;
    padding: 10px 14px;
    border-radius: 9px;
    margin-top: 10px;
    box-sizing: border-box;
}
.ev-maps-btn:hover {
    background: #1D4ED8;
}
.ev-gmaps-cta {
    display: block;
    width: 100%;
    text-align: center;
    background: linear-gradient(90deg, #10B981 0%, #2563EB 100%);
    color: #FFFFFF !important;
    text-decoration: none !important;
    font-weight: 800;
    font-size: 0.98rem;
    padding: 13px 16px;
    border-radius: 11px;
    margin: 10px 0 12px 0;
    box-sizing: border-box;
    box-shadow: 0 4px 14px rgba(16, 185, 129, 0.28);
    transition: filter 0.15s ease;
}
.ev-gmaps-cta:hover {
    filter: brightness(1.1);
}
.ev-dir-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin: 8px 0 12px 0;
}
.ev-dir-grid .ev-gmaps-cta {
    margin: 0;
    font-size: 0.9rem;
    padding: 12px 10px;
}
.ev-hero {
    background: linear-gradient(135deg, #1E222D 0%, #192030 100%);
    border: 1px solid #2A2E39;
    border-left: 4px solid #10B981;
    border-radius: 14px;
    padding: 14px 18px;
    margin-bottom: 12px;
}
.ev-hero-title {
    font-size: 1.4rem;
    font-weight: 800;
    color: #F8FAFC;
    margin: 0;
}
.ev-hero-author {
    font-size: 0.88rem;
    font-weight: 700;
    color: #34D399;
    letter-spacing: 0.3px;
    margin-top: 2px;
}
.ev-hero-author-inline {
    display: inline-block;
    font-size: 0.84rem;
    font-weight: 700;
    color: #34D399;
    letter-spacing: 0.3px;
    margin-left: 6px;
    vertical-align: middle;
}
.ev-hero-sub {
    font-size: 0.86rem;
    color: #94A3B8;
    margin-top: 4px;
}
.ev-kpi-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin: 10px 0 14px 0;
}
.ev-kpi {
    background: #1E222D;
    border: 1px solid #2A2E39;
    border-radius: 10px;
    padding: 10px 12px;
}
.ev-kpi-label {
    font-size: 0.74rem;
    color: #94A3B8;
}
.ev-kpi-val {
    font-size: 1.04rem;
    font-weight: 700;
    color: #F8FAFC;
    margin-top: 2px;
}
.ev-kpi-sub {
    font-size: 0.74rem;
    color: #94A3B8;
    margin-top: 2px;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def ora_corrente_italia_arrotondata():
    """Restituisce l'orario corrente italiano (Europe/Rome) arrotondato ai 5 minuti."""
    try:
        now_it = datetime.now(ZoneInfo("Europe/Rome")) if ZoneInfo is not None else datetime.now()
    except Exception:
        now_it = datetime.now()
    min_tot = now_it.hour * 60 + now_it.minute
    min_arr = ((min_tot + 2) // 5) * 5
    ore = (min_arr // 60) % 24
    minuti = min_arr % 60
    return time(ore, minuti)


def formatta_ora_min(minuti_da_mezzanotte):
    """Formatta i minuti dalla mezzanotte in HH:MM (aggiungendo +Ng se si supera la mezzanotte)."""
    tot_m = max(0, int(round(minuti_da_mezzanotte)))
    giorni, resto = divmod(tot_m, 1440)
    hh, mm = divmod(resto, 60)
    if giorni > 0:
        return f"{hh:02d}:{mm:02d} (+{giorni}g)"
    return f"{hh:02d}:{mm:02d}"


def formatta_euro(importo):
    """Formatta un importo in euro secondo lo stile italiano con virgola (es. 8,60 €)."""
    return f"{max(0.0, float(importo)):.2f}".replace(".", ",") + " €"


def e_colonnina_ebike(titolo):
    """Restituisce True se il nome identifica una colonnina/presa per biciclette o e-bike."""
    t_low = str(titolo or "").lower()
    return any(kw in t_low for kw in KEYWORDS_EBIKE)


def stagione_predefinita():
    """Determina la stagione iniziale in base al mese corrente."""
    mese = date.today().month
    if mese in (11, 12, 1, 2):
        return "❄️ Inverno (+18%)"
    if mese in (6, 7, 8):
        return "☀️ Estate (0%)"
    return "🌤️ Mezza stagione (+5%)"


def formatta_durata(minuti_totali):
    """Converte i minuti interi nel formato compatto Xh YYm (o YYm se sotto 1 ora)."""
    minuti = max(0, int(round(minuti_totali)))
    ore, resto = divmod(minuti, 60)
    if ore > 0:
        return f"{ore}h {resto:02d}m"
    return f"{resto}m"


def formatta_potenza(kw, is_dc, stalls=1):
    """Restituisce un'etichetta di potenza sempre esplicita e leggibile."""
    kw_int = int(kw or 0)
    if kw_int >= 150:
        base = f"⚡ {kw_int} kW HPC"
    elif kw_int >= 50:
        base = f"⚡ {kw_int} kW Fast DC"
    elif kw_int > 0:
        base = f"⚡ {kw_int} kW DC" if is_dc else f"🔌 {kw_int} kW AC"
    else:
        if is_dc:
            if stalls > 1:
                return f"⚡ Fast DC (~50-75 kW • {stalls} stalli)"
            return "⚡ Fast DC (~50-75 kW)"
        base = "🔌 Quick AC"
    if stalls > 1:
        return f"{base} ({stalls} stalli)"
    return base


def stima_kw_effettivi(kw, is_dc, titolo, in_ads):
    """Stima la potenza erogabile in kW quando il tag OSM non riporta il valore esatto."""
    if kw and kw > 0:
        return float(kw)
    t_low = titolo.lower()
    if any(b in t_low for b in BRAND_HPC_NOTI):
        return 250.0
    if is_dc:
        return 150.0 if in_ads else 75.0
    return 22.0


def classifica_fascia_potenza(item):
    """
    Classifica una colonnina in quattro fasce di velocità:
      - 'hyper': Hyper-Fast (>= 400 kW)
      - 'hpc': Ultra-Fast / HPC (150 – 399 kW, inclusi brand HPC noti senza tag kW su OSM)
      - 'fast': Fast DC (50 – 149 kW o DC senza tag kW)
      - 'lenta': Lente / AC (< 50 kW)
    """
    kw_dich = float(item.get("kw") or 0.0)
    if kw_dich > 0.0:
        if kw_dich >= 400.0:
            return "hyper"
        if kw_dich >= 150.0:
            return "hpc"
        if kw_dich >= 50.0:
            return "fast"
        return "lenta"
    eff_kw = float(
        item.get("eff_kw")
        or stima_kw_effettivi(
            0.0,
            bool(item.get("is_dc")),
            str(item.get("Stazione") or ""),
            bool(item.get("in_ads")),
        )
    )
    if eff_kw >= 400.0:
        return "hyper"
    if eff_kw >= 150.0:
        return "hpc"
    if bool(item.get("is_dc")) or eff_kw >= 50.0:
        return "fast"
    return "lenta"


def potenza_curva_auto(auto_cfg, soc_pct):
    """Restituisce la potenza massima di ricarica DC dell'auto alla percentuale di batteria indicata."""
    for soglia, kw in auto_cfg["curva_dc"]:
        if soc_pct <= soglia:
            return float(kw)
    return float(auto_cfg["curva_dc"][-1][1])


def potenza_picco_auto(auto_cfg):
    """Restituisce la potenza massima di picco DC (kW) supportata dall'auto selezionata."""
    curva = auto_cfg.get("curva_dc") or []
    if not curva:
        return 0.0
    return max(float(kw) for _, kw in curva)


def calcola_minuti_ricarica(auto_cfg, soc_inizio, soc_fine, station_kw):
    """Calcola i minuti reali di ricarica integrando la curva DC dell'auto passo-passo."""
    if soc_fine <= soc_inizio + 0.1:
        return 0
    bat_kwh = float(auto_cfg["bat_kwh"])
    st_kw = max(11.0, float(station_kw))
    minuti = 0.0
    p = float(soc_inizio)
    fine = float(soc_fine)
    passo = 1.0

    while p < fine:
        dp = min(passo, fine - p)
        punto_medio = p + dp / 2.0
        auto_kw = potenza_curva_auto(auto_cfg, punto_medio)
        eff_kw = max(7.0, min(auto_kw, st_kw))
        kwh_step = bat_kwh * (dp / 100.0)
        minuti += (kwh_step / eff_kw) * 60.0
        p += dp

    return max(1, int(math.ceil(minuti)))


def calcola_soc_per_minuti(auto_cfg, soc_inizio, minuti_target, station_kw, max_soc=90.0):
    """Calcola la percentuale raggiungibile in un dato numero di minuti di sosta."""
    bat_kwh = float(auto_cfg["bat_kwh"])
    st_kw = max(11.0, float(station_kw))
    p = float(soc_inizio)
    rimasti = float(minuti_target)

    while rimasti > 0 and p < max_soc:
        dp = min(1.0, max_soc - p)
        auto_kw = potenza_curva_auto(auto_cfg, p + dp / 2.0)
        eff_kw = max(7.0, min(auto_kw, st_kw))
        min_step = ((bat_kwh * (dp / 100.0)) / eff_kw) * 60.0
        if min_step <= rimasti:
            p += dp
            rimasti -= min_step
        else:
            p += dp * (rimasti / min_step)
            break

    return min(int(math.ceil(p)), int(max_soc))


def costruisci_profilo_auto(scelta_auto, custom_bat, custom_cons, custom_dc):
    """Restituisce dizionario configurazione auto con consumi calibrati e curva DC reale."""
    if "Kia e-Niro" in scelta_auto:
        return {
            "nome": "Kia e-Niro Evolution 2020",
            "bat_kwh": 64.0,
            "cons_base": 15.2,
            "curva_dc": [(55, 77), (77, 55), (100, 28)],
            "soc_veloce": 77,
            "is_tesla_car": False,
        }
    if "Model Y" in scelta_auto:
        return {
            "nome": "Tesla Model Y Performance 2023",
            "bat_kwh": 75.0,
            "cons_base": 19.0,
            "curva_dc": [(35, 250), (60, 140), (80, 75), (100, 40)],
            "soc_veloce": 80,
            "is_tesla_car": True,
        }
    if "Model 3" in scelta_auto:
        return {
            "nome": "Tesla Model 3 Long Range RWD",
            "bat_kwh": 79.0,
            "cons_base": 16.2,
            "curva_dc": [(45, 240), (80, 155), (100, 65)],
            "soc_veloce": 80,
            "is_tesla_car": True,
        }
    if "Mazda MX-30" in scelta_auto:
        return {
            "nome": "Mazda MX-30 Versione Lancio",
            "bat_kwh": 30.0,
            "cons_base": 19.5,
            "curva_dc": [(80, 38), (100, 18)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    if "BMW iX xDrive40" in scelta_auto:
        return {
            "nome": "BMW iX xDrive40 08/2023",
            "bat_kwh": 71.0,
            "cons_base": 21.5,
            "curva_dc": [(45, 150), (80, 105), (100, 55)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    if "BMW iX3 50 xDrive" in scelta_auto:
        return {
            "nome": "BMW iX3 50 xDrive",
            "bat_kwh": 108.7,
            "cons_base": 18.5,
            "curva_dc": [(50, 380), (80, 240), (100, 90)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    if "BMW iX5 60 xDrive" in scelta_auto:
        return {
            "nome": "BMW iX5 60 xDrive",
            "bat_kwh": 141.0,
            "cons_base": 21.5,
            "curva_dc": [(50, 430), (80, 270), (100, 100)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    if "Leapmotor B05" in scelta_auto and "56.2" in scelta_auto:
        return {
            "nome": "Leapmotor B05 56.2 kWh (Standard)",
            "bat_kwh": 55.0,
            "cons_base": 16.5,
            "curva_dc": [(50, 150), (80, 110), (100, 45)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    if "Leapmotor B05" in scelta_auto and "67.1" in scelta_auto:
        return {
            "nome": "Leapmotor B05 67.1 kWh (Long Range)",
            "bat_kwh": 65.0,
            "cons_base": 16.8,
            "curva_dc": [(50, 170), (80, 120), (100, 45)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    if "Leapmotor B10" in scelta_auto and "56.2" in scelta_auto:
        return {
            "nome": "Leapmotor B10 Standard (56.2 kWh)",
            "bat_kwh": 56.2,
            "cons_base": 21.0,
            "curva_dc": [(55, 135), (80, 95), (100, 45)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    if "Leapmotor B10" in scelta_auto and "67.1" in scelta_auto:
        return {
            "nome": "Leapmotor B10 Long Range (67.1 kWh)",
            "bat_kwh": 67.1,
            "cons_base": 21.5,
            "curva_dc": [(55, 160), (80, 110), (100, 50)],
            "soc_veloce": 80,
            "is_tesla_car": False,
        }
    dc_max = max(22.0, float(custom_dc))
    return {
        "nome": f"Auto Personalizzata ({custom_bat:.0f} kWh)",
        "bat_kwh": float(custom_bat),
        "cons_base": float(custom_cons),
        "curva_dc": [
            (45, dc_max),
            (75, max(22.0, round(dc_max * 0.68))),
            (100, max(15.0, round(dc_max * 0.35))),
        ],
        "soc_veloce": 80,
        "is_tesla_car": False,
    }


def _normalizza_poi(valore, fallback_nome):
    """Normalizza un campo food_name/mall_name gestendo sia stringhe che vecchi flag 0/1."""
    if isinstance(valore, str):
        testo = valore.strip()
        return (1, testo) if testo else (0, "")
    if isinstance(valore, (int, float, bool)) and int(valore) == 1:
        return 1, fallback_nome
    return 0, ""


def _scegli_nome_poi_migliore(nome_a, nome_b, fallback_nome):
    """Durante il raggruppamento stalli, preferisce sempre un nome reale rispetto a stringa vuota o fallback."""
    if nome_a and nome_a != fallback_nome:
        return nome_a
    if nome_b and nome_b != fallback_nome:
        return nome_b
    return nome_a or nome_b


@st.cache_resource
def load_chargers_db(db_mtime=0.0, filtro_ver=3):
    """
    Carica il database locale colonnine_it.json gestendo il formato a 8 campi
    [lat, lon, title, kw, is_dc, food_name, mall_name, in_ads] e la retrocompatibilità
    con record a 7 o 5 campi, escludendo sempre le prese per biciclette/e-bike e
    correggendo le stazioni Free To X senza tag kW (o <= 75 kW) a 300 kW HPC.
    """
    _ = (db_mtime, filtro_ver)
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colonnine_it.json")
    if not os.path.exists(db_path):
        return []
    with open(db_path, "r", encoding="utf-8") as f:
        grezzi = json.load(f)

    normalizzati = []
    for riga in grezzi:
        if len(riga) >= 8:
            lat, lon, titolo, kw, is_dc, raw_food, raw_mall, raw_in_ads = riga[:8]
            has_food, food_name = _normalizza_poi(raw_food, FALLBACK_FOOD_NAME)
            has_mall, mall_name = _normalizza_poi(raw_mall, FALLBACK_MALL_NAME)
            in_ads_db = 1 if int(raw_in_ads or 0) == 1 else 0
        elif len(riga) >= 7:
            lat, lon, titolo, kw, is_dc, raw_food, raw_mall = riga[:7]
            has_food, food_name = _normalizza_poi(raw_food, FALLBACK_FOOD_NAME)
            has_mall, mall_name = _normalizza_poi(raw_mall, FALLBACK_MALL_NAME)
            in_ads_db = 0
        elif len(riga) >= 5:
            lat, lon, titolo, kw, is_dc = riga[:5]
            has_food, food_name, has_mall, mall_name, in_ads_db = 0, "", 0, "", 0
        else:
            continue
        titolo_str = str(titolo)
        if e_colonnina_ebike(titolo_str):
            continue
        kw_int = int(kw or 0)
        is_dc_int = int(is_dc or 0)
        t_low_db = titolo_str.lower()
        # Tutte le stazioni Free To X autostradali sono HPC da 300-400 kW
        if ("free to x" in t_low_db or "freetox" in t_low_db) and kw_int <= 75:
            kw_int = 300
            is_dc_int = 1
        normalizzati.append(
            (
                float(lat),
                float(lon),
                titolo_str,
                kw_int,
                is_dc_int,
                has_food,
                has_mall,
                food_name,
                mall_name,
                in_ads_db,
            )
        )
    return normalizzati


def haversine_km(lat1, lon1, lat2, lon2):
    """Calcola la distanza geodetica in km con la formula di Haversine."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def costruisci_tracciato(coords):
    """Costruisce la sequenza (lat, lon, km_cumulati) e la lunghezza totale in km."""
    it = iter(coords)
    first_lat, first_lon = next(it)
    pts = [(first_lat, first_lon, 0.0)]
    cum = 0.0
    prev_lat, prev_lon = first_lat, first_lon
    for lat, lon in it:
        cum += haversine_km(prev_lat, prev_lon, lat, lon)
        pts.append((lat, lon, cum))
        prev_lat, prev_lon = lat, lon
    return pts, cum


def project(lat, lon, pts):
    """
    Calcola la distanza minima punto-segmento (km) e il Km progressivo interpolato
    proiettando ortogonalmente (lat, lon) su ogni segmento consecutivo della polilinea OSRM
    entro un bounding box locale, evitando errori sui lunghi rettilinei autostradali.
    """
    n_pts = len(pts)
    if n_pts == 0:
        return 9999.0, 0.0
    if n_pts == 1:
        return haversine_km(lat, lon, pts[0][0], pts[0][1]), pts[0][2]

    best_d = 9999.0
    best_km = 0.0
    cos_lat = math.cos(math.radians(lat))
    px = lon * cos_lat
    py = lat

    # Margine bounding box locale (~3.3 km in latitudine e ~3.1 km in longitudine)
    pad_lat = 0.03
    pad_lon = 0.04

    for i in range(n_pts - 1):
        lat1, lon1, km1 = pts[i]
        lat2, lon2, km2 = pts[i + 1]

        # Pre-filtro rapido su bounding box locale del segmento
        if lat1 <= lat2:
            if lat < lat1 - pad_lat or lat > lat2 + pad_lat:
                continue
        else:
            if lat < lat2 - pad_lat or lat > lat1 + pad_lat:
                continue

        if lon1 <= lon2:
            if lon < lon1 - pad_lon or lon > lon2 + pad_lon:
                continue
        else:
            if lon < lon2 - pad_lon or lon > lon1 + pad_lon:
                continue

        # Proiezione ortogonale in coordinate equirettangolari locali sul segmento (lat1,lon1)->(lat2,lon2)
        ax = lon1 * cos_lat
        ay = lat1
        dx = lon2 * cos_lat - ax
        dy = lat2 - ay
        seg_len_sq = dx * dx + dy * dy

        if seg_len_sq > 0.0:
            t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
        else:
            t = 0.0

        proj_lat = lat1 + t * (lat2 - lat1)
        proj_lon = lon1 + t * (lon2 - lon1)
        d = haversine_km(lat, lon, proj_lat, proj_lon)

        if d < best_d:
            best_d = d
            best_km = km1 + t * (km2 - km1)

    return best_d, best_km


TIPI_URBANI_CENTRI = ("city", "town", "village", "suburb")
TIPI_URBANI_COMUNI = ("municipality",)
TIPI_AREA_VASTA = ("county", "state_district", "province", "state", "region", "country")


def _e_query_citta_semplice(query_pulita):
    """Restituisce il nome della città se la query è un nome semplice di località (senza via o civico)."""
    if not query_pulita:
        return None
    senza_paese = re.sub(r",?\s*(italia|italy)\s*$", "", query_pulita, flags=re.IGNORECASE).strip(" ,")
    if not senza_paese or "," in senza_paese:
        return None
    if any(ch.isdigit() for ch in senza_paese):
        return None
    parole = senza_paese.lower().split()
    if not parole or len(parole) > 4:
        return None
    prefissi_strada = (
        "via", "viale", "piazza", "piazzale", "corso", "largo", "vicolo",
        "strada", "lungomare", "lungotevere", "contrada", "localita", "località",
        "stazione", "aeroporto", "uscita", "casello",
    )
    if parole[0] in prefissi_strada:
        return None
    return senza_paese


def _rank_risultato_urbano(loc):
    """
    Assegna un punteggio di priorità (più basso = migliore) per preferire i centri urbani
    (city, town, village, suburb, municipality) rispetto ad aree provinciali/regionali
    (county, state_district, province, state, region).
    """
    raw = getattr(loc, "raw", None) or {}
    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    addr_type = str(raw.get("addresstype") or "").strip().lower()
    osm_type = str(raw.get("type") or props.get("type") or "").strip().lower()
    osm_val = str(props.get("osm_value") or "").strip().lower()

    if addr_type in TIPI_URBANI_CENTRI or (not addr_type and (osm_type in TIPI_URBANI_CENTRI or osm_val in TIPI_URBANI_CENTRI)):
        rank = 0
    elif addr_type in TIPI_URBANI_COMUNI or (not addr_type and (osm_type in TIPI_URBANI_COMUNI or osm_val in TIPI_URBANI_COMUNI)):
        rank = 1
    elif addr_type in TIPI_AREA_VASTA or osm_type in TIPI_AREA_VASTA or osm_val in TIPI_AREA_VASTA:
        rank = 3
    else:
        rank = 2

    try:
        importanza = float(raw.get("importance") or 0.0)
    except Exception:
        importanza = 0.0
    return rank, -importanza


def _scegli_miglior_loc_urbano(risultati):
    """Seleziona il miglior risultato da una lista geopy dando precedenza al centro urbano."""
    if not risultati:
        return None
    if not isinstance(risultati, list):
        risultati = [risultati]
    migliore = min(enumerate(risultati), key=lambda coppia: (_rank_risultato_urbano(coppia[1]), coppia[0]))
    return migliore[1]


def _formatta_loc_geopy(loc, fallback_query):
    """Estrae (lat, lon, indirizzo_breve) da un risultato Location di geopy."""
    raw_addr = getattr(loc, "address", None) or fallback_query
    parti = [p.strip() for p in str(raw_addr).split(",") if p.strip()]
    indirizzo_breve = ", ".join(parti[:3]) if parti else str(raw_addr)
    return float(loc.latitude), float(loc.longitude), indirizzo_breve


@st.cache_data(ttl=86400)
def geocode_zona(q):
    """
    Geocodifica una località italiana per la Ricerca Colonnine in Zona centrando il punto
    sul centro urbano della città (city, town, village, suburb, municipality) anziché
    sul centro geometrico della provincia (county, state_district).
    """
    query_pulita = str(q).strip()
    if not query_pulita:
        return None

    nom = Nominatim(user_agent="ev_roadbook_it_2026", timeout=10)
    nome_citta = _e_query_citta_semplice(query_pulita)
    fallback_area_vasta = None

    # 1) Se è un nome semplice di città (es. "Bolzano"), interroga Nominatim con parametro strutturato 'city'
    if nome_citta:
        try:
            res_city = nom.geocode(
                {"city": nome_citta, "country": "Italia"},
                exactly_one=False,
                limit=6,
                addressdetails=True,
            )
            best_city = _scegli_miglior_loc_urbano(res_city)
            if best_city is not None:
                rank_c, _ = _rank_risultato_urbano(best_city)
                if rank_c <= 2:
                    return _formatta_loc_geopy(best_city, query_pulita)
                fallback_area_vasta = best_city
        except Exception:
            pass

    q_low = query_pulita.lower()
    tentativi = []
    if "italia" not in q_low and "italy" not in q_low:
        tentativi.append(f"{query_pulita}, Italia")
    tentativi.append(query_pulita)

    # 2) Ricerca Nominatim multi-risultato con filtro prioritario per centro urbano
    for query in tentativi:
        try:
            res_nom = nom.geocode(query, exactly_one=False, limit=8, addressdetails=True)
            best_nom = _scegli_miglior_loc_urbano(res_nom)
            if best_nom is not None:
                rank_n, _ = _rank_risultato_urbano(best_nom)
                if rank_n <= 2:
                    return _formatta_loc_geopy(best_nom, query)
                if fallback_area_vasta is None:
                    fallback_area_vasta = best_nom
        except Exception:
            pass

    # 3) Fallback Photon multi-risultato con lo stesso filtro urbano
    ph = Photon(user_agent="ev_roadbook_it_2026", timeout=10)
    for query in tentativi:
        try:
            res_ph = ph.geocode(query, exactly_one=False, limit=6)
            best_ph = _scegli_miglior_loc_urbano(res_ph)
            if best_ph is not None:
                rank_p, _ = _rank_risultato_urbano(best_ph)
                if rank_p <= 2:
                    return _formatta_loc_geopy(best_ph, query)
                if fallback_area_vasta is None:
                    fallback_area_vasta = best_ph
        except Exception:
            pass

    if fallback_area_vasta is not None:
        return _formatta_loc_geopy(fallback_area_vasta, query_pulita)
    return None


@st.cache_data(ttl=86400)
def geocode(q):
    """
    Geocodifica una località italiana preferendo il centro urbano della città rispetto
    al baricentro provinciale, con fallback su Photon/Nominatim.
    Restituisce (lat, lon, indirizzo) oppure None.
    """
    return geocode_zona(q)


def url_gmaps_colonnine_indirizzo(indirizzo_cercato):
    """Genera l'URL Google Maps con query testuale codificata sull'indirizzo cercato."""
    query = quote_plus(f"colonnine ricarica elettrica vicino {str(indirizzo_cercato).strip()}")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def url_gmaps_stazione(nome_colonnina, lat, lon):
    """
    Genera l'URL ufficiale Google Maps puntando sempre alle coordinate GPS esatte della colonnina,
    così il navigatore arriva al metro davanti agli stalli senza omonimie di uffici o marchi.
    """
    _ = nome_colonnina
    return f"https://www.google.com/maps/search/?api=1&query={float(lat):.5f},{float(lon):.5f}"


def url_gmaps_viaggio_multi_tappa(origine, destinazione, soste_tratta):
    """Genera l'URL ufficiale Google Maps Directions multi-tappa con tutte le soste come waypoints."""
    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(str(origine).strip())}"
        f"&destination={quote_plus(str(destinazione).strip())}"
    )
    if soste_tratta:
        waypoints = "|".join(f"{s['lat']},{s['lon']}" for s in soste_tratta)
        url += f"&waypoints={quote_plus(waypoints, safe='|,')}"
    return url + "&travelmode=driving"


@st.cache_data(ttl=3600)
def ottieni_rotte_osrm(p1, p2):
    """Richiede le rotte stradali a OSRM usando la chiamata standard con alternatives=true."""
    lat1, lon1 = p1
    lat2, lon2 = p2
    url = f"{URL_OSRM_BASE}{lon1},{lat1};{lon2},{lat2}"
    resp = requests.get(
        url,
        params={"overview": "full", "geometries": "polyline", "alternatives": "true"},
        timeout=20,
    )
    resp.raise_for_status()
    dati = resp.json()
    routes = dati.get("routes", [])
    if dati.get("code") != "Ok" or not routes:
        return []
    risultati = []
    for rt in routes:
        risultati.append((polyline.decode(rt["geometry"]), round(rt["duration"] / 60.0)))
    return risultati


def calcola_percorso_osrm(p1, p2, evita_autostrade=False):
    """
    Restituisce (coords, durata_min, stima_locale_statale).
    In modalità 'evita_autostrade', usa routes[1] se OSRM fornisce un'alternativa,
    altrimenti segnala stima_locale_statale=True per il calcolo locale statale/extraurbano.
    """
    routes = ottieni_rotte_osrm(p1, p2)
    if not routes:
        return None
    if evita_autostrade:
        if len(routes) >= 2:
            coords, dur = routes[1]
            return coords, dur, False
        coords, dur = routes[0]
        return coords, dur, True
    coords, dur = routes[0]
    return coords, dur, False


def cerca_colonnine_zona(db, centro_lat, centro_lon, raggio_km=10.0):
    """Estrae e raggruppa tutte le colonnine auto (AC e DC, escludendo e-bike) entro raggio_km."""
    raw_dest = []
    for lat, lon, titolo, kw, is_dc, has_food, has_mall, food_name, mall_name, in_ads_db in db:
        if e_colonnina_ebike(titolo):
            continue
        dist = haversine_km(lat, lon, centro_lat, centro_lon)
        if dist > raggio_km:
            continue
        t_low = titolo.lower()
        is_dest = "destination" in t_low
        is_tesla = ("tesla" in t_low or "supercharger" in t_low) and not is_dest
        is_brand_hpc = any(b in t_low for b in BRAND_HPC_NOTI) and not is_dest
        raw_dest.append(
            {
                "lat": lat,
                "lon": lon,
                "Stazione": titolo,
                "kw": kw,
                "is_dc": int(is_dc or is_tesla or is_brand_hpc or kw >= 50),
                "in_ads": bool(int(in_ads_db or 0) == 1),
                "has_food": has_food,
                "has_mall": has_mall,
                "food_name": food_name,
                "mall_name": mall_name,
                "Distanza": round(dist, 1),
                "Maps": url_gmaps_stazione(titolo, lat, lon),
            }
        )

    raw_dest.sort(key=lambda x: x["Distanza"])
    dest_c = []
    for item in raw_dest:
        unito = False
        for ex in dest_c:
            if haversine_km(item["lat"], item["lon"], ex["lat"], ex["lon"]) <= 0.08:
                ex["stalls"] += 1
                ex["kw"] = max(ex["kw"], item["kw"])
                ex["is_dc"] = max(ex["is_dc"], item["is_dc"])
                ex["in_ads"] = bool(ex["in_ads"] or item["in_ads"])
                ex["has_food"] = max(ex["has_food"], item["has_food"])
                ex["has_mall"] = max(ex["has_mall"], item["has_mall"])
                ex["food_name"] = _scegli_nome_poi_migliore(ex["food_name"], item["food_name"], FALLBACK_FOOD_NAME)
                ex["mall_name"] = _scegli_nome_poi_migliore(ex["mall_name"], item["mall_name"], FALLBACK_MALL_NAME)
                if "Colonnina Ricarica" in ex["Stazione"] and "Colonnina Ricarica" not in item["Stazione"]:
                    ex["Stazione"] = item["Stazione"]
                unito = True
                break
        if not unito:
            item["stalls"] = 1
            dest_c.append(item)

    for d in dest_c:
        d["Potenza"] = formatta_potenza(d["kw"], bool(d["is_dc"]), d["stalls"])
        d["eff_kw"] = stima_kw_effettivi(d["kw"], bool(d["is_dc"]), d["Stazione"], bool(d.get("in_ads")))
        d["Maps"] = url_gmaps_stazione(d["Stazione"], d["lat"], d["lon"])

    return dest_c


def trova_colonnina_piu_vicina(db, centro_lat, centro_lon):
    """Restituisce la singola colonnina auto più vicina nel database locale (escludendo e-bike)."""
    migliore = None
    best_dist = float("inf")
    for lat, lon, titolo, kw, is_dc, has_food, has_mall, food_name, mall_name, _in_ads_db in db:
        if e_colonnina_ebike(titolo):
            continue
        dist = haversine_km(lat, lon, centro_lat, centro_lon)
        if dist < best_dist:
            best_dist = dist
            t_low = titolo.lower()
            is_tesla = ("tesla" in t_low or "supercharger" in t_low) and ("destination" not in t_low)
            dc_flag = int(is_dc or is_tesla or kw >= 50)
            migliore = {
                "lat": lat,
                "lon": lon,
                "Stazione": titolo,
                "kw": kw,
                "is_dc": dc_flag,
                "food_name": food_name,
                "mall_name": mall_name,
                "Potenza": formatta_potenza(kw, bool(dc_flag), 1),
                "Distanza": round(dist, 1),
                "Maps": url_gmaps_stazione(titolo, lat, lon),
            }
    return migliore


def e_nome_area_di_servizio(titolo_low):
    """Restituisce True se il nome della colonnina contiene parole chiave esplicite di Area di Servizio."""
    if any(kw in titolo_low for kw in KEYWORDS_ADS_ESPLICITE):
        return True
    return bool(re.search(r"\bads\b", titolo_low))


def analizza_colonnine_tratta(db, pts, etichetta_tratta, offset_km=0.0, evita_autostrade=False, km_min=3.0):
    """Estrae e raggruppa le colonnine rapide lungo la tratta stradale."""
    raw_route = []
    lats = [p for p, _, _ in pts]
    lons = [p for _, p, _ in pts]
    min_lat, max_lat = min(lats) - 0.05, max(lats) + 0.05
    min_lon, max_lon = min(lons) - 0.05, max(lons) + 0.05

    for lat, lon, titolo, kw, is_dc, has_food, has_mall, food_name, mall_name, in_ads_db in db:
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue

        t_low = titolo.lower()
        food_low = (food_name or "").lower()
        mall_low = (mall_name or "").lower()
        is_dest = "destination" in t_low
        is_bike = e_colonnina_ebike(t_low)
        is_tesla = ("tesla" in t_low or "supercharger" in t_low) and not is_dest
        is_brand_hpc = any(b in t_low for b in BRAND_HPC_NOTI) and not is_dest
        ha_kw_ads_esplicita = e_nome_area_di_servizio(t_low) or e_nome_area_di_servizio(food_low)
        ha_mall_commerciale = bool(mall_name) and (mall_name != FALLBACK_MALL_NAME) and ("autogrill" not in mall_low)

        # Le colonnine in Area di Servizio autostradale con tag/ristoro AdS o HPC sono rapide DC
        is_ads_fast = (in_ads_db == 1) and (kw == 0 or kw >= 50) and (ha_kw_ads_esplicita or is_brand_hpc)
        # Recupero colonnine con kw=0 e is_dc=0 su OSM di grandi operatori italiani
        is_operatore_it_fallback = (
            (kw == 0)
            and (not is_dc)
            and (not is_dest)
            and (not is_bike)
            and any(op in t_low for op in OPERATORI_FAST_IT_NOTI)
        )

        # Esclude colonnine bici, Destination Charger lenti e colonnine AC lente in viaggio
        if is_dest or is_bike or (not is_dc and not is_tesla and not is_brand_hpc and not is_ads_fast and not is_operatore_it_fallback):
            continue

        d_rt, km = project(lat, lon, pts)
        if km < km_min or d_rt > 3.0:
            continue

        # Classificazione AdS rigorosa (nessun fallback basato solo sulla distanza):
        # - priority=1 ("In Autostrada (AdS)") SOLO SE:
        #   1) Nome colonnina o ristoro contiene parole chiave esplicite di Area di Servizio (entro 0.35 km), OPPURE
        #   2) Poligono ufficiale OSM in_ads_db == 1 strettamente adiacente alla carreggiata (<= 0.18 km),
        #      con caricatore rapido DC/HPC e senza centro commerciale/supermercato urbano adiacente
        #      (evita falsi positivi ai caselli come Be Charge / America Graffiti, Extracoop, Iper Tosano, Lidl).
        # - Tutte le altre entro 2.5 km: priority=2 ("Lungo la strada / Fuori casello")
        if evita_autostrade:
            in_ads = False
        elif d_rt <= 0.35 and ha_kw_ads_esplicita:
            in_ads = True
        elif (
            in_ads_db == 1
            and d_rt <= 0.18
            and (is_dc or kw >= 50 or is_brand_hpc)
            and not ha_mall_commerciale
        ):
            in_ads = True
        else:
            in_ads = False

        # Stima prudenziale 50 kW (Fast DC) per grandi operatori fuori autostrada privi di tag kW su OSM
        kw_eff = 50 if (is_operatore_it_fallback and not in_ads and not is_brand_hpc and not is_tesla) else kw

        priority = 1 if in_ads else 2
        if in_ads:
            detour_min = 2
        elif evita_autostrade:
            detour_min = 2 if d_rt <= 0.25 else max(4, int(round(d_rt * 3.0 + 3)))
        else:
            detour_min = max(5, int(round(d_rt * 3.5 + 4)))

        icona_fuori = "🔴" if is_tesla else "⚡"
        nota_pos = (
            "In Autostrada (AdS)"
            if in_ads
            else f"{icona_fuori} Lungo la strada / Fuori casello (~{max(0.1, d_rt):.1f} km)"
        )

        raw_route.append(
            {
                "Km": round(km + offset_km, 1),
                "Tratta": etichetta_tratta,
                "lat": lat,
                "lon": lon,
                "Stazione": titolo,
                "kw": kw_eff,
                "is_dc": 1,
                "is_tesla": is_tesla,
                "in_ads": in_ads,
                "priority": priority,
                "d_rt": round(d_rt, 2),
                "detour_min": detour_min,
                "Note": nota_pos,
                "has_food": has_food,
                "has_mall": has_mall,
                "food_name": food_name,
                "mall_name": mall_name,
                "Maps": url_gmaps_stazione(titolo, lat, lon),
            }
        )

    # Raggruppa stalli della stessa stazione entro 150 m lungo il percorso
    raw_route.sort(key=lambda x: x["Km"])
    route_c = []
    for item in raw_route:
        unito = False
        for ex in route_c:
            if haversine_km(item["lat"], item["lon"], ex["lat"], ex["lon"]) <= 0.15:
                ex["stalls"] += 1
                ex["kw"] = max(ex["kw"], item["kw"])
                ex["has_food"] = max(ex["has_food"], item["has_food"])
                ex["has_mall"] = max(ex["has_mall"], item["has_mall"])
                ex["food_name"] = _scegli_nome_poi_migliore(ex["food_name"], item["food_name"], FALLBACK_FOOD_NAME)
                ex["mall_name"] = _scegli_nome_poi_migliore(ex["mall_name"], item["mall_name"], FALLBACK_MALL_NAME)
                ex["in_ads"] = ex["in_ads"] or item["in_ads"]
                ex["priority"] = 1 if ex["in_ads"] else 2
                ex["is_tesla"] = ex["is_tesla"] or item["is_tesla"]
                ex["d_rt"] = min(ex["d_rt"], item["d_rt"])
                ex["detour_min"] = min(ex["detour_min"], item["detour_min"])
                if ex["in_ads"]:
                    ex["Note"] = "In Autostrada (AdS)"
                else:
                    icona_ex = "🔴" if ex["is_tesla"] else "⚡"
                    ex["Note"] = f"{icona_ex} Lungo la strada / Fuori casello (~{max(0.1, ex['d_rt']):.1f} km)"
                if "Colonnina Ricarica" in ex["Stazione"] and "Colonnina Ricarica" not in item["Stazione"]:
                    ex["Stazione"] = item["Stazione"]
                unito = True
                break
        if not unito:
            item["stalls"] = 1
            route_c.append(item)

    for r in route_c:
        r["Potenza"] = formatta_potenza(r["kw"], True, r["stalls"])
        r["eff_kw"] = stima_kw_effettivi(r["kw"], True, r["Stazione"], r["in_ads"])
        r["Maps"] = url_gmaps_stazione(r["Stazione"], r["lat"], r["lon"])

    return route_c


CITTA_NODI_ITALIA = (
    # Nord-Est e Trentino-Alto Adige
    (47.003, 11.505, "Brennero"),
    (46.895, 11.431, "Vipiteno"),
    (46.715, 11.656, "Bressanone"),
    (46.640, 11.566, "Chiusa"),
    (46.498, 11.355, "Bolzano"),
    (46.671, 11.159, "Merano"),
    (46.796, 11.936, "Brunico"),
    (46.070, 11.121, "Trento"),
    (45.891, 11.040, "Rovereto"),
    (45.557, 10.768, "Affi"),
    (45.438, 10.992, "Verona"),
    (45.545, 11.535, "Vicenza"),
    (45.406, 11.877, "Padova"),
    (45.490, 12.242, "Mestre"),
    (45.441, 12.316, "Venezia"),
    (45.667, 12.242, "Treviso"),
    (46.140, 12.216, "Belluno"),
    (46.538, 12.137, "Cortina"),
    (45.956, 12.660, "Pordenone"),
    (46.071, 13.235, "Udine"),
    (45.650, 13.777, "Trieste"),
    (45.070, 11.790, "Rovigo"),
    # Nord-Ovest e Lombardia
    (45.156, 10.791, "Mantova"),
    (45.541, 10.212, "Brescia"),
    (45.698, 9.677, "Bergamo"),
    (45.464, 9.190, "Milano"),
    (45.808, 9.085, "Como"),
    (45.821, 8.826, "Varese"),
    (45.185, 9.158, "Pavia"),
    (45.133, 10.022, "Cremona"),
    (45.447, 8.622, "Novara"),
    (45.070, 7.687, "Torino"),
    (45.737, 7.317, "Aosta"),
    (44.912, 8.615, "Alessandria"),
    (44.384, 7.543, "Cuneo"),
    (44.406, 8.946, "Genova"),
    (44.309, 8.477, "Savona"),
    (43.889, 8.039, "Imperia"),
    (44.102, 9.824, "La Spezia"),
    # Emilia-Romagna e Centro
    (45.052, 9.693, "Piacenza"),
    (44.801, 10.328, "Parma"),
    (44.699, 10.631, "Reggio Emilia"),
    (44.647, 10.925, "Modena"),
    (44.495, 11.343, "Bologna"),
    (44.838, 11.620, "Ferrara"),
    (44.353, 11.714, "Imola"),
    (44.418, 12.203, "Ravenna"),
    (44.223, 12.041, "Forlì"),
    (44.140, 12.243, "Cesena"),
    (44.068, 12.569, "Rimini"),
    (43.910, 12.913, "Pesaro"),
    (43.840, 13.019, "Fano"),
    (43.713, 13.218, "Senigallia"),
    (43.616, 13.519, "Ancona"),
    (43.307, 13.729, "Civitanova"),
    (42.954, 13.881, "S. Benedetto"),
    (43.770, 11.256, "Firenze"),
    (43.878, 11.102, "Prato"),
    (43.930, 10.918, "Pistoia"),
    (43.843, 10.503, "Lucca"),
    (43.873, 10.251, "Viareggio"),
    (43.723, 10.402, "Pisa"),
    (43.548, 10.311, "Livorno"),
    (43.463, 11.880, "Arezzo"),
    (43.319, 11.331, "Siena"),
    (42.764, 11.112, "Grosseto"),
    (43.111, 12.391, "Perugia"),
    (42.719, 12.111, "Orvieto"),
    (42.564, 12.643, "Terni"),
    (42.421, 12.108, "Viterbo"),
    (42.405, 12.857, "Rieti"),
    (42.350, 13.399, "L'Aquila"),
    (42.462, 14.216, "Pescara"),
    (42.112, 14.708, "Vasto"),
    (42.003, 14.997, "Termoli"),
    (41.903, 12.496, "Roma"),
    (41.640, 13.351, "Frosinone"),
    (41.487, 13.833, "Cassino"),
    (41.467, 12.904, "Latina"),
    # Sud e Isole
    (41.072, 14.331, "Caserta"),
    (40.852, 14.268, "Napoli"),
    (40.682, 14.768, "Salerno"),
    (40.914, 14.791, "Avellino"),
    (41.130, 14.783, "Benevento"),
    (41.462, 15.545, "Foggia"),
    (41.117, 16.872, "Bari"),
    (40.464, 17.247, "Taranto"),
    (40.633, 17.942, "Brindisi"),
    (40.352, 18.175, "Lecce"),
    (40.640, 15.805, "Potenza"),
    (40.666, 16.604, "Matera"),
    (39.298, 16.254, "Cosenza"),
    (38.967, 16.309, "Lamezia"),
    (38.910, 16.588, "Catanzaro"),
    (38.111, 15.647, "Reggio Cal."),
    (38.194, 15.554, "Messina"),
    (37.508, 15.083, "Catania"),
    (37.075, 15.287, "Siracusa"),
    (38.116, 13.361, "Palermo"),
    (38.017, 12.536, "Trapani"),
    (37.311, 13.577, "Agrigento"),
    (40.923, 9.496, "Olbia"),
    (40.726, 8.559, "Sassari"),
    (39.906, 8.590, "Oristano"),
    (39.224, 9.122, "Cagliari"),
)


CITTA_CAPOLUOGHI_PRIORITARI = frozenset(
    {
        "Bolzano", "Trento", "Verona", "Vicenza", "Padova", "Venezia", "Mestre", "Treviso",
        "Udine", "Trieste", "Belluno", "Pordenone", "Rovigo", "Mantova", "Brescia", "Bergamo",
        "Milano", "Como", "Varese", "Pavia", "Cremona", "Novara", "Torino", "Aosta", "Alessandria",
        "Cuneo", "Genova", "Savona", "Imperia", "La Spezia", "Piacenza", "Parma", "Reggio Emilia",
        "Modena", "Bologna", "Ferrara", "Ravenna", "Forlì", "Cesena", "Rimini", "Pesaro", "Ancona",
        "Firenze", "Prato", "Pistoia", "Lucca", "Pisa", "Livorno", "Arezzo", "Siena", "Grosseto",
        "Perugia", "Terni", "Viterbo", "Rieti", "L'Aquila", "Pescara", "Roma", "Frosinone",
        "Latina", "Caserta", "Napoli", "Salerno", "Avellino", "Benevento", "Foggia", "Bari",
        "Taranto", "Brindisi", "Lecce", "Potenza", "Matera", "Cosenza", "Catanzaro", "Reggio Cal.",
        "Messina", "Catania", "Siracusa", "Palermo", "Trapani", "Agrigento", "Olbia", "Sassari",
        "Oristano", "Cagliari",
    }
)


def estrai_citta_passaggio(pts, km_tot, orig_nome="", dest_nome="", max_nodi=4):
    """
    Estrae al massimo 3-4 città/nodi principali realmente attraversati lungo il tracciato OSRM
    dando priorità massima ai capoluoghi di provincia (Verona, Bolzano, Trento, Vicenza, Padova,
    Brescia, Bergamo, Milano, Bologna, Modena, Firenze, ecc.) rispetto a località minori come Affi.
    """
    if not pts or km_tot < 45.0:
        return []
    orig_low = str(orig_nome or "").strip().lower()
    dest_low = str(dest_nome or "").strip().lower()
    margine_estremi = max(14.0, km_tot * 0.10)
    limite_nodi = 3 if km_tot < 240.0 else min(4, int(max_nodi))

    capoluoghi = []
    minori = []
    for lat_c, lon_c, nome_c in CITTA_NODI_ITALIA:
        n_low = nome_c.lower()
        if (orig_low and (n_low in orig_low or orig_low in n_low)) or (
            dest_low and (n_low in dest_low or dest_low in n_low)
        ):
            continue
        e_capoluogo = nome_c in CITTA_CAPOLUOGHI_PRIORITARI
        # Soglia leggermente più ampia (18 km) per i capoluoghi i cui svincoli autostradali passano in tangenziale
        soglia_dist = 18.0 if e_capoluogo else 8.0
        d_rt, km_c = project(lat_c, lon_c, pts)
        if d_rt <= soglia_dist and margine_estremi <= km_c <= (km_tot - margine_estremi):
            item = {
                "nome": nome_c,
                "km": round(float(km_c), 1),
                "d_rt": round(float(d_rt), 2),
                "capoluogo": e_capoluogo,
            }
            if e_capoluogo:
                capoluoghi.append(item)
            else:
                minori.append(item)

    if not capoluoghi and not minori:
        return []

    gap_min = max(26.0, km_tot / (limite_nodi + 2.0))

    # 1) Priorità massima ai capoluoghi di provincia ben spaziati lungo la tratta
    capoluoghi.sort(key=lambda x: x["km"])
    scelti = []
    for cand in capoluoghi:
        if not scelti:
            scelti.append(cand)
        elif cand["km"] - scelti[-1]["km"] >= gap_min:
            scelti.append(cand)
        elif cand["d_rt"] < scelti[-1]["d_rt"] - 1.5:
            scelti[-1] = cand

    if len(scelti) > limite_nodi:
        indici = [round(k * (len(scelti) - 1) / (limite_nodi - 1)) for k in range(limite_nodi)]
        scelti = [scelti[idx] for idx in sorted(set(indici))]

    # 2) Integra località minori (es. Affi, Chiusa) SOLO se i capoluoghi trovati sono meno di 2
    if len(scelti) < 2 and minori:
        minori.sort(key=lambda x: (x["nome"] == "Affi", x["d_rt"], x["km"]))
        for cand in minori:
            if len(scelti) >= 2:
                break
            if all(abs(cand["km"] - s["km"]) >= gap_min for s in scelti):
                scelti.append(cand)

    scelti.sort(key=lambda x: x["km"])
    return [{"nome": c["nome"], "km": c["km"], "capoluogo": c["capoluogo"]} for c in scelti]


def costruisci_percorso_completo(db, p1, p2, andata_ritorno, evita_autostrade=False, orig_nome="", dest_nome=""):
    """
    Calcola percorso (andata e opzionale ritorno), colonnine e città principali attraversate lungo la tratta.
    Se evita_autostrade=True e OSRM non fornisce routes[1], stima localmente
    l'alternativa statale/extraurbana (km * 1.12 e durata a 58 km/h medi).
    """
    r1 = calcola_percorso_osrm(p1, p2, evita_autostrade=evita_autostrade)
    if not r1:
        return None
    coords1, dur1, stima1 = r1
    pts1, km1_base = costruisci_tracciato(coords1)
    if stima1:
        km1 = km1_base * 1.12
        dur1 = round((km1 / 58.0) * 60.0)
        pts1 = [(lat, lon, km * 1.12) for lat, lon, km in pts1]
    else:
        km1 = km1_base

    ch1 = analizza_colonnine_tratta(
        db,
        pts1,
        "➡️ Andata",
        offset_km=0.0,
        evita_autostrade=evita_autostrade,
        km_min=1.0,
    )
    nodi_andata = estrai_citta_passaggio(pts1, km1, orig_nome=orig_nome, dest_nome=dest_nome, max_nodi=4)
    nodi_ritorno = []
    all_ch, tot_km, tot_dur = list(ch1), km1, dur1
    dur2 = 0

    if andata_ritorno:
        r2 = calcola_percorso_osrm(p2, p1, evita_autostrade=evita_autostrade)
        if not r2:
            return None
        coords2, dur2, stima2 = r2
        pts2, km2_base = costruisci_tracciato(coords2)
        if stima2:
            km2 = km2_base * 1.12
            dur2 = round((km2 / 58.0) * 60.0)
            pts2 = [(lat, lon, km * 1.12) for lat, lon, km in pts2]
        else:
            km2 = km2_base

        ch2 = analizza_colonnine_tratta(
            db,
            pts2,
            "⬅️ Ritorno",
            offset_km=km1,
            evita_autostrade=evita_autostrade,
        )
        nodi_ritorno = estrai_citta_passaggio(pts2, km2, orig_nome=dest_nome, dest_nome=orig_nome, max_nodi=4)
        all_ch = ch1 + ch2
        tot_km = km1 + km2
        tot_dur = dur1 + dur2

    return all_ch, km1, tot_km, tot_dur, dur1, dur2, nodi_andata, nodi_ritorno


def estrai_info_operatore(scelta_operatore, operatore_custom=""):
    """Restituisce (nome_leggibile, tupla_keywords) per l'operatore scelto, oppure (None, None) per Tutti."""
    if scelta_operatore in OPERATORI_KEYWORDS:
        return OPERATORI_KEYWORDS[scelta_operatore]
    if "Altro" in scelta_operatore:
        custom = str(operatore_custom or "").strip()
        if custom:
            return custom, (custom.lower(),)
    return None, None


def match_operatore_kw(stazione, keywords):
    """Restituisce True se il nome della stazione corrisponde ad almeno una keyword dell'operatore."""
    if not keywords:
        return True
    nome_low = stazione.get("Stazione", "").lower()
    return any(kw in nome_low for kw in keywords)


def conviene_sosta_unica_oltre_80(
    auto_cfg,
    stazione,
    arr_pct,
    target_unico,
    next_ch,
    km_per_pct,
    km_target_fine,
    req_fine,
    km1=0.0,
    andata_ritorno=False,
    p_arr_b=20.0,
):
    """
    Confronta sul tempo totale reale (curva DC dell'auto + 7 min fissi di manovra della sosta extra):
    - Scenario 1: sosta unica fino a target_unico (max 90%) per chiudere la tratta.
    - Scenario 2: sosta 1 fino all'80% + 7 min fissi di ingresso/uscita + ricarica alla seconda sosta.
    Restituisce True se lo Scenario 1 è più veloce o pari allo Scenario 2.
    """
    if target_unico <= TETTO_RICARICA_INTERMEDIA:
        return True
    if target_unico > TETTO_SOSTA_UNICA_OTTIMALE:
        return False

    km_s = float(stazione["Km"])
    t_scenario_1 = calcola_minuti_ricarica(auto_cfg, arr_pct, target_unico, stazione["eff_kw"])
    t_sosta1_80 = calcola_minuti_ricarica(auto_cfg, arr_pct, TETTO_RICARICA_INTERMEDIA, stazione["eff_kw"])

    max_km_s2 = km_s + max(0.0, (TETTO_RICARICA_INTERMEDIA - 5.0) * km_per_pct)
    cand_s2 = [c for c in next_ch if km_s + 3.0 < float(c["Km"]) <= max_km_s2]
    if not cand_s2:
        # Nessuna seconda colonnina raggiungibile dall'80%: lo Scenario 1 è l'unica scelta valida
        return True

    miglior_extra_s2 = None
    for s2 in cand_s2:
        km_2 = float(s2["Km"])
        arr_s2 = TETTO_RICARICA_INTERMEDIA - (km_2 - km_s) / km_per_pct
        if arr_s2 < 5.0:
            continue
        need_end_s2 = math.ceil((km_target_fine - km_2) / km_per_pct + req_fine + 1.0)
        need_b_s2 = (
            math.ceil((km1 - km_2) / km_per_pct + p_arr_b + 1.0)
            if (andata_ritorno and km_2 < km1 and km_target_fine >= km1)
            else 0
        )
        target_s2 = max(int(math.ceil(arr_s2 + 2)), need_end_s2, need_b_s2)
        if target_s2 > 95:
            continue
        t_ric_s2 = calcola_minuti_ricarica(auto_cfg, arr_s2, target_s2, s2["eff_kw"])
        # 7 minuti fissi di ingresso/uscita/manovra + eventuale deviazione extra se fuori casello
        overhead_s2 = MINUTI_FISSI_SOSTA_EXTRA + max(0, int(s2.get("detour_min", 2)) - 2)
        t_tot_s2 = overhead_s2 + t_ric_s2
        if miglior_extra_s2 is None or t_tot_s2 < miglior_extra_s2:
            miglior_extra_s2 = t_tot_s2

    if miglior_extra_s2 is None:
        return True

    t_scenario_2 = t_sosta1_80 + miglior_extra_s2
    return t_scenario_1 <= t_scenario_2


def valuta_rabbocco_ponte_hpc(
    auto_cfg,
    stazione,
    arr_pct,
    target_pieno,
    pool_avanti,
    km_per_pct,
    km_fine_tratta,
):
    """
    Valuta l'ottimizzazione "Rabbocco Ponte" quando un'auto veloce (picco DC >= 100 kW) si ferma
    su una colonnina lenta (potenza <= 65 kW, es. 50 kW):
      - Piano 1: ricarica completa fino a target_pieno sulla colonnina lenta.
      - Piano 2: rabbocco minimo sulla colonnina lenta per raggiungere una stazione Ultra-Fast / HPC
        (>= 150 kW) lungo la stessa tratta con almeno il 10% di batteria + 7 minuti fissi di manovra
        sosta extra + ricarica sull'HPC fino alla stessa energia residua equivalente.
    Se il Piano 2 fa risparmiare almeno 5 minuti netti rispetto al Piano 1, restituisce i dettagli
    del miglior Rabbocco Ponte; altrimenti restituisce None.
    """
    st_kw = float(stazione.get("eff_kw", 0.0))
    if st_kw > SOGLIA_COLONNINA_LENTA_KW:
        return None
    if potenza_picco_auto(auto_cfg) < SOGLIA_AUTO_VELOCE_KW:
        return None
    if float(target_pieno) <= float(arr_pct) + 4.0:
        return None

    km_s = float(stazione["Km"])
    tratta_s = stazione.get("Tratta")
    candidati_hpc = [
        c for c in pool_avanti
        if float(c.get("eff_kw", 0.0)) >= SOGLIA_HPC_VELOCE_KW
        and km_s + 3.0 < float(c["Km"]) < float(km_fine_tratta) - 1.0
        and (not tratta_s or c.get("Tratta") == tratta_s)
    ]
    if not candidati_hpc:
        return None

    t_piano_1 = calcola_minuti_ricarica(auto_cfg, arr_pct, target_pieno, st_kw)
    migliore = None

    for hpc in candidati_hpc:
        km_hpc = float(hpc["Km"])
        dist_hpc = km_hpc - km_s
        consumo_tratto_pct = dist_hpc / km_per_pct

        # Rabbocco minimo sulla colonnina lenta per arrivare all'HPC con almeno il 10% di batteria
        target_ponte = max(
            int(math.ceil(float(arr_pct) + 2.0)),
            int(math.ceil(consumo_tratto_pct + SOGLIA_ARRIVO_HPC_PONTE)),
        )
        if target_ponte >= int(target_pieno) - 2 or target_ponte > TETTO_RICARICA_INTERMEDIA:
            continue

        arr_hpc = float(target_ponte) - consumo_tratto_pct
        if arr_hpc < SOGLIA_ARRIVO_HPC_PONTE - 0.1:
            continue

        # Batteria target equivalente sull'HPC per trovarsi al km dell'HPC con la stessa energia del Piano 1
        target_hpc_eq = max(
            int(math.ceil(arr_hpc + 2.0)),
            int(math.ceil(float(target_pieno) - consumo_tratto_pct)),
        )
        if target_hpc_eq > 95:
            continue

        t_ponte_lenta = calcola_minuti_ricarica(auto_cfg, arr_pct, target_ponte, st_kw)
        t_ric_hpc = calcola_minuti_ricarica(auto_cfg, arr_hpc, target_hpc_eq, hpc["eff_kw"])
        t_piano_2 = t_ponte_lenta + MINUTI_FISSI_SOSTA_EXTRA + t_ric_hpc
        risparmio_min = t_piano_1 - t_piano_2

        if risparmio_min >= RISPARMIO_MINIMO_PONTE_MIN:
            cand_res = {
                "hpc": hpc,
                "target_ponte": int(target_ponte),
                "min_ponte": int(t_ponte_lenta),
                "arr_hpc": round(arr_hpc, 1),
                "target_hpc_eq": int(target_hpc_eq),
                "min_hpc_eq": int(t_ric_hpc),
                "t_piano_1": int(t_piano_1),
                "t_piano_2": int(t_piano_2),
                "risparmio_min": int(risparmio_min),
            }
            if migliore is None or (
                cand_res["risparmio_min"],
                int(bool(hpc.get("in_ads"))),
                float(hpc.get("eff_kw", 0.0)),
                -dist_hpc,
            ) > (
                migliore["risparmio_min"],
                int(bool(migliore["hpc"].get("in_ads"))),
                float(migliore["hpc"].get("eff_kw", 0.0)),
                -(float(migliore["hpc"]["Km"]) - km_s),
            ):
                migliore = cand_res

    return migliore


def trova_jolly_stazione(stazione, arr_pct, pool_jolly, km_per_pct, km_fine_tratta=None):
    """
    Cerca la colonnina di riserva Piano B (Jolly) successiva entro il chilometro finale della tratta
    corrente seguendo la cascata di sicurezza a 3 livelli fino all'1% di batteria residua:
      - Livello 1: Area di Servizio in Autostrada (in_ads / priority == 1) con residua >= 4%;
      - Livello 2: Emergenza fuori casello (DC/Fast entro 3 km) con residua >= 4%;
      - Livello 3: Riserva estrema 1% - 3% (precedenza AdS se presenti entrambe).
    """
    margine_km = max(0.0, (arr_pct - SOGLIA_EMERGENZA_ESTREMA_JOLLY) * km_per_pct)
    if margine_km <= 0.5:
        return None
    km_s = float(stazione["Km"])
    limite_sup_km = km_s + margine_km
    if km_fine_tratta is not None:
        limite_sup_km = min(limite_sup_km, float(km_fine_tratta))
    if limite_sup_km <= km_s + 0.5:
        return None

    tratta_corr = stazione.get("Tratta")
    candidati = [
        cand for cand in pool_jolly
        if (km_s + 0.5 < float(cand["Km"]) <= limite_sup_km)
        and (cand["Km"], cand["Stazione"]) != (stazione["Km"], stazione["Stazione"])
        and (not tratta_corr or cand.get("Tratta") == tratta_corr)
    ]
    if not candidati:
        return None

    def _pct_res(c):
        return arr_pct - (float(c["Km"]) - km_s) / km_per_pct

    def _e_ads(c):
        return bool(c.get("in_ads") or c.get("priority") == 1)

    # Livello 1: In Autostrada (AdS) con batteria residua >= 4%
    liv1 = [c for c in candidati if _e_ads(c) and _pct_res(c) >= SOGLIA_EMERGENZA_JOLLY]
    if liv1:
        cand = min(liv1, key=lambda c: float(c["Km"]))
        livello = 1
    else:
        # Livello 2: Emergenza fuori casello (DC/Fast entro 3 km) con batteria residua >= 4%
        liv2 = [
            c for c in candidati
            if (not _e_ads(c))
            and float(c.get("d_rt", 0.0)) <= 3.0
            and _pct_res(c) >= SOGLIA_EMERGENZA_JOLLY
        ]
        if liv2:
            cand = min(liv2, key=lambda c: float(c["Km"]))
            livello = 2
        else:
            # Livello 3: Riserva estrema 1% - 3% (precedenza AdS se presenti entrambe)
            liv3_ads = [c for c in candidati if _e_ads(c) and _pct_res(c) >= SOGLIA_EMERGENZA_ESTREMA_JOLLY]
            liv3_fuori = [
                c for c in candidati
                if (not _e_ads(c))
                and float(c.get("d_rt", 0.0)) <= 3.0
                and _pct_res(c) >= SOGLIA_EMERGENZA_ESTREMA_JOLLY
            ]
            pool_liv3 = liv3_ads if liv3_ads else liv3_fuori
            if not pool_liv3:
                return None
            cand = min(pool_liv3, key=lambda c: float(c["Km"]))
            livello = 3

    pct_em_reale = _pct_res(cand)
    if livello == 3:
        pct_em_int = max(1, min(3, int(round(pct_em_reale))))
    else:
        pct_em_int = max(int(SOGLIA_EMERGENZA_JOLLY), int(round(pct_em_reale)))

    return {
        "Stazione": cand["Stazione"],
        "Km": cand["Km"],
        "delta_km": round(float(cand["Km"]) - km_s, 1),
        "pct_emergenza": pct_em_int,
        "livello": livello,
        "riserva_estrema": livello == 3,
        "in_ads": _e_ads(cand),
        "lat": cand.get("lat"),
        "lon": cand.get("lon"),
        "city": cand.get("city") or cand.get("comune") or cand.get("municipality"),
        "address": cand.get("address") or cand.get("indirizzo"),
    }


def simula_piano_viaggio(
    colonnine,
    auto_cfg,
    km1,
    tot_km,
    durata_guida_min,
    andata_ritorno,
    km_per_pct,
    p_start,
    p_arr_b,
    p_home_a,
    stile_codice,
    p_colonnina=15,
    pranzo_dove="pranzo_anche_fuori",
    extra_ads_rabbocco=None,
    consenti_rabbocco_ads=False,
    escludi_chiavi=None,
    forza_pausa_opzionale=False,
    operatore_keywords=None,
    nome_operatore=None,
    colonnine_jolly_pool=None,
):
    """
    Calcola un piano completo di soste garantendo:
    - rispetto di % Arrivo (B) e % Rientro a Casa (A);
    - sfruttamento autonomia fino alla % desiderata all'arrivo in colonnina (p_colonnina);
    - regola del Jolly (Piano B garantito tra p_colonnina e il 4% d'emergenza);
    - tetto massimo di ricarica all'80% nelle soste intermedie (superato solo se non esistono
      altre colonnine più avanti o per una % arrivo desiderata molto alta).
    """
    escludi_chiavi = escludi_chiavi or set()
    pool = [c for c in colonnine if (c["Km"], c["Stazione"]) not in escludi_chiavi]
    pool_jolly = sorted(
        colonnine_jolly_pool if colonnine_jolly_pool is not None else pool,
        key=lambda x: x["Km"],
    )
    ads_rabbocco_pool = [
        c for c in (extra_ads_rabbocco or [])
        if c["in_ads"] and (c["Km"], c["Stazione"]) not in escludi_chiavi
    ]

    final_req = float(p_home_a if andata_ritorno else p_arr_b)
    p_target_col = max(5.0, min(40.0, float(p_colonnina)))
    p_res = min(p_target_col, max(5.0, float(p_arr_b), final_req)) if p_target_col > max(float(p_arr_b), final_req) else p_target_col

    # Verifica immediata se il viaggio è fattibile senza ricariche obbligatorie
    pct_b_diretto = p_start - (km1 / km_per_pct)
    pct_fine_diretto = p_start - (tot_km / km_per_pct)
    nessuna_sosta_necessaria = (pct_b_diretto >= p_arr_b) and (pct_fine_diretto >= final_req)

    if nessuna_sosta_necessaria and not forza_pausa_opzionale:
        return {
            "valido": True,
            "soste": [],
            "minuti_ricarica": 0,
            "minuti_deviazione": 0,
            "tempo_totale_min": int(durata_guida_min),
            "pct_arrivo_b": round(pct_b_diretto, 1),
            "pct_rientro_a": round(pct_fine_diretto, 1) if andata_ritorno else None,
            "errore": None,
            "sosta_opzionale": False,
        }

    # Caso speciale: viaggio diretto fattibile ma proposta di pausa caffè/pranzo opzionale
    if nessuna_sosta_necessaria and forza_pausa_opzionale:
        if not pool:
            return {"valido": False, "errore": "Nessuna colonnina disponibile con il filtro rete scelto per la pausa opzionale."}
        mid_min, mid_max = tot_km * 0.25, tot_km * 0.80
        fascia = [c for c in pool if mid_min <= c["Km"] <= mid_max] or pool
        avviso = None
        if stile_codice == "pranzo":
            if pranzo_dove == "pranzo_ads":
                solo_ads_f = [c for c in fascia if c["in_ads"]]
                scelta_pool = solo_ads_f or fascia
                if not solo_ads_f:
                    avviso = "⚠️ Nessun Autogrill / Area di Servizio nella fascia utile: proposta la migliore colonnina rapida disponibile."
                scelta = max(scelta_pool, key=lambda c: (int(c["in_ads"]), c["has_food"], c["eff_kw"], -abs(c["Km"] - tot_km * 0.5)))
            else:
                con_mall = [c for c in fascia if c["has_mall"] == 1]
                con_food = [c for c in fascia if c["has_food"] == 1]
                scelta_pool = con_mall or con_food or fascia
                if not con_mall and not con_food:
                    avviso = "⚠️ Nessun centro commerciale o ristorante entro 200 m nella fascia utile: proposta la migliore colonnina rapida lungo il percorso."
                scelta = max(scelta_pool, key=lambda c: (c["has_mall"], c["has_food"], c["eff_kw"], -abs(c["Km"] - tot_km * 0.5)))
            min_pausa = 35
        else:
            con_ristoro = [c for c in fascia if c["in_ads"] or c["has_food"] == 1]
            scelta_pool = con_ristoro or fascia
            scelta = max(scelta_pool, key=lambda c: (int(c["in_ads"]), c["has_food"], c["eff_kw"], -abs(c["Km"] - tot_km * 0.5)))
            min_pausa = 15

        arr_pct = max(p_res, p_start - scelta["Km"] / km_per_pct)
        tetto_opz = float(TETTO_RICARICA_INTERMEDIA) if arr_pct < TETTO_RICARICA_INTERMEDIA else 90.0
        charge_to = calcola_soc_per_minuti(auto_cfg, arr_pct, min_pausa, scelta["eff_kw"], max_soc=tetto_opz)
        charge_to = min(int(tetto_opz), max(int(math.ceil(arr_pct)) + 5, charge_to))
        min_ric = calcola_minuti_ricarica(auto_cfg, arr_pct, charge_to, scelta["eff_kw"])
        pct_b = (charge_to - (km1 - scelta["Km"]) / km_per_pct) if scelta["Km"] <= km1 else pct_b_diretto
        pct_end = charge_to - (tot_km - scelta["Km"]) / km_per_pct
        sosta = {
            **scelta,
            "arr_pct": round(arr_pct, 1),
            "charge_to": int(charge_to),
            "minuti_ricarica": min_ric,
            "avviso": avviso,
            "nota_extra": "☕ Sosta opzionale di pausa con ricarica comoda (la batteria basterebbe anche senza soste).",
        }
        return {
            "valido": True,
            "soste": [sosta],
            "minuti_ricarica": min_ric,
            "minuti_deviazione": scelta["detour_min"],
            "tempo_totale_min": int(durata_guida_min + min_ric + scelta["detour_min"]),
            "pct_arrivo_b": round(pct_b, 1),
            "pct_rientro_a": round(pct_end, 1) if andata_ritorno else None,
            "errore": None,
            "sosta_opzionale": True,
        }

    # Pianificazione iterativa con rispetto garantito di Arrivo (B) e Rientro (A)
    curr_km = 0.0
    curr_pct = float(p_start)
    soste = []
    pct_arrivo_b = None
    max_soste = 10
    hpc_ponte_forzata = None

    def trova_jolly(stazione, arr_pct):
        """Cerca una colonnina successiva (Jolly / Piano B) entro la tratta corrente con precedenza alle AdS."""
        e_andata_ar = andata_ritorno and (
            "Ritorno" not in str(stazione.get("Tratta", "")) and float(stazione["Km"]) <= float(km1) + 0.05
        )
        km_limite = float(km1) if e_andata_ar else float(tot_km)
        return trova_jolly_stazione(stazione, arr_pct, pool_jolly, km_per_pct, km_fine_tratta=km_limite)

    def calcola_target_sosta(stazione, arr_pct):
        """
        Determina la % target di ripartenza ottimizzando il tempo totale reale di viaggio:
        - Entro l'80% se basta a chiudere la tratta.
        - Oltre l'80% (fino a max 90%) SOLO se evitare una seconda sosta risulta più veloce o pari
          rispetto a fermarsi all'80% + 7 min fissi di manovra + ricarica della seconda sosta.
        - Fino al 95% solo se non esistono altre colonnine compatibili avanti o % arrivo > 75%.
        """
        tetto_eccezione = 95
        km_s = float(stazione["Km"])
        need_end = math.ceil((tot_km - km_s) / km_per_pct + final_req + 1.0)
        need_b = math.ceil((km1 - km_s) / km_per_pct + p_arr_b + 1.0) if (andata_ritorno and km_s < km1) else 0
        target_unico = max(int(math.ceil(arr_pct + 4)), need_end, need_b)

        next_ch = [c for c in pool if km_s + 3.0 < c["Km"] < tot_km - 1.0]
        next_ch_pre_b = [c for c in pool if km_s + 3.0 < c["Km"] < km1 - 1.0] if (andata_ritorno and km_s < km1) else []
        arr_molto_alta = (final_req > 75.0) or (andata_ritorno and km_s < km1 and p_arr_b > 75.0)

        # Caso 1: chiude il viaggio rimanendo entro l'80%
        if target_unico <= TETTO_RICARICA_INTERMEDIA:
            return min(TETTO_RICARICA_INTERMEDIA, target_unico), True

        # Caso 2: supera l'80% (fino a max 90%) se lo Scenario 1 (sosta unica) conviene sul tempo totale
        if TETTO_RICARICA_INTERMEDIA < target_unico <= TETTO_SOSTA_UNICA_OTTIMALE and next_ch:
            if conviene_sosta_unica_oltre_80(
                auto_cfg=auto_cfg,
                stazione=stazione,
                arr_pct=arr_pct,
                target_unico=target_unico,
                next_ch=next_ch,
                km_per_pct=km_per_pct,
                km_target_fine=tot_km,
                req_fine=final_req,
                km1=km1,
                andata_ritorno=andata_ritorno,
                p_arr_b=p_arr_b,
            ):
                return int(target_unico), True

        # Caso 3 (eccezione fino al 95%): nessuna altra colonnina compatibile avanti o "% Arrivo desiderata" > 75%
        if (not next_ch or arr_molto_alta) and target_unico <= tetto_eccezione:
            return min(tetto_eccezione, target_unico), True

        # Caso 4: sosta intermedia -> resta all'80% (salvo superamento conveniente fino al 90% per chiudere l'andata B)
        if stile_codice == "pranzo":
            base_target = TETTO_RICARICA_INTERMEDIA
        elif stile_codice == "caffe":
            base_target = min(
                TETTO_RICARICA_INTERMEDIA,
                max(
                    min(auto_cfg["soc_veloce"], TETTO_RICARICA_INTERMEDIA),
                    calcola_soc_per_minuti(auto_cfg, arr_pct, 18, stazione["eff_kw"], max_soc=float(TETTO_RICARICA_INTERMEDIA)),
                ),
            )
        else:
            base_target = min(auto_cfg["soc_veloce"], TETTO_RICARICA_INTERMEDIA)

        target = min(TETTO_RICARICA_INTERMEDIA, max(base_target, int(math.ceil(arr_pct + 15))))

        # Assicura carica adeguata verso la prossima colonnina restando entro l'80%
        if next_ch:
            dist_prossima = next_ch[0]["Km"] - km_s
            min_comodo = math.ceil(dist_prossima / km_per_pct + p_res + 1.0)
            target = min(TETTO_RICARICA_INTERMEDIA, max(target, min_comodo))

        # Sulla tratta di andata (A/R), valuta se superare l'80% (max 90%) conviene per evitare una 2ª sosta prima di B
        if andata_ritorno and km_s < km1 and need_b > target:
            if not next_ch_pre_b or p_arr_b > 75.0:
                target = min(tetto_eccezione, need_b)
            elif need_b <= TETTO_SOSTA_UNICA_OTTIMALE and conviene_sosta_unica_oltre_80(
                auto_cfg=auto_cfg,
                stazione=stazione,
                arr_pct=arr_pct,
                target_unico=need_b,
                next_ch=next_ch_pre_b,
                km_per_pct=km_per_pct,
                km_target_fine=km1,
                req_fine=p_arr_b,
            ):
                target = int(need_b)

        return target, False

    def valuta_lista(lista_stazioni):
        """Calcola metriche di ricarica, tempo e Jolly garantito per ogni stazione raggiungibile."""
        out = []
        for c in lista_stazioni:
            arr_pct = curr_pct - (c["Km"] - curr_km) / km_per_pct
            charge_to, chiude_viaggio = calcola_target_sosta(c, arr_pct)
            min_ric = calcola_minuti_ricarica(auto_cfg, arr_pct, charge_to, c["eff_kw"])
            jolly = trova_jolly(c, arr_pct)
            out.append(
                {
                    "stazione": c,
                    "arr_pct": arr_pct,
                    "charge_to": charge_to,
                    "chiude": chiude_viaggio,
                    "min_ric": min_ric,
                    "costo_tempo": min_ric + c["detour_min"],
                    "jolly": jolly,
                }
            )
        return out

    while len(soste) < max_soste:
        # Se la sosta precedente ha attivato un Rabbocco Ponte su colonnina lenta, pianifica subito l'HPC veloce
        if hpc_ponte_forzata is not None:
            st_hpc = hpc_ponte_forzata
            hpc_ponte_forzata = None
            tratto_verso_b = bool(andata_ritorno and curr_km < km1)
            arr_hpc = curr_pct - (float(st_hpc["Km"]) - curr_km) / km_per_pct
            if float(st_hpc["Km"]) > curr_km + 1.0 and arr_hpc >= 5.0:
                charge_to_hpc, _ = calcola_target_sosta(st_hpc, arr_hpc)
                min_ric_hpc = calcola_minuti_ricarica(auto_cfg, arr_hpc, charge_to_hpc, st_hpc["eff_kw"])
                jolly_hpc = trova_jolly(st_hpc, arr_hpc)
                soste.append(
                    {
                        **st_hpc,
                        "arr_pct": round(arr_hpc, 1),
                        "charge_to": int(charge_to_hpc),
                        "minuti_ricarica": int(min_ric_hpc),
                        "jolly": jolly_hpc,
                        "avviso": None,
                        "nota_extra": "⚡ Completamento rapido su HPC dopo Rabbocco Ponte: massima velocità di ricarica.",
                    }
                )
                curr_km = float(st_hpc["Km"])
                curr_pct = float(charge_to_hpc)
                if tratto_verso_b and curr_km >= km1:
                    pct_arrivo_b = round(curr_pct, 1)
                continue

        if andata_ritorno and curr_km < km1:
            pct_a_b = curr_pct - (km1 - curr_km) / km_per_pct
            pct_a_casa = curr_pct - (tot_km - curr_km) / km_per_pct
            post_b_raggiungibile = any(
                km1 < c["Km"] <= km1 + max(0.0, (pct_a_b - p_res) * km_per_pct)
                for c in (pool + ads_rabbocco_pool)
            )
            if pct_a_b >= p_arr_b and (pct_a_casa >= final_req or post_b_raggiungibile):
                pct_arrivo_b = round(pct_a_b, 1)
                curr_km = km1
                curr_pct = pct_a_b
                if pct_a_casa >= final_req:
                    break
                continue
            limite_superiore_km = km1
            tratto_verso_b = True
        else:
            pct_finale = curr_pct - (tot_km - curr_km) / km_per_pct
            if pct_finale >= final_req:
                if not andata_ritorno:
                    pct_arrivo_b = round(pct_finale, 1)
                break
            limite_superiore_km = tot_km - 1.0
            tratto_verso_b = False

        max_reach_km = curr_km + max(0.0, (curr_pct - p_target_col) * km_per_pct)
        reach = [
            c
            for c in pool
            if curr_km + 3.0 < c["Km"] <= min(limite_superiore_km, max_reach_km)
        ]

        # Se con una soglia p_target_col alta non ci sono colonnine raggiungibili, allarga fino al 5% d'emergenza
        if not reach and p_target_col > 5.0:
            max_reach_emerg = curr_km + max(0.0, (curr_pct - 5.0) * km_per_pct)
            reach = [
                c
                for c in pool
                if curr_km + 3.0 < c["Km"] <= min(limite_superiore_km, max_reach_emerg)
            ]
            if reach:
                max_reach_km = max_reach_emerg

        # Candidati AdS per eventuale rabbocco veloce <= 8 min senza uscire dal casello
        reach_ads_extra = [
            c
            for c in ads_rabbocco_pool
            if curr_km + 3.0 < c["Km"] <= min(limite_superiore_km, max_reach_km)
        ]

        valutazioni = valuta_lista(reach)
        valutazioni_ads_extra = valuta_lista(reach_ads_extra) if reach_ads_extra else []

        avviso_card = None
        nota_extra = None
        scelta = None
        valutazioni_attive = valutazioni

        # Regola rabbocco veloce <= 8 min in Autostrada (solo se vicino alla finestra di discesa target):
        soglia_arr_ideale = p_target_col + (22.0 if stile_codice == "pranzo" else 12.0)
        applica_rabbocco_ads = consenti_rabbocco_ads or (auto_cfg["is_tesla_car"] and stile_codice == "veloce")
        if applica_rabbocco_ads:
            tutte_val_ads = [v for v in valutazioni if v["stazione"]["in_ads"]] + valutazioni_ads_extra
            min_arr_disp = min((v["arr_pct"] for v in (valutazioni + valutazioni_ads_extra)), default=100.0)
            soglia_rabbocco = max(soglia_arr_ideale + 6.0, min_arr_disp + 12.0)
            ads_rabbocco_rapido = [
                v for v in tutte_val_ads
                if v["chiude"] and v["min_ric"] <= 8 and v["arr_pct"] <= soglia_rabbocco
            ]
            if ads_rabbocco_rapido:
                ads_con_jolly = [v for v in ads_rabbocco_rapido if v["jolly"] is not None]
                cand_rapidi = ads_con_jolly if ads_con_jolly else ads_rabbocco_rapido
                scelta = min(cand_rapidi, key=lambda v: (v["costo_tempo"], -v["stazione"]["eff_kw"], v["arr_pct"]))
                nota_extra = "⚡ Scelta in Autostrada: rabbocco veloce (≤ 8 min) senza uscire dal casello."

        if scelta is None:
            if not valutazioni:
                return {
                    "valido": False,
                    "soste": soste,
                    "errore": (
                        f"Nessuna colonnina raggiungibile entro {max_reach_km:.0f} km "
                        f"con i filtri di rete selezionati (arrivo minimo in colonnina {p_target_col:.0f}%)."
                    ),
                }

            # In modalità mista di salvataggio per operatore preferito, dà precedenza all'operatore scelto
            valutazioni_attive = valutazioni
            if operatore_keywords:
                val_op = [v for v in valutazioni if match_operatore_kw(v["stazione"], operatore_keywords)]
                if val_op:
                    valutazioni_attive = val_op
                elif nome_operatore:
                    nota_extra = (
                        f"ℹ️ Tratto scoperto da '{nome_operatore}': "
                        f"selezionata altra colonnina veloce per garantire il completamento del viaggio."
                    )

            # Sfrutta l'autonomia fino alla finestra ideale attorno a p_target_col
            # (tra target_colonnina e target_colonnina + 12%; +22% per sosta pranzo)
            # evitando di fermarsi prematuramente al 50-60% di batteria.
            min_arr_attive = min(v["arr_pct"] for v in valutazioni_attive)
            finestra_ideale = [v for v in valutazioni_attive if v["arr_pct"] <= soglia_arr_ideale]

            # Regola del Jolly (Piano B garantito entro il 4% d'emergenza, Livello 1 o 2):
            # 1) Preferisce stazioni nella finestra ideale con Jolly sicuro (>= 4%);
            # 2) Altrimenti arretra solo se non si supera il 65% all'arrivo e senza perdere chiusura in 1 sosta;
            # 3) Se nessuna soddisfa i criteri, usa la finestra ideale (o le stazioni più avanzate).
            finestra_ideale_jolly = [
                v for v in finestra_ideale
                if v["jolly"] is not None and v["jolly"].get("livello", 3) <= 2
            ]
            if finestra_ideale_jolly:
                fascia_utile = finestra_ideale_jolly
            else:
                finestra_chiude = any(v["chiude"] for v in finestra_ideale)
                tutte_jolly = [
                    v for v in valutazioni_attive
                    if v["jolly"] is not None
                    and v["jolly"].get("livello", 3) <= 2
                    and v["arr_pct"] <= 65.0
                    and (not finestra_chiude or v["chiude"])
                ]
                if tutte_jolly:
                    min_arr_jolly = min(v["arr_pct"] for v in tutte_jolly)
                    fascia_utile = [v for v in tutte_jolly if v["arr_pct"] <= min_arr_jolly + 12.0]
                elif finestra_ideale:
                    fascia_utile = finestra_ideale
                else:
                    fascia_utile = [v for v in valutazioni_attive if v["arr_pct"] <= min_arr_attive + 12.0]

            if stile_codice == "pranzo":
                if pranzo_dove == "pranzo_ads":
                    # Sosta Pranzo Solo Autogrill / Area di Servizio
                    solo_ads_fascia = [v for v in fascia_utile if v["stazione"]["in_ads"]]
                    if solo_ads_fascia:
                        scelta = min(
                            solo_ads_fascia,
                            key=lambda v: (
                                not v["chiude"],
                                -v["stazione"]["has_food"],
                                v["costo_tempo"] - (v["stazione"]["Km"] - curr_km) * 0.05,
                            ),
                        )
                    else:
                        scelta = min(
                            fascia_utile,
                            key=lambda v: (
                                not v["chiude"],
                                v["costo_tempo"] - (v["stazione"]["Km"] - curr_km) * 0.06,
                            ),
                        )
                        avviso_card = (
                            "⚠️ Nessun Autogrill / Area di Servizio disponibile nella fascia utile: "
                            "selezionata la migliore colonnina rapida per garantire la batteria desiderata."
                        )
                else:
                    # Sosta Pranzo anche fuori casello: privilegia Centro Commerciale poi Ristorante
                    con_mall = [v for v in fascia_utile if v["stazione"]["has_mall"] == 1]
                    con_food = [v for v in fascia_utile if v["stazione"]["has_food"] == 1]

                    if con_mall:
                        scelta = min(
                            con_mall,
                            key=lambda v: (
                                not v["chiude"],
                                -v["stazione"]["has_food"],
                                v["costo_tempo"] - (v["stazione"]["Km"] - curr_km) * 0.05,
                            ),
                        )
                    elif con_food:
                        scelta = min(
                            con_food,
                            key=lambda v: (
                                not v["chiude"],
                                v["costo_tempo"] - (v["stazione"]["Km"] - curr_km) * 0.05,
                            ),
                        )
                    else:
                        scelta = min(
                            fascia_utile,
                            key=lambda v: (
                                not v["chiude"],
                                v["costo_tempo"] - (v["stazione"]["Km"] - curr_km) * 0.06,
                            ),
                        )
                        avviso_card = (
                            "⚠️ Nessun centro commerciale o ristorante entro 200 m nella fascia utile: "
                            "selezionata comunque la migliore colonnina rapida per garantire la batteria desiderata."
                        )

            elif stile_codice == "caffe":
                # Pausa Caffè / Pranzo in Autogrill: dà priorità assoluta alle AdS in Autostrada
                solo_ads_caffe = [v for v in fascia_utile if v["stazione"]["in_ads"]]
                con_ristoro = [v for v in fascia_utile if v["stazione"]["has_food"] == 1]
                if solo_ads_caffe:
                    candidati_caffe = solo_ads_caffe
                elif con_ristoro:
                    candidati_caffe = con_ristoro
                else:
                    candidati_caffe = fascia_utile
                    avviso_card = (
                        "⚠️ Nessun Autogrill / Area di Servizio o punto ristoro entro 200 m nella fascia utile: "
                        "selezionata la colonnina rapida più conveniente."
                    )
                scelta = min(
                    candidati_caffe,
                    key=lambda v: (
                        not v["chiude"],
                        not v["stazione"]["in_ads"],
                        v["costo_tempo"] - (v["stazione"]["Km"] - curr_km) * 0.06,
                    ),
                )

            else:
                # Viaggio Veloce / Diretto: sfrutta la finestra ideale vicino al target con Jolly garantito
                chiudono = [v for v in fascia_utile if v["chiude"]]
                if chiudono:
                    scelta = min(
                        chiudono,
                        key=lambda v: (v["costo_tempo"], not v["stazione"]["in_ads"], -v["stazione"]["eff_kw"], v["arr_pct"]),
                    )
                else:
                    scelta = min(
                        fascia_utile,
                        key=lambda v: (
                            v["costo_tempo"] - (v["stazione"]["Km"] - curr_km) * 0.14,
                            not v["stazione"]["in_ads"],
                            -v["stazione"]["eff_kw"],
                            v["arr_pct"],
                        ),
                    )

        # Regola generale per il Piano B (Jolly) garantito nella stessa rete autostradale:
        # se la stazione scelta è in Autostrada ma non ha un Jolly dentro l'Autostrada prima della fine tratta,
        # valuta la stazione autostradale precedente (prev_ads) purché:
        #   a) distante almeno 8 km dalla partenza della tratta (e da curr_km) e arrivo <= 65%;
        #   b) permette di completare la tratta con lo stesso identico numero di soste rispettando la regola
        #      del minor tempo totale (fino a max 90% se conviene rispetto a una seconda sosta);
        #   c) la stazione autostradale successiva diventa Piano B (Jolly) garantito senza uscire al casello.
        ha_jolly_in_ads = bool(scelta.get("jolly") and scelta["jolly"].get("livello") == 1)
        if scelta["stazione"].get("in_ads") and not ha_jolly_in_ads and valutazioni_attive:
            km_inizio_tratta = (
                float(km1)
                if (andata_ritorno and not tratto_verso_b and float(scelta["stazione"]["Km"]) > float(km1) + 0.05)
                else 0.0
            )
            val_pool_prev = [
                v for v in valutazioni_attive
                if v["stazione"].get("in_ads")
                and float(v["stazione"]["Km"]) < float(scelta["stazione"]["Km"]) - 0.5
                and (float(v["stazione"]["Km"]) - km_inizio_tratta) >= 8.0
                and (float(v["stazione"]["Km"]) - curr_km) >= 8.0
            ]
            if val_pool_prev:
                prev_ads = max(val_pool_prev, key=lambda v: float(v["stazione"]["Km"]))
                prev_jolly = prev_ads.get("jolly")

                if prev_ads["arr_pct"] <= 65.0 and prev_jolly is not None and prev_jolly.get("livello") == 1:
                    def _stima_soste_residue(start_km, start_pct):
                        """Stima il numero minimo di soste successive necessarie da (start_km, start_pct)."""
                        s_km = float(start_km)
                        s_pct = float(start_pct)
                        n_extra = 0
                        for _ in range(max_soste):
                            if andata_ritorno and s_km < km1:
                                p_b = s_pct - (km1 - s_km) / km_per_pct
                                p_end = s_pct - (tot_km - s_km) / km_per_pct
                                post_b_ok = any(
                                    km1 < c["Km"] <= km1 + max(0.0, (p_b - p_res) * km_per_pct)
                                    for c in (pool + ads_rabbocco_pool)
                                )
                                if p_b >= p_arr_b and (p_end >= final_req or post_b_ok):
                                    s_km = km1
                                    s_pct = p_b
                                    if p_end >= final_req:
                                        return n_extra
                                    continue
                                lim_sup = km1
                            else:
                                p_fin = s_pct - (tot_km - s_km) / km_per_pct
                                if p_fin >= final_req:
                                    return n_extra
                                lim_sup = tot_km - 1.0

                            max_r = s_km + max(0.0, (s_pct - p_target_col) * km_per_pct)
                            cands = [c for c in pool if s_km + 3.0 < c["Km"] <= min(lim_sup, max_r)]
                            if not cands and p_target_col > 5.0:
                                max_r = s_km + max(0.0, (s_pct - 5.0) * km_per_pct)
                                cands = [c for c in pool if s_km + 3.0 < c["Km"] <= min(lim_sup, max_r)]
                            if not cands:
                                return None
                            nxt = max(cands, key=lambda c: float(c["Km"]))
                            arr_nxt = s_pct - (float(nxt["Km"]) - s_km) / km_per_pct
                            chg_nxt, _ = calcola_target_sosta(nxt, arr_nxt)
                            s_km = float(nxt["Km"])
                            s_pct = float(chg_nxt)
                            n_extra += 1
                        return None

                    rem_ultima = _stima_soste_residue(scelta["stazione"]["Km"], scelta["charge_to"])
                    if rem_ultima is not None:
                        km_p = float(prev_ads["stazione"]["Km"])
                        need_end_p = math.ceil((tot_km - km_p) / km_per_pct + final_req + 1.0)
                        need_b_p = (
                            math.ceil((km1 - km_p) / km_per_pct + p_arr_b + 1.0)
                            if (andata_ritorno and km_p < km1)
                            else 0
                        )
                        next_ch_p = [c for c in pool if km_p + 3.0 < float(c["Km"]) < tot_km - 1.0]
                        charge_prev_ok = None

                        # Tentativo 1: usa il target già calcolato (che include già la regola tempo totale max 90%)
                        cand_base = min(TETTO_SOSTA_UNICA_OTTIMALE, int(prev_ads["charge_to"]))
                        rem_base = _stima_soste_residue(km_p, cand_base)
                        if rem_base is not None and rem_base <= rem_ultima:
                            charge_prev_ok = cand_base
                        else:
                            # Tentativo 2: verifica se spingersi tra 81% e 90% evita soste extra e conviene sul tempo
                            for cand_extra in range(TETTO_RICARICA_INTERMEDIA + 1, TETTO_SOSTA_UNICA_OTTIMALE + 1):
                                rem_extra = _stima_soste_residue(km_p, cand_extra)
                                if rem_extra is not None and rem_extra <= rem_ultima:
                                    km_ref_conv = km1 if (andata_ritorno and km_p < km1 and rem_ultima > 0) else tot_km
                                    req_ref_conv = p_arr_b if (andata_ritorno and km_p < km1 and rem_ultima > 0) else final_req
                                    ch_ref_conv = (
                                        [c for c in pool if km_p + 3.0 < float(c["Km"]) < km1 - 1.0]
                                        if (andata_ritorno and km_p < km1 and rem_ultima > 0)
                                        else next_ch_p
                                    )
                                    if conviene_sosta_unica_oltre_80(
                                        auto_cfg=auto_cfg,
                                        stazione=prev_ads["stazione"],
                                        arr_pct=prev_ads["arr_pct"],
                                        target_unico=cand_extra,
                                        next_ch=ch_ref_conv,
                                        km_per_pct=km_per_pct,
                                        km_target_fine=km_ref_conv,
                                        req_fine=req_ref_conv,
                                        km1=km1,
                                        andata_ritorno=andata_ritorno,
                                        p_arr_b=p_arr_b,
                                    ):
                                        charge_prev_ok = cand_extra
                                    break

                        if charge_prev_ok is not None:
                            min_ric_prev = calcola_minuti_ricarica(
                                auto_cfg,
                                prev_ads["arr_pct"],
                                charge_prev_ok,
                                prev_ads["stazione"]["eff_kw"],
                            )
                            scelta = {
                                **prev_ads,
                                "charge_to": int(charge_prev_ok),
                                "min_ric": int(min_ric_prev),
                                "costo_tempo": int(min_ric_prev) + prev_ads["stazione"]["detour_min"],
                                "chiude": rem_ultima == 0,
                            }
                            if charge_prev_ok > TETTO_RICARICA_INTERMEDIA:
                                nota_extra = (
                                    f"🛡️ Anticipata alla penultima AdS (carica al {charge_prev_ok}% più veloce di una sosta extra): "
                                    f"garantito Piano B (Jolly) in Autostrada."
                                )
                            else:
                                nota_extra = (
                                    "🛡️ Anticipata alla penultima AdS: garantito Piano B (Jolly) in Autostrada "
                                    "senza soste aggiuntive."
                                )

        # Ottimizzazione "Rabbocco Ponte": se un'auto veloce (picco >= 100 kW) si ferma su una colonnina lenta
        # (<= 65 kW), valuta se limitare la carica al solo rabbocco minimo per raggiungere una HPC (>= 150 kW)
        # lungo la stessa tratta risparmiando almeno 5 minuti netti (inclusi i 7 minuti fissi della sosta extra).
        km_fine_tratta_ponte = (
            float(km1)
            if (andata_ritorno and float(scelta["stazione"]["Km"]) < float(km1))
            else float(tot_km)
        )
        ponte_hpc = valuta_rabbocco_ponte_hpc(
            auto_cfg=auto_cfg,
            stazione=scelta["stazione"],
            arr_pct=scelta["arr_pct"],
            target_pieno=scelta["charge_to"],
            pool_avanti=pool + ads_rabbocco_pool,
            km_per_pct=km_per_pct,
            km_fine_tratta=km_fine_tratta_ponte,
        )
        if ponte_hpc is not None:
            scelta = {
                **scelta,
                "charge_to": int(ponte_hpc["target_ponte"]),
                "min_ric": int(ponte_hpc["min_ponte"]),
                "costo_tempo": int(ponte_hpc["min_ponte"]) + scelta["stazione"]["detour_min"],
                "chiude": False,
            }
            hpc_ponte_forzata = ponte_hpc["hpc"]
            nota_ponte = (
                f"⚡ Rabbocco Ponte ({int(round(scelta['stazione']['eff_kw']))} kW → "
                f"{int(round(ponte_hpc['hpc']['eff_kw']))} kW): carichi solo fino al "
                f"{ponte_hpc['target_ponte']}% per raggiungere {ponte_hpc['hpc']['Stazione']} "
                f"risparmiando ~{ponte_hpc['risparmio_min']} min netti."
            )
            nota_extra = f"{nota_ponte} {nota_extra}".strip() if nota_extra else nota_ponte

        st_scelta = scelta["stazione"]
        soste.append(
            {
                **st_scelta,
                "arr_pct": round(scelta["arr_pct"], 1),
                "charge_to": int(scelta["charge_to"]),
                "minuti_ricarica": int(scelta["min_ric"]),
                "jolly": scelta.get("jolly"),
                "avviso": avviso_card,
                "nota_extra": nota_extra,
            }
        )

        curr_km = st_scelta["Km"]
        curr_pct = float(scelta["charge_to"])

        if tratto_verso_b and curr_km >= km1:
            pct_arrivo_b = round(curr_pct, 1)

    pct_finale_reale = curr_pct - (tot_km - curr_km) / km_per_pct
    if pct_arrivo_b is None:
        pct_arrivo_b = round(pct_finale_reale, 1)

    if pct_arrivo_b < p_arr_b - 0.5 or pct_finale_reale < final_req - 0.5:
        return {
            "valido": False,
            "soste": soste,
            "errore": "Non è stato possibile garantire le percentuali di batteria richieste con le colonnine selezionate.",
        }

    tot_min_ric = sum(s["minuti_ricarica"] for s in soste)
    tot_min_dev = sum(s["detour_min"] for s in soste)
    return {
        "valido": True,
        "soste": soste,
        "minuti_ricarica": tot_min_ric,
        "minuti_deviazione": tot_min_dev,
        "tempo_totale_min": int(durata_guida_min + tot_min_ric + tot_min_dev),
        "pct_arrivo_b": round(pct_arrivo_b, 1),
        "pct_rientro_a": round(pct_finale_reale, 1) if andata_ritorno else None,
        "errore": None,
        "sosta_opzionale": False,
    }


def genera_soluzioni_confronto(
    all_ch,
    auto_cfg,
    km1,
    tot_km,
    durata_guida_min,
    andata_ritorno,
    km_per_pct,
    p_start,
    p_arr_b,
    p_home_a,
    stile_codice,
    rete_codice,
    pranzo_dove,
    consenti_rabbocco_ads,
    p_colonnina=15,
    operatore_keywords=None,
    nome_operatore=None,
):
    """
    Genera Soluzione A (Consigliata nel rispetto della rete e dell'operatore scelti) e Soluzione B (Alternativa).
    Se è scelto un operatore specifico ma la copertura non basta, emette un avviso chiaro e calcola
    una soluzione mista di salvataggio privilegiando l'operatore scelto fin dove possibile.
    """
    def _sim(colonnine_pool, **extra_kwargs):
        return simula_piano_viaggio(
            colonnine_pool,
            auto_cfg,
            km1,
            tot_km,
            durata_guida_min,
            andata_ritorno,
            km_per_pct,
            p_start,
            p_arr_b,
            p_home_a,
            stile_codice,
            p_colonnina=p_colonnina,
            pranzo_dove=pranzo_dove,
            colonnine_jolly_pool=all_ch,
            **extra_kwargs,
        )

    solo_ads = [c for c in all_ch if c["in_ads"]]
    solo_tesla = [c for c in all_ch if c["is_tesla"]]

    if rete_codice == "ads":
        pool_primario = solo_ads
    elif rete_codice == "tesla":
        pool_primario = solo_tesla
    else:
        pool_primario = all_ch

    # Rabbocco veloce <= 8 min in Autostrada valido per qualsiasi operatore scelto (Tesla, Ionity, Ewiva, ecc.)
    e_filtro_operatore = (rete_codice in ("tesla", "operatore")) or bool(operatore_keywords)
    extra_ads = solo_ads if (consenti_rabbocco_ads and e_filtro_operatore) else []

    avviso_copertura_op = None
    op_kw_attivo_su_a = operatore_keywords

    if operatore_keywords:
        pool_solo_op = [c for c in pool_primario if match_operatore_kw(c, operatore_keywords)]
        sol_a = _sim(
            pool_solo_op,
            extra_ads_rabbocco=extra_ads,
            consenti_rabbocco_ads=consenti_rabbocco_ads,
        )
        if not sol_a["valido"]:
            # Copertura insufficiente con il solo operatore scelto -> soluzione mista di salvataggio
            avviso_copertura_op = (
                f"⚠️ Attenzione: copertura insufficiente con il solo operatore '{nome_operatore}' "
                f"per completare questo viaggio. È stata calcolata una soluzione mista usando "
                f"'{nome_operatore}' fin dove possibile e colmando i tratti scoperti con altre colonnine veloci."
            )
            pool_salvataggio = pool_primario if pool_primario else all_ch
            sol_a = _sim(
                pool_salvataggio,
                extra_ads_rabbocco=extra_ads,
                consenti_rabbocco_ads=consenti_rabbocco_ads,
                operatore_keywords=operatore_keywords,
                nome_operatore=nome_operatore,
            )
            if not sol_a["valido"] and pool_salvataggio is not all_ch:
                sol_a = _sim(
                    all_ch,
                    operatore_keywords=operatore_keywords,
                    nome_operatore=nome_operatore,
                )
            op_kw_attivo_su_a = None
    else:
        sol_a = _sim(
            pool_primario,
            extra_ads_rabbocco=extra_ads,
            consenti_rabbocco_ads=consenti_rabbocco_ads,
        )

    sol_a["pool_rete"] = "tutte" if avviso_copertura_op else rete_codice
    sol_a["consenti_rabbocco_ads"] = consenti_rabbocco_ads
    sol_a["operatore_keywords"] = op_kw_attivo_su_a
    sol_a["avviso_copertura_op"] = avviso_copertura_op

    # Caso senza soste obbligatorie
    if sol_a["valido"] and len(sol_a["soste"]) == 0:
        if stile_codice in ("caffe", "pranzo"):
            etichetta_b = "Con Pausa Pranzo" if stile_codice == "pranzo" else "Con Pausa Caffè"
            sol_b = _sim(pool_primario, forza_pausa_opzionale=True)
            sol_b["pool_rete"] = rete_codice
            sol_b["consenti_rabbocco_ads"] = False
            return sol_a, sol_b, f"Alternativa — {etichetta_b} opzionale"
        return sol_a, None, "Nessuna alternativa necessaria"

    sol_b = None
    desc_b = "Alternativa — Solo Autostrada"
    usa_fuori_casello = sol_a["valido"] and any(not s["in_ads"] for s in sol_a["soste"])

    # Se A usa colonnine fuori casello (in Tutte o Solo Tesla), prova B Solo Autostrada
    if usa_fuori_casello and solo_ads:
        cand_ads = _sim(solo_ads)
        if cand_ads["valido"]:
            sol_b = cand_ads
            sol_b["pool_rete"] = "ads"
            sol_b["consenti_rabbocco_ads"] = False
            desc_b = "Alternativa — Solo Autostrada (zero caselli)"

    # Se la rete scelta è Solo Autostrada o Solo Operatore/Tesla e A fallisce, offre comunque B su tutte le colonnine
    if not sol_a["valido"] and rete_codice in ("ads", "tesla", "operatore"):
        cand_tutte = _sim(all_ch)
        if cand_tutte["valido"]:
            sol_b = cand_tutte
            sol_b["pool_rete"] = "tutte"
            sol_b["consenti_rabbocco_ads"] = False
            desc_b = "Alternativa — Tutte le colonnine veloci"

    # Altra opzione concreta escludendo le stazioni scelte in A
    if sol_b is None and sol_a["valido"] and sol_a["soste"]:
        chiavi_a = {(s["Km"], s["Stazione"]) for s in sol_a["soste"]}
        cand_alt = _sim(
            pool_primario,
            extra_ads_rabbocco=extra_ads,
            consenti_rabbocco_ads=consenti_rabbocco_ads,
            escludi_chiavi=chiavi_a,
        )
        if cand_alt["valido"]:
            sol_b = cand_alt
            sol_b["pool_rete"] = rete_codice
            sol_b["consenti_rabbocco_ads"] = consenti_rabbocco_ads
            tutte_ads_b = all(s["in_ads"] for s in sol_b["soste"])
            desc_b = "Alternativa — Solo Autostrada" if tutte_ads_b else "Alternativa — Altra opzione di sosta"
        elif rete_codice == "ads":
            # Se non esiste un'altra sequenza Solo AdS, confronta con Tutte le colonnine veloci
            cand_tutte = _sim(all_ch, escludi_chiavi=chiavi_a)
            if cand_tutte["valido"]:
                sol_b = cand_tutte
                sol_b["pool_rete"] = "tutte"
                sol_b["consenti_rabbocco_ads"] = False
                desc_b = "Alternativa — Anche fuori casello"

    # In modalità 'tutte' senza filtro operatore specifico, scambia A e B se B risulta più veloce
    if rete_codice == "tutte" and not operatore_keywords and sol_a["valido"] and sol_b and sol_b["valido"]:
        if (sol_b["tempo_totale_min"], len(sol_b["soste"])) < (sol_a["tempo_totale_min"], len(sol_a["soste"])):
            sol_a, sol_b = sol_b, sol_a
            tutte_ads_b = all(s["in_ads"] for s in sol_b["soste"])
            desc_b = "Alternativa — Solo Autostrada" if tutte_ads_b else "Alternativa — Altra opzione"

    return sol_a, sol_b, desc_b


def url_gmaps_poi(nome_poi, lat, lon, categoria_poi=""):
    """Genera l'URL ufficiale Google Maps con categoria, nome reale del POI/locale e coordinate esatte."""
    nome_pulito = str(nome_poi or "").strip()
    cat_pulita = str(categoria_poi or "").strip()
    coords = f"{float(lat):.5f},{float(lon):.5f}"
    if nome_pulito and nome_pulito not in (FALLBACK_FOOD_NAME, FALLBACK_MALL_NAME):
        if cat_pulita and cat_pulita.lower() not in nome_pulito.lower():
            query_testo = f"{cat_pulita} {nome_pulito} {coords}"
        else:
            query_testo = f"{nome_pulito} {coords}"
    elif cat_pulita:
        query_testo = f"{cat_pulita} {coords}"
    else:
        query_testo = coords
    return f"https://www.google.com/maps/search/?api=1&query={quote(query_testo)}"


def costruisci_badge_locali(lat, lon, has_food, has_mall, food_name="", mall_name=""):
    """Genera i badge HTML cliccabili verso Google Maps per Ristorante e Centro Commerciale."""
    badges = []
    if has_food:
        url_food = html.escape(url_gmaps_poi(food_name, lat, lon, categoria_poi="ristorante"), quote=True)
        badges.append(
            f'<a class="ev-badge ev-b-blue ev-badge-link" href="{url_food}" '
            f'target="_blank" rel="noopener noreferrer" '
            f'title="Apri su Google Maps">🍽️ Ristorante</a>'
        )
    if has_mall:
        url_mall = html.escape(url_gmaps_poi(mall_name, lat, lon, categoria_poi="centro commerciale"), quote=True)
        badges.append(
            f'<a class="ev-badge ev-b-purple ev-badge-link" href="{url_mall}" '
            f'target="_blank" rel="noopener noreferrer" '
            f'title="Apri su Google Maps">🛒 Centro Comm.</a>'
        )
    return badges


def testo_preferenza_sosta(stile_codice):
    """Restituisce la descrizione esplicita della preferenza di sosta applicata."""
    if stile_codice == "caffe":
        return "☕ Pausa Caffè / Pranzo in Autogrill (resta in Autostrada)"
    if stile_codice == "pranzo":
        return "🍽️ Sosta Pranzo Fuori Casello (Centro Comm. / Ristorante)"
    return "⚡ Viaggio Veloce / Diretto (solo soste strettamente necessarie)"


def calcola_cronologia_e_costi(sol, res):
    """
    Arricchisce una soluzione valida con:
    - orari stimati (HH:MM) di arrivo/ripartenza per ogni sosta, arrivo a destinazione,
      ripartenza del ritorno (dopo permanenza) e rientro finale a casa;
    - chilometraggio azzerato a 0.0 km per le soste della tratta di ritorno (km_tratta);
    - riepilogo economico completo (energia casa + ricarica colonnine + pedaggio + totale).
    """
    if not sol or not sol.get("valido"):
        return sol

    andata_ritorno = bool(res.get("andata_ritorno", False))
    km1 = float(sol.get("km1", res["km1"]))
    tot_km = float(sol.get("tot_km", res["tot_km"]))
    km2 = max(0.0, tot_km - km1) if andata_ritorno else 0.0
    tot_dur = float(sol.get("tot_dur", res["tot_dur"]))
    dur1 = float(sol.get("dur1", res.get("dur1", tot_dur / 2.0 if andata_ritorno else tot_dur)))
    dur2 = float(sol.get("dur2", res.get("dur2", max(0.0, tot_dur - dur1)))) if andata_ritorno else 0.0

    # Ora di partenza in minuti dalla mezzanotte
    ora_part_str = str(res.get("ora_partenza", "09:00"))
    try:
        hh_p, mm_p = [int(x) for x in ora_part_str.split(":")[:2]]
        min_partenza = hh_p * 60 + mm_p
    except Exception:
        min_partenza = 9 * 60
    sosta_dest_min = int(round(float(res.get("ore_permanenza", 2.0)) * 60.0)) if andata_ritorno else 0

    vel_andata = (dur1 / km1) if km1 > 0.0 else 0.0
    vel_ritorno = (dur2 / km2) if km2 > 0.0 else vel_andata

    soste_orig = sol.get("soste", [])
    soste_andata = []
    soste_ritorno = []
    for s in soste_orig:
        e_ritorno = andata_ritorno and ("Ritorno" in str(s.get("Tratta", "")) or float(s["Km"]) > km1 + 0.05)
        if e_ritorno:
            soste_ritorno.append(dict(s))
        else:
            soste_andata.append(dict(s))

    # Cronologia tratta di andata (Km 0 -> km1)
    t_curr = float(min_partenza)
    km_prev = 0.0
    for s in soste_andata:
        km_s = max(km_prev, float(s["Km"]))
        guida_min = (km_s - km_prev) * vel_andata
        dev_tot = float(s.get("detour_min", 0))
        dev_in = dev_tot / 2.0
        dev_out = dev_tot - dev_in
        t_arr = t_curr + guida_min + dev_in
        t_rip = t_arr + float(s.get("minuti_ricarica", 0))
        s["km_tratta"] = round(float(s["Km"]), 1)
        s["e_ritorno"] = False
        s["ora_arrivo"] = formatta_ora_min(t_arr)
        s["ora_ripartenza"] = formatta_ora_min(t_rip)
        t_curr = t_rip + dev_out
        km_prev = km_s

    t_arrivo_b = t_curr + max(0.0, (km1 - km_prev) * vel_andata)
    ora_arrivo_b = formatta_ora_min(t_arrivo_b)

    # Cronologia tratta di ritorno (Km azzerato a 0.0 da destinazione -> km2)
    ora_partenza_ritorno = None
    ora_rientro_a = None
    if andata_ritorno:
        t_part_rit = t_arrivo_b + float(sosta_dest_min)
        ora_partenza_ritorno = formatta_ora_min(t_part_rit)
        t_curr = t_part_rit
        km_prev_r = 0.0
        for s in soste_ritorno:
            km_rel = max(0.0, round(float(s["Km"]) - km1, 1))
            km_eff = max(km_prev_r, km_rel)
            guida_min = (km_eff - km_prev_r) * vel_ritorno
            dev_tot = float(s.get("detour_min", 0))
            dev_in = dev_tot / 2.0
            dev_out = dev_tot - dev_in
            t_arr = t_curr + guida_min + dev_in
            t_rip = t_arr + float(s.get("minuti_ricarica", 0))
            s["km_tratta"] = km_rel
            s["e_ritorno"] = True
            s["ora_arrivo"] = formatta_ora_min(t_arr)
            s["ora_ripartenza"] = formatta_ora_min(t_rip)
            t_curr = t_rip + dev_out
            km_prev_r = km_eff

        t_rientro_a = t_curr + max(0.0, (km2 - km_prev_r) * vel_ritorno)
        ora_rientro_a = formatta_ora_min(t_rientro_a)

    soste_arricchite = soste_andata + soste_ritorno

    # Calcolo economico completo: energia da casa + ricariche colonnine + pedaggio autostradale
    bat_kwh = float(res["auto_cfg"]["bat_kwh"])
    kwh_viaggio = float(sol.get("kwh_totali", res["kwh_totali"]))
    kwh_colonnine = round(
        sum(
            max(0.0, (float(s["charge_to"]) - float(s["arr_pct"])) / 100.0 * bat_kwh)
            for s in soste_arricchite
        ),
        1,
    )
    kwh_casa = max(0.0, round(kwh_viaggio - kwh_colonnine, 1))
    prezzo_casa = float(res.get("costo_casa_kwh", 0.25))
    prezzo_col = float(res.get("costo_col_kwh", 0.69))
    costo_casa = round(kwh_casa * prezzo_casa, 2)
    costo_col = round(kwh_colonnine * prezzo_col, 2)

    tipo_strada_txt = str(sol.get("tipo_strada", ""))
    senza_autostrada = ("Senza Autostrada" in tipo_strada_txt) or (res.get("strada_codice") == "no_auto")
    costo_pedaggio = 0.0 if senza_autostrada else round(tot_km * COSTO_PEDAGGIO_KM, 2)
    costo_totale = round(costo_casa + costo_col + costo_pedaggio, 2)

    return {
        **sol,
        "soste": soste_arricchite,
        "km2": round(km2, 1),
        "dur1": int(round(dur1)),
        "dur2": int(round(dur2)),
        "ora_partenza": ora_part_str,
        "ora_arrivo_b": ora_arrivo_b,
        "ora_partenza_ritorno": ora_partenza_ritorno,
        "ora_rientro_a": ora_rientro_a,
        "kwh_casa": kwh_casa,
        "kwh_colonnine": kwh_colonnine,
        "costo_casa": costo_casa,
        "costo_colonnine": costo_col,
        "costo_pedaggio": costo_pedaggio,
        "costo_totale": costo_totale,
        "senza_autostrada": senza_autostrada,
    }


def risolvi_soluzione_interattiva(sol_base, res, codice_sol):
    """
    Ricalcola dinamicamente le soste, i tempi, gli orari, i costi e le percentuali finali
    tenendo conto delle eventuali stazioni alternative selezionate nei menu a tendina.
    Restituisce (soluzione_aggiornata, lista_controlli_selectbox).
    """
    if not sol_base or not sol_base.get("valido") or "auto_cfg" not in res:
        return sol_base, []
    if not sol_base.get("soste"):
        return calcola_cronologia_e_costi(sol_base, res), []

    all_ch = sol_base.get("all_ch", res["all_ch"])
    pool_rete = sol_base.get("pool_rete", res.get("rete_codice", "tutte"))
    consenti_rabbocco = sol_base.get("consenti_rabbocco_ads", False)

    if pool_rete == "ads":
        pool_compatibile = [c for c in all_ch if c["in_ads"]]
    elif pool_rete == "tesla":
        pool_compatibile = [c for c in all_ch if c["is_tesla"]]
    else:
        pool_compatibile = list(all_ch)

    op_kw_sol = sol_base.get("operatore_keywords")
    if op_kw_sol:
        pool_compatibile = [c for c in pool_compatibile if match_operatore_kw(c, op_kw_sol)]

    calc_id = res["calc_id"]
    km1 = sol_base.get("km1", res["km1"])
    tot_km = sol_base.get("tot_km", res["tot_km"])
    tot_dur = sol_base.get("tot_dur", res["tot_dur"])
    andata_ritorno = res["andata_ritorno"]
    km_per_pct = sol_base.get("km_per_pct", res["km_per_pct"])
    auto_cfg = res["auto_cfg"]
    p_start = float(res["p_start"])
    p_arr_b = float(res["p_arr_b"])
    p_home_a = float(res["p_home_a"])
    final_req = p_home_a if andata_ritorno else p_arr_b
    stile_codice = res["stile_codice"]
    soste_default = sol_base["soste"]
    n_soste = len(soste_default)

    curr_km = 0.0
    curr_pct = p_start
    soste_attive = []
    controlli = []

    for i, def_s in enumerate(soste_default):
        # Se l'utente ha spostato una sosta precedente oltre questa posizione, salta eventuali soste superate
        if i > 0 and def_s["Km"] <= curr_km + 2.0:
            continue

        # Autonomia massima raggiungibile dalla posizione corrente fino al 5% minimo di batteria
        max_km_5 = curr_km + max(0.0, (curr_pct - 5.0) * km_per_pct)
        if andata_ritorno and def_s["Km"] < km1:
            max_reach = min(max_km_5, km1)
        else:
            max_reach = min(max_km_5, tot_km - 1.0)

        e_sosta_ritorno = andata_ritorno and ("Ritorno" in str(def_s.get("Tratta", "")) or def_s["Km"] > km1 + 0.05)
        e_sosta1_batteria_bassa = (i == 0) and (not e_sosta_ritorno) and (p_start <= 25.0)

        # Includi eventuali AdS per rabbocco veloce se l'opzione è attiva con Solo Tesla o operatore preferito
        pool_corrente = list(pool_compatibile)
        chiavi_esistenti = {(c["Km"], c["Stazione"]) for c in pool_corrente}
        if (pool_rete in ("tesla", "operatore") or bool(op_kw_sol)) and consenti_rabbocco:
            for c in all_ch:
                if c["in_ads"] and (c["Km"], c["Stazione"]) not in chiavi_esistenti:
                    pool_corrente.append(c)
                    chiavi_esistenti.add((c["Km"], c["Stazione"]))

        # Se si parte con batteria bassa (<= 25%), nel menu Sosta 1 includi anche le colonnine veloci
        # tra Km 1.0 e Km 10.0 vicine alla partenza (prima di entrare in autostrada)
        if e_sosta1_batteria_bassa:
            for c in all_ch:
                if (1.0 <= c["Km"] <= 10.0) and (c["Km"], c["Stazione"]) not in chiavi_esistenti:
                    pool_corrente.append(c)
                    chiavi_esistenti.add((c["Km"], c["Stazione"]))

        # Mostra tutte le colonnine compatibili raggiungibili (5% <= batteria <= 75%),
        # ordinate per vicinanza crescente alla stazione principale con delta km esplicito
        # (con contachilometri azzerato da destinazione per le soste del ritorno)
        if e_sosta_ritorno:
            km_inf_filtro = max(curr_km + 10.0, km1 + 3.0)
        elif e_sosta1_batteria_bassa:
            km_inf_filtro = curr_km + 1.0
        else:
            km_inf_filtro = curr_km + 10.0
        dest_breve = res["dest"]

        def _fmt_km_opz(km_val):
            if e_sosta_ritorno:
                return f"Km {max(0.0, km_val - km1):.1f} da {dest_breve}"
            return f"Km {km_val:.1f}"

        def_key = f"{def_s['Km']:.1f}|{def_s['Stazione']}"
        arr_def = max(0.0, curr_pct - (def_s["Km"] - curr_km) / km_per_pct)
        opzioni_id = [def_key]
        mappa_etichette = {
            def_key: f"⭐ {def_s['Stazione']} • {_fmt_km_opz(def_s['Km'])} (Arrivo al {arr_def:.0f}%)"
        }
        mappa_stazioni = {def_key: def_s}

        alternative = [
            c for c in pool_corrente
            if (km_inf_filtro <= c["Km"] <= max(max_reach, def_s["Km"]))
            and (
                (2.0 if (e_sosta1_batteria_bassa and c["Km"] <= 10.0) else 5.0)
                <= (curr_pct - (c["Km"] - curr_km) / km_per_pct)
                <= 75.0
            )
            and f"{c['Km']:.1f}|{c['Stazione']}" != def_key
        ]
        alternative.sort(key=lambda x: (abs(x["Km"] - def_s["Km"]), x["Km"]))

        for alt in alternative:
            alt_key = f"{alt['Km']:.1f}|{alt['Stazione']}"
            if alt_key in mappa_stazioni:
                continue
            arr_alt = max(0.0, curr_pct - (alt["Km"] - curr_km) / km_per_pct)
            delta_km = alt["Km"] - def_s["Km"]
            delta_int = int(round(abs(delta_km)))
            direzione = f"⬅️ Prima (-{delta_int} km)" if delta_km < 0 else f"➡️ Dopo (+{delta_int} km)"
            opzioni_id.append(alt_key)
            mappa_etichette[alt_key] = (
                f"{direzione} • {alt['Stazione']} • {_fmt_km_opz(alt['Km'])} (Arrivo al {arr_alt:.0f}%)"
            )
            mappa_stazioni[alt_key] = alt

        sel_key = f"sel_alt_{calc_id}_{codice_sol}_{i}"
        scelto_id = st.session_state.get(sel_key, def_key)
        if scelto_id not in mappa_stazioni:
            scelto_id = def_key
            st.session_state[sel_key] = def_key

        st_scelta = mappa_stazioni[scelto_id]
        cambiata_dall_utente = scelto_id != def_key
        arr_pct = curr_pct - (st_scelta["Km"] - curr_km) / km_per_pct

        # Calcola il Jolly (Piano B entro il 4% d'emergenza) rigorosamente entro la tratta corrente
        km_limite_jolly = float(km1) if (andata_ritorno and not e_sosta_ritorno) else float(tot_km)
        jolly_dyn = trova_jolly_stazione(
            st_scelta,
            arr_pct,
            all_ch,
            km_per_pct,
            km_fine_tratta=km_limite_jolly,
        )

        # Ricalcola carica necessaria, minuti e avvisi rispettando il tetto rigido dell'80%
        sosta_hpc_extra = None
        if (not cambiata_dall_utente) and abs(arr_pct - def_s["arr_pct"]) < 0.6:
            charge_to = int(def_s["charge_to"])
            min_ric = int(def_s["minuti_ricarica"])
            avviso_dyn = def_s.get("avviso")
            nota_dyn = def_s.get("nota_extra")
        else:
            tetto_eccezione = 95
            next_ch_dyn = [c for c in pool_corrente if st_scelta["Km"] + 3.0 < c["Km"] < tot_km - 1.0]
            arr_molto_alta_dyn = (final_req > 75.0) or (andata_ritorno and st_scelta["Km"] < km1 and p_arr_b > 75.0)

            if i == n_soste - 1:
                if sol_base.get("sosta_opzionale"):
                    min_pausa = 35 if stile_codice == "pranzo" else 15
                    charge_to = calcola_soc_per_minuti(auto_cfg, arr_pct, min_pausa, st_scelta["eff_kw"], max_soc=float(TETTO_RICARICA_INTERMEDIA))
                    charge_to = min(TETTO_RICARICA_INTERMEDIA, max(int(math.ceil(arr_pct)) + 5, charge_to))
                else:
                    need_end = math.ceil((tot_km - st_scelta["Km"]) / km_per_pct + final_req + 1.0)
                    need_b = (
                        math.ceil((km1 - st_scelta["Km"]) / km_per_pct + p_arr_b + 1.0)
                        if (andata_ritorno and st_scelta["Km"] < km1)
                        else 0
                    )
                    needed_pct = max(int(math.ceil(arr_pct + 4)), need_end, need_b)
                    # Supera l'80% (fino a 90%) se conviene sul tempo totale rispetto a una 2ª sosta;
                    # fino a 95% solo se non esistono altre colonnine avanti o % arrivo > 75%
                    if not next_ch_dyn or arr_molto_alta_dyn:
                        charge_to = min(tetto_eccezione, needed_pct)
                    elif needed_pct <= TETTO_RICARICA_INTERMEDIA:
                        charge_to = int(needed_pct)
                    elif needed_pct <= TETTO_SOSTA_UNICA_OTTIMALE and conviene_sosta_unica_oltre_80(
                        auto_cfg=auto_cfg,
                        stazione=st_scelta,
                        arr_pct=arr_pct,
                        target_unico=needed_pct,
                        next_ch=next_ch_dyn,
                        km_per_pct=km_per_pct,
                        km_target_fine=tot_km,
                        req_fine=final_req,
                        km1=km1,
                        andata_ritorno=andata_ritorno,
                        p_arr_b=p_arr_b,
                    ):
                        charge_to = int(needed_pct)
                    else:
                        charge_to = TETTO_RICARICA_INTERMEDIA
            else:
                next_km_ref = soste_default[i + 1]["Km"]
                p_col_ref = float(res.get("p_colonnina", 15))
                need_next = math.ceil((next_km_ref - st_scelta["Km"]) / km_per_pct + p_col_ref + 1.0)
                need_b = (
                    math.ceil((km1 - st_scelta["Km"]) / km_per_pct + p_arr_b + 1.0)
                    if (andata_ritorno and st_scelta["Km"] < km1 <= next_km_ref)
                    else 0
                )
                base_dyn = min(TETTO_RICARICA_INTERMEDIA, int(math.ceil(arr_pct + 15)))
                needed_pct = max(base_dyn, need_next, need_b)
                next_ch_pre_b_dyn = (
                    [c for c in pool_corrente if st_scelta["Km"] + 3.0 < c["Km"] < km1 - 1.0]
                    if (andata_ritorno and st_scelta["Km"] < km1 <= next_km_ref)
                    else [1]
                )
                if andata_ritorno and st_scelta["Km"] < km1 <= next_km_ref and (not next_ch_pre_b_dyn or p_arr_b > 75.0) and need_b > TETTO_RICARICA_INTERMEDIA:
                    charge_to = min(tetto_eccezione, need_b)
                else:
                    charge_to = min(TETTO_RICARICA_INTERMEDIA, needed_pct)

            min_ric = calcola_minuti_ricarica(auto_cfg, arr_pct, charge_to, st_scelta["eff_kw"])
            avviso_dyn = None
            nota_dyn = (
                "🔄 Stazione cambiata manualmente: ricarica, tempi e batteria all'arrivo aggiornati."
                if cambiata_dall_utente
                else def_s.get("nota_extra")
            )

            # Ottimizzazione "Rabbocco Ponte" anche in caso di selezione manuale di una colonnina lenta (<= 65 kW)
            if (not sol_base.get("sosta_opzionale")) and float(st_scelta.get("eff_kw", 0.0)) <= SOGLIA_COLONNINA_LENTA_KW:
                ha_next_stessa_tratta = False
                if i < n_soste - 1:
                    next_def = soste_default[i + 1]
                    next_e_rit = andata_ritorno and (
                        "Ritorno" in str(next_def.get("Tratta", "")) or float(next_def["Km"]) > km1 + 0.05
                    )
                    ha_next_stessa_tratta = next_e_rit == e_sosta_ritorno

                pool_ponte_dyn = [soste_default[i + 1]] if ha_next_stessa_tratta else pool_corrente
                ponte_dyn = valuta_rabbocco_ponte_hpc(
                    auto_cfg=auto_cfg,
                    stazione=st_scelta,
                    arr_pct=arr_pct,
                    target_pieno=charge_to,
                    pool_avanti=pool_ponte_dyn,
                    km_per_pct=km_per_pct,
                    km_fine_tratta=km_limite_jolly,
                )
                if ponte_dyn is not None:
                    charge_to = int(ponte_dyn["target_ponte"])
                    min_ric = int(ponte_dyn["min_ponte"])
                    nota_dyn = (
                        f"⚡ Rabbocco Ponte ({int(round(st_scelta['eff_kw']))} kW → "
                        f"{int(round(ponte_dyn['hpc']['eff_kw']))} kW): carichi solo fino al "
                        f"{charge_to}% per raggiungere {ponte_dyn['hpc']['Stazione']} "
                        f"risparmiando ~{ponte_dyn['risparmio_min']} min netti."
                    )
                    if not ha_next_stessa_tratta:
                        hpc_st = ponte_dyn["hpc"]
                        arr_hpc_dyn = float(charge_to) - (float(hpc_st["Km"]) - float(st_scelta["Km"])) / km_per_pct
                        next_ch_hpc = [c for c in pool_corrente if float(hpc_st["Km"]) + 3.0 < float(c["Km"]) < tot_km - 1.0]
                        if i == n_soste - 1:
                            need_end_h = math.ceil((tot_km - float(hpc_st["Km"])) / km_per_pct + final_req + 1.0)
                            need_b_h = (
                                math.ceil((km1 - float(hpc_st["Km"])) / km_per_pct + p_arr_b + 1.0)
                                if (andata_ritorno and float(hpc_st["Km"]) < km1)
                                else 0
                            )
                            needed_hpc = max(int(math.ceil(arr_hpc_dyn + 4)), need_end_h, need_b_h)
                            if not next_ch_hpc or arr_molto_alta_dyn:
                                charge_to_hpc = min(tetto_eccezione, needed_hpc)
                            elif needed_hpc <= TETTO_RICARICA_INTERMEDIA:
                                charge_to_hpc = int(needed_hpc)
                            elif needed_hpc <= TETTO_SOSTA_UNICA_OTTIMALE and conviene_sosta_unica_oltre_80(
                                auto_cfg=auto_cfg,
                                stazione=hpc_st,
                                arr_pct=arr_hpc_dyn,
                                target_unico=needed_hpc,
                                next_ch=next_ch_hpc,
                                km_per_pct=km_per_pct,
                                km_target_fine=tot_km,
                                req_fine=final_req,
                                km1=km1,
                                andata_ritorno=andata_ritorno,
                                p_arr_b=p_arr_b,
                            ):
                                charge_to_hpc = int(needed_hpc)
                            else:
                                charge_to_hpc = TETTO_RICARICA_INTERMEDIA
                        else:
                            next_km_h = float(soste_default[i + 1]["Km"])
                            p_col_h = float(res.get("p_colonnina", 15))
                            need_next_h = math.ceil((next_km_h - float(hpc_st["Km"])) / km_per_pct + p_col_h + 1.0)
                            need_b_h = (
                                math.ceil((km1 - float(hpc_st["Km"])) / km_per_pct + p_arr_b + 1.0)
                                if (andata_ritorno and float(hpc_st["Km"]) < km1 <= next_km_h)
                                else 0
                            )
                            needed_hpc = max(int(math.ceil(arr_hpc_dyn + 15)), need_next_h, need_b_h)
                            charge_to_hpc = min(TETTO_SOSTA_UNICA_OTTIMALE if needed_hpc <= TETTO_SOSTA_UNICA_OTTIMALE else tetto_eccezione, max(TETTO_RICARICA_INTERMEDIA if needed_hpc > TETTO_RICARICA_INTERMEDIA else needed_hpc, needed_hpc))
                            if needed_hpc > TETTO_RICARICA_INTERMEDIA and not arr_molto_alta_dyn:
                                charge_to_hpc = min(TETTO_RICARICA_INTERMEDIA, needed_hpc)
                                if needed_hpc <= TETTO_SOSTA_UNICA_OTTIMALE and conviene_sosta_unica_oltre_80(
                                    auto_cfg=auto_cfg,
                                    stazione=hpc_st,
                                    arr_pct=arr_hpc_dyn,
                                    target_unico=needed_hpc,
                                    next_ch=next_ch_hpc,
                                    km_per_pct=km_per_pct,
                                    km_target_fine=km1 if (andata_ritorno and float(hpc_st["Km"]) < km1) else tot_km,
                                    req_fine=p_arr_b if (andata_ritorno and float(hpc_st["Km"]) < km1) else final_req,
                                ):
                                    charge_to_hpc = int(needed_hpc)

                        min_ric_hpc = calcola_minuti_ricarica(auto_cfg, arr_hpc_dyn, charge_to_hpc, hpc_st["eff_kw"])
                        jolly_hpc_dyn = trova_jolly_stazione(
                            hpc_st,
                            arr_hpc_dyn,
                            all_ch,
                            km_per_pct,
                            km_fine_tratta=km_limite_jolly,
                        )
                        sosta_hpc_extra = {
                            **hpc_st,
                            "arr_pct": round(arr_hpc_dyn, 1),
                            "charge_to": int(charge_to_hpc),
                            "minuti_ricarica": int(min_ric_hpc),
                            "jolly": jolly_hpc_dyn,
                            "avviso": None,
                            "nota_extra": "⚡ Completamento rapido su HPC dopo Rabbocco Ponte: massima velocità di ricarica.",
                        }

            if (
                sosta_hpc_extra is None
                and i == n_soste - 1
                and not sol_base.get("sosta_opzionale")
                and (charge_to - (tot_km - st_scelta["Km"]) / km_per_pct) < final_req - 0.5
            ):
                if charge_to <= TETTO_RICARICA_INTERMEDIA and next_ch_dyn:
                    avviso_dyn = (
                        "⚠️ Stazione anticipata con tetto rigido all'80%: con questa sola sosta "
                        "la batteria finale scende sotto la soglia richiesta. Scegli una stazione più avanti "
                        "oppure prevedi una seconda breve sosta lungo il percorso."
                    )
                else:
                    avviso_dyn = (
                        "⚠️ Stazione troppo anticipata: anche caricando al massimo la batteria finale scende "
                        "sotto la soglia minima richiesta. Scegli una stazione più avanti lungo il percorso."
                    )
            elif sosta_hpc_extra is None and stile_codice in ("caffe", "pranzo") and st_scelta.get("has_food", 0) == 0 and st_scelta.get("has_mall", 0) == 0:
                avviso_dyn = "⚠️ Nessun ristorante o centro commerciale mappato entro 200 m da questa stazione."

        sosta_agg = {
            **st_scelta,
            "arr_pct": round(arr_pct, 1),
            "charge_to": int(charge_to),
            "minuti_ricarica": int(min_ric),
            "jolly": jolly_dyn,
            "avviso": avviso_dyn,
            "nota_extra": nota_dyn,
        }
        soste_attive.append(sosta_agg)
        controlli.append((sel_key, opzioni_id, mappa_etichette))

        if sosta_hpc_extra is not None:
            soste_attive.append(sosta_hpc_extra)
            controlli.append(None)
            curr_km = float(sosta_hpc_extra["Km"])
            curr_pct = float(sosta_hpc_extra["charge_to"])
        else:
            curr_km = st_scelta["Km"]
            curr_pct = float(charge_to)

    # Calcolo esatto della batteria stimata all'arrivo (B) e al rientro a casa (A)
    if andata_ritorno:
        soste_pre_b = [s for s in soste_attive if s["Km"] <= km1]
        if soste_pre_b:
            ult_b = soste_pre_b[-1]
            pct_arrivo_b = round(ult_b["charge_to"] - (km1 - ult_b["Km"]) / km_per_pct, 1)
        else:
            pct_arrivo_b = round(p_start - km1 / km_per_pct, 1)
        pct_rientro_a = round(curr_pct - (tot_km - curr_km) / km_per_pct, 1)
    else:
        pct_arrivo_b = round(curr_pct - (tot_km - curr_km) / km_per_pct, 1)
        pct_rientro_a = None

    tot_min_ric = sum(s["minuti_ricarica"] for s in soste_attive)
    tot_min_dev = sum(s["detour_min"] for s in soste_attive)
    sol_agg = {
        **sol_base,
        "soste": soste_attive,
        "minuti_ricarica": tot_min_ric,
        "minuti_deviazione": tot_min_dev,
        "tempo_totale_min": int(tot_dur + tot_min_ric + tot_min_dev),
        "pct_arrivo_b": pct_arrivo_b,
        "pct_rientro_a": pct_rientro_a,
    }
    return calcola_cronologia_e_costi(sol_agg, res), controlli


def render_step_header(numero, titolo):
    """Renderizza l'intestazione compatta di uno step del flusso guidato."""
    st.markdown(
        f'<div class="ev-step-header">'
        f'<span class="ev-step-title">{html.escape(titolo)}</span>'
        f'<span class="ev-step-tag">STEP {numero} / 3</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def costruisci_righe_locali(lat, lon, food_name, mall_name):
    """Genera le righe chiave-valore cliccabili di ristorante e centro commerciale, evitando duplicati identici."""
    righe = []
    food_pulito = (food_name or "").strip()
    mall_pulito = (mall_name or "").strip()
    nomi_coincidenti = bool(food_pulito and mall_pulito and food_pulito.lower() == mall_pulito.lower())

    if food_pulito:
        url_food = html.escape(url_gmaps_poi(food_pulito, lat, lon, categoria_poi="ristorante"), quote=True)
        righe.append(
            f'<div class="ev-kv">'
            f'<span class="ev-k">🍽️ Vicino a</span>'
            f'<span class="ev-v"><a href="{url_food}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#60A5FA; text-decoration:none;">{html.escape(food_pulito)} ↗</a></span>'
            f'</div>'
        )
    if mall_pulito and not nomi_coincidenti:
        url_mall = html.escape(url_gmaps_poi(mall_pulito, lat, lon, categoria_poi="centro commerciale"), quote=True)
        righe.append(
            f'<div class="ev-kv">'
            f'<span class="ev-k">🛒 Vicino a</span>'
            f'<span class="ev-v"><a href="{url_mall}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#C084FC; text-decoration:none;">{html.escape(mall_pulito)} ↗</a></span>'
            f'</div>'
        )
    return "".join(righe)


def render_banner_verdetto(sol_a, res):
    """Mostra il verdetto immediato con riassunto telegrafico Andata/Ritorno in cima ai risultati."""
    if not sol_a or not sol_a.get("valido"):
        st.markdown(
            '<div class="ev-card" style="background:linear-gradient(135deg, #2D1A1E 0%, #1E222D 100%); '
            'border-color:rgba(239,68,68,0.45); border-left:5px solid #EF4444; padding:16px 18px;">'
            '<div style="font-size:1.12rem; font-weight:800; color:#FCA5A5;">⚠️ IMPOSSIBILE COMPLETARE CON I FILTRI ATTUALI</div>'
            '<div class="ev-card-sub" style="margin-top:4px;">Verifica Soluzione B o allenta i filtri di rete/operatore.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    soste_a = sol_a.get("soste", [])
    n_soste = len(soste_a)
    tempo_tot_txt = formatta_durata(sol_a["tempo_totale_min"])
    bat_kwh = float(res["auto_cfg"]["bat_kwh"])
    km_fine_andata = float(sol_a.get("km1", res["km1"]))

    def _formatta_km_soste(soste_tratta, is_ritorno=False):
        km_vals = []
        for s in soste_tratta:
            km_rel = max(0.0, float(s["Km"]) - km_fine_andata) if is_ritorno else float(s["Km"])
            km_vals.append(f"{int(round(km_rel))} km")
        if len(km_vals) == 1:
            return f"dopo {km_vals[0]}"
        if len(km_vals) == 2:
            return f"dopo {km_vals[0]} e {km_vals[1]}"
        return f"dopo {', '.join(km_vals[:-1])} e {km_vals[-1]}"

    def _riassunto_tratta_telegrafico(soste_tratta, solo_andata=False, is_ritorno=False):
        if not soste_tratta:
            return "nessuna sosta (arrivo diretto)" if solo_andata else "nessuna sosta"
        km_info = _formatta_km_soste(soste_tratta, is_ritorno=is_ritorno)
        if len(soste_tratta) == 1:
            s = soste_tratta[0]
            kwh_s = max(1, int(round(max(0.0, (float(s["charge_to"]) - float(s["arr_pct"])) / 100.0 * bat_kwh))))
            min_s = int(s["minuti_ricarica"])
            nome_s = html.escape(s["Stazione"])
            return f"1 sosta di ~{min_s} min (+{kwh_s} kWh) presso <strong>{nome_s}</strong> ({km_info})"
        min_tot = sum(int(s["minuti_ricarica"]) for s in soste_tratta)
        kwh_tot = max(
            1,
            int(
                round(
                    sum(
                        max(0.0, (float(s["charge_to"]) - float(s["arr_pct"])) / 100.0 * bat_kwh)
                        for s in soste_tratta
                    )
                )
            ),
        )
        nomi = ", ".join(html.escape(s["Stazione"]) for s in soste_tratta)
        return f"{len(soste_tratta)} soste per ~{min_tot} min (+{kwh_tot} kWh) presso <strong>{nomi}</strong> ({km_info})"

    if res["andata_ritorno"]:
        soste_andata = [s for s in soste_a if not s.get("e_ritorno")]
        soste_ritorno = [s for s in soste_a if s.get("e_ritorno")]
        riepilogo_telegrafico = (
            f'<div style="margin-top:9px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.10); '
            f'font-size:0.88rem; color:#F1F5F9; line-height:1.55;">'
            f'<div>➡️ <strong>Andata:</strong> {_riassunto_tratta_telegrafico(soste_andata, solo_andata=False, is_ritorno=False)}</div>'
            f'<div>⬅️ <strong>Ritorno:</strong> {_riassunto_tratta_telegrafico(soste_ritorno, solo_andata=False, is_ritorno=True)}</div>'
            f'</div>'
        )
    else:
        riepilogo_telegrafico = (
            f'<div style="margin-top:9px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.10); '
            f'font-size:0.88rem; color:#F1F5F9; line-height:1.55;">'
            f'<div>➡️ <strong>Andata:</strong> {_riassunto_tratta_telegrafico(soste_a, solo_andata=True, is_ritorno=False)}</div>'
            f'</div>'
        )

    if n_soste == 0:
        dettaglio = f"Tempo totale stimato: {tempo_tot_txt} • Arrivo a {html.escape(res['dest'])} con ~{sol_a['pct_arrivo_b']:.0f}%"
        if res["andata_ritorno"] and sol_a.get("pct_rientro_a") is not None:
            dettaglio += f" • Rientro con ~{sol_a['pct_rientro_a']:.0f}%"
        st.markdown(
            f'<div class="ev-card" style="background:linear-gradient(135deg, #132C24 0%, #1E222D 100%); '
            f'border-color:rgba(16,185,129,0.5); border-left:5px solid #10B981; padding:16px 18px;">'
            f'<div style="font-size:1.12rem; font-weight:800; color:#34D399;">✅ NESSUNA SOSTA NECESSARIA — Puoi arrivare diretto a destinazione!</div>'
            f'<div class="ev-card-sub" style="margin-top:5px; color:#CBD5E1;">{dettaglio}</div>'
            f'{riepilogo_telegrafico}'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        titolo_soste = "🛑 NECESSARIA 1 SOSTA" if n_soste == 1 else f"🛑 NECESSARIE {n_soste} SOSTE"
        st.markdown(
            f'<div class="ev-card" style="background:linear-gradient(135deg, #2A2318 0%, #1A2338 100%); '
            f'border-color:rgba(245,158,11,0.5); border-left:5px solid #F59E0B; padding:16px 18px;">'
            f'<div style="font-size:1.15rem; font-weight:800; color:#FBBF24;">{titolo_soste}</div>'
            f'<div class="ev-card-sub" style="margin-top:5px; color:#E2E8F0;">'
            f'⏱️ Tempo totale stimato: <strong>{tempo_tot_txt}</strong> '
            f'(di cui {sol_a["minuti_ricarica"]} min di ricarica • Soluzione A consigliata)'
            f'</div>'
            f'{riepilogo_telegrafico}'
            f'</div>',
            unsafe_allow_html=True,
        )


def render_consiglio_batteria_bassa(sol_a, res):
    """
    Mostra un riquadro informativo non invasivo 'Consiglio di buon senso' quando l'utente
    parte con batteria <= 20% oppure arriva alla prima sosta pianificata sotto il 10%.
    """
    pct_partenza = int(round(float(res.get("p_start", 100))))
    soste_a = sol_a.get("soste", []) if (sol_a and sol_a.get("valido")) else []
    prima_arr = float(soste_a[0]["arr_pct"]) if soste_a else None

    mostra_consiglio = (pct_partenza <= 20) or (prima_arr is not None and prima_arr < 10.0)
    if not mostra_consiglio:
        return

    orig_raw = str(res.get("orig", "")).strip()
    orig_esc = html.escape(orig_raw)
    url_col_partenza = html.escape(
        f"https://www.google.com/maps/search/?api=1&query=colonnine+ricarica+rapida+vicino+{quote(orig_raw)}",
        quote=True,
    )

    if prima_arr is not None:
        pct_prima_txt = int(round(prima_arr))
        testo_consiglio = (
            f"💡 <strong>Consiglio di buon senso:</strong> stai partendo con batteria bassa "
            f"(<strong>{pct_partenza}%</strong>). Anche se il percorso arriva alla prima colonnina "
            f"(al <strong>{pct_prima_txt}%</strong>), ti consigliamo di fare un rabbocco veloce "
            f"prima di entrare in autostrada per viaggiare senza rischi."
        )
    elif sol_a and sol_a.get("valido"):
        pct_dest_txt = int(round(float(sol_a.get("pct_arrivo_b", 0.0))))
        testo_consiglio = (
            f"💡 <strong>Consiglio di buon senso:</strong> stai partendo con batteria bassa "
            f"(<strong>{pct_partenza}%</strong>). Anche se il percorso arriva diretto a destinazione "
            f"(al <strong>{pct_dest_txt}%</strong>), ti consigliamo di fare un rabbocco veloce "
            f"prima di entrare in autostrada per viaggiare senza rischi."
        )
    else:
        testo_consiglio = (
            f"💡 <strong>Consiglio di buon senso:</strong> stai partendo con batteria bassa "
            f"(<strong>{pct_partenza}%</strong>). Ti consigliamo di fare un rabbocco veloce "
            f"prima di entrare in autostrada per viaggiare senza rischi."
        )

    st.markdown(
        f'<div class="ev-card" style="background:rgba(59,130,246,0.10); '
        f'border:1px solid rgba(59,130,246,0.38); border-left:4px solid #3B82F6; padding:12px 15px;">'
        f'<div style="font-size:0.89rem; color:#E2E8F0; line-height:1.45;">{testo_consiglio}</div>'
        f'<div style="margin-top:8px;">'
        f'<a href="{url_col_partenza}" target="_blank" rel="noopener noreferrer" '
        f'style="color:#60A5FA; font-weight:700; font-size:0.88rem; text-decoration:none;">'
        f'📍 Trova colonnina veloce vicino a {orig_esc} prima del casello ↗'
        f'</a>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_card_sosta(indice, sosta, res=None, km1_offset=0.0, tot_km_sol=None, colore_bordo="#10B981"):
    """Renderizza una singola fermata di ricarica come Dark Card finanziaria mobile-first."""
    nome = html.escape(sosta["Stazione"])
    tratta = html.escape(sosta["Tratta"])
    nota_pos = html.escape(sosta["Note"])
    potenza = html.escape(sosta["Potenza"])
    maps_url = html.escape(url_gmaps_stazione(sosta["Stazione"], sosta["lat"], sosta["lon"]), quote=True)

    e_ritorno = bool(sosta.get("e_ritorno"))
    dest_nome = html.escape(res["dest"]) if res else "destinazione"
    km_mostrato = float(sosta.get("km_tratta", sosta["Km"]))
    km_etichetta = f"Km {km_mostrato:.1f} da {dest_nome}" if e_ritorno else f"Km {km_mostrato:.1f}"

    food_name = sosta.get("food_name") or (FALLBACK_FOOD_NAME if sosta.get("has_food") else "")
    mall_name = sosta.get("mall_name") or (FALLBACK_MALL_NAME if sosta.get("has_mall") else "")

    badges = []
    if sosta["in_ads"]:
        badges.append('<span class="ev-badge ev-b-green">🛣️ In Autostrada</span>')
    else:
        icona_fc = "🔴" if sosta.get("is_tesla") else "⚡"
        badges.append(f'<span class="ev-badge ev-b-amber">{icona_fc} Lungo la strada / Fuori casello</span>')
    badges.extend(
        costruisci_badge_locali(
            sosta["lat"],
            sosta["lon"],
            1 if food_name else 0,
            1 if mall_name else 0,
            food_name=food_name,
            mall_name=mall_name,
        )
    )

    righe_locali = costruisci_righe_locali(sosta["lat"], sosta["lon"], food_name, mall_name)

    riga_orario = ""
    if sosta.get("ora_arrivo") and sosta.get("ora_ripartenza"):
        riga_orario = (
            f'<div class="ev-kv">'
            f'<span class="ev-k">🕒 Orario stimato</span>'
            f'<span class="ev-v">Arrivo ore {html.escape(sosta["ora_arrivo"])} → Ripartenza ore {html.escape(sosta["ora_ripartenza"])}</span>'
            f'</div>'
        )

    jolly = sosta.get("jolly")
    if jolly:
        j_nome = html.escape(jolly["Stazione"])
        j_km_disp = max(0.0, float(jolly["Km"]) - km1_offset) if e_ritorno else float(jolly["Km"])
        j_km_txt = f"Km {j_km_disp:.1f} da {dest_nome}" if e_ritorno else f"Km {j_km_disp:.1f}"
        livello_j = int(jolly.get("livello") or (3 if jolly.get("riserva_estrema") else (1 if jolly.get("in_ads") else 2)))

        if livello_j == 1:
            # Livello 1: In Autostrada (AdS) con batteria residua >= 4%
            j_dettaglio = f'{j_nome} • 🛣️ AdS • {j_km_txt} (+{jolly["delta_km"]:.1f} km, residua ~{jolly["pct_emergenza"]}%)'
            j_colore = "#93C5FD"
        elif livello_j == 2:
            # Livello 2: Emergenza fuori casello con batteria residua >= 4%
            j_dettaglio = (
                f'🚨 Emergenza fuori casello: {j_nome} • +{jolly["delta_km"]:.1f} km '
                f'(residua ~{jolly["pct_emergenza"]}% — valuta uscita al casello o riduci velocità)'
            )
            j_colore = "#FBBF24"
        else:
            # Livello 3: Riserva estrema 1% - 3% (rischio esplicito)
            j_dettaglio = (
                f'⚠️ Riserva estrema: {j_nome} • +{jolly["delta_km"]:.1f} km '
                f'(~{jolly["pct_emergenza"]}% — rischio stop/carroattrezzi: riduci velocità a 90 km/h o valuta uscita al casello)'
            )
            j_colore = "#FCA5A5"

        riga_jolly = (
            f'<div class="ev-kv">'
            f'<span class="ev-k">🛟 Piano B (Jolly)</span>'
            f'<span class="ev-v" style="color:{j_colore};">{j_dettaglio}</span>'
            f'</div>'
        )
    else:
        pct_arr_int = int(round(float(sosta["arr_pct"])))
        msg_no_jolly = (
            f"⚠️ Nessuna colonnina raggiungibile con la batteria residua ({pct_arr_int}%): "
            f"riduci subito velocità a 80-90 km/h o esci al primo casello"
        )
        riga_jolly = (
            f'<div class="ev-kv">'
            f'<span class="ev-k">🛟 Piano B (Jolly)</span>'
            f'<span class="ev-v" style="color:#FCA5A5;">{msg_no_jolly}</span>'
            f'</div>'
        )

    extra_html = ""
    if sosta.get("nota_extra"):
        extra_html += f'<div class="ev-info-box">{html.escape(sosta["nota_extra"])}</div>'
    if sosta.get("avviso"):
        extra_html += f'<div class="ev-warn-box">{html.escape(sosta["avviso"])}</div>'

    card_html = (
        f'<div class="ev-card" style="border-left-color:{colore_bordo};">'
        f'<div class="ev-card-top">'
        f'<div>'
        f'<div class="ev-card-title">🛑 Sosta {indice} • {nome}</div>'
        f'<div class="ev-card-sub">{tratta} • {km_etichetta} • {nota_pos}</div>'
        f'</div>'
        f'<div class="ev-badges">{"".join(badges)}</div>'
        f'</div>'
        f'{riga_orario}'
        f'<div class="ev-kv"><span class="ev-k">Potenza stazione</span><span class="ev-v">{potenza}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">Batteria (Arrivo → Ripartenza)</span><span class="ev-v">{sosta["arr_pct"]:.0f}% → {sosta["charge_to"]}%</span></div>'
        f'<div class="ev-kv"><span class="ev-k">Tempo stimato di ricarica</span><span class="ev-v">⏱️ {sosta["minuti_ricarica"]} min</span></div>'
        f'{riga_jolly}'
        f'{righe_locali}'
        f'{extra_html}'
        f'<a class="ev-maps-btn" href="{maps_url}" target="_blank" rel="noopener noreferrer">📍 Apri Google Maps</a>'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render_card_arrivo_destinazione(sol, res, colore_bordo="#10B981"):
    """Renderizza la card di Arrivo a destinazione (B) con orario stimato, batteria e colonnina più vicina."""
    dest_esc = html.escape(res["dest"])
    km1_sol = float(sol.get("km1", res["km1"]))
    pct_b = sol["pct_arrivo_b"]
    min_b = res["p_arr_b"]
    ok_b = pct_b >= min_b - 0.5
    col_b = "#34D399" if ok_b else "#F87171"
    ora_arr_b = html.escape(str(sol.get("ora_arrivo_b") or "--:--"))

    etichetta_km = "Distanza tratta di andata" if res["andata_ritorno"] else "Distanza totale raggiunta"
    righe = (
        f'<div class="ev-kv"><span class="ev-k">{etichetta_km}</span>'
        f'<span class="ev-v">{km1_sol:.1f} km</span></div>'
        f'<div class="ev-kv"><span class="ev-k">🕒 Arrivo previsto</span>'
        f'<span class="ev-v">ore {ora_arr_b}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">Batteria stimata all\'arrivo</span>'
        f'<span class="ev-v" style="color:{col_b};">{pct_b:.0f}% <span style="color:#94A3B8;font-weight:500;">(min. {min_b}%)</span></span></div>'
    )

    badge_stato = (
        '<span class="ev-badge ev-b-green">✅ Arrivo Garantito</span>'
        if ok_b
        else '<span class="ev-badge ev-b-amber">⚠️ Sotto soglia minima</span>'
    )

    # Indicazione colonnina auto più vicina al punto di arrivo dal database locale (escluse e-bike)
    riga_vicina = ""
    vicina = res.get("colonnina_vicina_dest")
    if vicina and not e_colonnina_ebike(vicina.get("Stazione", "")):
        v_nome = html.escape(vicina["Stazione"])
        v_pot = html.escape(vicina["Potenza"])
        v_maps = html.escape(vicina["Maps"], quote=True)
        riga_vicina = (
            f'<div class="ev-kv">'
            f'<span class="ev-k">Colonnina più vicina a {dest_esc} (DB locale)</span>'
            f'<span class="ev-v">{v_nome} • {vicina["Distanza"]:.1f} km • {v_pot} '
            f'<a href="{v_maps}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#60A5FA; text-decoration:none; margin-left:4px;">📍 Maps ↗</a></span>'
            f'</div>'
        )

    # Pulsante diretto con query testuale sull'indirizzo di destinazione + tooltip "?" e nota ℹ️
    url_gmaps_zona = html.escape(url_gmaps_colonnine_indirizzo(res["dest"]), quote=True)
    testo_help_gmaps = (
        "Serve a verificare su Google Maps anche eventuali colonnine installate di recente "
        "e non ancora mappate nel database locale."
    )
    btn_gmaps_zona = (
        f'<div style="display:flex; align-items:center; gap:8px; margin-top:10px;">'
        f'<a class="ev-gmaps-cta" style="margin-top:0; flex:1;" href="{url_gmaps_zona}" '
        f'target="_blank" rel="noopener noreferrer">'
        f'🔍 Cerca colonnine aggiornate a destinazione su Google Maps'
        f'</a>'
        f'<span title="{testo_help_gmaps}" '
        f'style="display:inline-flex; align-items:center; justify-content:center; '
        f'width:30px; height:30px; flex-shrink:0; border-radius:50%; '
        f'background:rgba(59,130,246,0.16); border:1px solid rgba(59,130,246,0.38); '
        f'color:#93C5FD; font-weight:800; font-size:0.9rem; cursor:help;">?</span>'
        f'</div>'
        f'<div style="font-size:0.78rem; color:#94A3B8; margin-top:6px; line-height:1.35;">'
        f'ℹ️ {testo_help_gmaps}'
        f'</div>'
    )

    card_html = (
        f'<div class="ev-card" style="border-left-color:{colore_bordo};">'
        f'<div class="ev-card-top">'
        f'<div class="ev-card-title">🏁 Arrivo a {dest_esc}</div>'
        f'<div class="ev-badges">{badge_stato}</div>'
        f'</div>'
        f'{righe}'
        f'{riga_vicina}'
        f'{btn_gmaps_zona}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render_card_rientro_finale(sol, res, colore_bordo="#10B981"):
    """Renderizza la card finale dedicata al rientro a casa (A) in modalità Andata + Ritorno."""
    orig_esc = html.escape(res["orig"])
    dest_esc = html.escape(res["dest"])
    km2_sol = float(sol.get("km2", max(0.0, float(sol.get("tot_km", res["tot_km"])) - float(sol.get("km1", res["km1"])))))
    tot_km_sol = float(sol.get("tot_km", res["tot_km"]))
    pct_a = sol["pct_rientro_a"]
    min_a = res["p_home_a"]
    ok_a = pct_a >= min_a - 0.5
    col_a = "#34D399" if ok_a else "#F87171"
    ora_rip_rit = html.escape(str(sol.get("ora_partenza_ritorno") or "--:--"))
    ora_rientro = html.escape(str(sol.get("ora_rientro_a") or "--:--"))
    ore_perm = float(res.get("ore_permanenza", 2.0))

    badge_stato = (
        '<span class="ev-badge ev-b-green">✅ Rientro Garantito</span>'
        if ok_a
        else '<span class="ev-badge ev-b-amber">⚠️ Sotto soglia minima</span>'
    )

    card_html = (
        f'<div class="ev-card" style="border-left-color:{colore_bordo};">'
        f'<div class="ev-card-top">'
        f'<div class="ev-card-title">🏠 Rientro Finale a {orig_esc}</div>'
        f'<div class="ev-badges">{badge_stato}</div>'
        f'</div>'
        f'<div class="ev-kv"><span class="ev-k">Ripartenza da {dest_esc} (dopo {ore_perm:.1f}h di sosta)</span>'
        f'<span class="ev-v">ore {ora_rip_rit}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">Distanza ritorno • Totale A/R</span>'
        f'<span class="ev-v">{km2_sol:.1f} km • {tot_km_sol:.1f} km totali</span></div>'
        f'<div class="ev-kv"><span class="ev-k">🕒 Rientro previsto</span>'
        f'<span class="ev-v">ore {ora_rientro}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">Batteria stimata al rientro</span>'
        f'<span class="ev-v" style="color:{col_a};">{pct_a:.0f}% <span style="color:#94A3B8;font-weight:500;">(min. {min_a}%)</span></span></div>'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render_card_colonnina_generica(item, mostra_km=False, km1_offset=0.0, dest_nome=""):
    """Renderizza una colonnina (Cerca in Zona o Modalità Esperto) in formato Dark Card compatto."""
    nome = html.escape(item["Stazione"])
    potenza = html.escape(item["Potenza"])
    maps_url = html.escape(url_gmaps_stazione(item["Stazione"], item["lat"], item["lon"]), quote=True)
    fascia = classifica_fascia_potenza(item)
    is_hyper = fascia == "hyper"
    is_hpc = fascia == "hpc"
    is_fast = fascia in ("hyper", "hpc", "fast")
    colore_bordo = "#F59E0B" if is_hyper else ("#10B981" if is_hpc else ("#3B82F6" if is_fast else "#64748B"))

    food_name = item.get("food_name") or (FALLBACK_FOOD_NAME if item.get("has_food") else "")
    mall_name = item.get("mall_name") or (FALLBACK_MALL_NAME if item.get("has_mall") else "")

    badges = []
    if is_hyper:
        badges.append(f'<span class="ev-badge ev-b-amber">{potenza}</span>')
    elif is_hpc:
        badges.append(f'<span class="ev-badge ev-b-green">{potenza}</span>')
    elif is_fast:
        badges.append(f'<span class="ev-badge ev-b-blue">{potenza}</span>')
    else:
        badges.append(f'<span class="ev-badge ev-b-slate">{potenza}</span>')
    badges.extend(
        costruisci_badge_locali(
            item["lat"],
            item["lon"],
            1 if food_name else 0,
            1 if mall_name else 0,
            food_name=food_name,
            mall_name=mall_name,
        )
    )

    righe = []
    if mostra_km:
        tratta_str = str(item.get("Tratta", ""))
        e_ritorno = ("Ritorno" in tratta_str) and float(km1_offset or 0.0) > 0.0
        km_val = max(0.0, float(item["Km"]) - float(km1_offset)) if e_ritorno else float(item["Km"])
        pos_km_txt = f"Km {km_val:.1f} da {html.escape(dest_nome)}" if (e_ritorno and dest_nome) else f"Km {km_val:.1f}"
        righe.append(
            f'<div class="ev-kv"><span class="ev-k">Posizione</span>'
            f'<span class="ev-v">{html.escape(tratta_str)} • {pos_km_txt}</span></div>'
        )
        righe.append(
            f'<div class="ev-kv"><span class="ev-k">Accesso</span>'
            f'<span class="ev-v">{html.escape(item["Note"])}</span></div>'
        )
    else:
        tipo_txt = (
            "⚡ Hyper-Fast (>= 400 kW)"
            if is_hyper
            else ("🚀 Ultra-Fast / HPC" if is_hpc else ("🔌 Fast DC" if is_fast else "🐢 Lenta / AC"))
        )
        righe.append(
            f'<div class="ev-kv"><span class="ev-k">Distanza dal punto cercato</span>'
            f'<span class="ev-v">{item["Distanza"]:.1f} km</span></div>'
        )
        righe.append(
            f'<div class="ev-kv"><span class="ev-k">Tipologia</span>'
            f'<span class="ev-v">{tipo_txt}</span></div>'
        )

    righe_locali = costruisci_righe_locali(item["lat"], item["lon"], food_name, mall_name)
    if righe_locali:
        righe.append(righe_locali)

    card_html = (
        f'<div class="ev-card" style="border-left-color:{colore_bordo};">'
        f'<div class="ev-card-top">'
        f'<div class="ev-card-title">{nome}</div>'
        f'<div class="ev-badges">{"".join(badges)}</div>'
        f'</div>'
        f'{"".join(righe)}'
        f'<a class="ev-maps-btn" href="{maps_url}" target="_blank" rel="noopener noreferrer">📍 Apri Google Maps</a>'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render_card_confronto_strade(sol_auto, sol_extra, res):
    """
    Renderizza il riquadro blu st.info(...) di confronto diretto tra percorso in Autostrada
    e percorso su Statali / Extraurbano con 3 righe chiare calcolate sull'intero viaggio (A o A/R).
    """
    if not sol_auto or not sol_extra:
        return

    ambito_txt = "(valori totali su Andata + Ritorno)" if res["andata_ritorno"] else "(valori su Solo Andata)"

    tot_km_auto = float(sol_auto.get("tot_km", res["tot_km"]))
    kwh_auto = float(sol_auto.get("kwh_totali", res["kwh_totali"]))
    kwh_extra = float(sol_extra.get("kwh_totali", res["kwh_totali"]))
    bat_kwh = float(res.get("auto_cfg", {}).get("bat_kwh", 64.0))

    tempo_auto = int(sol_auto["tempo_totale_min"]) if sol_auto.get("valido") else int(sol_auto.get("tot_dur", res["tot_dur"]))
    tempo_extra = int(sol_extra["tempo_totale_min"]) if sol_extra.get("valido") else int(sol_extra.get("tot_dur", res["tot_dur"]))

    def fmt_min_diff(minuti_abs):
        """Formatta una differenza di minuti in modo naturale ('16 min' oppure '1h 12m')."""
        if minuti_abs < 60:
            return f"{minuti_abs} min"
        return f"{minuti_abs // 60}h {minuti_abs % 60:02d}m"

    # 1. Differenza Tempo (durata totale senza autostrada - durata totale con autostrada)
    diff_min = tempo_extra - tempo_auto
    if diff_min > 0:
        diff_tempo_txt = f"+{fmt_min_diff(diff_min)} in più su Statale rispetto all'Autostrada"
    elif diff_min < 0:
        diff_tempo_txt = f"-{fmt_min_diff(abs(diff_min))} in meno su Statale rispetto all'Autostrada"
    else:
        diff_tempo_txt = "Stesso tempo totale tra Statale e Autostrada"

    # 2. Energia risparmiata (kWh totali autostrada - kWh totali statale, più % batteria equivalente)
    diff_kwh = round(kwh_auto - kwh_extra, 1)
    diff_bat_pct = round((diff_kwh / bat_kwh) * 100.0, 1) if bat_kwh > 0 else 0.0
    if diff_kwh >= 0:
        energia_txt = f"{diff_kwh:.1f} kWh (+{diff_bat_pct:.1f}% di batteria all'arrivo) scegliendo la Statale"
    else:
        energia_txt = f"+{abs(diff_kwh):.1f} kWh consumati (-{abs(diff_bat_pct):.1f}% di batteria) su Statale"

    # 3. Pedaggio evitato (km totali percorso autostradale * 0.078 €/km)
    pedaggio_evitato = tot_km_auto * COSTO_PEDAGGIO_KM
    pedaggio_txt = f"~{pedaggio_evitato:.2f} € (stima indicativa su {tot_km_auto:.0f} km di autostrada)"

    st.info(
        f"⚖️ **Confronto diretto: Autostrada vs Statali / Extraurbano** {ambito_txt}\n\n"
        f"⏱️ **Differenza Tempo:** {diff_tempo_txt}\n\n"
        f"🔋 **Energia risparmiata:** {energia_txt}\n\n"
        f"💶 **Pedaggio evitato:** {pedaggio_txt}"
    )


def _tronca_testo_svg(testo, max_len=23):
    """Accorcia in modo pulito una stringa per le etichette del grafico SVG senza spezzare entità HTML."""
    s = str(testo or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip(" -•,") + "…"


def _pulisci_nome_gestore_svg(testo, max_len=26):
    """
    Abbrevia in modo intelligente le ragioni sociali lunghe nei fumetti SVG di Sosta e Jolly
    (es. 'Autostrada del Brennero S.p.A.' -> 'A22 Brennero') rimuovendo suffissi societari.
    """
    s = str(testo or "").strip()
    if not s:
        return s
    s = re.sub(r"autostrada\s+del\s+brennero(?:\s+s\.?p\.?a\.?)?", "A22 Brennero", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(?:s\.p\.a\.?|s\.r\.l\.?|spa|srl|italia)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s).strip(" -•,")
    return _tronca_testo_svg(s, max_len=max_len)


@st.cache_data(ttl=86400)
def _reverse_comune_coordinate(lat_r, lon_r):
    """
    Recupera in modo leggero e cachato (24h) il nome del comune italiano dalle coordinate
    della stazione, con timeout breve e fallback istantaneo sulle città locali se offline.
    """
    try:
        loc = Nominatim(user_agent="ev_roadbook_it_2026", timeout=3).reverse(
            (float(lat_r), float(lon_r)),
            language="it",
            zoom=10,
            addressdetails=True,
        )
        if loc and isinstance(getattr(loc, "raw", None), dict):
            addr = loc.raw.get("address") or {}
            for chiave in ("city", "town", "village", "municipality", "hamlet", "suburb"):
                val = str(addr.get(chiave) or "").strip()
                if val:
                    # Pulisce eventuali nomi bilingue es. "Bolzano - Bozen" -> "Bolzano"
                    return re.split(r"\s*[-/]\s*", val)[0].strip()
    except Exception:
        pass

    # Fallback locale istantaneo sulle città principali entro 12 km
    best_nome = ""
    best_d = 12.0
    for lat_c, lon_c, nome_c in CITTA_NODI_ITALIA:
        d = haversine_km(float(lat_r), float(lon_r), lat_c, lon_c)
        if d < best_d:
            best_d = d
            best_nome = nome_c
    return best_nome


def _estrai_comune_stazione(stazione):
    """Estrae il comune/località dalla scheda della colonnina (o da coordinate GPS cachate)."""
    if not isinstance(stazione, dict):
        return ""
    for chiave in ("city", "comune", "municipality", "localita"):
        val = str(stazione.get(chiave) or "").strip()
        if val:
            return re.split(r"\s*[-/]\s*", val)[0].strip()

    addr = str(stazione.get("address") or stazione.get("indirizzo") or "").strip()
    if addr:
        parti = [p.strip() for p in addr.split(",") if p.strip()]
        if len(parti) >= 2:
            cand_addr = re.sub(r"\b\d{5}\b", "", parti[-2]).strip()
            if cand_addr:
                return re.split(r"\s*[-/]\s*", cand_addr)[0].strip()

    lat_v = stazione.get("lat")
    lon_v = stazione.get("lon")
    if lat_v is not None and lon_v is not None:
        return _reverse_comune_coordinate(round(float(lat_v), 3), round(float(lon_v), 3))
    return ""


def _formatta_etichetta_stazione_svg(stazione, fallback_nome="Sosta", max_len=26):
    """
    Compone l'etichetta compatta per i riquadri SVG di Sosta e Jolly:
    nome gestore pulito + eventuale comune tra parentesi (es. 'Be Charge (Montebello)'),
    troncato in modo pulito entro max_len (26 caratteri complessivi con '...').
    """
    nome_base = _pulisci_nome_gestore_svg(stazione.get("Stazione", fallback_nome), max_len=max_len)
    comune = _estrai_comune_stazione(stazione)
    if comune and comune.lower() not in nome_base.lower():
        completo = f"{nome_base} ({comune})"
        if len(completo) <= max_len:
            return completo
        # Se supera 26 caratteri, prova ad accorciare il comune alla prima parola (es. Montebello Vicentino -> Montebello)
        comune_breve = comune.split()[0] if " " in comune else comune
        completo_breve = f"{nome_base} ({comune_breve})"
        if len(completo_breve) <= max_len:
            return completo_breve
        return completo[: max_len - 3].rstrip(" -•,(") + "..."
    if len(nome_base) <= max_len:
        return nome_base
    return nome_base[: max_len - 3].rstrip(" -•,") + "..."


def genera_svg_roadbook_visivo(
    titolo_tratta,
    orig_nome,
    dest_nome,
    km_tratta,
    pct_partenza,
    pct_arrivo,
    soste_tratta,
    nodi_citta,
    km_per_pct,
    km_offset=0.0,
):
    """
    Genera un grafico SVG vettoriale leggero e responsive su sfondo scuro (#0F172A)
    con strada sinuosa, partenza, città di passaggio, macchinina azzurra con fumetto %,
    soste di ricarica principali, Piano B / Jolly, arrivo e legenda compatta in basso.
    """
    km_tot = max(1.0, float(km_tratta or 1.0))
    km_p_pct = max(0.5, float(km_per_pct or 3.5))

    def _xy_strada(t_val):
        t_c = max(0.0, min(1.0, float(t_val)))
        x = 64.0 + t_c * 672.0
        y = 168.0 + 34.0 * math.sin(t_c * 2.0 * math.pi - 0.35)
        return x, y

    def _angolo_strada(t_val):
        dt = 0.004
        x1, y1 = _xy_strada(max(0.0, t_val - dt))
        x2, y2 = _xy_strada(min(1.0, t_val + dt))
        return math.degrees(math.atan2(y2 - y1, x2 - x1))

    # Normalizza le soste della tratta con chilometraggio relativo (azzerato da inizio tratta)
    soste_norm = []
    for s in soste_tratta or []:
        km_rel = max(0.5, min(km_tot - 0.5, float(s.get("Km", 0.0)) - float(km_offset)))
        soste_norm.append({**s, "_km_rel": km_rel})
    soste_norm.sort(key=lambda x: x["_km_rel"])

    def _stima_pct_a_km(km_q):
        curr_k = 0.0
        curr_p = float(pct_partenza)
        for s in soste_norm:
            if s["_km_rel"] < km_q - 0.05:
                curr_k = s["_km_rel"]
                curr_p = float(s.get("charge_to", curr_p))
        val = curr_p - (km_q - curr_k) / km_p_pct
        return max(0.0, min(100.0, val))

    # Costruisce i marcatori interni (soste, jolly e città di passaggio)
    nodi_interni = []
    km_soste_e_jolly = []

    for s in soste_norm:
        km_s = s["_km_rel"]
        km_soste_e_jolly.append(km_s)
        nodi_interni.append(
            {
                "tipo": "stop",
                "km": km_s,
                "t": km_s / km_tot,
                "nome": _formatta_etichetta_stazione_svg(s, fallback_nome="Sosta", max_len=26),
                "min_ric": int(s.get("minuti_ricarica", 0)),
                "arr_pct": float(s.get("arr_pct", _stima_pct_a_km(km_s))),
                "chg_pct": float(s.get("charge_to", 80.0)),
            }
        )
        jolly = s.get("jolly")
        if jolly:
            delta_j = max(1.0, float(jolly.get("delta_km", 10.0)))
            km_j = min(km_tot - 1.5, km_s + delta_j)
            if km_j > km_s + 0.5:
                km_soste_e_jolly.append(km_j)
                nodi_interni.append(
                    {
                        "tipo": "jolly",
                        "km": km_j,
                        "t": km_j / km_tot,
                        "nome": _formatta_etichetta_stazione_svg(jolly, fallback_nome="Piano B", max_len=26),
                        "delta_km": delta_j,
                    }
                )

    # Aggiunge max 3-4 città principali attraversate dando priorità ai capoluoghi di provincia
    soglia_vicinanza = max(12.0, km_tot * 0.075)
    citta_ordinate = sorted(
        list(nodi_citta or []),
        key=lambda c: (0 if (c.get("capoluogo") or c.get("nome") in CITTA_CAPOLUOGHI_PRIORITARI) else 1, float(c.get("km", 0.0))),
    )
    n_citta_agg = 0
    for c in citta_ordinate:
        if n_citta_agg >= 4:
            break
        km_c = float(c.get("km", 0.0))
        if km_c < max(12.0, km_tot * 0.10) or km_c > km_tot - max(12.0, km_tot * 0.10):
            continue
        if any(abs(km_c - ks) < soglia_vicinanza for ks in km_soste_e_jolly):
            continue
        nodi_interni.append(
            {
                "tipo": "city",
                "km": km_c,
                "t": km_c / km_tot,
                "nome": _tronca_testo_svg(c.get("nome", ""), 14),
                "pct": _stima_pct_a_km(km_c),
            }
        )
        n_citta_agg += 1

    nodi_interni.sort(key=lambda n: (n["km"], 0 if n["tipo"] == "stop" else (1 if n["tipo"] == "jolly" else 2)))

    # Distanziamento minimo visivo su t_vis per evitare sovrapposizione di icone ed etichette
    n_int = len(nodi_interni)
    if n_int > 0:
        t_min_int = 0.18 if n_int <= 5 else 0.13
        t_max_int = 0.89
        gap_vis = min(0.115, (t_max_int - t_min_int) / max(1, n_int))
        t_vals = [max(t_min_int, min(t_max_int, n["t"])) for n in nodi_interni]
        for idx in range(1, n_int):
            if t_vals[idx] - t_vals[idx - 1] < gap_vis:
                t_vals[idx] = t_vals[idx - 1] + gap_vis
        if t_vals[-1] > t_max_int:
            eccesso = t_vals[-1] - t_max_int
            t_vals = [max(t_min_int, v - eccesso) for v in t_vals]
            for idx in range(n_int - 2, -1, -1):
                if t_vals[idx + 1] - t_vals[idx] < gap_vis:
                    t_vals[idx] = max(0.08, t_vals[idx + 1] - gap_vis)
        for idx, n in enumerate(nodi_interni):
            n["t_vis"] = max(0.08, min(0.91, t_vals[idx]))

    # Costruisce il percorso SVG della strada sinuosa (60 segmenti fluidi)
    punti_road = []
    for step in range(61):
        xr, yr = _xy_strada(step / 60.0)
        punti_road.append(f"{xr:.1f},{yr:.1f}")
    path_road = "M " + " L ".join(punti_road)

    parti_svg = [
        '<svg id="ev-roadbook-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 340" '
        'preserveAspectRatio="xMidYMid meet" width="100%" height="100%" '
        'style="display:block; width:100%; height:100%; user-select:none; -webkit-user-select:none; '
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;">',
        # Sfondo scuro elegante + griglia discreta da navigatore
        '<rect x="1" y="1" width="798" height="338" rx="16" fill="#0F172A" stroke="#1E293B" stroke-width="2"/>',
        '<g stroke="#1E293B" stroke-width="1" opacity="0.45">',
        '<line x1="0" y1="88" x2="800" y2="88"/>',
        '<line x1="0" y1="168" x2="800" y2="168"/>',
        '<line x1="0" y1="248" x2="800" y2="248"/>',
        '<line x1="200" y1="36" x2="200" y2="294"/>',
        '<line x1="400" y1="36" x2="400" y2="294"/>',
        '<line x1="600" y1="36" x2="600" y2="294"/>',
        '</g>',
        # Intestazione tratta in alto a sinistra (lascia libero l'angolo destro per i comandi zoom)
        f'<text x="20" y="25" fill="#94A3B8" font-size="12" font-weight="700" letter-spacing="0.4">'
        f'{html.escape(titolo_tratta)} • {km_tot:.0f} km</text>',
        # Strada sinuosa stilizzata (doppio bordo #334155 + asfalto #1E293B + linea centrale tratteggiata)
        f'<path d="{path_road}" fill="none" stroke="#334155" stroke-width="26" stroke-linecap="round" stroke-linejoin="round"/>',
        f'<path d="{path_road}" fill="none" stroke="#1E293B" stroke-width="19" stroke-linecap="round" stroke-linejoin="round"/>',
        f'<path d="{path_road}" fill="none" stroke="#94A3B8" stroke-width="2" stroke-dasharray="9 8" stroke-linecap="round" opacity="0.72"/>',
    ]

    # Registra i rettangoli occupati dalle etichette per posizionare macchinina + fumetto senza sovrapposizioni
    box_occupati = []

    # Disegna Partenza (t=0.0)
    xs, ys = _xy_strada(0.0)
    nome_orig_svg = html.escape(_tronca_testo_svg(orig_nome, 15))
    box_occupati.append((12.0, ys + 38.0, 126.0, ys + 78.0))
    parti_svg.append(
        f'<line x1="{xs:.1f}" y1="{ys + 12:.1f}" x2="{xs:.1f}" y2="{ys + 38:.1f}" stroke="#10B981" stroke-width="1.4" stroke-dasharray="2 2"/>'
        f'<circle cx="{xs:.1f}" cy="{ys:.1f}" r="15" fill="#10B981" opacity="0.22"/>'
        f'<circle cx="{xs:.1f}" cy="{ys:.1f}" r="10.5" fill="#10B981" stroke="#ECFDF5" stroke-width="2"/>'
        f'<circle cx="{xs:.1f}" cy="{ys:.1f}" r="4" fill="#0F172A"/>'
        f'<rect x="12" y="{ys + 38:.1f}" width="114" height="40" rx="8" fill="#0B1220" fill-opacity="0.94" stroke="#10B981" stroke-width="1.2"/>'
        f'<text x="69" y="{ys + 54:.1f}" text-anchor="middle" fill="#34D399" font-size="11" font-weight="800">{nome_orig_svg}</text>'
        f'<text x="69" y="{ys + 70:.1f}" text-anchor="middle" fill="#E2E8F0" font-size="10.5" font-weight="600">Km 0 • {pct_partenza:.0f}%</text>'
    )

    # Disegna Arrivo (t=1.0)
    xe, ye = _xy_strada(1.0)
    nome_dest_svg = html.escape(_tronca_testo_svg(dest_nome, 15))
    # Posiziona etichetta arrivo in basso se c'è spazio, altrimenti in alto
    y_arr_box = ye + 38.0 if ye <= 185.0 else ye - 78.0
    y_arr_l1 = ye + 12.0 if ye <= 185.0 else ye - 12.0
    y_arr_l2 = y_arr_box if ye <= 185.0 else y_arr_box + 40.0
    box_occupati.append((674.0, y_arr_box, 788.0, y_arr_box + 40.0))
    parti_svg.append(
        f'<line x1="{xe:.1f}" y1="{y_arr_l1:.1f}" x2="{xe:.1f}" y2="{y_arr_l2:.1f}" stroke="#EF4444" stroke-width="1.4" stroke-dasharray="2 2"/>'
        f'<circle cx="{xe:.1f}" cy="{ye:.1f}" r="15" fill="#EF4444" opacity="0.22"/>'
        f'<circle cx="{xe:.1f}" cy="{ye:.1f}" r="11" fill="#EF4444" stroke="#FEE2E2" stroke-width="2"/>'
        f'<path d="M {xe - 3.5:.1f} {ye + 5:.1f} L {xe - 3.5:.1f} {ye - 5:.1f} L {xe + 5.5:.1f} {ye - 2.5:.1f} L {xe - 3.5:.1f} {ye + 0.5:.1f} Z" fill="#FFFFFF"/>'
        f'<rect x="674" y="{y_arr_box:.1f}" width="114" height="40" rx="8" fill="#0B1220" fill-opacity="0.94" stroke="#EF4444" stroke-width="1.2"/>'
        f'<text x="731" y="{y_arr_box + 16:.1f}" text-anchor="middle" fill="#FCA5A5" font-size="11" font-weight="800">{nome_dest_svg}</text>'
        f'<text x="731" y="{y_arr_box + 32:.1f}" text-anchor="middle" fill="#E2E8F0" font-size="10.5" font-weight="600">Km {km_tot:.0f} • {pct_arrivo:.0f}%</text>'
    )

    # Disegna tutti i nodi interni alternando etichette sopra (-1) e sotto (+1) per zero sovrapposizioni
    ultimo_lato_per_fascia = {"top": -999.0, "bot": -999.0}
    for idx_n, nodo in enumerate(nodi_interni):
        xn, yn = _xy_strada(nodo["t_vis"])
        tipo = nodo["tipo"]

        # Sceglie il lato con più spazio orizzontale rispetto all'etichetta precedente su quel lato
        dist_top = xn - ultimo_lato_per_fascia["top"]
        dist_bot = xn - ultimo_lato_per_fascia["bot"]
        if tipo == "jolly":
            # Il Jolly si alterna volentieri sul lato opposto rispetto alla sua sosta
            lato = 1 if dist_bot >= dist_top else -1
        elif dist_top >= dist_bot:
            lato = -1
        else:
            lato = 1

        if tipo == "stop":
            box_w, box_h = 166.0, 50.0
        elif tipo == "jolly":
            box_w, box_h = 154.0, 38.0
        else:
            box_w, box_h = 104.0, 38.0

        bx = max(12.0, min(788.0 - box_w, xn - box_w / 2.0))
        if lato == -1:
            by = max(36.0, yn - 26.0 - box_h)
            if by + box_h > yn - 18.0:
                by = max(36.0, yn - 18.0 - box_h)
            ultimo_lato_per_fascia["top"] = bx + box_w
            ly1, ly2 = yn - 12.0, by + box_h
        else:
            by = min(292.0 - box_h, yn + 26.0)
            if by < yn + 18.0:
                by = min(292.0 - box_h, yn + 18.0)
            ultimo_lato_per_fascia["bot"] = bx + box_w
            ly1, ly2 = yn + 12.0, by

        box_occupati.append((bx, by, bx + box_w, by + box_h))
        cx_lbl = bx + box_w / 2.0
        nome_esc = html.escape(nodo["nome"])

        if tipo == "stop":
            parti_svg.append(
                f'<line x1="{xn:.1f}" y1="{ly1:.1f}" x2="{cx_lbl:.1f}" y2="{ly2:.1f}" stroke="#10B981" stroke-width="1.5" stroke-dasharray="2 2"/>'
                f'<circle cx="{xn:.1f}" cy="{yn:.1f}" r="17" fill="#10B981" opacity="0.22"/>'
                f'<circle cx="{xn:.1f}" cy="{yn:.1f}" r="12.5" fill="#10B981" stroke="#ECFDF5" stroke-width="2"/>'
                f'<text x="{xn:.1f}" y="{yn + 4.5:.1f}" text-anchor="middle" fill="#0F172A" font-size="13" font-weight="900">⚡</text>'
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{box_w:.1f}" height="{box_h:.1f}" rx="8" fill="#0B1220" fill-opacity="0.95" stroke="#10B981" stroke-width="1.4"/>'
                f'<text x="{cx_lbl:.1f}" y="{by + 15:.1f}" text-anchor="middle" fill="#34D399" font-size="9.8" font-weight="800">{nome_esc}</text>'
                f'<text x="{cx_lbl:.1f}" y="{by + 29:.1f}" text-anchor="middle" fill="#F8FAFC" font-size="10" font-weight="700">Km {nodo["km"]:.0f} • ~{nodo["min_ric"]} min</text>'
                f'<text x="{cx_lbl:.1f}" y="{by + 43:.1f}" text-anchor="middle" fill="#94A3B8" font-size="10" font-weight="600">{nodo["arr_pct"]:.0f}% → {nodo["chg_pct"]:.0f}%</text>'
            )
        elif tipo == "jolly":
            parti_svg.append(
                f'<line x1="{xn:.1f}" y1="{ly1:.1f}" x2="{cx_lbl:.1f}" y2="{ly2:.1f}" stroke="#F59E0B" stroke-width="1.3" stroke-dasharray="2 2"/>'
                f'<circle cx="{xn:.1f}" cy="{yn:.1f}" r="10" fill="#F59E0B" stroke="#FEF3C7" stroke-width="1.6"/>'
                f'<text x="{xn:.1f}" y="{yn + 4:.1f}" text-anchor="middle" fill="#0F172A" font-size="11.5" font-weight="900">J</text>'
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{box_w:.1f}" height="{box_h:.1f}" rx="7" fill="#0B1220" fill-opacity="0.93" stroke="#F59E0B" stroke-width="1.2"/>'
                f'<text x="{cx_lbl:.1f}" y="{by + 15:.1f}" text-anchor="middle" fill="#FBBF24" font-size="9.4" font-weight="800">{nome_esc}</text>'
                f'<text x="{cx_lbl:.1f}" y="{by + 30:.1f}" text-anchor="middle" fill="#FDE68A" font-size="9.8" font-weight="600">Jolly +{nodo["delta_km"]:.0f} km</text>'
            )
        else:
            parti_svg.append(
                f'<line x1="{xn:.1f}" y1="{ly1:.1f}" x2="{cx_lbl:.1f}" y2="{ly2:.1f}" stroke="#64748B" stroke-width="1.1" stroke-dasharray="2 2"/>'
                f'<circle cx="{xn:.1f}" cy="{yn:.1f}" r="6.2" fill="#F8FAFC" stroke="#475569" stroke-width="2"/>'
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{box_w:.1f}" height="{box_h:.1f}" rx="7" fill="#0B1220" fill-opacity="0.88" stroke="#334155" stroke-width="1"/>'
                f'<text x="{cx_lbl:.1f}" y="{by + 15:.1f}" text-anchor="middle" fill="#F1F5F9" font-size="10.2" font-weight="700">{nome_esc}</text>'
                f'<text x="{cx_lbl:.1f}" y="{by + 30:.1f}" text-anchor="middle" fill="#94A3B8" font-size="9.8" font-weight="600">Km {nodo["km"]:.0f} • {nodo["pct"]:.0f}%</text>'
            )

    # Posizionamento anti-sovrapposizione della macchinina azzurra + fumetto verde batteria:
    # se Sosta 1 cade nel primo 25% del tracciato, sposta la macchinina dopo Sosta 1 su un tratto libero
    # verificando geometricamente che il fumetto non si sovrapponga mai a nessun riquadro testuale.
    def _rettangoli_collidono(r1, r2, pad=5.0):
        return not (
            r1[2] + pad <= r2[0]
            or r1[0] - pad >= r2[2]
            or r1[3] + pad <= r2[1]
            or r1[1] - pad >= r2[3]
        )

    def _punteggio_car(t_cand, fumetto_sopra=True):
        x_c, y_c = _xy_strada(t_cand)
        if fumetto_sopra:
            rect_fum = (x_c - 24.0, y_c - 56.0, x_c + 24.0, y_c - 28.0)
        else:
            rect_fum = (x_c - 24.0, y_c + 18.0, x_c + 24.0, y_c + 46.0)
        rect_auto = (x_c - 20.0, y_c - 18.0, x_c + 20.0, y_c + 10.0)
        collisioni_fum = sum(1 for b in box_occupati if _rettangoli_collidono(rect_fum, b, pad=5.0))
        collisioni_auto = sum(1 for b in box_occupati if _rettangoli_collidono(rect_auto, b, pad=3.0))
        # Evita sovrapposizione con i cerchi dei nodi sulla carreggiata
        min_dist_nodi = min(
            [abs(t_cand - 0.0), abs(t_cand - 1.0)] + [abs(t_cand - n["t_vis"]) for n in nodi_interni]
        )
        penalita_nodi = 10 if min_dist_nodi < 0.055 else 0
        fuori_margini = 10 if (rect_fum[1] < 32.0 or rect_fum[3] > 296.0) else 0
        return (collisioni_fum * 100 + collisioni_auto * 20 + penalita_nodi + fuori_margini, -min_dist_nodi)

    ancore_t = [0.0] + [n["t_vis"] for n in nodi_interni] + [1.0]
    sosta1_early = any(n["tipo"] == "stop" and (n["t"] <= 0.25 or n["t_vis"] <= 0.28) for n in nodi_interni)

    candidati_t = []
    if sosta1_early:
        # Cerca prima i tratti liberi subito dopo Sosta 1 (evitando il tratto iniziale compresso)
        idx_s1 = next((idx for idx, n in enumerate(nodi_interni) if n["tipo"] == "stop"), 0)
        for seg_i in range(idx_s1 + 1, len(ancore_t) - 1):
            t_a, t_b = ancore_t[seg_i], ancore_t[seg_i + 1]
            if t_b - t_a >= 0.07:
                candidati_t.extend([(t_a + t_b) * 0.5, t_a + (t_b - t_a) * 0.38, t_a + (t_b - t_a) * 0.62])
    else:
        primo_t = nodi_interni[0]["t_vis"] if nodi_interni else 0.45
        candidati_t.append(max(0.08, min(0.17, primo_t * 0.52)))
        for seg_i in range(len(ancore_t) - 1):
            t_a, t_b = ancore_t[seg_i], ancore_t[seg_i + 1]
            if t_b - t_a >= 0.08:
                candidati_t.append((t_a + t_b) * 0.5)

    # Aggiunge sempre un campione uniforme lungo tutta la carreggiata come rete di sicurezza
    for step_t in (0.35, 0.48, 0.60, 0.72, 0.25, 0.82):
        candidati_t.append(step_t)

    miglior_car = None
    for fum_sopra in (True, False):
        for tc in candidati_t:
            score = _punteggio_car(tc, fumetto_sopra=fum_sopra)
            # Preferisce leggermente fumetto sopra a parità di zero collisioni
            chiave = (score[0], 0 if fum_sopra else 1, score[1])
            if miglior_car is None or chiave < miglior_car[0]:
                miglior_car = (chiave, tc, fum_sopra)
        if miglior_car is not None and miglior_car[0][0] == 0:
            break

    t_car = miglior_car[1] if miglior_car is not None else 0.14
    fumetto_car_sopra = miglior_car[2] if miglior_car is not None else True

    # Calcola la % batteria reale stimata al km corrispondente alla posizione della macchinina
    km_car = km_tot * t_car
    pct_car = _stima_pct_a_km(km_car)
    xc, yc = _xy_strada(t_car)
    ang_car = _angolo_strada(t_car)

    if fumetto_car_sopra:
        fumetto_svg = (
            f'<g transform="translate({xc:.1f},{yc - 44:.1f})">'
            '<rect x="-22" y="-11" width="44" height="20" rx="10" fill="#10B981" stroke="#ECFDF5" stroke-width="1.2"/>'
            '<polygon points="-4,9 4,9 0,14" fill="#10B981"/>'
            f'<text x="0" y="3" text-anchor="middle" fill="#06281E" font-size="10.5" font-weight="900">{pct_car:.0f}%</text>'
            '</g>'
        )
    else:
        fumetto_svg = (
            f'<g transform="translate({xc:.1f},{yc + 31:.1f})">'
            '<rect x="-22" y="-10" width="44" height="20" rx="10" fill="#10B981" stroke="#ECFDF5" stroke-width="1.2"/>'
            '<polygon points="-4,-10 4,-10 0,-15" fill="#10B981"/>'
            f'<text x="0" y="4" text-anchor="middle" fill="#06281E" font-size="10.5" font-weight="900">{pct_car:.0f}%</text>'
            '</g>'
        )

    # Disegna la macchinina azzurra stilizzata + fumetto verde batteria
    parti_svg.append(
        f'<g transform="translate({xc:.1f},{yc - 6:.1f}) rotate({ang_car:.1f})">'
        '<ellipse cx="0" cy="11" rx="18" ry="4" fill="#020617" opacity="0.55"/>'
        '<rect x="-17" y="-2" width="34" height="12" rx="6" fill="#38BDF8" stroke="#0284C7" stroke-width="1.4"/>'
        '<path d="M -10 -2 C -8 -11, 7 -11, 11 -2 Z" fill="#38BDF8" stroke="#0284C7" stroke-width="1.3"/>'
        '<path d="M -7 -2 C -5.5 -8.5, 5.5 -8.5, 8.5 -2 Z" fill="#E0F2FE"/>'
        # Occhietti simpatici sul parabrezza
        '<circle cx="1.2" cy="-5.0" r="1.9" fill="#FFFFFF"/>'
        '<circle cx="5.2" cy="-5.0" r="1.9" fill="#FFFFFF"/>'
        '<circle cx="1.7" cy="-5.0" r="0.95" fill="#0F172A"/>'
        '<circle cx="5.7" cy="-5.0" r="0.95" fill="#0F172A"/>'
        # Fanale anteriore luminoso e ruote scure con cerchione chiaro
        '<circle cx="15.5" cy="3.0" r="1.7" fill="#FEF08A"/>'
        '<circle cx="-9.5" cy="10.0" r="4.6" fill="#0F172A" stroke="#334155" stroke-width="1"/>'
        '<circle cx="-9.5" cy="10.0" r="2.0" fill="#E2E8F0"/>'
        '<circle cx="9.5" cy="10.0" r="4.6" fill="#0F172A" stroke="#334155" stroke-width="1"/>'
        '<circle cx="9.5" cy="10.0" r="2.0" fill="#E2E8F0"/>'
        '</g>'
        f'{fumetto_svg}'
    )

    # Legenda compatta nella parte bassa dell'SVG
    parti_svg.append(
        '<line x1="18" y1="300" x2="782" y2="300" stroke="#1E293B" stroke-width="1.2"/>'
        '<g font-size="11" font-weight="600" fill="#CBD5E1">'
        '<circle cx="56" cy="320" r="5.5" fill="#10B981" stroke="#ECFDF5" stroke-width="1.2"/>'
        '<text x="67" y="324">Partenza</text>'
        '<circle cx="166" cy="320" r="5" fill="#F8FAFC" stroke="#475569" stroke-width="1.5"/>'
        '<text x="177" y="324">Città di passaggio</text>'
        '<circle cx="332" cy="320" r="7.5" fill="#10B981" stroke="#ECFDF5" stroke-width="1.2"/>'
        '<text x="332" y="323.5" text-anchor="middle" fill="#0F172A" font-size="9" font-weight="900">⚡</text>'
        '<text x="345" y="324">Sosta Ricarica</text>'
        '<circle cx="486" cy="320" r="7.5" fill="#F59E0B" stroke="#FEF3C7" stroke-width="1.2"/>'
        '<text x="486" y="323.5" text-anchor="middle" fill="#0F172A" font-size="9" font-weight="900">J</text>'
        '<text x="499" y="324">Piano B / Jolly</text>'
        '<circle cx="642" cy="320" r="6" fill="#EF4444" stroke="#FEE2E2" stroke-width="1.2"/>'
        '<text x="654" y="324">Arrivo</text>'
        '</g>'
        '</svg>'
    )

    return "".join(parti_svg)


def render_svg_mappa_interattiva(svg_markup, height=360):
    """
    Renderizza un SVG della Mappa Visiva tramite components.html in un contenitore interattivo
    autocontenuto (senza librerie esterne) con:
      - pinch-to-zoom a 2 dita centrato sul punto medio (1.0x -> 4.0x) via viewBox nativo nitido;
      - pan fluido con 1 dito o mouse drag quando zoomato (> 1.0x);
      - zoom con rotella del mouse centrato sul cursore;
      - doppio tap / doppio clic per ripristinare istantaneamente 1.0x;
      - 3 pulsanti discreti semi-trasparenti (+, −, ↺) nell'angolo in alto a destra.
    """
    html_interattivo = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
<style>
  html, body {{
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    background: transparent;
    overflow: hidden;
  }}
  .ev-zoom-box {{
    position: relative;
    width: 100%;
    height: {int(height) - 4}px;
    background: #0F172A;
    border: 1px solid #1E293B;
    border-radius: 16px;
    overflow: hidden;
    touch-action: none;
    cursor: grab;
    box-sizing: border-box;
  }}
  .ev-zoom-box.is-panning {{
    cursor: grabbing;
  }}
  .ev-zoom-controls {{
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 20;
    display: flex;
    gap: 5px;
    align-items: center;
  }}
  .ev-zbtn {{
    width: 30px;
    height: 30px;
    border-radius: 8px;
    border: 1px solid rgba(148, 163, 184, 0.34);
    background: rgba(15, 23, 42, 0.74);
    color: #F1F5F9;
    font-size: 16px;
    font-weight: 700;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    padding: 0;
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    transition: background 0.15s ease, border-color 0.15s ease;
    touch-action: manipulation;
  }}
  .ev-zbtn:hover {{
    background: rgba(30, 41, 59, 0.92);
    border-color: rgba(56, 189, 248, 0.65);
  }}
  .ev-zbtn:active {{
    transform: scale(0.94);
  }}
</style>
</head>
<body>
<div class="ev-zoom-box" id="evZoomBox">
  <div class="ev-zoom-controls">
    <button type="button" class="ev-zbtn" id="btnZoomIn" title="Zoom avanti">+</button>
    <button type="button" class="ev-zbtn" id="btnZoomOut" title="Zoom indietro">&minus;</button>
    <button type="button" class="ev-zbtn" id="btnZoomReset" title="Ripristina vista 1x">&#8634;</button>
  </div>
  {svg_markup}
</div>
<script>
(function() {{
  var box = document.getElementById("evZoomBox");
  var svg = box ? box.querySelector("svg") : null;
  if (!box || !svg) return;

  var BASE_W = 800.0;
  var BASE_H = 340.0;
  var MIN_SCALE = 1.0;
  var MAX_SCALE = 4.0;

  var scale = 1.0;
  var vx = 0.0;
  var vy = 0.0;

  function clamp(v, mn, mx) {{
    return Math.max(mn, Math.min(mx, v));
  }}

  function getRenderMetrics() {{
    var rect = svg.getBoundingClientRect();
    var boxRatio = rect.width / Math.max(1, rect.height);
    var svgRatio = BASE_W / BASE_H;
    var renW, renH, offX, offY;
    if (boxRatio > svgRatio) {{
      renH = rect.height;
      renW = renH * svgRatio;
      offX = (rect.width - renW) / 2.0;
      offY = 0.0;
    }} else {{
      renW = rect.width;
      renH = renW / svgRatio;
      offX = 0.0;
      offY = (rect.height - renH) / 2.0;
    }}
    return {{ rect: rect, renW: Math.max(1, renW), renH: Math.max(1, renH), offX: offX, offY: offY }};
  }}

  function applyViewBox() {{
    var vw = BASE_W / scale;
    var vh = BASE_H / scale;
    vx = clamp(vx, 0.0, BASE_W - vw);
    vy = clamp(vy, 0.0, BASE_H - vh);
    svg.setAttribute("viewBox", vx.toFixed(2) + " " + vy.toFixed(2) + " " + vw.toFixed(2) + " " + vh.toFixed(2));
  }}

  function zoomAtClient(newScale, clientX, clientY) {{
    newScale = clamp(newScale, MIN_SCALE, MAX_SCALE);
    var m = getRenderMetrics();
    var fx = clamp((clientX - m.rect.left - m.offX) / m.renW, 0.0, 1.0);
    var fy = clamp((clientY - m.rect.top - m.offY) / m.renH, 0.0, 1.0);

    var oldVw = BASE_W / scale;
    var oldVh = BASE_H / scale;
    var wx = vx + fx * oldVw;
    var wy = vy + fy * oldVh;

    scale = newScale;
    var newVw = BASE_W / scale;
    var newVh = BASE_H / scale;
    vx = wx - fx * newVw;
    vy = wy - fy * newVh;
    applyViewBox();
  }}

  function zoomAtCenter(factor) {{
    var rect = svg.getBoundingClientRect();
    zoomAtClient(scale * factor, rect.left + rect.width / 2.0, rect.top + rect.height / 2.0);
  }}

  function resetView() {{
    scale = 1.0;
    vx = 0.0;
    vy = 0.0;
    applyViewBox();
  }}

  // Pulsanti rapidi (+, -, reset)
  document.getElementById("btnZoomIn").addEventListener("click", function(e) {{
    e.stopPropagation();
    zoomAtCenter(1.35);
  }});
  document.getElementById("btnZoomOut").addEventListener("click", function(e) {{
    e.stopPropagation();
    zoomAtCenter(1.0 / 1.35);
  }});
  document.getElementById("btnZoomReset").addEventListener("click", function(e) {{
    e.stopPropagation();
    resetView();
  }});

  // Doppio clic mouse per reset immediato
  box.addEventListener("dblclick", function(e) {{
    if (e.target && e.target.closest(".ev-zoom-controls")) return;
    e.preventDefault();
    resetView();
  }});

  // Zoom con rotella mouse centrato sul cursore
  box.addEventListener("wheel", function(e) {{
    e.preventDefault();
    var factor = e.deltaY < 0 ? 1.18 : (1.0 / 1.18);
    zoomAtClient(scale * factor, e.clientX, e.clientY);
  }}, {{ passive: false }});

  // Trascinamento mouse (pan quando zoom > 1.0x)
  var mouseDown = false;
  var lastMX = 0, lastMY = 0;
  box.addEventListener("mousedown", function(e) {{
    if (e.target && e.target.closest(".ev-zoom-controls")) return;
    if (scale <= 1.001) return;
    mouseDown = true;
    lastMX = e.clientX;
    lastMY = e.clientY;
    box.classList.add("is-panning");
  }});
  window.addEventListener("mousemove", function(e) {{
    if (!mouseDown) return;
    var m = getRenderMetrics();
    var vw = BASE_W / scale;
    var vh = BASE_H / scale;
    vx -= (e.clientX - lastMX) * (vw / m.renW);
    vy -= (e.clientY - lastMY) * (vh / m.renH);
    lastMX = e.clientX;
    lastMY = e.clientY;
    applyViewBox();
  }});
  window.addEventListener("mouseup", function() {{
    if (mouseDown) {{
      mouseDown = false;
      box.classList.remove("is-panning");
    }}
  }});

  // Eventi touch: pinch-to-zoom a 2 dita + pan fluido a 1 dito + doppio tap reset
  var pinchActive = false;
  var pinchStartDist = 0;
  var pinchStartScale = 1.0;
  var pinchLastMidX = 0, pinchLastMidY = 0;
  var panActive = false;
  var touchMoved = false;
  var lastTX = 0, lastTY = 0;
  var lastTapTime = 0;

  function touchDist(t1, t2) {{
    var dx = t2.clientX - t1.clientX;
    var dy = t2.clientY - t1.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }}

  box.addEventListener("touchstart", function(e) {{
    if (e.target && e.target.closest(".ev-zoom-controls")) return;
    if (e.touches.length === 2) {{
      e.preventDefault();
      pinchActive = true;
      panActive = false;
      touchMoved = true;
      pinchStartDist = Math.max(10, touchDist(e.touches[0], e.touches[1]));
      pinchStartScale = scale;
      pinchLastMidX = (e.touches[0].clientX + e.touches[1].clientX) / 2.0;
      pinchLastMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2.0;
    }} else if (e.touches.length === 1) {{
      pinchActive = false;
      panActive = scale > 1.001;
      touchMoved = false;
      lastTX = e.touches[0].clientX;
      lastTY = e.touches[0].clientY;
    }}
  }}, {{ passive: false }});

  box.addEventListener("touchmove", function(e) {{
    if (e.target && e.target.closest(".ev-zoom-controls")) return;
    if (pinchActive && e.touches.length === 2) {{
      e.preventDefault();
      var dist = Math.max(10, touchDist(e.touches[0], e.touches[1]));
      var midX = (e.touches[0].clientX + e.touches[1].clientX) / 2.0;
      var midY = (e.touches[0].clientY + e.touches[1].clientY) / 2.0;

      // Pan del punto medio + zoom centrato sul punto medio tra le due dita
      var m = getRenderMetrics();
      var vw = BASE_W / scale;
      var vh = BASE_H / scale;
      vx -= (midX - pinchLastMidX) * (vw / m.renW);
      vy -= (midY - pinchLastMidY) * (vh / m.renH);
      applyViewBox();

      var targetScale = pinchStartScale * (dist / pinchStartDist);
      zoomAtClient(targetScale, midX, midY);

      pinchLastMidX = midX;
      pinchLastMidY = midY;
    }} else if (panActive && e.touches.length === 1) {{
      e.preventDefault();
      var tx = e.touches[0].clientX;
      var ty = e.touches[0].clientY;
      if (Math.abs(tx - lastTX) > 2 || Math.abs(ty - lastTY) > 2) {{
        touchMoved = true;
      }}
      var m1 = getRenderMetrics();
      var vw1 = BASE_W / scale;
      var vh1 = BASE_H / scale;
      vx -= (tx - lastTX) * (vw1 / m1.renW);
      vy -= (ty - lastTY) * (vh1 / m1.renH);
      lastTX = tx;
      lastTY = ty;
      applyViewBox();
    }}
  }}, {{ passive: false }});

  box.addEventListener("touchend", function(e) {{
    if (e.target && e.target.closest(".ev-zoom-controls")) return;
    if (e.touches.length < 2) {{
      pinchActive = false;
    }}
    if (e.touches.length === 1 && scale > 1.001) {{
      panActive = true;
      lastTX = e.touches[0].clientX;
      lastTY = e.touches[0].clientY;
      return;
    }}
    if (e.touches.length === 0) {{
      panActive = false;
      if (!touchMoved) {{
        var now = Date.now();
        if (now - lastTapTime > 0 && now - lastTapTime < 310) {{
          e.preventDefault();
          resetView();
          lastTapTime = 0;
        }} else {{
          lastTapTime = now;
        }}
      }}
    }}
  }}, {{ passive: false }});

  applyViewBox();
}})();
</script>
</body>
</html>"""
    components.html(html_interattivo, height=int(height), scrolling=False)


def render_roadbook_visivo(sol, res):
    """Renderizza l'expander '🗺️ Mappa Visiva del Percorso' con SVG vettoriale zoomabile per ogni tratta."""
    if not sol or not sol.get("valido"):
        return

    andata_ritorno = bool(res.get("andata_ritorno"))
    km1 = float(sol.get("km1", res.get("km1", 0.0)))
    tot_km = float(sol.get("tot_km", res.get("tot_km", km1)))
    km2 = max(0.0, tot_km - km1) if andata_ritorno else 0.0
    km_per_pct = float(sol.get("km_per_pct", res.get("km_per_pct", 3.5)))
    p_start = float(res.get("p_start", 80.0))
    pct_arr_b = float(sol.get("pct_arrivo_b", 0.0))
    pct_rientro_a = sol.get("pct_rientro_a")

    soste_sol = sol.get("soste", [])
    if andata_ritorno:
        soste_andata = [s for s in soste_sol if not s.get("e_ritorno")]
        soste_ritorno = [s for s in soste_sol if s.get("e_ritorno")]
    else:
        soste_andata = list(soste_sol)
        soste_ritorno = []

    with st.expander("🗺️ Mappa Visiva del Percorso", expanded=True):
        svg_andata = genera_svg_roadbook_visivo(
            titolo_tratta=f"➡️ ANDATA • {res['orig']} → {res['dest']}",
            orig_nome=res["orig"],
            dest_nome=res["dest"],
            km_tratta=km1,
            pct_partenza=p_start,
            pct_arrivo=pct_arr_b,
            soste_tratta=soste_andata,
            nodi_citta=res.get("nodi_andata", []),
            km_per_pct=km_per_pct,
            km_offset=0.0,
        )
        render_svg_mappa_interattiva(svg_andata, height=360)

        if andata_ritorno and km2 > 0.5:
            svg_ritorno = genera_svg_roadbook_visivo(
                titolo_tratta=f"⬅️ RITORNO • {res['dest']} → {res['orig']}",
                orig_nome=res["dest"],
                dest_nome=res["orig"],
                km_tratta=km2,
                pct_partenza=pct_arr_b,
                pct_arrivo=float(pct_rientro_a if pct_rientro_a is not None else 0.0),
                soste_tratta=soste_ritorno,
                nodi_citta=res.get("nodi_ritorno", []),
                km_per_pct=km_per_pct,
                km_offset=km1,
            )
            render_svg_mappa_interattiva(svg_ritorno, height=360)


def render_calcolatore_risparmio_annuale(current_user, auto_cfg=None, guida_scelta=None):
    """Renderizza in fondo alla pagina il calcolatore richiudibile Elettrica vs Termica su base annua e 5 anni."""
    st.markdown('<div style="margin-top: 1.2rem;"></div>', unsafe_allow_html=True)
    with st.expander("💶 Calcolatore Risparmio Annuale: Elettrica vs Termica", expanded=False):
        utente_low = str(current_user or "").strip().lower()
        default_casa = 0.21 if utente_low in ("fabio", "elizabeth", "alessia") else 0.25
        if "calc_ann_prezzo_casa" not in st.session_state:
            st.session_state["calc_ann_prezzo_casa"] = float(
                st.session_state.get("num_costo_casa", default_casa)
            )

        # Inizializza i default specifici per profilo utente (es. Federico agente 50.000 km/anno)
        if st.session_state.get("calc_ann_init_user") != utente_low:
            st.session_state["calc_ann_init_user"] = utente_low
            if utente_low == "federico":
                st.session_state["calc_lav_dist"] = 0.0
                st.session_state["calc_ext_km"] = 50000
                st.session_state["calc_ext_auto_pct"] = 60
                st.session_state["calc_ext_casa_pct"] = 60
            else:
                st.session_state.setdefault("calc_lav_dist", 15.0)
                st.session_state.setdefault("calc_ext_km", 7000)
                st.session_state.setdefault("calc_ext_auto_pct", 50)
                st.session_state.setdefault("calc_ext_casa_pct", 80)

        # Menu auto dedicato in cima al calcolatore, preselezionato sull'auto scelta nello Step 1
        opz_auto_calc = opzioni_auto_per_utente(current_user)
        auto_step1 = st.session_state.get("sel_auto")
        if auto_step1 not in opz_auto_calc:
            auto_step1 = opz_auto_calc[0] if opz_auto_calc else ""

        if (
            st.session_state.get("calc_ann_auto") not in opz_auto_calc
            or st.session_state.get("calc_ann_sync_step1") != auto_step1
        ):
            st.session_state["calc_ann_auto"] = auto_step1
            st.session_state["calc_ann_sync_step1"] = auto_step1

        scelta_auto_calc = st.selectbox(
            "🚗 Auto per il calcolo",
            opz_auto_calc,
            key="calc_ann_auto",
        )

        custom_cons_calc = float(st.session_state.get("num_cons", 18.0))
        if "Altra Auto" in str(scelta_auto_calc):
            custom_cons_calc = float(
                st.number_input(
                    "⚡ Consumo base autostradale estivo (kWh/100km)",
                    min_value=10.0,
                    max_value=35.0,
                    value=custom_cons_calc,
                    step=0.5,
                    format="%.1f",
                    key="calc_ann_custom_cons",
                )
            )

        cfg_eff = (
            costruisci_profilo_auto(
                scelta_auto_calc,
                float(st.session_state.get("num_bat", 60.0)),
                custom_cons_calc,
                float(st.session_state.get("num_dc", 120.0)),
            )
            if scelta_auto_calc
            else (auto_cfg if isinstance(auto_cfg, dict) and auto_cfg.get("cons_base") else None)
        )

        # Prezzo medio pubblico di default: 0.47 €/kWh (Tesla Supercharger) per auto/utenti Tesla, altrimenti 0.65 €/kWh
        e_tesla_calc = (
            bool(isinstance(cfg_eff, dict) and cfg_eff.get("is_tesla_car"))
            or ("tesla" in str(scelta_auto_calc or "").lower())
            or (utente_low in UTENTI_TESLA)
        )
        default_pub = 0.47 if e_tesla_calc else 0.65
        if (
            "calc_ann_prezzo_pub" not in st.session_state
            or st.session_state.get("calc_ann_last_tesla") != e_tesla_calc
        ):
            st.session_state["calc_ann_prezzo_pub"] = default_pub
            st.session_state["calc_ann_last_tesla"] = e_tesla_calc

        guida_eff = guida_scelta or st.session_state.get("sel_guida") or "🚗 Guida Regolare"
        stile_dict = STILI_GUIDA.get(guida_eff, {"autostrada": 1.0, "statale": 1.0})

        # Consumi medi annui stimati (media ponderata delle 4 stagioni = +7% sulla base estiva)
        # Fallback sicuro: 21.0 kWh/100km autostrada e 16.0 kWh/100km extraurbano se nessun profilo auto è disponibile
        if isinstance(cfg_eff, dict) and cfg_eff.get("cons_base"):
            nome_auto_calc = str(cfg_eff.get("nome") or "Auto Elettrica")
            cons_base_val = float(cfg_eff["cons_base"])
            cons_auto_annuo = round(cons_base_val * 1.07 * stile_dict["autostrada"], 1)
            cons_extra_annuo = round(
                cons_base_val * FATTORE_CONSUMO_EXTRAURBANO * 1.07 * stile_dict["statale"],
                1,
            )
        else:
            nome_auto_calc = "Auto Elettrica"
            cons_auto_annuo = 21.0
            cons_extra_annuo = 16.0
        # Consumo urbano (-15% rispetto all'extraurbano per effetto della frenata rigenerativa in città)
        cons_urbano_annuo = round(cons_extra_annuo * 0.85, 1)

        def _fmt_int_it(val):
            return f"{int(round(val)):,}".replace(",", ".")

        def _fmt_risp_it(val):
            segno = "+" if val >= 0 else "-"
            return f"{segno}{_fmt_int_it(abs(val))} €"

        st.caption(
            f"Consumi medi annui stimati ({nome_auto_calc}): "
            f"🏙️ Urbano {cons_urbano_annuo:.1f} • 🛣️ Misto {cons_extra_annuo:.1f} • 🛣️ Autostrada {cons_auto_annuo:.1f} kWh/100km."
        )

        # --- SEZIONE 1: TRAGITTO CASA - LAVORO (GIORNI LAVORATIVI REALI) ---
        st.markdown(
            '<div class="ev-step-header" style="border-left-color:#10B981; margin-top:8px;">'
            '<span class="ev-step-title">🏢 1. Tragitto Casa – Lavoro (giorni lavorativi reali)</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        c_l1, c_l2, c_l3 = st.columns(3)
        dist_casa_lav = float(
            c_l1.number_input(
                "📍 Distanza Casa → Lavoro (km sola andata — indicare 0 se non c'è)",
                min_value=0.0,
                max_value=300.0,
                step=1.0,
                format="%.1f",
                help="Indicare 0 se non c'è un percorso fisso casa/lavoro, es. agenti o lavoro itinerante",
                key="calc_lav_dist",
            )
        )
        tratte_giorno = int(
            c_l2.selectbox(
                "🔄 Tratte al giorno (2 = A/R, 4 = pausa pranzo)",
                options=[2, 4, 1, 3, 6],
                index=0,
                key="calc_lav_tratte",
            )
        )
        gg_ferie = int(
            c_l3.number_input(
                "📅 Giorni chiusura / ferie all'anno",
                min_value=0,
                max_value=100,
                value=20,
                step=1,
                key="calc_lav_ferie",
            )
        )

        gg_eff = max(0, 252 - gg_ferie)
        km_lavoro = round(dist_casa_lav * tratte_giorno * gg_eff)
        if dist_casa_lav <= 0.0:
            st.markdown(
                '<div class="ev-info-box" style="margin-top:2px; margin-bottom:10px;">'
                'ℹ️ <strong>Nessun percorso fisso Casa – Lavoro (0 km):</strong> '
                '<span style="color:#94A3B8;">il confronto finale considera direttamente i Viaggi / Percorrenza Annua qui sotto.</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="ev-info-box" style="margin-top:2px; margin-bottom:10px;">'
                f'📆 <strong>= {gg_eff} giorni lavorativi effettivi/anno ({_fmt_int_it(km_lavoro)} km)</strong> '
                f'<span style="color:#94A3B8;">(252 giorni lavorativi base italiani meno {gg_ferie} gg di ferie/chiusura)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        opzioni_percorso_lav = [
            "🏙️ Urbano",
            "🛣️ Misto (Urbano + Extraurbano)",
            "🛣️ Prevalenza Autostrada",
        ]
        tipo_percorso_lav = st.radio(
            "Tipo percorso Casa – Lavoro",
            opzioni_percorso_lav,
            index=1,
            horizontal=True,
            key="calc_lav_tipo_perc",
        )
        if tipo_percorso_lav == "🏙️ Urbano":
            cons_lavoro = cons_urbano_annuo
        elif tipo_percorso_lav == "🛣️ Prevalenza Autostrada":
            cons_lavoro = cons_auto_annuo
        else:
            cons_lavoro = cons_extra_annuo

        cl_s1, cl_s2 = st.columns([2, 1])
        quota_casa_lav = int(
            cl_s1.slider(
                "🏠 Ricariche a Casa per Casa – Lavoro (%)",
                min_value=0,
                max_value=100,
                value=95,
                step=5,
                key="calc_lav_casa_pct",
            )
        )
        cl_s2.markdown(
            f'<div style="margin-top:24px; padding:8px 10px; border-radius:10px; background:#161A23; '
            f'border:1px solid #2A2E39; text-align:center; font-size:0.84rem; font-weight:700; color:#E2E8F0;">'
            f'🏠 {quota_casa_lav}% Casa / ⚡ {100 - quota_casa_lav}% Pubbliche'
            f'</div>',
            unsafe_allow_html=True,
        )

        # --- SEZIONE 2: VIAGGI EXTRA, WEEKEND E TRASFERTE ---
        st.markdown(
            '<div class="ev-step-header" style="border-left-color:#3B82F6; margin-top:12px;">'
            '<span class="ev-step-title">🏖️ 2. Viaggi Extra, Weekend e Trasferte</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        ce_1, ce_2 = st.columns(2)
        km_extra_annui = int(
            ce_1.number_input(
                "🗺️ Km extra / itineranti annui (weekend, gite, vacanze, lavoro)",
                min_value=0,
                max_value=150000,
                step=500,
                key="calc_ext_km",
            )
        )
        quota_auto_ext = int(
            ce_2.slider(
                "🛣️ Quota autostrada viaggi extra (%)",
                min_value=0,
                max_value=100,
                step=5,
                key="calc_ext_auto_pct",
            )
        )

        ce_s1, ce_s2 = st.columns([2, 1])
        quota_casa_ext = int(
            ce_s1.slider(
                "🏠 Ricariche a Casa sui viaggi extra (%)",
                min_value=0,
                max_value=100,
                step=5,
                key="calc_ext_casa_pct",
            )
        )
        ce_s2.markdown(
            f'<div style="margin-top:24px; padding:8px 10px; border-radius:10px; background:#161A23; '
            f'border:1px solid #2A2E39; text-align:center; font-size:0.84rem; font-weight:700; color:#E2E8F0;">'
            f'🏠 {quota_casa_ext}% Casa / ⚡ {100 - quota_casa_ext}% Pubbliche'
            f'</div>',
            unsafe_allow_html=True,
        )

        # --- PARAMETRI TARIFFE CONDIVISI (SU UNA RIGA COMPATTA) ---
        st.markdown(
            '<div class="ev-step-header" style="border-left-color:#F59E0B; margin-top:12px;">'
            '<span class="ev-step-title">💶 Tariffe Energia &amp; Riferimento Termica</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        t1, t2, t3, t4 = st.columns(4)
        prezzo_casa = float(
            t1.number_input(
                "🏠 Casa (€/kWh)",
                min_value=0.05,
                max_value=1.00,
                step=0.01,
                format="%.2f",
                help="Tariffa energia domestica in €/kWh",
                key="calc_ann_prezzo_casa",
            )
        )
        prezzo_pub = float(
            t2.number_input(
                "⚡ Pubblica (€/kWh)",
                min_value=0.10,
                max_value=1.50,
                step=0.01,
                format="%.2f",
                help="0,47 €/kWh tariffa Tesla Supercharger • 0,65 €/kWh media pubblica (modifica se usi abbonamenti)",
                key="calc_ann_prezzo_pub",
            )
        )
        prezzo_carb = float(
            t3.number_input(
                "⛽ Carburante (€/L)",
                min_value=0.80,
                max_value=3.50,
                value=2.05,
                step=0.05,
                format="%.2f",
                help="Media benzina/diesel 2026, modificabile",
                key="calc_ann_carb",
            )
        )
        consumo_kml = float(
            t4.number_input(
                "⛽ Termica (km/L)",
                min_value=5.0,
                max_value=35.0,
                value=16.0,
                step=0.5,
                format="%.1f",
                help="Consumo medio dell'auto termica di confronto",
                key="calc_ann_kml",
            )
        )

        # --- CALCOLO SEPARATO CASA-LAVORO / VIAGGI EXTRA / TOTALE ---
        kwh_lavoro = (km_lavoro / 100.0) * cons_lavoro
        prezzo_ev_lav = (quota_casa_lav / 100.0) * prezzo_casa + ((100 - quota_casa_lav) / 100.0) * prezzo_pub
        spesa_ev_lav = kwh_lavoro * prezzo_ev_lav
        spesa_term_lav = (km_lavoro / max(1.0, consumo_kml)) * prezzo_carb
        risp_lav = spesa_term_lav - spesa_ev_lav

        km_ext_auto = km_extra_annui * (quota_auto_ext / 100.0)
        km_ext_misto = km_extra_annui - km_ext_auto
        kwh_extra = (km_ext_auto / 100.0) * cons_auto_annuo + (km_ext_misto / 100.0) * cons_extra_annuo
        prezzo_ev_ext = (quota_casa_ext / 100.0) * prezzo_casa + ((100 - quota_casa_ext) / 100.0) * prezzo_pub
        spesa_ev_ext = kwh_extra * prezzo_ev_ext
        spesa_term_ext = (km_extra_annui / max(1.0, consumo_kml)) * prezzo_carb
        risp_ext = spesa_term_ext - spesa_ev_ext

        km_tot_annui = km_lavoro + km_extra_annui
        kwh_tot_annui = kwh_lavoro + kwh_extra
        litri_tot_annui = km_tot_annui / max(1.0, consumo_kml)
        spesa_ev_tot = spesa_ev_lav + spesa_ev_ext
        spesa_term_tot = spesa_term_lav + spesa_term_ext
        risp_tot = spesa_term_tot - spesa_ev_tot
        risp_5anni = risp_tot * 5.0

        col_rl = "#34D399" if risp_lav >= 0 else "#F87171"
        col_re = "#34D399" if risp_ext >= 0 else "#F87171"
        col_rt = "#34D399" if risp_tot >= 0 else "#F87171"
        euro_100_ev = (spesa_ev_tot / km_tot_annui) * 100.0 if km_tot_annui > 0 else 0.0
        euro_100_term = (spesa_term_tot / km_tot_annui) * 100.0 if km_tot_annui > 0 else 0.0

        # --- RIEPILOGO FINALE (NASCONDE CASA-LAVORO SE DISTANZA == 0) + PROIEZIONE 5 ANNI ---
        riga_casa_lavoro_html = ""
        if dist_casa_lav > 0.0:
            riga_casa_lavoro_html = (
                f'<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">'
                f'<td style="padding:10px 12px; font-weight:600;">🏢 Casa – Lavoro ({_fmt_int_it(km_lavoro)} km)</td>'
                f'<td style="padding:10px 10px; text-align:right; color:#93C5FD; font-weight:600;">{_fmt_int_it(spesa_ev_lav)} €</td>'
                f'<td style="padding:10px 10px; text-align:right; color:#FDE68A; font-weight:600;">{_fmt_int_it(spesa_term_lav)} €</td>'
                f'<td style="padding:10px 12px; text-align:right; color:{col_rl}; font-weight:700;">{_fmt_risp_it(risp_lav)}</td>'
                f'</tr>'
            )
        label_extra_tab = (
            f"🏖️ Viaggi / Percorrenza Annua ({_fmt_int_it(km_extra_annui)} km)"
            if dist_casa_lav <= 0.0
            else f"🏖️ Viaggi Extra ({_fmt_int_it(km_extra_annui)} km)"
        )

        st.markdown(
            f'<div style="overflow-x:auto; margin-top:12px; border-radius:12px; border:1px solid #2A2E39; background:#131722;">'
            f'<table style="width:100%; border-collapse:collapse; font-size:0.9rem; color:#F1F5F9;">'
            f'<thead>'
            f'<tr style="background:#1A202E; border-bottom:1px solid #2A2E39; text-align:left;">'
            f'<th style="padding:10px 12px; color:#CBD5E1; font-weight:700;">Categoria &amp; Km</th>'
            f'<th style="padding:10px 10px; color:#60A5FA; font-weight:700; text-align:right;">⚡ Costo Elettrico</th>'
            f'<th style="padding:10px 10px; color:#FBBF24; font-weight:700; text-align:right;">⛽ Costo Termico</th>'
            f'<th style="padding:10px 12px; color:#34D399; font-weight:700; text-align:right;">💰 Risparmio Netto</th>'
            f'</tr>'
            f'</thead>'
            f'<tbody>'
            f'{riga_casa_lavoro_html}'
            f'<tr style="border-bottom:1px solid rgba(255,255,255,0.08);">'
            f'<td style="padding:10px 12px; font-weight:600;">{label_extra_tab}</td>'
            f'<td style="padding:10px 10px; text-align:right; color:#93C5FD; font-weight:600;">{_fmt_int_it(spesa_ev_ext)} €</td>'
            f'<td style="padding:10px 10px; text-align:right; color:#FDE68A; font-weight:600;">{_fmt_int_it(spesa_term_ext)} €</td>'
            f'<td style="padding:10px 12px; text-align:right; color:{col_re}; font-weight:700;">{_fmt_risp_it(risp_ext)}</td>'
            f'</tr>'
            f'<tr style="background:rgba(16,185,129,0.14); border-top:2px solid rgba(16,185,129,0.45);">'
            f'<td style="padding:12px 12px; font-weight:800; color:#FFFFFF;">🏆 TOTALE ANNUO ({_fmt_int_it(km_tot_annui)} km)</td>'
            f'<td style="padding:12px 10px; text-align:right; color:#60A5FA; font-weight:800;">{_fmt_int_it(spesa_ev_tot)} €</td>'
            f'<td style="padding:12px 10px; text-align:right; color:#FBBF24; font-weight:800;">{_fmt_int_it(spesa_term_tot)} €</td>'
            f'<td style="padding:12px 12px; text-align:right; color:{col_rt}; font-weight:900; font-size:0.98rem;">{_fmt_risp_it(risp_tot)}</td>'
            f'</tr>'
            f'</tbody>'
            f'</table>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<div class="ev-card" style="margin-top:10px; border-left:5px solid {col_rt}; '
            f'background:linear-gradient(135deg, #132C24 0%, #1A202E 100%); padding:14px 16px;">'
            f'<div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:10px;">'
            f'<div>'
            f'<div style="font-size:0.82rem; color:#CBD5E1; font-weight:600;">💰 Risparmio Netto Totale Annuo</div>'
            f'<div style="font-size:1.35rem; font-weight:900; color:{col_rt};">{_fmt_risp_it(risp_tot)} / anno</div>'
            f'<div style="font-size:0.80rem; color:#94A3B8; margin-top:2px;">'
            f'⚡ {_fmt_int_it(kwh_tot_annui)} kWh (~{euro_100_ev:.2f} €/100km) vs ⛽ {_fmt_int_it(litri_tot_annui)} L (~{euro_100_term:.2f} €/100km)'
            f'</div>'
            f'</div>'
            f'<div style="text-align:right; padding:8px 14px; border-radius:10px; '
            f'background:rgba(15,23,42,0.72); border:1px solid rgba(52,211,153,0.35);">'
            f'<div style="font-size:0.78rem; color:#CBD5E1; font-weight:600;">📈 Proiezione su 5 anni</div>'
            f'<div style="font-size:1.22rem; font-weight:900; color:{col_rt};">{_fmt_risp_it(risp_5anni)}</div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def render_riepilogo_soluzione(sol, controlli, res, colore_bordo):
    """Renderizza riepilogo tempi, selettore alternative + card di ogni sosta e card finale di arrivo."""
    if not sol or not sol.get("valido"):
        msg = sol.get("errore") if sol else "Nessuna soluzione alternativa disponibile."
        st.markdown(
            f'<div class="ev-card" style="border-left-color:#EF4444;">'
            f'<div class="ev-card-title">⚠️ Soluzione non disponibile</div>'
            f'<div class="ev-warn-box">{html.escape(msg)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    # Banner di avviso esplicito in caso di copertura insufficiente col solo operatore scelto
    if sol.get("avviso_copertura_op"):
        st.warning(sol["avviso_copertura_op"])

    n_soste = len(sol["soste"])
    stato_badge = (
        '<span class="ev-badge ev-b-green">✅ Diretto • 0 soste</span>'
        if n_soste == 0
        else f'<span class="ev-badge ev-b-blue">🔋 {n_soste} {"sosta" if n_soste == 1 else "soste"} • {sol["minuti_ricarica"]} min ricarica</span>'
    )

    stile_codice = res.get("stile_codice", "veloce")
    pref_txt = html.escape(testo_preferenza_sosta(stile_codice))
    if (
        stile_codice in ("caffe", "pranzo")
        and n_soste > 0
        and not (stile_codice == "caffe" and any(s.get("in_ads") for s in sol["soste"]))
        and all(s.get("has_food", 0) == 0 and s.get("has_mall", 0) == 0 for s in sol["soste"])
    ):
        pref_txt += ' <span style="color:#FBBF24;">(Nessun locale mappato entro 200m, sosta ottimizzata per ricarica)</span>'

    dur_sol = sol.get("tot_dur", res["tot_dur"])
    km_sol = sol.get("tot_km", res["tot_km"])
    km1_sol = float(sol.get("km1", res["km1"]))
    km2_sol = float(sol.get("km2", max(0.0, km_sol - km1_sol)))
    cons_sol = sol.get("cons_eff", res["cons_eff"])
    kwh_sol = sol.get("kwh_totali", res["kwh_totali"])
    tipo_strada_sol = sol.get("tipo_strada")

    # Dati economici completi (casa + colonnine + pedaggio + totale)
    kwh_casa = float(sol.get("kwh_casa", kwh_sol))
    kwh_col = float(sol.get("kwh_colonnine", 0.0))
    costo_casa = float(sol.get("costo_casa", 0.0))
    costo_col = float(sol.get("costo_colonnine", 0.0))
    costo_pedaggio = float(sol.get("costo_pedaggio", 0.0))
    costo_totale = float(sol.get("costo_totale", costo_casa + costo_col + costo_pedaggio))
    senza_auto = bool(sol.get("senza_autostrada", False))
    pedaggio_txt = "0,00 € (senza autostrada)" if senza_auto else f"~{formatta_euro(costo_pedaggio)}"

    riga_strada = ""
    if tipo_strada_sol:
        riga_strada = (
            f'<div class="ev-kv"><span class="ev-k">Percorso e consumo</span>'
            f'<span class="ev-v">{html.escape(tipo_strada_sol)} • {km_sol:.0f} km • {kwh_sol:.1f} kWh ({cons_sol:.1f} kWh/100km)</span></div>'
        )

    riepilogo_html = (
        f'<div class="ev-card" style="border-left-color:{colore_bordo};">'
        f'<div class="ev-card-top">'
        f'<div class="ev-card-title">📊 Riepilogo Tempi &amp; Costi</div>'
        f'<div class="ev-badges">{stato_badge}</div>'
        f'</div>'
        f'{riga_strada}'
        f'<div class="ev-kv"><span class="ev-k">Tempo guida stradale</span><span class="ev-v">{formatta_durata(dur_sol)}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">Ricarica + deviazioni</span><span class="ev-v">{sol["minuti_ricarica"]} min ricarica (+{sol["minuti_deviazione"]} min manovre/deviazioni)</span></div>'
        f'<div class="ev-kv"><span class="ev-k">Preferenza sosta applicata</span><span class="ev-v">{pref_txt}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">🏠 Energia da casa ({kwh_casa:.1f} kWh)</span><span class="ev-v">~{formatta_euro(costo_casa)}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">⚡ Ricarica alle colonnine ({kwh_col:.1f} kWh)</span><span class="ev-v">~{formatta_euro(costo_col)}</span></div>'
        f'<div class="ev-kv"><span class="ev-k">🛣️ Stima pedaggio autostradale</span><span class="ev-v">{pedaggio_txt}</span></div>'
        f'<div class="ev-kv" style="border-top:1px solid rgba(255,255,255,0.12); padding-top:8px; margin-top:2px;">'
        f'<span class="ev-k" style="color:#F8FAFC; font-weight:700;">💶 Costo Totale Stimato Viaggio</span>'
        f'<span class="ev-v" style="color:#34D399; font-size:1.02rem; font-weight:800;">~{formatta_euro(costo_totale)}</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(riepilogo_html, unsafe_allow_html=True)

    soste_sol = sol.get("soste", [])
    orig_esc = html.escape(res["orig"])
    dest_esc = html.escape(res["dest"])
    ora_part = html.escape(str(sol.get("ora_partenza") or res.get("ora_partenza", "--:--")))
    ora_rip_rit = html.escape(str(sol.get("ora_partenza_ritorno") or "--:--"))

    def _render_sosta_con_selettore(idx_global, sosta_item):
        if idx_global - 1 < len(controlli) and controlli[idx_global - 1] is not None:
            sel_key, opzioni_id, mappa_etichette = controlli[idx_global - 1]
            st.selectbox(
                f"🔄 Cambia stazione Sosta {idx_global} (alternative prima/dopo lungo il percorso)",
                options=opzioni_id,
                format_func=lambda sid, m=mappa_etichette: m[sid],
                key=sel_key,
            )
        render_card_sosta(
            idx_global,
            sosta_item,
            res=res,
            km1_offset=km1_sol,
            tot_km_sol=km_sol,
            colore_bordo=colore_bordo,
        )

    if res["andata_ritorno"]:
        # --- SEZIONE 1: VIAGGIO DI ANDATA ---
        st.markdown(
            f'<div class="ev-step-header" style="border-left-color:#10B981; margin-top:14px;">'
            f'<span class="ev-step-title">➡️ VIAGGIO DI ANDATA ({orig_esc} → {dest_esc})</span>'
            f'<span class="ev-step-tag" style="background:rgba(16,185,129,0.16); color:#34D399;">{km1_sol:.0f} km • Partenza {ora_part}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        soste_andata_idx = [(idx, s) for idx, s in enumerate(soste_sol, start=1) if not s.get("e_ritorno")]
        soste_ritorno_idx = [(idx, s) for idx, s in enumerate(soste_sol, start=1) if s.get("e_ritorno")]

        for idx_g, sosta in soste_andata_idx:
            _render_sosta_con_selettore(idx_g, sosta)

        # Card Arrivo a Destinazione subito al termine dell'andata
        render_card_arrivo_destinazione(sol, res, colore_bordo=colore_bordo)

        # --- SEZIONE 2: VIAGGIO DI RITORNO (contachilometri azzerato a Km 0.0) ---
        st.markdown(
            f'<div class="ev-step-header" style="border-left-color:#3B82F6; margin-top:16px;">'
            f'<span class="ev-step-title">⬅️ VIAGGIO DI RITORNO ({dest_esc} → {orig_esc})</span>'
            f'<span class="ev-step-tag">{km2_sol:.0f} km • Ripartenza {ora_rip_rit}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for idx_g, sosta in soste_ritorno_idx:
            _render_sosta_con_selettore(idx_g, sosta)

        # Card Finale di Rientro a Casa
        render_card_rientro_finale(sol, res, colore_bordo=colore_bordo)
    else:
        # Solo Andata: soste progressive e card di arrivo a destinazione
        for idx_g, sosta in enumerate(soste_sol, start=1):
            _render_sosta_con_selettore(idx_g, sosta)
        render_card_arrivo_destinazione(sol, res, colore_bordo=colore_bordo)

    # Pulsante/i per aprire l'intero itinerario su Google Maps (etichetta dinamica con soste vs diretto)
    if res["andata_ritorno"]:
        soste_andata = [s for s in soste_sol if not s.get("e_ritorno")]
        soste_ritorno = [s for s in soste_sol if s.get("e_ritorno")]
        url_andata = html.escape(url_gmaps_viaggio_multi_tappa(res["orig"], res["dest"], soste_andata), quote=True)
        url_ritorno = html.escape(url_gmaps_viaggio_multi_tappa(res["dest"], res["orig"], soste_ritorno), quote=True)
        lbl_andata = "🗺️ Apri Andata su Maps (con soste)" if soste_andata else "🗺️ Apri Andata su Maps (diretto)"
        lbl_ritorno = "🗺️ Apri Ritorno su Maps (con soste)" if soste_ritorno else "🗺️ Apri Ritorno su Maps (diretto)"
        st.markdown(
            f'<div class="ev-dir-grid">'
            f'<a class="ev-gmaps-cta" href="{url_andata}" target="_blank" rel="noopener noreferrer">{lbl_andata}</a>'
            f'<a class="ev-gmaps-cta" href="{url_ritorno}" target="_blank" rel="noopener noreferrer">{lbl_ritorno}</a>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        url_intero = html.escape(url_gmaps_viaggio_multi_tappa(res["orig"], res["dest"], soste_sol), quote=True)
        lbl_intero = (
            "🗺️ Apri Intero Viaggio su Google Maps (con soste)"
            if soste_sol
            else "🗺️ Apri Intero Viaggio su Google Maps (diretto)"
        )
        st.markdown(
            f'<a class="ev-gmaps-cta" href="{url_intero}" target="_blank" rel="noopener noreferrer">'
            f'{lbl_intero}'
            f'</a>',
            unsafe_allow_html=True,
        )


def inietta_icona_mobile_head():
    """Inietta nel <head> i tag apple-touch-icon, icon e meta web-app per iPhone iOS e Android."""
    icon_data_uri = f"data:image/png;base64,{APP_ICON_B64}"
    components.html(
        f"""
        <script>
            (function() {{
                var doc = window.parent.document;
                var head = doc.head || doc.getElementsByTagName("head")[0];
                if (!head) return;
                var dataUri = "{icon_data_uri}";

                function upsertLink(rel, attrs) {{
                    var sel = 'link[rel="' + rel + '"][data-ev-app-icon="1"]';
                    var el = head.querySelector(sel);
                    if (!el) {{
                        el = doc.createElement("link");
                        el.setAttribute("rel", rel);
                        el.setAttribute("data-ev-app-icon", "1");
                        head.appendChild(el);
                    }}
                    Object.keys(attrs).forEach(function(k) {{
                        el.setAttribute(k, attrs[k]);
                    }});
                }}

                function upsertMeta(name, content) {{
                    var el = head.querySelector('meta[name="' + name + '"]');
                    if (!el) {{
                        el = doc.createElement("meta");
                        el.setAttribute("name", name);
                        head.appendChild(el);
                    }}
                    el.setAttribute("content", content);
                }}

                upsertLink("apple-touch-icon", {{ sizes: "180x180", href: dataUri }});
                upsertLink("icon", {{ type: "image/png", sizes: "192x192", href: dataUri }});
                upsertMeta("apple-mobile-web-app-title", "EV Roadbook");
                upsertMeta("apple-mobile-web-app-capable", "yes");
            }})();
        </script>
        """,
        height=0,
    )


# ==============================================================================
# INTERFACCIA PRINCIPALE
# ==============================================================================
inietta_stile_dark()
inietta_icona_mobile_head()

# Schermata di Login sicura all'apertura (st.secrets["users"] con fallback solo locale)
if not st.session_state.get("auth_user"):
    utenti_auth, is_fallback_locale = carica_utenti_autorizzati()
    st.markdown(
        '<div class="ev-hero">'
        '<div class="ev-hero-title">⚡ EV Roadbook Italia</div>'
        '<div class="ev-hero-author">by Fabio Tod</div>'
        '<div class="ev-hero-sub">Accesso riservato • Pianificazione guidata Mobile-First</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    with st.form("login_form", clear_on_submit=False):
        st.markdown(
            '<div class="ev-card-title" style="margin-bottom: 4px;">🔐 Accedi al Roadbook</div>'
            '<div class="ev-card-sub" style="margin-bottom: 12px;">Inserisci nome utente e password per iniziare.</div>',
            unsafe_allow_html=True,
        )
        utente_inp = st.text_input(
            "👤 Utente",
            placeholder="Es. Fabio",
            autocomplete="username",
            key="login_utente_inp",
        )
        password_inp = st.text_input(
            "🔑 Password",
            type="password",
            placeholder="Inserisci la password",
            autocomplete="current-password",
            key="login_password_inp",
        )
        submit_login = st.form_submit_button("🔑 Accedi", type="primary", use_container_width=True)
        if submit_login:
            mappa_lower = {nome.lower(): (nome, pwd) for nome, pwd in utenti_auth.items()}
            chiave_utente = utente_inp.strip().lower()
            if chiave_utente in mappa_lower:
                nome_canonico, pwd_attesa = mappa_lower[chiave_utente]
                if hmac.compare_digest(password_inp, pwd_attesa):
                    st.session_state["auth_user"] = nome_canonico
                    st.rerun()
            st.error("❌ Utente o password non validi. Controlla le credenziali e riprova.")

    # Imposta attributi HTML autocomplete e name per abilitare salvataggio e autocompilazione password su browser/smartphone
    components.html(
        """
        <script>
            (function() {
                function impostaAutocompleteLogin() {
                    var doc = window.parent.document;
                    var root = doc.querySelector('[data-testid="stForm"]') || doc;
                    var inpUser = root.querySelector('input[type="text"]');
                    if (inpUser) {
                        inpUser.setAttribute("autocomplete", "username");
                        inpUser.setAttribute("name", "username");
                    }
                    var inpPwd = root.querySelector('input[type="password"]');
                    if (inpPwd) {
                        inpPwd.setAttribute("autocomplete", "current-password");
                        inpPwd.setAttribute("name", "password");
                    }
                }
                impostaAutocompleteLogin();
                setTimeout(impostaAutocompleteLogin, 150);
                setTimeout(impostaAutocompleteLogin, 500);
            })();
        </script>
        """,
        height=0,
    )

    if is_fallback_locale:
        st.markdown(
            '<div class="ev-info-box" style="text-align: center;">'
            'ℹ️ <strong>Modalità test locale</strong> — Credenziali caricate dal fallback locale.'
            '</div>',
            unsafe_allow_html=True,
        )
    st.stop()

current_user = st.session_state["auth_user"]

# Scelta guidata obbligatoria al primo accesso dopo il Login sull'operatore/abbonamento preferito
if not st.session_state.get("operatore_configurato", False):
    st.markdown(
        '<div class="ev-hero">'
        '<div class="ev-hero-title">⚡ EV Roadbook Italia <span class="ev-hero-author-inline">by Fabio Tod</span></div>'
        '<div class="ev-hero-sub">Configurazione rapida iniziale • Pianificazione guidata Mobile-First</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="ev-card" style="border-left-color:#10B981; padding:18px 20px;">'
        f'<div class="ev-card-title" style="font-size:1.12rem; margin-bottom:8px;">'
        f'👋 Benvenuto {html.escape(current_user)}! Prima di iniziare...'
        f'</div>'
        f'<div class="ev-card-sub" style="font-size:0.93rem; color:#CBD5E1; line-height:1.45;">'
        f'Hai un abbonamento o un operatore di ricarica preferito (es. Ionity, Enel X, Be Charge, Tesla) '
        f'oppure vuoi usare tutte le colonnine veloci disponibili?'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    opzioni_onboarding = [
        "🌐 Nessuno (usa tutte le colonnine veloci)",
        "⚡ Atlante",
        "🔌 Be Charge / Plenitude",
        "🔌 Enel X",
        "⚡ Ewiva",
        "⚡ Free To X",
        "⚡ Ionity",
        "🏔️ Neogy",
        "🔴 Tesla Supercharger",
        "✍️ Altro marchio...",
    ]
    idx_onb_default = (
        opzioni_onboarding.index("🔴 Tesla Supercharger")
        if str(current_user or "").strip().lower() in UTENTI_TESLA
        else 0
    )
    scelta_onb = st.selectbox(
        "🏢 Operatore / Abbonamento preferito",
        opzioni_onboarding,
        index=idx_onb_default,
        key="sel_onboarding_op",
    )
    custom_onb = ""
    if "Altro" in scelta_onb:
        custom_onb = st.text_input(
            "Scrivi il nome del tuo operatore / marchio",
            placeholder="Es. Electra, Alperia, A2A, Duferco...",
            key="inp_onboarding_op_custom",
        )

    if st.button("✅ Conferma e Vai all'App", type="primary", use_container_width=True, key="btn_conferma_onboarding"):
        if scelta_onb.startswith("🌐"):
            op_target = OPERATORI_OPZIONI[0]
            op_custom_target = ""
            rete_target = "tutte"
            rabbocco_target = False
        elif "Altro" in scelta_onb:
            op_target = OPERATORI_OPZIONI[-1]
            op_custom_target = custom_onb.strip()
            rete_target = "operatore" if op_custom_target else "tutte"
            rabbocco_target = bool(op_custom_target)
        else:
            op_target = scelta_onb if scelta_onb in OPERATORI_OPZIONI else OPERATORI_OPZIONI[0]
            op_custom_target = ""
            rete_target = "operatore"
            rabbocco_target = True

        st.session_state["default_operatore"] = op_target
        st.session_state["default_operatore_custom"] = op_custom_target
        st.session_state["default_rete"] = rete_target
        st.session_state["sel_operatore"] = op_target
        st.session_state["inp_operatore_custom"] = op_custom_target
        st.session_state["rad_rete"] = rete_target
        st.session_state["chk_rabbocco_ads"] = rabbocco_target
        st.session_state["operatore_configurato"] = True
        st.rerun()

    st.stop()

_db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colonnine_it.json")
_db_mtime = os.path.getmtime(_db_file) if os.path.exists(_db_file) else 0.0
db = load_chargers_db(_db_mtime, filtro_ver=3)

# Inizializzazione stato sessione (benvenuto pulito all'apertura / dopo F5)
if st.session_state.pop("reset_nuovo_viaggio", False):
    st.session_state["mostra_cfg"] = True
    st.session_state["cfg_expanded"] = True
    st.session_state["inp_orig"] = ""
    st.session_state["inp_dest"] = ""
    st.session_state["sel_operatore"] = st.session_state.get("default_operatore", OPERATORI_OPZIONI[0])
    st.session_state["inp_operatore_custom"] = st.session_state.get("default_operatore_custom", "")
    st.session_state["rad_rete"] = st.session_state.get("default_rete", "tutte")
    st.session_state["inp_ora_partenza"] = ora_corrente_italia_arrotondata()
    st.session_state.pop("roadbook_res", None)
    for k in list(st.session_state.keys()):
        if str(k).startswith("sel_alt_"):
            st.session_state.pop(k, None)

if "mostra_cfg" not in st.session_state:
    st.session_state["mostra_cfg"] = False
if "cfg_expanded" not in st.session_state:
    st.session_state["cfg_expanded"] = False
if "inp_orig" not in st.session_state:
    st.session_state["inp_orig"] = ""
if "inp_dest" not in st.session_state:
    st.session_state["inp_dest"] = ""
if "inp_ora_partenza" not in st.session_state:
    st.session_state["inp_ora_partenza"] = ora_corrente_italia_arrotondata()
if "inp_zona_localita" not in st.session_state:
    st.session_state["inp_zona_localita"] = ""
if "sync_zona_dest" in st.session_state:
    st.session_state["inp_zona_localita"] = st.session_state.pop("sync_zona_dest")

st.markdown(
    f'<div class="ev-hero">'
    f'<div class="ev-hero-title">⚡ EV Roadbook Italia <span class="ev-hero-author-inline">by Fabio Tod</span></div>'
    f'<div class="ev-hero-sub">Ciao {html.escape(current_user)}, buon viaggio! 😎🚗⚡ • Pianificazione guidata Mobile-First</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# Due modalità principali in alto
tab_viaggio, tab_zona = st.tabs([
    "🗺️ Pianifica Viaggio",
    "📍 Cerca Colonnine",
])

# ==============================================================================
# TAB 1: PIANIFICA VIAGGIO
# ==============================================================================
with tab_viaggio:
    # Pulsante in alto: all'avvio è l'unica azione visibile; al click azzera e apre la configurazione
    if st.button("🚀 Nuovo Viaggio", type="primary", use_container_width=True, key="btn_nuovo_viaggio"):
        st.session_state["reset_nuovo_viaggio"] = True
        st.rerun()

    avvia_calcolo = False
    if st.session_state["mostra_cfg"] or "roadbook_res" in st.session_state:
        cfg_aperto = st.session_state["cfg_expanded"]
        if cfg_aperto:
            etichetta_expander = "⚙️ Configura Viaggio (Auto, Itinerario, Soste)"
        else:
            etichetta_expander = "⚙️ Configura Viaggio (Auto, Itinerario, Soste) — Tocca per modificare"

        with st.expander(etichetta_expander, expanded=cfg_aperto):
            # --- STEP 1: Auto, Tariffe, Stagione e Guida ---
            render_step_header(1, "🚗 Auto, Tariffe, Stagione e Guida")

            opzioni_auto_utente = opzioni_auto_per_utente(current_user)
            if st.session_state.get("sel_auto") not in opzioni_auto_utente:
                st.session_state.pop("sel_auto", None)

            scelta_auto = st.selectbox("Che auto guidi?", opzioni_auto_utente, index=0, key="sel_auto")
            # Preseleziona automaticamente Tesla Supercharger e rabbocco veloce AdS quando si sceglie un'auto Tesla
            if st.session_state.get("last_auto_sel") != scelta_auto:
                st.session_state["last_auto_sel"] = scelta_auto
                e_auto_tesla_sel = "tesla" in str(scelta_auto or "").lower()
                if e_auto_tesla_sel:
                    st.session_state["chk_rabbocco_ads"] = True
                    st.session_state["rad_rete"] = "operatore"
                    if st.session_state.get("sel_operatore", OPERATORI_OPZIONI[0]) == OPERATORI_OPZIONI[0]:
                        st.session_state["sel_operatore"] = "🔴 Tesla Supercharger"
                elif "chk_rabbocco_ads" not in st.session_state:
                    st.session_state["chk_rabbocco_ads"] = st.session_state.get("default_rete") == "operatore"

            custom_bat, custom_cons, custom_dc = 60.0, 18.0, 120
            if "Altra Auto" in scelta_auto:
                c_a1, c_a2, c_a3 = st.columns(3)
                custom_bat = c_a1.number_input("Batteria (kWh)", 15.0, 150.0, 60.0, 1.0, key="num_bat")
                custom_cons = c_a2.number_input("Consumo base (kWh/100km)", 10.0, 35.0, 18.0, 0.5, key="num_cons")
                custom_dc = c_a3.number_input("Max DC (kW)", 22, 350, 120, 10, key="num_dc")

            auto_cfg = costruisci_profilo_auto(scelta_auto, custom_bat, custom_cons, custom_dc)

            # Tariffe energia modificabili (casa e colonnine veloci) per il riepilogo economico completo
            # Tariffa casa predefinita calibrata sulla bolletta reale Alperia (0.21 €/kWh) per Fabio, Elizabeth, Alessia
            costo_casa_default = (
                0.21
                if str(current_user or "").strip().lower() in ("fabio", "elizabeth", "alessia")
                else 0.25
            )
            c_t1, c_t2 = st.columns(2)
            costo_casa_kwh = c_t1.number_input(
                "🏠 Costo energia casa (€/kWh)",
                min_value=0.05,
                max_value=1.50,
                value=costo_casa_default,
                step=0.01,
                format="%.2f",
                key="num_costo_casa",
            )
            costo_col_kwh = c_t2.number_input(
                "⚡ Costo colonnine veloci (€/kWh)",
                min_value=0.10,
                max_value=2.00,
                value=0.69,
                step=0.01,
                format="%.2f",
                key="num_costo_col",
            )

            stagioni_chiavi = list(STAGIONI.keys())
            idx_stagione_default = stagioni_chiavi.index(stagione_predefinita())
            c_s1, c_s2 = st.columns(2)
            stagione_scelta = c_s1.selectbox(
                "Stagione (auto-rilevata dal mese)",
                stagioni_chiavi,
                index=idx_stagione_default,
                key="sel_stagione",
            )
            guida_scelta = c_s2.selectbox(
                "Stile di guida (piede)",
                list(STILI_GUIDA.keys()),
                index=1,
                key="sel_guida",
            )

            # Consumo differenziato: Autostrada (-12%/0%/+20%) vs Statale (-16% base + -8%/0%/+12%)
            cons_eff = round(
                auto_cfg["cons_base"] * STAGIONI[stagione_scelta] * STILI_GUIDA[guida_scelta]["autostrada"],
                1,
            )
            cons_extra = round(
                auto_cfg["cons_base"]
                * STAGIONI[stagione_scelta]
                * FATTORE_CONSUMO_EXTRAURBANO
                * STILI_GUIDA[guida_scelta]["statale"],
                1,
            )
            km_per_pct = auto_cfg["bat_kwh"] / cons_eff
            km_per_pct_extra = auto_cfg["bat_kwh"] / cons_extra
            autonomia_100 = round(km_per_pct * 100.0)
            autonomia_100_extra = round(km_per_pct_extra * 100.0)

            st.markdown(
                f'<div class="ev-card" style="background:#131722; border-left-color:#3B82F6; padding:10px 14px;">'
                f'<div class="ev-kv"><span class="ev-k">Consumo stimato Autostrada</span><span class="ev-v">{cons_eff:.1f} kWh/100km (~{autonomia_100} km)</span></div>'
                f'<div class="ev-kv"><span class="ev-k">Consumo stimato Statali / Extraurbano</span><span class="ev-v">{cons_extra:.1f} kWh/100km (~{autonomia_100_extra} km)</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # --- STEP 2: Itinerario e Batteria ---
            render_step_header(2, "🗺️ Itinerario e Batteria")

            c_r1, c_r2 = st.columns(2)
            orig = c_r1.text_input("Partenza (A)", placeholder="Es. Creazzo, Vicenza", key="inp_orig")
            dest = c_r2.text_input("Arrivo (B)", placeholder="Es. Bologna", key="inp_dest")

            modalita_viaggio = st.radio(
                "Tipo di viaggio",
                ["➡️ Solo Andata", "🔄 Andata + Ritorno"],
                horizontal=True,
                key="rad_mod",
            )
            andata_ritorno = "Ritorno" in modalita_viaggio

            # Ora di partenza modificabile (preimpostata sull'ora italiana arrotondata ai 5 min) + permanenza se A/R
            ore_permanenza = 2.0
            if andata_ritorno:
                c_o1, c_o2 = st.columns(2)
                ora_partenza_val = c_o1.time_input(
                    "🕒 Ora di partenza",
                    step=300,
                    key="inp_ora_partenza",
                )
                ore_permanenza = c_o2.number_input(
                    "⏱️ Durata permanenza a destinazione (ore)",
                    min_value=0.0,
                    max_value=72.0,
                    value=2.0,
                    step=0.5,
                    format="%.1f",
                    key="num_permanenza",
                )
            else:
                ora_partenza_val = st.time_input(
                    "🕒 Ora di partenza",
                    step=300,
                    key="inp_ora_partenza",
                )
            ora_partenza_str = ora_partenza_val.strftime("%H:%M")

            strada_label = st.radio(
                "Preferenza strade",
                list(PREFERENZE_STRADE.keys()),
                index=0,
                key="rad_strada",
            )
            strada_codice = PREFERENZE_STRADE[strada_label]
            if strada_codice == "no_auto":
                st.markdown(
                    f'<div class="ev-info-box" style="margin-top:2px; margin-bottom:10px;">'
                    f'🚗 Consumo extraurbano applicato (-16% base + piede adattivo): '
                    f'<strong>{cons_extra:.1f} kWh/100km</strong> (autonomia ~{autonomia_100_extra} km)'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif strada_codice == "confronta":
                ambito_prev = "(valori totali su Andata + Ritorno)" if andata_ritorno else "(valori su Solo Andata)"
                st.markdown(
                    f'<div class="ev-info-box" style="margin-top:2px; margin-bottom:10px;">'
                    f'⚖️ Confronto diretto: <strong>Autostrada</strong> ({cons_eff:.1f} kWh/100km) '
                    f'vs <strong>Statali / Extraurbano</strong> ({cons_extra:.1f} kWh/100km, -16%) — {ambito_prev}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Controlli batteria anti-sfioramento per smartphone (pulsanti - / + con step 5%)
            c_b1, c_b2 = st.columns(2)
            p_start = int(
                c_b1.number_input(
                    "🔋 % Batteria alla Partenza",
                    min_value=5,
                    max_value=100,
                    value=90,
                    step=5,
                    key="num_start",
                )
            )
            p_colonnina = int(
                c_b2.number_input(
                    "🔌 % Batteria desiderata all'arrivo in colonnina",
                    min_value=5,
                    max_value=100,
                    value=15,
                    step=5,
                    key="num_arr_col",
                    help="Sfrutta l'autonomia fino a questa soglia garantendo sempre un Jolly (Piano B) entro il 4% d'emergenza.",
                )
            )

            p_home_a = 10
            if andata_ritorno:
                c_b3, c_b4 = st.columns(2)
                p_arr_b = int(
                    c_b3.number_input(
                        "🏁 % Minima all'Arrivo (B)",
                        min_value=5,
                        max_value=100,
                        value=20,
                        step=5,
                        key="num_arr_b",
                    )
                )
                p_home_a = int(
                    c_b4.number_input(
                        "🏠 % Minima al Rientro a Casa (A)",
                        min_value=5,
                        max_value=100,
                        value=10,
                        step=5,
                        key="num_home_a",
                        help="Utile per rientrare scarichi e ricaricare a casa a basso costo.",
                    )
                )
            else:
                p_arr_b = int(
                    st.number_input(
                        "🏁 % Minima all'Arrivo (B)",
                        min_value=5,
                        max_value=100,
                        value=20,
                        step=5,
                        key="num_arr_b",
                    )
                )

            # --- STEP 3: Rete, Operatore e Stile di Sosta ---
            render_step_header(3, "☕ Rete, Operatore e Stile di Sosta")

            scelta_operatore = st.selectbox(
                "🏢 Operatore / Marchio preferito",
                OPERATORI_OPZIONI,
                index=0,
                key="sel_operatore",
            )
            operatore_custom = ""
            if "Altro" in scelta_operatore:
                operatore_custom = st.text_input(
                    "Scrivi il nome dell'operatore / marchio",
                    placeholder="Es. Electra, Alperia, A2A, Duferco...",
                    key="inp_operatore_custom",
                )
            nome_operatore, operatore_keywords = estrai_info_operatore(scelta_operatore, operatore_custom)

            # Visibilità 3ª opzione in base all'operatore scelto, all'auto Tesla e al profilo utente:
            # - Operatore specifico -> 3 opzioni con "🎯 Solo {nome_operatore} (anche fuori casello)"
            #   (oppure "🔴 Solo Tesla Supercharger" se ha scelto Tesla)
            # - "Tutti gli operatori" -> "🔴 Solo Tesla Supercharger" per utenti Tesla (Fabio, Elizabeth, Alessia,
            #   Federico) o se è selezionata una qualsiasi auto Tesla; per gli altri utenti solo le prime 2 opzioni
            utente_famiglia_tesla = (
                str(current_user or "").strip().lower() in UTENTI_TESLA
                or ("tesla" in str(scelta_auto or "").lower())
            )
            mappa_etichette_rete = {
                "tutte": "⚡ Tutte le colonnine veloci (Precedenza Autostrada)",
                "ads": "🛣️ Solo in Autostrada (Aree di Servizio / Autogrill)",
            }
            if nome_operatore:
                if "tesla" in nome_operatore.lower():
                    mappa_etichette_rete["operatore"] = "🔴 Solo Tesla Supercharger"
                else:
                    mappa_etichette_rete["operatore"] = f"🎯 Solo {nome_operatore} (anche fuori casello)"
                opzioni_rete = ["tutte", "ads", "operatore"]
            elif utente_famiglia_tesla:
                mappa_etichette_rete["operatore"] = "🔴 Solo Tesla Supercharger"
                opzioni_rete = ["tutte", "ads", "operatore"]
            else:
                opzioni_rete = ["tutte", "ads"]

            if st.session_state.get("rad_rete") not in opzioni_rete:
                st.session_state.pop("rad_rete", None)

            rete_scelta_cod = st.radio(
                "Circuito / Rete di ricarica preferita",
                options=opzioni_rete,
                format_func=lambda c, m=mappa_etichette_rete: m[c],
                index=0,
                key="rad_rete",
            )
            if rete_scelta_cod == "operatore":
                rete_codice = "operatore" if nome_operatore else "tesla"
            else:
                rete_codice = rete_scelta_cod

            # Checkbox rabbocco veloce <= 8 min in Autostrada valida per Tesla e qualsiasi operatore scelto
            consenti_rabbocco_ads = False
            if rete_scelta_cod == "operatore" or (rete_scelta_cod != "ads" and bool(operatore_keywords)):
                consenti_rabbocco_ads = st.checkbox(
                    "⚡ Consenti rabbocco veloce (<= 8 min) in Autostrada senza uscire dal casello",
                    key="chk_rabbocco_ads",
                )

            stile_label = st.radio(
                "Come preferisci gestire le fermate durante il viaggio?",
                list(STILI_SOSTA.keys()),
                index=0,
                key="rad_sosta",
            )
            stile_codice = STILI_SOSTA[stile_label]
            # La distinzione Autogrill (caffe) vs Fuori Casello (pranzo) è già esplicita nel selettore
            pranzo_dove = "pranzo_anche_fuori"

            avvia_calcolo = st.button(
                "🚀 Calcola Roadbook",
                type="primary",
                use_container_width=True,
                key="btn_calcola",
            )

    if avvia_calcolo:
        if not db:
            st.error("File colonnine_it.json non trovato nella cartella dell'app. Esegui prima aggiorna_db.py.")
        elif not orig.strip() or not dest.strip():
            st.warning("Inserisci sia la località di Partenza (A) sia quella di Arrivo (B) prima di calcolare il viaggio.")
        else:
            with st.spinner("Calcolo percorso stradale e simulazione soste in corso..."):
                try:
                    g1, g2 = geocode(orig), geocode(dest)
                    if not g1 or not g2:
                        st.error("Località non trovata. Prova a specificare meglio città o provincia.")
                    else:
                        p1 = (g1[0], g1[1])
                        p2 = (g2[0], g2[1])
                        raggio_corrente = float(st.session_state.get("sld_zona_raggio", 10))
                        dest_ch = cerca_colonnine_zona(db, g2[0], g2[1], raggio_km=raggio_corrente)
                        colonnina_vicina_dest = dest_ch[0] if dest_ch else trova_colonnina_piu_vicina(db, g2[0], g2[1])

                        rete_no_auto = rete_codice if rete_codice in ("tesla", "operatore") else "tutte"

                        if strada_codice == "confronta":
                            # Calcola entrambi i percorsi: standard (Autostrada) e alternativa Statali/Extraurbano
                            res_auto = costruisci_percorso_completo(
                                db, p1, p2, andata_ritorno, evita_autostrade=False, orig_nome=orig, dest_nome=dest
                            )
                            res_no = costruisci_percorso_completo(
                                db, p1, p2, andata_ritorno, evita_autostrade=True, orig_nome=orig, dest_nome=dest
                            )
                            if not res_auto or not res_no:
                                st.error("Impossibile calcolare uno dei due percorsi tramite OSRM.")
                                st.stop()

                            all_ch_a, km1_a, tot_km_a, tot_dur_a, dur1_a, dur2_a, nodi_andata_a, nodi_ritorno_a = res_auto
                            all_ch_b, km1_b, tot_km_b, tot_dur_b, dur1_b, dur2_b, _nodi_ab, _nodi_rb = res_no

                            sol_a, _, _ = genera_soluzioni_confronto(
                                all_ch=all_ch_a,
                                auto_cfg=auto_cfg,
                                km1=km1_a,
                                tot_km=tot_km_a,
                                durata_guida_min=tot_dur_a,
                                andata_ritorno=andata_ritorno,
                                km_per_pct=km_per_pct,
                                p_start=p_start,
                                p_arr_b=p_arr_b,
                                p_home_a=p_home_a,
                                stile_codice=stile_codice,
                                rete_codice=rete_codice,
                                pranzo_dove=pranzo_dove,
                                consenti_rabbocco_ads=consenti_rabbocco_ads,
                                p_colonnina=p_colonnina,
                                operatore_keywords=operatore_keywords,
                                nome_operatore=nome_operatore,
                            )
                            sol_b, _, _ = genera_soluzioni_confronto(
                                all_ch=all_ch_b,
                                auto_cfg=auto_cfg,
                                km1=km1_b,
                                tot_km=tot_km_b,
                                durata_guida_min=tot_dur_b,
                                andata_ritorno=andata_ritorno,
                                km_per_pct=km_per_pct_extra,
                                p_start=p_start,
                                p_arr_b=p_arr_b,
                                p_home_a=p_home_a,
                                stile_codice=stile_codice,
                                rete_codice=rete_no_auto,
                                pranzo_dove="pranzo_anche_fuori",
                                consenti_rabbocco_ads=False,
                                p_colonnina=p_colonnina,
                                operatore_keywords=operatore_keywords,
                                nome_operatore=nome_operatore,
                            )

                            kwh_a = round((tot_km_a / 100.0) * cons_eff, 1)
                            kwh_b = round((tot_km_b / 100.0) * cons_extra, 1)
                            sol_a.update({
                                "km1": km1_a, "tot_km": tot_km_a, "tot_dur": tot_dur_a,
                                "dur1": dur1_a, "dur2": dur2_a,
                                "cons_eff": cons_eff, "kwh_totali": kwh_a,
                                "km_per_pct": km_per_pct, "all_ch": all_ch_a,
                                "tipo_strada": "🛣️ Con Autostrada",
                            })
                            sol_b.update({
                                "km1": km1_b, "tot_km": tot_km_b, "tot_dur": tot_dur_b,
                                "dur1": dur1_b, "dur2": dur2_b,
                                "cons_eff": cons_extra, "kwh_totali": kwh_b,
                                "km_per_pct": km_per_pct_extra, "all_ch": all_ch_b,
                                "tipo_strada": "🚗 Senza Autostrada (Statali)",
                            })

                            km1, tot_km, tot_dur, dur1, dur2 = km1_a, tot_km_a, tot_dur_a, dur1_a, dur2_a
                            cons_eff_viaggio, km_per_pct_viaggio, kwh_totali = cons_eff, km_per_pct, kwh_a
                            all_ch = all_ch_a
                            nodi_andata, nodi_ritorno = nodi_andata_a, nodi_ritorno_a
                            desc_a = "🛣️ Con Autostrada"
                            desc_b = "🚗 Senza Autostrada — Statali / Extraurbano"

                        else:
                            evita_auto = strada_codice == "no_auto"
                            res_rt = costruisci_percorso_completo(
                                db, p1, p2, andata_ritorno, evita_autostrade=evita_auto, orig_nome=orig, dest_nome=dest
                            )
                            if not res_rt:
                                st.error("Impossibile calcolare il percorso stradale tramite OSRM.")
                                st.stop()

                            all_ch, km1, tot_km, tot_dur, dur1, dur2, nodi_andata, nodi_ritorno = res_rt
                            cons_eff_viaggio = cons_extra if evita_auto else cons_eff
                            km_per_pct_viaggio = km_per_pct_extra if evita_auto else km_per_pct
                            kwh_totali = round((tot_km / 100.0) * cons_eff_viaggio, 1)
                            rete_eff = rete_no_auto if evita_auto else rete_codice
                            pranzo_eff = "pranzo_anche_fuori" if evita_auto else pranzo_dove
                            rabbocco_eff = False if evita_auto else consenti_rabbocco_ads

                            sol_a, sol_b, desc_b = genera_soluzioni_confronto(
                                all_ch=all_ch,
                                auto_cfg=auto_cfg,
                                km1=km1,
                                tot_km=tot_km,
                                durata_guida_min=tot_dur,
                                andata_ritorno=andata_ritorno,
                                km_per_pct=km_per_pct_viaggio,
                                p_start=p_start,
                                p_arr_b=p_arr_b,
                                p_home_a=p_home_a,
                                stile_codice=stile_codice,
                                rete_codice=rete_eff,
                                pranzo_dove=pranzo_eff,
                                consenti_rabbocco_ads=rabbocco_eff,
                                p_colonnina=p_colonnina,
                                operatore_keywords=operatore_keywords,
                                nome_operatore=nome_operatore,
                            )
                            tipo_txt = "🚗 Senza Autostrada (Statali)" if evita_auto else "🛣️ Con Autostrada"
                            sol_a["tipo_strada"] = tipo_txt
                            sol_a["dur1"] = dur1
                            sol_a["dur2"] = dur2
                            if sol_b is not None:
                                sol_b["tipo_strada"] = tipo_txt
                                sol_b["dur1"] = dur1
                                sol_b["dur2"] = dur2
                            desc_a = "Consigliata — Meno tempo/soste"

                        calc_id = st.session_state.get("calc_counter", 0) + 1
                        st.session_state["calc_counter"] = calc_id

                        st.session_state["roadbook_res"] = {
                            "calc_id": calc_id,
                            "orig": orig,
                            "dest": dest,
                            "dest_lat": g2[0],
                            "dest_lon": g2[1],
                            "colonnina_vicina_dest": colonnina_vicina_dest,
                            "andata_ritorno": andata_ritorno,
                            "strada_codice": strada_codice,
                            "km1": km1,
                            "tot_km": tot_km,
                            "tot_dur": tot_dur,
                            "dur1": dur1,
                            "dur2": dur2,
                            "ora_partenza": ora_partenza_str,
                            "ore_permanenza": float(ore_permanenza),
                            "costo_casa_kwh": float(costo_casa_kwh),
                            "costo_col_kwh": float(costo_col_kwh),
                            "cons_eff": cons_eff_viaggio,
                            "kwh_totali": kwh_totali,
                            "km_per_pct": km_per_pct_viaggio,
                            "p_start": p_start,
                            "p_colonnina": p_colonnina,
                            "p_arr_b": p_arr_b,
                            "p_home_a": p_home_a,
                            "stile_codice": stile_codice,
                            "rete_codice": rete_codice,
                            "auto_cfg": auto_cfg,
                            "auto_nome": auto_cfg["nome"],
                            "sol_a": sol_a,
                            "sol_b": sol_b,
                            "desc_a": desc_a,
                            "desc_b": desc_b,
                            "all_ch": all_ch,
                            "nodi_andata": nodi_andata,
                            "nodi_ritorno": nodi_ritorno,
                        }

                        # Precarica i risultati di arrivo (B) anche nel Tab 2 per consultazione immediata
                        st.session_state["sync_zona_dest"] = dest
                        st.session_state["zona_res"] = {
                            "localita": dest,
                            "indirizzo": g2[2],
                            "lat": g2[0],
                            "lon": g2[1],
                            "raggio_km": raggio_corrente,
                            "stazioni": dest_ch,
                        }

                        # Chiude automaticamente il pannello parametri e attiva lo scroll one-shot sui risultati
                        st.session_state["cfg_expanded"] = False
                        st.session_state["trigger_scroll"] = True
                        st.rerun()
                except Exception as ex:
                    st.error(f"Errore durante il calcolo del viaggio: {ex}")

    # ==========================================================================
    # VISUALIZZAZIONE RISULTATI DEL VIAGGIO
    # ==========================================================================
    if "roadbook_res" in st.session_state:
        res = st.session_state["roadbook_res"]
        sol_a_dyn, ctrl_a = risolvi_soluzione_interattiva(res["sol_a"], res, "A")
        sol_b_dyn, ctrl_b = risolvi_soluzione_interattiva(res["sol_b"], res, "B")

        st.markdown('<div id="ancora-risultati"></div>', unsafe_allow_html=True)
        st.markdown("### 🏁 Risultati del Viaggio")
        if st.session_state.get("trigger_scroll"):
            st.session_state["trigger_scroll"] = False
            _scroll_calc_id = res.get("calc_id", 0)
            components.html(
                f"""
                <script>
                    (function() {{
                        var key = "ev_scrolled_{_scroll_calc_id}";
                        try {{
                            if (window.parent.sessionStorage.getItem(key)) return;
                            window.parent.sessionStorage.setItem(key, "1");
                        }} catch (e) {{}}
                        setTimeout(function() {{
                            var el = window.parent.document.getElementById("ancora-risultati");
                            if (el) {{
                                el.scrollIntoView({{behavior: "smooth", block: "start"}});
                            }}
                        }}, 150);
                    }})();
                </script>
                """,
                height=0,
            )

        # Verdetto immediato ben evidente in cima ai risultati prima delle card Soluzione A / B
        render_banner_verdetto(sol_a_dyn, res)
        render_consiglio_batteria_bassa(sol_a_dyn, res)

        # KPI sintetici in griglia mobile-first con consumo totale (kWh) + consumo medio (kWh/100km)
        st.markdown(
            f'<div class="ev-kpi-grid">'
            f'<div class="ev-kpi">'
            f'<div class="ev-kpi-label">Distanza totale</div>'
            f'<div class="ev-kpi-val">{res["tot_km"]:.0f} km</div>'
            f'<div class="ev-kpi-sub">{"Andata + Ritorno" if res["andata_ritorno"] else "Solo Andata"}</div>'
            f'</div>'
            f'<div class="ev-kpi">'
            f'<div class="ev-kpi-label">Solo guida</div>'
            f'<div class="ev-kpi-val">{formatta_durata(res["tot_dur"])}</div>'
            f'<div class="ev-kpi-sub">{html.escape(res["auto_nome"].split(" ")[0])}</div>'
            f'</div>'
            f'<div class="ev-kpi">'
            f'<div class="ev-kpi-label">Consumo stimato</div>'
            f'<div class="ev-kpi-val">{res["kwh_totali"]:.1f} kWh</div>'
            f'<div class="ev-kpi-sub">{res["cons_eff"]:.1f} kWh/100km</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Riquadro dedicato di confronto diretto Autostrada vs Statali / Extraurbano sull'intero viaggio
        if res.get("strada_codice") == "confronta" and sol_a_dyn and sol_b_dyn:
            render_card_confronto_strade(sol_a_dyn, sol_b_dyn, res)

        # Roadbook Visivo in SVG vettoriale subito sopra le card delle soste
        render_roadbook_visivo(sol_a_dyn, res)

        # Scheda espandibile SOLUZIONE A (aperta di default)
        tempo_a_txt = formatta_durata(sol_a_dyn["tempo_totale_min"]) if sol_a_dyn and sol_a_dyn.get("valido") else "N.D."
        desc_a_txt = res.get("desc_a", "Consigliata — Meno tempo/soste")
        titolo_a = f"🟢 SOLUZIONE A ({desc_a_txt}) — Totale: {tempo_a_txt}"
        with st.expander(titolo_a, expanded=True):
            render_riepilogo_soluzione(sol_a_dyn, ctrl_a, res, colore_bordo="#10B981")

        # Scheda espandibile SOLUZIONE B (aperta di default anche in modalità confronto o se personalizzata)
        if sol_b_dyn is not None:
            tempo_b_txt = formatta_durata(sol_b_dyn["tempo_totale_min"]) if sol_b_dyn.get("valido") else "N.D."
            titolo_b = f"🔵 SOLUZIONE B ({res['desc_b']}) — Totale: {tempo_b_txt}"
            b_modificata = any(
                st.session_state.get(item[0]) not in (None, item[1][0])
                for item in ctrl_b
                if item is not None
            )
            espandi_b = b_modificata or (res.get("strada_codice") == "confronta")
            with st.expander(titolo_b, expanded=espandi_b):
                render_riepilogo_soluzione(sol_b_dyn, ctrl_b, res, colore_bordo="#3B82F6")
        else:
            with st.expander("🔵 SOLUZIONE B (Alternativa — Solo Autostrada / Altra opzione) — Non necessaria", expanded=False):
                st.markdown(
                    '<div class="ev-card" style="border-left-color:#3B82F6;">'
                    '<div class="ev-card-title">ℹ️ Nessuna alternativa diversa necessaria</div>'
                    '<div class="ev-card-sub" style="margin-top:6px;">La Soluzione A copre già il viaggio senza bisogno di soste o senza alternative migliori disponibili.</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

        # Modalità esperto (spenta di default, in fondo al Tab 1)
        st.markdown("---")
        mostra_tutte = st.checkbox(
            "🔍 Mostra tutte le colonnine lungo il percorso (modalità esperto)",
            value=False,
            key="chk_esperto",
        )
        if mostra_tutte:
            if res["all_ch"]:
                km1_esp = float(res.get("km1", 0.0)) if res.get("andata_ritorno") else 0.0
                dest_esp = res.get("dest", "")
                st.caption(f"Totale colonnine Fast/HPC rilevate lungo il percorso: {len(res['all_ch'])}")

                def _chiave_ord_esperto(ch):
                    e_rit = 1 if "Ritorno" in str(ch.get("Tratta", "")) else 0
                    return (e_rit, float(ch.get("Km", 0.0)))

                hyper_list = sorted(
                    [ch for ch in res["all_ch"] if classifica_fascia_potenza(ch) == "hyper"],
                    key=_chiave_ord_esperto,
                )
                hpc_list = sorted(
                    [ch for ch in res["all_ch"] if classifica_fascia_potenza(ch) == "hpc"],
                    key=_chiave_ord_esperto,
                )
                fast_list = sorted(
                    [ch for ch in res["all_ch"] if classifica_fascia_potenza(ch) not in ("hyper", "hpc")],
                    key=_chiave_ord_esperto,
                )

                if hyper_list:
                    with st.expander(f"⚡ Hyper-Fast (>= 400 kW) — {len(hyper_list)} stazioni", expanded=True):
                        for ch in hyper_list:
                            render_card_colonnina_generica(
                                ch,
                                mostra_km=True,
                                km1_offset=km1_esp,
                                dest_nome=dest_esp,
                            )

                if hpc_list:
                    with st.expander(f"🚀 Ultra-Fast / HPC (150 – 399 kW) — {len(hpc_list)} stazioni", expanded=True):
                        for ch in hpc_list:
                            render_card_colonnina_generica(
                                ch,
                                mostra_km=True,
                                km1_offset=km1_esp,
                                dest_nome=dest_esp,
                            )

                if fast_list:
                    with st.expander(f"🔌 Fast DC Standard (50 – 149 kW) — {len(fast_list)} stazioni", expanded=False):
                        for ch in fast_list:
                            render_card_colonnina_generica(
                                ch,
                                mostra_km=True,
                                km1_offset=km1_esp,
                                dest_nome=dest_esp,
                            )
            else:
                st.info("Nessuna colonnina Fast/HPC rilevata lungo il percorso.")

# ==============================================================================
# TAB 2: CERCA COLONNINE
# ==============================================================================
with tab_zona:
    st.markdown(
        '<div class="ev-card" style="border-left-color:#3B82F6;">'
        '<div class="ev-card-title">📍 Cerca Colonnine</div>'
        '<div class="ev-card-sub" style="margin-top:4px;">Inserisci qualsiasi città, indirizzo o località in Italia, imposta i filtri e trova subito le colonnine vicine.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    localita_zona = st.text_input(
        "Città o indirizzo da cercare",
        placeholder="Es. Senigallia, Bologna, Roma...",
        key="inp_zona_localita",
    )

    raggio_zona = st.slider(
        "Raggio di ricerca (km)",
        min_value=2,
        max_value=30,
        value=10,
        step=1,
        key="sld_zona_raggio",
    )

    # Tutti i filtri posizionati prima del pulsante di ricerca (e interattivi anche sui risultati)
    op_zona_scelto = st.selectbox(
        "🏢 Operatore / Marchio",
        OPERATORI_ZONA_OPZIONI,
        index=0,
        key="sel_zona_operatore",
    )
    op_zona_custom = ""
    if "Altro" in op_zona_scelto:
        op_zona_custom = st.text_input(
            "Scrivi il nome dell'operatore / marchio",
            placeholder="Es. Electra, Alperia, A2A, Duferco...",
            key="inp_zona_op_custom",
        )
    _, op_zona_kw = estrai_info_operatore(op_zona_scelto, op_zona_custom)

    f1, f2, f3 = st.columns(3)
    solo_fast_z = f1.checkbox("⚡ Solo Fast/HPC", value=False, key="flt_zona_fast")
    con_food_z = f2.checkbox("🍽️ Vicino Ristorante", value=False, key="flt_zona_food")
    con_mall_z = f3.checkbox("🛒 Vicino Centro Comm.", value=False, key="flt_zona_mall")

    if st.button("🔍 Cerca Colonnine", type="primary", use_container_width=True, key="btn_cerca_zona"):
        if not db:
            st.error("File colonnine_it.json non trovato nella cartella dell'app.")
        elif not localita_zona.strip():
            st.warning("Inserisci una città o un indirizzo valido.")
        else:
            with st.spinner(f"Ricerca colonnine entro {raggio_zona} km da {localita_zona.strip()}..."):
                geo_z = geocode_zona(localita_zona.strip())
                if not geo_z:
                    st.error("Località non trovata. Prova a specificare meglio città o provincia.")
                else:
                    lat_z, lon_z, addr_z = geo_z
                    stazioni_z = cerca_colonnine_zona(db, lat_z, lon_z, raggio_km=float(raggio_zona))
                    st.session_state["zona_res"] = {
                        "localita": localita_zona.strip(),
                        "indirizzo": addr_z,
                        "lat": lat_z,
                        "lon": lon_z,
                        "raggio_km": float(raggio_zona),
                        "stazioni": stazioni_z,
                    }

    if "zona_res" in st.session_state:
        zres = st.session_state["zona_res"]

        # Se l'utente sposta lo slider del raggio su una località già cercata, aggiorna subito la lista
        if "lat" in zres and "lon" in zres and zres.get("raggio_km") != float(raggio_zona):
            zres["raggio_km"] = float(raggio_zona)
            zres["stazioni"] = cerca_colonnine_zona(db, zres["lat"], zres["lon"], raggio_km=float(raggio_zona))
            st.session_state["zona_res"] = zres

        # Conferma immediata del punto e delle coordinate rilevate + pulsante diretto Google Maps con indirizzo specifico
        if "lat" in zres and "lon" in zres:
            addr_disp = html.escape(zres.get("indirizzo") or zres["localita"])
            url_gmaps_zona = html.escape(url_gmaps_colonnine_indirizzo(zres["localita"]), quote=True)
            st.markdown(
                f'<div class="ev-info-box" style="margin-top:4px; margin-bottom:8px;">'
                f'📍 <strong>Centro rilevato:</strong> {addr_disp} ({zres["lat"]:.4f}, {zres["lon"]:.4f})'
                f'</div>'
                f'<a class="ev-gmaps-cta" href="{url_gmaps_zona}" target="_blank" rel="noopener noreferrer">'
                f'🔍 Vedi tutte le colonnine aggiornate su Google Maps'
                f'</a>',
                unsafe_allow_html=True,
            )

        raggio_disp = int(zres.get("raggio_km", raggio_zona))
        st.markdown(f"### 🏙️ Colonnine vicino a {zres['localita']} (entro {raggio_disp} km)")

        filtrate_z = [
            d
            for d in zres["stazioni"]
            if (not solo_fast_z or classifica_fascia_potenza(d) in ("hyper", "hpc", "fast"))
            and (not con_food_z or d["has_food"] == 1)
            and (not con_mall_z or d["has_mall"] == 1)
            and match_operatore_kw(d, op_zona_kw)
        ]

        if filtrate_z:
            hyper_z = sorted(
                [d for d in filtrate_z if classifica_fascia_potenza(d) == "hyper"],
                key=lambda x: x["Distanza"],
            )
            hpc_z = sorted(
                [d for d in filtrate_z if classifica_fascia_potenza(d) == "hpc"],
                key=lambda x: x["Distanza"],
            )
            fast_z = sorted(
                [d for d in filtrate_z if classifica_fascia_potenza(d) == "fast"],
                key=lambda x: x["Distanza"],
            )
            lente_z = (
                sorted(
                    [d for d in filtrate_z if classifica_fascia_potenza(d) == "lenta"],
                    key=lambda x: x["Distanza"],
                )
                if not solo_fast_z
                else []
            )

            st.caption(
                f"Trovate {len(filtrate_z)} stazioni entro {raggio_disp} km — "
                f"raggruppate per velocità decrescente (fino a 15 stazioni per fascia)."
            )

            def _render_gruppo_zona(titolo_gruppo, colore_gruppo, lista_gruppo):
                if not lista_gruppo:
                    return
                st.markdown(
                    f'<div class="ev-step-header" style="border-left-color:{colore_gruppo}; margin-top:14px;">'
                    f'<span class="ev-step-title">{titolo_gruppo}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if len(lista_gruppo) > 15:
                    st.caption(f"Mostrate le 15 stazioni più vicine su {len(lista_gruppo)} in questa fascia.")
                for item in lista_gruppo[:15]:
                    render_card_colonnina_generica(item, mostra_km=False)

            # Gruppo 1 (sempre in cima, se presenti): Hyper-Fast (>= 400 kW)
            _render_gruppo_zona(
                f"⚡ Hyper-Fast (>= 400 kW) ({len(hyper_z)} stazioni)",
                "#F59E0B",
                hyper_z,
            )
            # Gruppo 2: Ultra-Fast / HPC (150 – 399 kW)
            _render_gruppo_zona(
                f"🚀 Ultra-Fast / HPC (150 – 399 kW) ({len(hpc_z)} stazioni)",
                "#10B981",
                hpc_z,
            )
            # Gruppo 3: Fast DC (50 – 149 kW)
            _render_gruppo_zona(
                f"🔌 Fast DC (50 – 149 kW) ({len(fast_z)} stazioni)",
                "#3B82F6",
                fast_z,
            )
            # Gruppo 4 (solo se non è attiva la spunta "Solo Fast/HPC"): Lente / AC (< 50 kW)
            if not solo_fast_z:
                _render_gruppo_zona(
                    f"🐢 Lente / AC (< 50 kW) ({len(lente_z)} stazioni)",
                    "#64748B",
                    lente_z,
                )
        else:
            st.markdown(
                '<div class="ev-card" style="border-left-color:#64748B;">'
                '<div class="ev-card-title">Nessuna colonnina trovata con i filtri attivi</div>'
                '<div class="ev-card-sub" style="margin-top:4px;">Prova ad aumentare il raggio di ricerca o modificare i filtri sopra.</div>'
                '</div>',
                unsafe_allow_html=True,
            )

# Calcolatore compatto di risparmio annuale Elettrica vs Termica in fondo alla pagina
render_calcolatore_risparmio_annuale(current_user)

# Pulsante discreto di disconnessione in fondo alla pagina
st.markdown(
    '<div style="margin-top: 1.4rem; border-top: 1px solid #2A2E39; padding-top: 0.9rem;"></div>',
    unsafe_allow_html=True,
)
_col_out_l, _col_out_r = st.columns([3, 1])
with _col_out_l:
    st.caption(f"Accesso attivo: {current_user}")
with _col_out_r:
    if st.button("🚪 Esci", key="btn_logout_ev", use_container_width=True):
        for chiave in (
            "auth_user",
            "operatore_configurato",
            "default_operatore",
            "default_operatore_custom",
            "default_rete",
            "sel_operatore",
            "inp_operatore_custom",
            "rad_rete",
            "chk_rabbocco_ads",
            "roadbook_res",
            "zona_res",
            "mostra_cfg",
            "cfg_expanded",
            "sel_auto",
            "last_auto_sel",
            "num_costo_casa",
            "calc_ann_prezzo_casa",
            "calc_ann_prezzo_pub",
            "calc_ann_last_tesla",
            "calc_ann_init_user",
            "calc_ann_auto",
            "calc_ann_sync_step1",
            "calc_lav_dist",
            "calc_ext_km",
            "calc_ext_auto_pct",
            "calc_ext_casa_pct",
            "sel_onboarding_op",
        ):
            st.session_state.pop(chiave, None)
        for chiave in list(st.session_state.keys()):
            if str(chiave).startswith("sel_alt_"):
                st.session_state.pop(chiave, None)
        st.rerun()
