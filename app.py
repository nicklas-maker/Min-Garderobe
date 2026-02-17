import streamlit as st
import os
import json
import requests
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from google import genai
from PIL import Image
from io import BytesIO

# --- KONFIGURATION ---
CATEGORIES = ["Top", "Bund", "Strømper", "Sko", "Overtøj"]
CATEGORY_LABELS = {
    "Overtøj": "Overtøj",
    "Top": "Trøje",   
    "Bund": "Bukser", 
    "Strømper": "Strømper",
    "Sko": "Sko"
}

# Hvor meget skal temperatur-afvigelse straffes?
# Formel: abs(dagens_temp - tøjets_gns) * FACTOR
TEMP_PENALTY_FACTOR = 0.5 

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

# --- AI HELPER FUNCTIONS ---

def load_image_from_url(url):
    """Henter et billede fra en URL (GitHub) og gør det klar til AI."""
    try:
        response = requests.get(url)
        response.raise_for_status()
        return Image.open(BytesIO(response.content))
    except Exception as e:
        print(f"Kunne ikke hente billede: {e}")
        return None

def get_ai_feedback(outfit_items):
    """Sender billederne til Gemini for en 'Smagsdommer' vurdering."""
    
    api_key = None
    if "google_api_key" in st.secrets:
        api_key = st.secrets["google_api_key"]
    
    if not api_key:
        return "⚠️ Mangler Google API Nøgle i Secrets."

    images = []
    for item in outfit_items:
        img_url = item.get('image_path')
        if img_url and img_url.startswith('http'):
            img = load_image_from_url(img_url)
            if img:
                images.append(img)
    
    if not images:
        return "⚠️ Kunne ikke finde billeder af outfittet."

    system_instruction = """Du er en ærlig og direkte modeekspert med speciale i 'Modern Heritage' og klassisk herremode. Du foretrækker harmoni, jordfarver og tekstur.

Din opgave:
Se på de vedhæftede billeder, som udgør ét samlet outfit.

Output format (Vær kort!):
1. Start med DOMMEN: Enten '✅ Godkendt' eller '⚠️ Justering anbefales'.
2. Giv KOMMENTAREN: Max 1-2 sætninger.
   - Hvis godkendt: Hvorfor virker det? (Fx 'Godt spil mellem teksturerne').
   - Hvis justering: Hvad clasher? (Fx 'Skoene er for formelle til de bukser').
3. LØSNINGEN (Kun ved fejl): Foreslå specifikt én ting der skal ændres (Fx 'Prøv et par brune støvler i stedet')."""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=images,
            config={
                "system_instruction": system_instruction,
                "temperature": 0.5,
            }
        )
        return response.text
    except Exception as e:
        return f"AI Fejl: {str(e)}"

# --- VEJR FUNKTIONER ---

@st.cache_data
def get_coordinates(city_name):
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=da&format=json"
        response = requests.get(url).json()
        if "results" in response:
            return response["results"][0]["latitude"], response["results"][0]["longitude"]
    except Exception as e:
        print(f"Koordinat-fejl: {e}")
    return None, None

