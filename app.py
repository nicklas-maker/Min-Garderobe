import streamlit as st
import os
import json
import requests
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# --- KONFIGURATION ---
CATEGORIES = ["Top", "Bund", "Strømper", "Sko", "Overtøj"]
CATEGORY_LABELS = {
    "Overtøj": "Overtøj",
    "Top": "Trøje",   
    "Bund": "Bukser", 
    "Strømper": "Strømper",
    "Sko": "Sko"
}

# --- FIREBASE INIT ---
if not firebase_admin._apps:
    if os.path.exists("firestore_key.json"):
        cred = credentials.Certificate("firestore_key.json")
        firebase_admin.initialize_app(cred)
    elif "firebase" in st.secrets:
        key_dict = dict(st.secrets["firebase"])
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    else:
        st.error("Mangler Firebase nøgle! (firestore_key.json eller Secrets)")
        st.stop()

db = firestore.client()

# --- VEJR FUNKTIONER ---

def get_coordinates(city_name):
    """Finder breddegrad/længdegrad for en by via OpenMeteo Geocoding."""
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=da&format=json"
        response = requests.get(url).json()
        if "results" in response:
            return response["results"][0]["latitude"], response["results"][0]["longitude"]
    except Exception as e:
        st.sidebar.error(f"Kunne ikke finde koordinater: {e}")
    return None, None

def get_weather_forecast(lat, lon):
    """Henter dagens vejrprofil (Morgen, Max, Regn, Vind)."""
    try:
        # Vi henter 'forecast' for at få dagens spænd. Bruger timezone=auto for at undgå fejl.
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max&hourly=temperature_2m,apparent_temperature&forecast_days=1&timezone=auto"
        response = requests.get(url)
        response.raise_for_status() # Tjekker om forbindelsen lykkedes
        data = response.json()
        
        # Ekstra tjek: Fik vi de forventede data?
        if 'daily' not in data:
            st.sidebar.error(f"Vejrdata mangler 'daily'. API Svar: {data}")
            return None

        daily = data['daily']
        hourly = data['hourly']
        
        # Find temperatur kl 08:00 (index 8)
        # Vi bruger min(..., 23) for at sikre at vi ikke crasher sent på dagen hvis index løber tør
        # Vi tjekker også om listen overhovedet er lang nok
        if len(hourly['temperature_2m']) > 8:
            temp_morning = hourly['temperature_2m'][8]
        else:
            temp_morning = daily['temperature_2m_max'][0] # Fallback

        current_hour = min(datetime.now().hour, 23)
        if len(hourly['apparent_temperature']) > current_hour:
            feels_like_now = hourly['apparent_temperature'][current_hour]
        else:
            feels_like_now = daily['temperature_2m_max'][0] # Fallback

        return {
            "temp_max": daily['temperature_2m_max'][0],
            "temp_min": daily['temperature_2m_min'][0],
            "temp_morning": temp_morning,
            "feels_like_now": feels_like_now,
            "rain_mm": daily['precipitation_sum'][0],
            "wind_kph": daily['wind_speed_10m_max'][0]
        }
    except Exception as e:
        # Vis fejlen direkte i sidebaren så vi kan se den på mobilen
        st.sidebar.error(f"Vejrfejl: {e}") 
        return None

# --- HISTORIK FUNKTIONER ---

def save_outfit_to_history(outfit_items, weather_data, location):
    """Gemmer dagens outfit og vejr i 'history' samlingen."""
    
    # Lav en liste med ID'er og metadata for nem analyse
    outfit_summary = []
    for item in outfit_items:
        data = item['analysis']
        summary = {
            "id": item['id'],
            "category": data.get('category'),
            "type": data.get('type', 'Ukendt'),
            "material": data.get('material', 'Ukendt'),
            "season": data.get('season', 'Ukendt')
        }
        outfit_summary.append(summary)

    doc_data = {
        "date": datetime.now(), # Firestore Timestamp
        "location": location,
        "weather": weather_data,
        "outfit": outfit_summary
    }
    
    db.collection("history").add(doc_data)

