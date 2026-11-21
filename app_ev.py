"""
EV Roadbook Italia — Interfaccia Mobile-First Dark Card UI.
Pianificatore guidato di viaggi in auto elettrica con due modalità principali
(Pianifica Viaggio e Cerca Colonnine in Zona / Arrivo), database locale OSM/Geofabrik,
curve di ricarica DC reali, filtri rete/Autogrill/Supercharger e garanzia energetica
su Arrivo (B) e Rientro a Casa (A).
"""

from datetime import date, datetime, time
import hmac
import html
import json
import math
import os
import re
from urllib.parse import quote_plus
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from geopy.geocoders import Nominatim, Photon
import polyline
import requests
import streamlit as st
import streamlit.components.v1 as components


# Configurazione pagina (layout centrato ideale per Mobile-First Dark Card UI)
st.set_page_config(
    page_title="EV Roadbook Italia",
    page_icon="⚡",
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
AUTO_MAZDA_MX30 = "🚗 Mazda MX-30 Versione Lancio (35.5 kWh)"
AUTO_BMW_IX40 = "🚙 BMW iX xDrive40 08/2023 (76.6 kWh)"
AUTO_PERSONALIZZATA = "⚙️ Altra Auto (Personalizzata)"

AUTO_OPZIONI = [
    AUTO_KIA_ENIRO,
    AUTO_TESLA_MY,
    AUTO_MAZDA_MX30,
    AUTO_BMW_IX40,
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
TETTO_RICARICA_INTERMEDIA = 80  # Tetto massimo ricarica intermedia (evita fase lenta 80%-100%)
SOGLIA_EMERGENZA_JOLLY = 4.0  # Soglia minima di emergenza (%) per il calcolo del Jolly (Piano B)
KEYWORDS_EBIKE = ("biciclett", "bici", "ebike", "e-bike", "bike")
UTENTI_FALLBACK_LOCALE = {
    "Fabio": "ev2026",
    "Alessia": "ev2026",
    "Elizabeth": "ev2026",
    "Luciano": "ev2026",
    "Andrea": "ev2026",
}


def opzioni_auto_per_utente(current_user):
    """Restituisce le auto dedicate all'utente collegato (confronto case-insensitive)."""
    utente_low = str(current_user or "").strip().lower()
    if utente_low in ("fabio", "alessia", "elizabeth"):
        return [AUTO_KIA_ENIRO, AUTO_TESLA_MY, AUTO_PERSONALIZZATA]
    if utente_low == "luciano":
        return [AUTO_MAZDA_MX30, AUTO_PERSONALIZZATA]
    if utente_low == "andrea":
        return [AUTO_BMW_IX40, AUTO_PERSONALIZZATA]
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
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #131722 !important;
    color: #E6EDF3 !important;
}
[data-testid="stHeader"] {
    background-color: #131722 !important;
}
.block-container {
    padding-top: 2.5rem !important;
    padding-bottom: 2.5rem !important;
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
/* Expander stile dark card */
[data-testid="stExpander"] {
    background-color: #1E222D !important;
    border: 1px solid #2A2E39 !important;
    border-radius: 12px !important;
    margin-bottom: 0.85rem !important;
    overflow: hidden;
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
        base = "⚡ Fast DC" if is_dc else "🔌 Quick AC"
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


def potenza_curva_auto(auto_cfg, soc_pct):
    """Restituisce la potenza massima di ricarica DC dell'auto alla percentuale di batteria indicata."""
    for soglia, kw in auto_cfg["curva_dc"]:
        if soc_pct <= soglia:
            return float(kw)
    return float(auto_cfg["curva_dc"][-1][1])


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
def load_chargers_db(db_mtime=0.0, filtro_ver=2):
    """
    Carica il database locale colonnine_it.json gestendo il formato a 8 campi
    [lat, lon, title, kw, is_dc, food_name, mall_name, in_ads] e la retrocompatibilità
    con record a 7 o 5 campi, escludendo sempre le prese per biciclette/e-bike.
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
        normalizzati.append(
            (
                float(lat),
                float(lon),
                titolo_str,
                int(kw),
                int(is_dc),
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


@st.cache_data(ttl=86400)
def geocode(q):
    """
    Geocodifica una località italiana tramite Photon con fallback su Nominatim.
    Se la query non contiene già 'italia' o 'italy', prova prima '{q.strip()}, Italia'
    e come fallback la query originale. Restituisce (lat, lon, indirizzo) oppure None.
    """
    query_pulita = str(q).strip()
    if not query_pulita:
        return None
    q_low = query_pulita.lower()
    tentativi = []
    if "italia" not in q_low and "italy" not in q_low:
        tentativi.append(f"{query_pulita}, Italia")
    tentativi.append(query_pulita)

    for query in tentativi:
        for cls in (Photon, Nominatim):
            try:
                loc = cls(user_agent="ev_roadbook_it_2026", timeout=10).geocode(query)
                if loc:
                    raw_addr = getattr(loc, "address", None) or query
                    parti = [p.strip() for p in str(raw_addr).split(",") if p.strip()]
                    indirizzo_breve = ", ".join(parti[:3]) if parti else str(raw_addr)
                    return float(loc.latitude), float(loc.longitude), indirizzo_breve
            except Exception:
                pass
    return None


def url_gmaps_colonnine_indirizzo(indirizzo_cercato):
    """Genera l'URL Google Maps con query testuale codificata sull'indirizzo cercato."""
    query = quote_plus(f"colonnine ricarica elettrica vicino {str(indirizzo_cercato).strip()}")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


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


def cerca_colonnine_zona(db, centro_lat, centro_lon, raggio_km=5.0):
    """Estrae e raggruppa tutte le colonnine auto (AC e DC, escludendo e-bike) entro raggio_km."""
    raw_dest = []
    for lat, lon, titolo, kw, is_dc, has_food, has_mall, food_name, mall_name, _in_ads_db in db:
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
                "has_food": has_food,
                "has_mall": has_mall,
                "food_name": food_name,
                "mall_name": mall_name,
                "Distanza": round(dist, 1),
                "Maps": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
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
                "Maps": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
            }
    return migliore


def e_nome_area_di_servizio(titolo_low):
    """Restituisce True se il nome della colonnina contiene parole chiave esplicite di Area di Servizio."""
    if any(kw in titolo_low for kw in KEYWORDS_ADS_ESPLICITE):
        return True
    return bool(re.search(r"\bads\b", titolo_low))


def analizza_colonnine_tratta(db, pts, etichetta_tratta, offset_km=0.0, evita_autostrade=False):
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
        if km < 3.0 or d_rt > 2.5:
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
                "Maps": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
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

    return route_c


def costruisci_percorso_completo(db, p1, p2, andata_ritorno, evita_autostrade=False):
    """
    Calcola percorso (andata e opzionale ritorno) e colonnine lungo la tratta.
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

    ch1 = analizza_colonnine_tratta(db, pts1, "➡️ Andata", offset_km=0.0, evita_autostrade=evita_autostrade)
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
        all_ch = ch1 + ch2
        tot_km = km1 + km2
        tot_dur = dur1 + dur2

    return all_ch, km1, tot_km, tot_dur, dur1, dur2


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


def trova_jolly_stazione(stazione, arr_pct, pool_jolly, km_per_pct):
    """
    Cerca la colonnina di riserva Piano B (Jolly) successiva entro la riserva residua
    fino al 4% minimo di emergenza.
    Dà sempre precedenza alle Aree di Servizio in Autostrada (priority == 1 / in_ads)
    rispetto alle colonnine fuori casello; usa una colonnina fuori casello SOLO se
    nessuna AdS autostradale è raggiungibile entro la riserva residua.
    """
    margine_km = max(0.0, (arr_pct - SOGLIA_EMERGENZA_JOLLY) * km_per_pct)
    if margine_km <= 0.5:
        return None
    km_s = stazione["Km"]
    candidati = [
        cand for cand in pool_jolly
        if (km_s + 0.5 < cand["Km"] <= km_s + margine_km)
        and (cand["Km"], cand["Stazione"]) != (km_s, stazione["Stazione"])
    ]
    if not candidati:
        return None

    # Precedenza assoluta alle colonnine in Autostrada (AdS: priority == 1 / in_ads)
    candidati_ads = [c for c in candidati if c.get("in_ads") or c.get("priority") == 1]
    pool_scelta = candidati_ads if candidati_ads else candidati
    cand = min(pool_scelta, key=lambda c: c["Km"])

    pct_em = round(arr_pct - (cand["Km"] - km_s) / km_per_pct, 0)
    return {
        "Stazione": cand["Stazione"],
        "Km": cand["Km"],
        "delta_km": round(cand["Km"] - km_s, 1),
        "pct_emergenza": max(int(SOGLIA_EMERGENZA_JOLLY), int(pct_em)),
        "in_ads": bool(cand.get("in_ads") or cand.get("priority") == 1),
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

    def trova_jolly(stazione, arr_pct):
        """Cerca una colonnina successiva (Jolly / Piano B) dando precedenza alle AdS in Autostrada."""
        return trova_jolly_stazione(stazione, arr_pct, pool_jolly, km_per_pct)

    def calcola_target_sosta(stazione, arr_pct):
        """
        Determina la % target di ripartenza rispettando il tetto massimo rigido dell'80%
        nelle soste intermedie (charge_to <= 80 anche se comporta una seconda breve sosta).
        L'unico caso in cui charge_to può superare 80% (fino a 95%) è se più avanti lungo
        tutto il percorso non esiste alcuna altra colonnina compatibile prima della destinazione,
        oppure se l'utente ha impostato una "% Arrivo desiderata" > 75%.
        """
        tetto_eccezione = 95
        km_s = stazione["Km"]
        need_end = math.ceil((tot_km - km_s) / km_per_pct + final_req + 1.0)
        need_b = math.ceil((km1 - km_s) / km_per_pct + p_arr_b + 1.0) if (andata_ritorno and km_s < km1) else 0

        next_ch = [c for c in pool if km_s + 3.0 < c["Km"] < tot_km - 1.0]
        next_ch_pre_b = [c for c in pool if km_s + 3.0 < c["Km"] < km1 - 1.0] if (andata_ritorno and km_s < km1) else []
        arr_molto_alta = (final_req > 75.0) or (andata_ritorno and km_s < km1 and p_arr_b > 75.0)

        # Caso 1: chiude il viaggio rimanendo entro il tetto rigido dell'80%
        if need_end <= TETTO_RICARICA_INTERMEDIA and need_b <= TETTO_RICARICA_INTERMEDIA:
            needed_pct = max(int(math.ceil(arr_pct + 4)), need_end, need_b)
            return min(TETTO_RICARICA_INTERMEDIA, needed_pct), True

        # Caso 2 (unica eccezione > 80%, fino a 95%): nessuna altra colonnina compatibile più avanti
        # prima della destinazione, oppure "% Arrivo desiderata" > 75%
        if (not next_ch or arr_molto_alta) and need_end <= tetto_eccezione:
            needed_pct = max(int(math.ceil(arr_pct + 4)), need_end, need_b)
            return min(tetto_eccezione, needed_pct), True

        # Caso 3: sosta intermedia -> tetto massimo rigido all'80% (mai superato se esistono altre colonnine)
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

        # Assicura carica adeguata verso la prossima colonnina restando sempre entro il tetto rigido 80%
        if next_ch:
            dist_prossima = next_ch[0]["Km"] - km_s
            min_comodo = math.ceil(dist_prossima / km_per_pct + p_res + 1.0)
            target = min(TETTO_RICARICA_INTERMEDIA, max(target, min_comodo))

        # Unica eccezione sulla tappa intermedia B (A/R): nessuna colonnina compatibile prima di B o % arrivo B > 75%
        if andata_ritorno and km_s < km1 and (not next_ch_pre_b or p_arr_b > 75.0) and need_b > target:
            target = min(tetto_eccezione, need_b)

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

            # Regola del Jolly (Piano B garantito entro il 4% d'emergenza):
            # 1) Preferisce stazioni nella finestra ideale con Jolly garantito;
            # 2) Altrimenti arretra solo alle stazioni più avanzate con Jolly garantito;
            # 3) Se nessuna ha un Jolly, usa la finestra ideale (o le stazioni più avanzate).
            finestra_ideale_jolly = [v for v in finestra_ideale if v["jolly"] is not None]
            if finestra_ideale_jolly:
                fascia_utile = finestra_ideale_jolly
            else:
                tutte_jolly = [v for v in valutazioni_attive if v["jolly"] is not None]
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

    e_filtro_tesla = (rete_codice == "tesla") or bool(operatore_keywords and "tesla" in operatore_keywords)
    extra_ads = solo_ads if (consenti_rabbocco_ads and e_filtro_tesla) else []

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

    # Se la rete scelta è Solo Autostrada o Solo Tesla e A fallisce, offre comunque B su tutte le colonnine
    if not sol_a["valido"] and rete_codice in ("ads", "tesla"):
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


def costruisci_badge_locali(lat, lon, has_food, has_mall):
    """Genera i badge HTML cliccabili verso Google Maps API per Ristorante e Centro Commerciale."""
    badges = []
    if has_food:
        url_food = html.escape(
            f"https://www.google.com/maps/search/?api=1&query=ristorante+{lat},{lon}",
            quote=True,
        )
        badges.append(
            f'<a class="ev-badge ev-b-blue ev-badge-link" href="{url_food}" '
            f'target="_blank" rel="noopener noreferrer" '
            f'title="Cerca ristoranti vicini su Google Maps">🍽️ Ristorante</a>'
        )
    if has_mall:
        url_mall = html.escape(
            f"https://www.google.com/maps/search/?api=1&query=supermercato+centro+commerciale+{lat},{lon}",
            quote=True,
        )
        badges.append(
            f'<a class="ev-badge ev-b-purple ev-badge-link" href="{url_mall}" '
            f'target="_blank" rel="noopener noreferrer" '
            f'title="Cerca supermercati e centri commerciali vicini su Google Maps">🛒 Centro Comm.</a>'
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

        # Includi eventuali AdS per rabbocco veloce se l'opzione è attiva in modalità Tesla
        pool_corrente = list(pool_compatibile)
        if pool_rete == "tesla" and consenti_rabbocco:
            chiavi_esistenti = {(c["Km"], c["Stazione"]) for c in pool_corrente}
            for c in all_ch:
                if c["in_ads"] and (c["Km"], c["Stazione"]) not in chiavi_esistenti:
                    pool_corrente.append(c)

        # Mostra tutte le colonnine compatibili raggiungibili (5% <= batteria <= 75%),
        # ordinate per vicinanza crescente alla stazione principale con delta km esplicito
        # (con contachilometri azzerato da destinazione per le soste del ritorno)
        e_sosta_ritorno = andata_ritorno and ("Ritorno" in str(def_s.get("Tratta", "")) or def_s["Km"] > km1 + 0.05)
        km_inf_filtro = max(curr_km + 10.0, km1 + 3.0) if e_sosta_ritorno else (curr_km + 10.0)
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
            and (5.0 <= (curr_pct - (c["Km"] - curr_km) / km_per_pct) <= 75.0)
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

        # Calcola il Jolly (Piano B entro il 4% d'emergenza) con precedenza alle AdS in Autostrada
        jolly_dyn = trova_jolly_stazione(st_scelta, arr_pct, all_ch, km_per_pct)

        # Ricalcola carica necessaria, minuti e avvisi rispettando il tetto rigido dell'80%
        if (not cambiata_dall_utente) and abs(arr_pct - def_s["arr_pct"]) < 0.6:
            charge_to = int(def_s["charge_to"])
            min_ric = int(def_s["minuti_ricarica"])
            avviso_dyn = def_s.get("avviso")
            nota_dyn = def_s.get("nota_extra")
            jolly_dyn = def_s.get("jolly") or jolly_dyn
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
                    # Supera l'80% (fino a 95%) solo se non esistono altre colonnine compatibili avanti o % arrivo > 75%
                    if not next_ch_dyn or arr_molto_alta_dyn:
                        charge_to = min(tetto_eccezione, needed_pct)
                    else:
                        charge_to = min(TETTO_RICARICA_INTERMEDIA, needed_pct)
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
            if (
                i == n_soste - 1
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
            elif stile_codice in ("caffe", "pranzo") and st_scelta.get("has_food", 0) == 0 and st_scelta.get("has_mall", 0) == 0:
                avviso_dyn = "⚠️ Nessun ristorante o centro commerciale mappato entro 200 m da questa stazione."

            nota_dyn = (
                "🔄 Stazione cambiata manualmente: ricarica, tempi e batteria all'arrivo aggiornati."
                if cambiata_dall_utente
                else def_s.get("nota_extra")
            )

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
    """Genera le righe chiave-valore con i nomi reali cliccabili di ristorante e centro commerciale."""
    righe = []
    if food_name:
        url_food = html.escape(
            f"https://www.google.com/maps/search/?api=1&query=ristorante+{lat},{lon}",
            quote=True,
        )
        righe.append(
            f'<div class="ev-kv">'
            f'<span class="ev-k">🍽️ Vicino a</span>'
            f'<span class="ev-v"><a href="{url_food}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#60A5FA; text-decoration:none;">{html.escape(food_name)} ↗</a></span>'
            f'</div>'
        )
    if mall_name:
        url_mall = html.escape(
            f"https://www.google.com/maps/search/?api=1&query=supermercato+centro+commerciale+{lat},{lon}",
            quote=True,
        )
        righe.append(
            f'<div class="ev-kv">'
            f'<span class="ev-k">🛒 Vicino a</span>'
            f'<span class="ev-v"><a href="{url_mall}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#C084FC; text-decoration:none;">{html.escape(mall_name)} ↗</a></span>'
            f'</div>'
        )
    return "".join(righe)


def render_banner_verdetto(sol_a, res):
    """Mostra il verdetto immediato e ben evidente in cima ai risultati prima di Soluzione A / B."""
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

    n_soste = len(sol_a["soste"])
    tempo_tot_txt = formatta_durata(sol_a["tempo_totale_min"])

    if n_soste == 0:
        dettaglio = f"Tempo totale stimato: {tempo_tot_txt} • Arrivo a {html.escape(res['dest'])} con ~{sol_a['pct_arrivo_b']:.0f}%"
        if res["andata_ritorno"] and sol_a.get("pct_rientro_a") is not None:
            dettaglio += f" • Rientro con ~{sol_a['pct_rientro_a']:.0f}%"
        st.markdown(
            f'<div class="ev-card" style="background:linear-gradient(135deg, #132C24 0%, #1E222D 100%); '
            f'border-color:rgba(16,185,129,0.5); border-left:5px solid #10B981; padding:16px 18px;">'
            f'<div style="font-size:1.12rem; font-weight:800; color:#34D399;">✅ NESSUNA SOSTA NECESSARIA — Puoi arrivare diretto a destinazione!</div>'
            f'<div class="ev-card-sub" style="margin-top:5px; color:#CBD5E1;">{dettaglio}</div>'
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
            f'</div>',
            unsafe_allow_html=True,
        )


def render_card_sosta(indice, sosta, res=None, km1_offset=0.0, colore_bordo="#10B981"):
    """Renderizza una singola fermata di ricarica come Dark Card finanziaria mobile-first."""
    nome = html.escape(sosta["Stazione"])
    tratta = html.escape(sosta["Tratta"])
    nota_pos = html.escape(sosta["Note"])
    potenza = html.escape(sosta["Potenza"])
    maps_url = html.escape(sosta["Maps"], quote=True)

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

    riga_jolly = ""
    jolly = sosta.get("jolly")
    if jolly:
        j_nome = html.escape(jolly["Stazione"])
        j_km_disp = max(0.0, float(jolly["Km"]) - km1_offset) if e_ritorno else float(jolly["Km"])
        j_km_txt = f"Km {j_km_disp:.1f} da {dest_nome}" if e_ritorno else f"Km {j_km_disp:.1f}"
        if jolly.get("in_ads"):
            j_dettaglio = f'{j_nome} • 🛣️ AdS • {j_km_txt} (+{jolly["delta_km"]:.1f} km, emerg. ~{jolly["pct_emergenza"]}%)'
        else:
            j_dettaglio = f'{j_nome} • Fuori casello d\'emergenza • +{jolly["delta_km"]:.1f} km ({j_km_txt}, emerg. ~{jolly["pct_emergenza"]}%)'
        riga_jolly = (
            f'<div class="ev-kv">'
            f'<span class="ev-k">🛟 Piano B (Jolly)</span>'
            f'<span class="ev-v" style="color:#93C5FD;">{j_dettaglio}</span>'
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


def render_card_colonnina_generica(item, mostra_km=False):
    """Renderizza una colonnina (Cerca in Zona o Modalità Esperto) in formato Dark Card compatto."""
    nome = html.escape(item["Stazione"])
    potenza = html.escape(item["Potenza"])
    maps_url = html.escape(item["Maps"], quote=True)
    is_fast = bool(item.get("is_dc") or item.get("kw", 0) >= 50)
    colore_bordo = "#10B981" if item.get("kw", 0) >= 150 else ("#3B82F6" if is_fast else "#64748B")

    food_name = item.get("food_name") or (FALLBACK_FOOD_NAME if item.get("has_food") else "")
    mall_name = item.get("mall_name") or (FALLBACK_MALL_NAME if item.get("has_mall") else "")

    badges = []
    if is_fast:
        badges.append(f'<span class="ev-badge ev-b-green">{potenza}</span>')
    else:
        badges.append(f'<span class="ev-badge ev-b-slate">{potenza}</span>')
    badges.extend(
        costruisci_badge_locali(
            item["lat"],
            item["lon"],
            1 if food_name else 0,
            1 if mall_name else 0,
        )
    )

    righe = []
    if mostra_km:
        righe.append(
            f'<div class="ev-kv"><span class="ev-k">Posizione</span>'
            f'<span class="ev-v">{html.escape(item["Tratta"])} • Km {item["Km"]:.1f}</span></div>'
        )
        righe.append(
            f'<div class="ev-kv"><span class="ev-k">Accesso</span>'
            f'<span class="ev-v">{html.escape(item["Note"])}</span></div>'
        )
    else:
        tipo_txt = "⚡ Fast / HPC DC" if is_fast else "🔌 Quick AC"
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
        if idx_global - 1 < len(controlli):
            sel_key, opzioni_id, mappa_etichette = controlli[idx_global - 1]
            st.selectbox(
                f"🔄 Cambia stazione Sosta {idx_global} (alternative prima/dopo lungo il percorso)",
                options=opzioni_id,
                format_func=lambda sid, m=mappa_etichette: m[sid],
                key=sel_key,
            )
        render_card_sosta(idx_global, sosta_item, res=res, km1_offset=km1_sol, colore_bordo=colore_bordo)

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

    # Pulsante/i per aprire l'intero itinerario multi-tappa su Google Maps (con tutte le soste)
    if res["andata_ritorno"]:
        soste_andata = [s for s in soste_sol if not s.get("e_ritorno")]
        soste_ritorno = [s for s in soste_sol if s.get("e_ritorno")]
        url_andata = html.escape(url_gmaps_viaggio_multi_tappa(res["orig"], res["dest"], soste_andata), quote=True)
        url_ritorno = html.escape(url_gmaps_viaggio_multi_tappa(res["dest"], res["orig"], soste_ritorno), quote=True)
        st.markdown(
            f'<div class="ev-dir-grid">'
            f'<a class="ev-gmaps-cta" href="{url_andata}" target="_blank" rel="noopener noreferrer">🗺️ Apri Andata su Maps (con soste)</a>'
            f'<a class="ev-gmaps-cta" href="{url_ritorno}" target="_blank" rel="noopener noreferrer">🗺️ Apri Ritorno su Maps (con soste)</a>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        url_intero = html.escape(url_gmaps_viaggio_multi_tappa(res["orig"], res["dest"], soste_sol), quote=True)
        st.markdown(
            f'<a class="ev-gmaps-cta" href="{url_intero}" target="_blank" rel="noopener noreferrer">'
            f'🗺️ Apri Intero Viaggio su Google Maps (con soste)'
            f'</a>',
            unsafe_allow_html=True,
        )


# ==============================================================================
# INTERFACCIA PRINCIPALE
# ==============================================================================
inietta_stile_dark()

# Schermata di Login sicura all'apertura (st.secrets["users"] con fallback solo locale)
if not st.session_state.get("auth_user"):
    utenti_auth, is_fallback_locale = carica_utenti_autorizzati()
    st.markdown(
        '<div class="ev-hero">'
        '<div class="ev-hero-title">⚡ EV Roadbook Italia</div>'
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

_db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colonnine_it.json")
_db_mtime = os.path.getmtime(_db_file) if os.path.exists(_db_file) else 0.0
db = load_chargers_db(_db_mtime, filtro_ver=2)

# Inizializzazione stato sessione (benvenuto pulito all'apertura / dopo F5)
if st.session_state.pop("reset_nuovo_viaggio", False):
    st.session_state["mostra_cfg"] = True
    st.session_state["cfg_expanded"] = True
    st.session_state["inp_orig"] = ""
    st.session_state["inp_dest"] = ""
    st.session_state["inp_operatore_custom"] = ""
    st.session_state["sel_operatore"] = OPERATORI_OPZIONI[0]
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
    f'<div class="ev-hero-title">⚡ EV Roadbook Italia</div>'
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
            # Aggiorna il default della checkbox rabbocco veloce AdS quando cambia l'auto selezionata
            if st.session_state.get("last_auto_sel") != scelta_auto:
                st.session_state["last_auto_sel"] = scelta_auto
                st.session_state["chk_rabbocco_ads"] = "Model Y" in scelta_auto

            custom_bat, custom_cons, custom_dc = 60.0, 18.0, 120
            if "Altra Auto" in scelta_auto:
                c_a1, c_a2, c_a3 = st.columns(3)
                custom_bat = c_a1.number_input("Batteria (kWh)", 15.0, 150.0, 60.0, 1.0, key="num_bat")
                custom_cons = c_a2.number_input("Consumo base (kWh/100km)", 10.0, 35.0, 18.0, 0.5, key="num_cons")
                custom_dc = c_a3.number_input("Max DC (kW)", 22, 350, 120, 10, key="num_dc")

            auto_cfg = costruisci_profilo_auto(scelta_auto, custom_bat, custom_cons, custom_dc)

            # Tariffe energia modificabili (casa e colonnine veloci) per il riepilogo economico completo
            c_t1, c_t2 = st.columns(2)
            costo_casa_kwh = c_t1.number_input(
                "🏠 Costo energia casa (€/kWh)",
                min_value=0.05,
                max_value=1.50,
                value=0.25,
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

            rete_label = st.radio(
                "Circuito / Rete di ricarica preferita",
                list(RETI_PREFERITE.keys()),
                index=0,
                key="rad_rete",
            )
            rete_codice = RETI_PREFERITE[rete_label]

            consenti_rabbocco_ads = False
            if rete_codice == "tesla" or scelta_operatore == "🔴 Tesla Supercharger":
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
                        raggio_corrente = float(st.session_state.get("sld_zona_raggio", 5))
                        dest_ch = cerca_colonnine_zona(db, g2[0], g2[1], raggio_km=raggio_corrente)
                        colonnina_vicina_dest = dest_ch[0] if dest_ch else trova_colonnina_piu_vicina(db, g2[0], g2[1])

                        rete_no_auto = "tesla" if rete_codice == "tesla" else "tutte"

                        if strada_codice == "confronta":
                            # Calcola entrambi i percorsi: standard (Autostrada) e alternativa Statali/Extraurbano
                            res_auto = costruisci_percorso_completo(db, p1, p2, andata_ritorno, evita_autostrade=False)
                            res_no = costruisci_percorso_completo(db, p1, p2, andata_ritorno, evita_autostrade=True)
                            if not res_auto or not res_no:
                                st.error("Impossibile calcolare uno dei due percorsi tramite OSRM.")
                                st.stop()

                            all_ch_a, km1_a, tot_km_a, tot_dur_a, dur1_a, dur2_a = res_auto
                            all_ch_b, km1_b, tot_km_b, tot_dur_b, dur1_b, dur2_b = res_no

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
                            desc_a = "🛣️ Con Autostrada"
                            desc_b = "🚗 Senza Autostrada — Statali / Extraurbano"

                        else:
                            evita_auto = strada_codice == "no_auto"
                            res_rt = costruisci_percorso_completo(db, p1, p2, andata_ritorno, evita_autostrade=evita_auto)
                            if not res_rt:
                                st.error("Impossibile calcolare il percorso stradale tramite OSRM.")
                                st.stop()

                            all_ch, km1, tot_km, tot_dur, dur1, dur2 = res_rt
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

                        # Chiude automaticamente il pannello parametri e scorre esattamente su "Risultati del Viaggio"
                        st.session_state["cfg_expanded"] = False
                        st.session_state["scroll_risultati"] = True
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
        if st.session_state.pop("scroll_risultati", False):
            components.html(
                """
                <script>
                    setTimeout(function() {
                        var el = window.parent.document.getElementById("ancora-risultati");
                        if (el) {
                            el.scrollIntoView({behavior: "smooth", block: "start"});
                        }
                    }, 150);
                </script>
                """,
                height=0,
            )

        # Verdetto immediato ben evidente in cima ai risultati prima delle card Soluzione A / B
        render_banner_verdetto(sol_a_dyn, res)

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
                st.session_state.get(sel_key) not in (None, opzioni_id[0])
                for sel_key, opzioni_id, _ in ctrl_b
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
                st.caption(f"Totale colonnine Fast/HPC rilevate lungo il percorso: {len(res['all_ch'])}")
                for ch in res["all_ch"]:
                    render_card_colonnina_generica(ch, mostra_km=True)
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
        max_value=10,
        value=5,
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
                geo_z = geocode(localita_zona.strip())
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
            if (not solo_fast_z or d["is_dc"] == 1 or d["kw"] >= 50)
            and (not con_food_z or d["has_food"] == 1)
            and (not con_mall_z or d["has_mall"] == 1)
            and match_operatore_kw(d, op_zona_kw)
        ]

        if filtrate_z:
            st.caption(
                f"Trovate {len(filtrate_z)} stazioni entro {raggio_disp} km "
                f"(mostrate le prime {min(25, len(filtrate_z))} più vicine)."
            )
            for item in filtrate_z[:25]:
                render_card_colonnina_generica(item, mostra_km=False)
        else:
            st.markdown(
                '<div class="ev-card" style="border-left-color:#64748B;">'
                '<div class="ev-card-title">Nessuna colonnina trovata con i filtri attivi</div>'
                '<div class="ev-card-sub" style="margin-top:4px;">Prova ad aumentare il raggio di ricerca o modificare i filtri sopra.</div>'
                '</div>',
                unsafe_allow_html=True,
            )

# Pulsante discreto di disconnessione in fondo alla pagina
st.markdown(
    '<div style="margin-top: 2rem; border-top: 1px solid #2A2E39; padding-top: 0.9rem;"></div>',
    unsafe_allow_html=True,
)
_col_out_l, _col_out_r = st.columns([3, 1])
with _col_out_l:
    st.caption(f"Accesso attivo: {current_user}")
with _col_out_r:
    if st.button("🚪 Esci", key="btn_logout_ev", use_container_width=True):
        for chiave in ("auth_user", "roadbook_res", "zona_res", "mostra_cfg", "cfg_expanded", "sel_auto", "last_auto_sel"):
            st.session_state.pop(chiave, None)
        for chiave in list(st.session_state.keys()):
            if str(chiave).startswith("sel_alt_"):
                st.session_state.pop(chiave, None)
        st.rerun()