@st.cache_data(ttl=3600)
def fetch_weather_api_data(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,precipitation_sum,wind_speed_10m_max&hourly=apparent_temperature&forecast_days=2&timezone=auto"
    response = requests.get(url)
    response.raise_for_status() 
    return response.json()

def get_weather_forecast(lat, lon):
    try:
        data = fetch_weather_api_data(lat, lon)
        if 'daily' not in data: return None

        daily = data['daily']
        hourly = data['hourly']
        
        current_hour = min(datetime.now().hour, 23)
        
        # --- NY LOGIK: 10 Timers Gennemsnit ---
        hourly_feels = hourly['apparent_temperature']
        # Vi sikrer os at vi ikke går ud over arrayets længde
        end_index = min(current_hour + 10, len(hourly_feels))
        next_10_hours = hourly_feels[current_hour:end_index]
        
        if next_10_hours:
            avg_10h = sum(next_10_hours) / len(next_10_hours)
        else:
            avg_10h = daily['temperature_2m_max'][0] # Fallback

        # Henter "føles som" lige nu til display
        feels_like_now = hourly_feels[current_hour]

        return {
            "temp_max": daily['temperature_2m_max'][0], # Kun til info
            "avg_feels_like_10h": avg_10h, # Den nye vigtige værdi
            "feels_like_now": feels_like_now,
            "rain_mm": daily['precipitation_sum'][0],
            "wind_kph": daily['wind_speed_10m_max'][0]
        }
    except Exception as e:
        print(f"Vejrfejl (ignoreret i UI): {e}") 
        return None

# --- HISTORIK & STATISTIK FUNKTIONER ---

def update_item_stats(item_id, current_avg_temp):
    """Opdaterer gennemsnitstemperatur og brugs-antal på selve tøjet."""
    try:
        doc_ref = db.collection("wardrobe").document(item_id)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            old_count = data.get('usage_count', 0)
            old_avg = data.get('avg_temp', 0)
            
            # Formel for løbende gennemsnit:
            # Ny_Avg = ((Gammel_Avg * Gammel_Antal) + Ny_Værdi) / (Gammel_Antal + 1)
            
            if old_count == 0 or old_avg is None:
                new_avg = current_avg_temp
            else:
                new_avg = ((old_avg * old_count) + current_avg_temp) / (old_count + 1)
            
            doc_ref.update({
                'usage_count': old_count + 1,
                'avg_temp': new_avg,
                'last_worn': firestore.SERVER_TIMESTAMP
            })
    except Exception as e:
        print(f"Kunne ikke opdatere stats for {item_id}: {e}")

def save_outfit_to_history(outfit_items, weather_data, location):
    # 1. Gem selve outfittet i historikken (som før)
    outfit_summary = []
    for item in outfit_items:
        data = item['analysis']
        summary = {
            "id": item['id'],
            "category": data.get('category'),
            "type": data.get('type', 'Ukendt')
        }
        outfit_summary.append(summary)

    doc_data = {
        "date": datetime.now(),
        "location": location,
        "weather": weather_data,
        "outfit": outfit_summary
    }
    db.collection("history").add(doc_data)
    
    # 2. Opdater statistikken på hvert stykke tøj
    current_avg_temp = weather_data.get('avg_feels_like_10h')
    if current_avg_temp is not None:
        for item in outfit_items:
            update_item_stats(item['id'], current_avg_temp)

# --- SMART SCORE LOGIK ---

def calculate_match_score(target_color, allowed_list):
    """Beregner point for farve-match (Uændret)."""
    if target_color in allowed_list:
        return allowed_list.index(target_color), False
    
    synonyms = {
        "Hvid": "Creme", "Creme": "Hvid",
        "Navy": "Blå", "Blå": "Navy",
        "Grøn": "Oliven", "Oliven": "Grøn",
        "Rød": "Bordeaux", "Bordeaux": "Rød"
    }
    
    synonym_color = synonyms.get(target_color)
    if synonym_color and synonym_color in allowed_list:
        base_score = allowed_list.index(synonym_color)
        return base_score + 4, True
        
    return None, False

def calculate_smart_score(item, color_score, weather_data):
    """
    Ny logik:
    Score = Farvepoint + Temperaturstraf
    """
    weather_penalty = 0
    
    # Hent tøjets historiske gennemsnit (hvis det findes)
    item_avg = item.get('avg_temp') # Kommer fra Firestore
    current_avg = weather_data.get('avg_feels_like_10h')
    
    if item_avg is not None and current_avg is not None:
        # Beregn forskel
        diff = abs(current_avg - item_avg)
        # Gang med din valgte faktor
        weather_penalty = diff * TEMP_PENALTY_FACTOR
    
    # Hvis tøjet aldrig er brugt (item_avg er None), er straffen 0.
    # Tvivlen kommer den anklagede til gode.

    total_score = color_score + weather_penalty
    return total_score, weather_penalty

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
    if not current_outfit:
        return True, 0, False

    total_color_score = 0
    is_valid = True
    is_synonym_match = False

    for selected_item in current_outfit:
        cand_data = candidate['analysis']
        sel_data = selected_item['analysis']
        
        cand_cat = cand_data['category']
        sel_cat = selected_item['analysis']['category']
        
        cand_color = cand_data['primary_color']
        sel_color = sel_data['primary_color']

        allowed_by_selected = sel_data['compatibility'].get(cand_cat, [])
        allowed_by_candidate = cand_data['compatibility'].get(sel_cat, [])

        score1, syn1 = calculate_match_score(cand_color, allowed_by_selected)
        score2, syn2 = calculate_match_score(sel_color, allowed_by_candidate)

        if score1 is not None and score2 is not None:
            total_color_score += (score1 + score2)
            if syn1 or syn2:
                is_synonym_match = True
        else:
            is_valid = False
            break 
    
    return is_valid, total_color_score, is_synonym_match

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
            is_valid, _, _ = check_compatibility_basic(potential_item, temp_outfit)
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
        new_weather_data = get_weather_forecast(lat, lon)
        if new_weather_data:
            weather_data = new_weather_data
            st.session_state.weather = weather_data 
        elif 'weather' in st.session_state:
            weather_data = st.session_state.weather
            st.warning("Bruger gemt vejr (kunne ikke opdatere).")
        
        if weather_data:
            st.markdown(f"""
            <div class="weather-box">
                <b>{city}</b><br>
                🌡️ {weather_data['feels_like_now']}°C (Nu)<br>
                ⚖️ {weather_data['avg_feels_like_10h']:.1f}°C (10t gns)<br>
                ☔ {weather_data['rain_mm']} mm regn
            </div>
            """, unsafe_allow_html=True)
    else:
        st.warning("Kunne ikke finde byen.")

st.title("Dagens Outfit")

# Hent garderobe
wardrobe = load_wardrobe()
if not wardrobe:
    st.info("Databasen er tom. Tilføj tøj via admin.py.")
    st.stop()

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

if not missing_cats:
    st.balloons()
    st.success("🎉 Dit outfit er komplet!")

# GEM OUTFIT & BEDØM KNAPPER
if st.session_state.outfit:
    btn_col1, btn_col2 = st.columns(2)
    
    with btn_col1:
        if st.button("🔮 Bedøm Outfit", type="secondary", use_container_width=True):
            with st.spinner("Stylisten kigger på dit tøj..."):
                feedback = get_ai_feedback(list(st.session_state.outfit.values()))
                if "✅" in feedback:
                    st.success(feedback)
                else:
                    st.info(feedback)

    with btn_col2:
        if st.button("✅ Gem & Bær", type="primary", use_container_width=True):
            if weather_data:
                with st.spinner("Gemmer og opdaterer tøj-statistik..."):
                    save_outfit_to_history(list(st.session_state.outfit.values()), weather_data, city)
                    # Ryd cache så de nye statistikker indlæses næste gang
                    load_wardrobe.clear()
                st.toast("Outfit gemt! Statistik opdateret.", icon="📈")
            else:
                st.error("Kan ikke gemme uden vejrdata. Prøv at indtaste din by igen i sidebaren.")

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
                is_valid, color_score, is_synonym = check_compatibility_basic(item, current_selection_list)
                if is_valid:
                    # 2. Kør SMART SCORE (Temperatur)
                    smart_score, weather_penalty = calculate_smart_score(item, color_score, weather_data)
                    valid_items_with_score.append((smart_score, item, color_score, weather_penalty, is_synonym))
            
            # Sorter efter Smart Score (lavest er bedst)
            valid_items_with_score.sort(key=lambda x: x[0])
            
            if not valid_items_with_score:
                st.error(f"Ingen {CATEGORY_LABELS[cat].lower()} matcher farvevalget!")
            else:
                img_cols = st.columns(3)
                for idx, (smart_score, item, color_score, penalty, is_synonym) in enumerate(valid_items_with_score):
                    col = img_cols[idx % 3]
                    with col:
                        st.image(item['image_path'], use_container_width=True)
                        data = item['analysis']
                        name = data['display_name']
                        shade_str = f"({data.get('shade', 'Mellem')} {data.get('primary_color', '')})"
                        
                        label_text = f"{name}"
                        if is_synonym:
                            label_text += " ❗️"
                        
                        # Formater Score: Vis som heltal hvis muligt (fx 1.0 -> 1), ellers med 1 decimal
                        score_fmt = f"{smart_score:.0f}" if smart_score.is_integer() else f"{smart_score:.1f}"
                        label_text += f"\n{shade_str} {score_fmt}"
                        
                        # --- IKON LOGIK ---
                        is_dead_end = False
                        if st.session_state.outfit:
                            is_dead_end = check_dead_end(item, current_selection_list, wardrobe)
                        
                        icon_prefix = ""
                        
                        if is_dead_end:
                            icon_prefix += "⚠️ "
                        
                        # Tier Ikoner (Baseret udelukkende på farve-score)
                        if color_score == 0: 
                            icon_prefix += "⭐ "      # Perfekt match
                        elif color_score == 1: 
                            icon_prefix += "1️⃣ "     # Godt match
                        elif 2 <= color_score <= 3: 
                            icon_prefix += "2️⃣ "     # Acceptabelt match
                        elif 4 <= color_score <= 5: 
                            icon_prefix += "3️⃣ "     # Matcher, men med stor kontrast/synonym straf
                        
                        label_text = icon_prefix + label_text
                        
                        if st.button(label_text, key=f"add_{item['id']}"):
                            if is_dead_end:
                                st.toast(f"Blindgyde advarsel!", icon="⚠️")
                            st.session_state.outfit[cat] = item
                            st.rerun()
            
            if st.session_state.outfit:
                st.markdown("")
                with st.expander(f"💡 Inspiration: Farver til {CATEGORY_LABELS[cat].lower()}"):
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