@st.cache_data(ttl=600)
def get_relevant_history(current_temp_max):
    """Finder historik for dage der ligner i dag (+/- 3 grader)."""
    relevant_items = []
    try:
        # Hent al historik (simpelt filter i Python for fleksibilitet)
        docs = db.collection("history").stream()
        for doc in docs:
            data = doc.to_dict()
            hist_temp = data.get('weather', {}).get('temp_max', 0)
            
            # Hvis temperaturen minder om i dag (+/- 3 grader)
            if abs(hist_temp - current_temp_max) <= 3:
                for item in data.get('outfit', []):
                    relevant_items.append(item)
    except:
        pass
    return relevant_items

# --- SMART SCORE LOGIK ---

def calculate_smart_score(item, color_score, weather_data, history_items):
    """
    Beregner den endelige sorterings-score.
    Total = FarveScore + VejrStraf + HistorikBonus
    """
    penalty = 0
    bonus = 0
    data = item['analysis']
    material = data.get('material', '').lower()
    season = data.get('season', '').lower()
    item_type = data.get('type', '')
    
    if weather_data:
        temp_feels = weather_data['feels_like_now']
        is_raining = weather_data['rain_mm'] > 0.5
        
        # --- 1. HARDCODED FYSIK (Straf) ---
        
        # Regn-regler
        if is_raining:
            if material in ['ruskind', 'nubuck', 'silke', 'hvidt canvas', 'canvas']:
                penalty += 5 # Kæmpe straf
            if material in ['læder', 'voksbehandlet', 'gummi', 'syntetisk']:
                bonus += 1 # Lille bonus for regntæt
        
        # Temperatur-regler
        if temp_feels > 22: # Varmt
            if material in ['uld', 'tweed', 'fløjl'] or season == 'vinter':
                penalty += 5
            if material in ['hør', 'seersucker'] or season == 'sommer':
                bonus += 2
                
        elif temp_feels < 10: # Koldt
            if material in ['hør', 'mesh'] or season == 'sommer':
                penalty += 5
            if material in ['uld', 'kashmir', 'dun'] or season == 'vinter':
                bonus += 2

    # --- 2. TILLÆRT HISTORIK (Bonus) ---
    # Har vi valgt denne type/materiale før i dette vejr?
    if history_items:
        match_count = 0
        for hist_item in history_items:
            # Vi tjekker om Type og Materiale matcher
            if hist_item.get('type') == item_type and hist_item.get('material', '').lower() == material:
                match_count += 1
        
        # Giv bonus baseret på popularitet
        if match_count > 2:
            bonus += 2 # Brugeren kan lide dette!
        if match_count > 5:
            bonus += 2 # Brugeren ELSKER dette!

    # Samlet regnskab
    total_score = color_score + penalty - bonus
    return total_score, penalty # Returner også penalty så vi kan vise advarsel

# --- HOVED LOGIK ---

@st.cache_data(ttl=600)
def load_wardrobe():
    items = []
    try:
        docs = db.collection("wardrobe").stream()
        for doc in docs:
            item = doc.to_dict()
            item['id'] = doc.id
            items.append(item)
    except Exception as e:
        st.error(f"Fejl ved hentning af data: {e}")
    return items

def get_items_by_category(items, category):
    return [i for i in items if i['analysis']['category'] == category]

def check_compatibility_basic(candidate, current_outfit):
    """Den rene farve-matematik (som før)."""
    if not current_outfit:
        return True, 0

    total_color_score = 0
    is_valid = True

    for selected_item in current_outfit:
        cand_data = candidate['analysis']
        sel_data = selected_item['analysis']
        
        cand_cat = cand_data['category']
        sel_cat = selected_item['analysis']['category']
        
        cand_color = cand_data['primary_color']
        sel_color = sel_data['primary_color']

        allowed_by_selected = sel_data['compatibility'].get(cand_cat, [])
        allowed_by_candidate = cand_data['compatibility'].get(sel_cat, [])

        if cand_color in allowed_by_selected and sel_color in allowed_by_candidate:
            score1 = allowed_by_selected.index(cand_color)
            score2 = allowed_by_candidate.index(sel_color)
            total_color_score += (score1 + score2)
        else:
            is_valid = False
            break 
    
    return is_valid, total_color_score

def check_dead_end(candidate, current_outfit, wardrobe):
    temp_outfit = current_outfit + [candidate]
    filled_cats = {item['analysis']['category'] for item in temp_outfit}
    missing_cats = [c for c in CATEGORIES if c not in filled_cats]
    
    for missing_cat in missing_cats:
        potential_items = get_items_by_category(wardrobe, missing_cat)
        if not potential_items:
            continue
        found_match = False
        for potential_item in potential_items:
            is_valid, _ = check_compatibility_basic(potential_item, temp_outfit)
            if is_valid:
                found_match = True
                break 
        if not found_match:
            return True
    return False

# --- UI SETUP ---
st.set_page_config(page_title="Garderoben", page_icon="👔", layout="wide")

st.markdown("""
<style>
    .stButton>button { width: 100%; border-radius: 12px; height: auto; min-height: 3em; }
    img { border-radius: 10px; }
    div[data-testid="stExpander"] { border: none; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
    .weather-box { background-color: #e8f4f8; padding: 10px; border-radius: 10px; margin-bottom: 20px; color: #333; }
    .data-badge { font-size: 0.8em; color: #666; background-color: #f0f2f6; padding: 2px 6px; border-radius: 4px; margin-top: 4px; display: inline-block; }
    @media (max-width: 768px) {
        div[data-testid="stImage"] img { width: 75% !important; margin-left: auto; margin-right: auto; display: block; }
    }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR: LOKATION & VEJR ---
with st.sidebar:
    st.header("🌍 Lokation")
    city = st.text_input("Din by", value=st.session_state.get('city', 'Aalborg'))
    
    if city != st.session_state.get('city'):
        st.session_state.city = city
        st.rerun()

    # Hent vejr
    weather_data = None
    lat, lon = get_coordinates(city)
    
    if lat and lon:
        weather_data = get_weather_forecast(lat, lon)
        if weather_data:
            st.markdown(f"""
            <div class="weather-box">
                <b>{city}</b><br>
                🌡️ {weather_data['feels_like_now']}°C (Føles som)<br>
                ☔ {weather_data['rain_mm']} mm regn<br>
                💨 {weather_data['wind_kph']} km/t vind
            </div>
            """, unsafe_allow_html=True)
            
            # Gem vejr i session state så vi ikke henter det hele tiden
            st.session_state.weather = weather_data
    else:
        st.warning("Kunne ikke finde byen.")

st.title("Dagens Outfit")

# Hent garderobe & Historik
wardrobe = load_wardrobe()
if not wardrobe:
    st.info("Databasen er tom. Tilføj tøj via admin.py.")
    st.stop()

# Hent relevant historik til algoritmen
history_items = []
if weather_data:
    history_items = get_relevant_history(weather_data['temp_max'])

# Session State
if 'outfit' not in st.session_state:
    st.session_state.outfit = {} 

# Nulstil knap
if st.sidebar.button("🗑️ Nulstil Outfit"):
    st.session_state.outfit = {}
    st.rerun()

# --- VISNING AF OUTFIT GRID ---
selected_cats = [cat for cat in CATEGORIES if cat in st.session_state.outfit]

if selected_cats:
    cols = st.columns(len(selected_cats))
    for i, cat in enumerate(selected_cats):
        item = st.session_state.outfit[cat]
        data = item['analysis']
        with cols[i]:
            st.image(item['image_path'], width=175)
            shade_info = f"({data.get('shade', 'Mellem')} {data.get('primary_color', '')})"
            st.caption(f"✅ {data['display_name']} {shade_info}")
            if st.button("Fjern", key=f"del_{cat}"):
                del st.session_state.outfit[cat]
                st.rerun()
else:
    st.info("Start med at vælge en del af dit outfit nedenfor 👇")

st.divider()

# --- VÆLGER-SEKTION ---
missing_cats = [c for c in CATEGORIES if c not in st.session_state.outfit]

# Hvis outfittet er færdigt (eller bare delvist), vis "Gem" knap
if not missing_cats:
    st.balloons()
    st.success("🎉 Dit outfit er komplet!")

# GEM OUTFIT KNAP (Vises altid hvis man har valgt mindst én ting)
if st.session_state.outfit:
    if st.button("✅ Gem & Bær Dagens Outfit", type="primary", use_container_width=True):
        if weather_data:
            with st.spinner("Gemmer i historikken..."):
                save_outfit_to_history(list(st.session_state.outfit.values()), weather_data, city)
            st.toast("Outfit gemt! Jeg lærer af din stil.", icon="🧠")
        else:
            st.error("Kan ikke gemme uden vejrdata. Tjek din by.")

if missing_cats:
    st.subheader("Vælg næste del:")
    tabs = st.tabs([CATEGORY_LABELS[c] for c in missing_cats])
    
    for i, cat in enumerate(missing_cats):
        with tabs[i]:
            all_items = get_items_by_category(wardrobe, cat)
            valid_items_with_score = []
            current_selection_list = list(st.session_state.outfit.values())
            
            # 1. Kør Farve-Matematik
            for item in all_items:
                is_valid, color_score = check_compatibility_basic(item, current_selection_list)
                if is_valid:
                    # 2. Kør SMART SCORE (Vejr + Historik)
                    smart_score, weather_penalty = calculate_smart_score(item, color_score, weather_data, history_items)
                    valid_items_with_score.append((smart_score, item, color_score, weather_penalty))
            
            # Sorter efter Smart Score (lavest er bedst)
            valid_items_with_score.sort(key=lambda x: x[0])
            
            if not valid_items_with_score:
                st.error(f"Ingen {CATEGORY_LABELS[cat].lower()} matcher farvevalget!")
            else:
                img_cols = st.columns(3)
                for idx, (smart_score, item, color_score, penalty) in enumerate(valid_items_with_score):
                    col = img_cols[idx % 3]
                    with col:
                        st.image(item['image_path'], use_container_width=True)
                        data = item['analysis']
                        name = data['display_name']
                        shade_str = f"({data.get('shade', 'Mellem')} {data.get('primary_color', '')})"
                        
                        label_text = f"{name}\n{shade_str}"
                        
                        # --- IKON LOGIK ---
                        is_dead_end = False
                        if st.session_state.outfit:
                            is_dead_end = check_dead_end(item, current_selection_list, wardrobe)
                        
                        icon_prefix = ""
                        
                        if is_dead_end:
                            icon_prefix += "⚠️ "
                        
                        # Vis Vejr-Advarsel hvis straffen er høj
                        if penalty >= 5:
                            icon_prefix += "☔/❄️ " # Generel vejr advarsel
                        
                        # Tier Ikoner (Baseret på den rene farve-score, ikke vejr-score, for klarhedens skyld)
                        elif st.session_state.outfit:
                            if color_score == 0: icon_prefix += "⭐ "
                            elif color_score == 1: icon_prefix += "1️⃣ "
                            elif 2 <= color_score <= 3: icon_prefix += "2️⃣ "
                            elif 4 <= color_score <= 5: icon_prefix += "3️⃣ "
                        
                        label_text = icon_prefix + label_text
                        
                        if st.button(label_text, key=f"add_{item['id']}"):
                            if is_dead_end:
                                st.toast(f"Blindgyde advarsel!", icon="⚠️")
                            st.session_state.outfit[cat] = item
                            st.rerun()
            
            if st.session_state.outfit:
                st.markdown("")
                with st.expander(f"💡 Inspiration: Hvilken farve {CATEGORY_LABELS[cat].lower()} passer her?"):
                    # (Samme logik som før)
                    current_items = list(st.session_state.outfit.values())
                    first_item = current_items[0]
                    potential_colors = set(first_item['analysis']['compatibility'].get(cat, []))
                    for outfit_item in current_items[1:]:
                        allowed = set(outfit_item['analysis']['compatibility'].get(cat, []))
                        potential_colors = potential_colors.intersection(allowed)
                    if potential_colors:
                        st.write("Disse farver passer:")
                        st.markdown(" ".join([f"`{c}`" for c in sorted(list(potential_colors))]))
                    else:
                        st.warning("Ingen farve passer!")