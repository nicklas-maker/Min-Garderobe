import streamlit as st
import os
import json
import requests
import re
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

# Bonus for at være del af et tidligere godkendt outfit (trækkes fra scoren)
SUCCESS_BONUS = 2

# Straf for at genskabe et tidligere AFVIST outfit (lægges til scoren)
REJECTION_PENALTY = 10

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

def get_ai_feedback(outfit_items, candidates=None):
    """Sender billederne til Gemini for en 'Smagsdommer' vurdering (med eller uden kandidater)."""
    
    api_key = None
    if "google_api_key" in st.secrets:
        api_key = st.secrets["google_api_key"]
    
    if not api_key:
        return "⚠️ Mangler Google API Nøgle i Secrets."

    contents = []
    
    # 1. Tilføj Base Outfit
    has_base = len(outfit_items) > 0
    if has_base:
        if candidates:
            contents.append("=== BASE OUTFIT (FUNDAMENTET) ===")
        for item in outfit_items:
            img_url = item.get('image_path')
            category = item.get('analysis', {}).get('category', 'Ukendt')
            display_name = item.get('analysis', {}).get('display_name', '')
            
            if img_url and img_url.startswith('http'):
                img = load_image_from_url(img_url)
                if img:
                    contents.append(f"Valgt {category}: {display_name}. (Ignorer modellen og eventuelt andet tøj på dette specifikke billede).")
                    contents.append(img)
    
    # 2. Tilføj Kandidater (hvis nogen)
    if candidates:
        contents.append("=== KANDIDATER (VÆLG ÉN AF DISSE) ===")
        for item in candidates:
            img_url = item.get('image_path')
            category = item.get('analysis', {}).get('category', 'Ukendt')
            display_name = item.get('analysis', {}).get('display_name', '')
            item_id = item.get('id')
            
            if img_url and img_url.startswith('http'):
                img = load_image_from_url(img_url)
                if img:
                    contents.append(f"Kandidat ID: {item_id} | Kategori: {category} | Navn: {display_name}")
                    contents.append(img)

    if not contents:
        return "⚠️ Kunne ikke finde billeder at sende til AI."

    # 3. Dynamisk Prompt
    system_domain = """Du er en ærlig og direkte modeekspert. Dit domæne spænder over et spektrum fra 'Modern Heritage' (klassisk herremode, tekstur, jordfarver) til 'Maskulin smart-casual' (tidløs minimalisme, rene linjer).
Et outfit behøver IKKE at ramme begge stilarter på én gang. Din opgave er at vurdere, om tøjet fungerer som en harmonisk helhed."""

    if candidates:
        if has_base:
            system_instruction = f"""{system_domain}
Du har modtaget billeder af et 'Base Outfit' (Fundamentet) og nogle 'Kandidater'. Hver kandidat er tydeligt markeret med et 'Kandidat ID'.

Din opgave er to-delt:
TRIN 1: Vurder Base Outfittet. 
Er fundamentet i orden? Hvis delene i Base Outfittet i sig selv clasher fundamentalt, skal du stoppe her. Du må IKKE vælge en kandidat.
Returner i stedet præcist: '❌ FUNDAMENT AFVIST' efterfulgt af din brutalt ærlige begrundelse for, hvorfor basen ikke fungerer (hvad clasher?).

TRIN 2: Vælg Vinderen.
Hvis basen ER godkendt, skal du nu vurdere Kandidaterne. Vælg den af kandidaterne, der bedst komplementerer basen som en helhed.
* Hvis INGEN af kandidaterne passer acceptabelt til basen, skal du returnere præcist: '❌ INGEN VINDER' efterfulgt af en forklaring på, hvorfor de valgte kandidater ikke fungerer.
* Hvis du finder en vinder, SKAL du bruge præcis dette format med disse tre linjer:
✅ VINDER: [Kandidat ID]
BEGRUNDELSE_VALG: [En kort forklaring på, hvorfor netop denne kandidat vandt over de andre]
OUTFIT_BEDØMMELSE: [Skriv 1-2 sætninger, der UDELUKKENDE bedømmer det NYE samlede outfit (Base + Vinder). Denne tekst skal kunne læses for sig selv, som en generel anmeldelse af hele outfittet.]
"""
        else:
            system_instruction = f"""{system_domain}
Du har modtaget billeder af nogle 'Kandidater' til et outfit. Hver kandidat er tydeligt markeret med et 'Kandidat ID'.
Vælg den kandidat der er mest alsidig og stilfuld.
Returner præcist: '✅ VINDER: [Kandidat ID]' (du SKAL skrive det præcise ID fra teksten) efterfulgt af din begrundelse for valget.
"""
    else:
        system_instruction = f"""{system_domain}
Din opgave: Se på de vedhæftede billeder, som TIL SAMMEN udgør ét samlet outfit. Vurder udelukkende samspillet (helheden) mellem de dele, brugeren udtrykkeligt har valgt. Ignorer alt andet på billedet.
VIGTIGT OUTPUT KRAV: Du må KUN give ÉN samlet bedømmelse for hele outfittet.

Output format (Vær kort!):
1. Start med DOMMEN: Enten '✅ Godkendt' eller '⚠️ Justering anbefales'.
2. Giv KOMMENTAREN: Max 1-2 sætninger om hvorfor det virker, eller hvad der clasher.
3. LØSNINGEN (Kun ved fejl): Foreslå én ting der skal ændres for at redde outfittet."""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=contents,
            config={
                "system_instruction": system_instruction,
                "temperature": 0.3,
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
        
        hourly_feels = hourly['apparent_temperature']
        end_index = min(current_hour + 10, len(hourly_feels))
        next_10_hours = hourly_feels[current_hour:end_index]
        
        if next_10_hours:
            avg_10h = sum(next_10_hours) / len(next_10_hours)
        else:
            avg_10h = daily['temperature_2m_max'][0]

        feels_like_now = hourly_feels[current_hour]

        return {
            "temp_max": daily['temperature_2m_max'][0],
            "avg_feels_like_10h": avg_10h,
            "feels_like_now": feels_like_now,
            "rain_mm": daily['precipitation_sum'][0],
            "wind_kph": daily['wind_speed_10m_max'][0]
        }
    except Exception as e:
        print(f"Vejrfejl (ignoreret i UI): {e}") 
        return None

# --- HISTORIK & STATISTIK FUNKTIONER ---

def update_item_stats(item_id, current_avg_temp):
    try:
        doc_ref = db.collection("wardrobe").document(item_id)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            old_count = data.get('usage_count', 0)
            old_avg = data.get('avg_temp', 0)
            
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

def get_global_style_stats():
    try:
        doc = db.collection("stats").document("style_stats").get()
        if doc.exists:
            return doc.to_dict().get('average_score', 0.0)
    except:
        pass
    return None

def update_global_style_stats(new_score):
    try:
        doc_ref = db.collection("stats").document("style_stats")
        doc = doc_ref.get()
        
        if doc.exists:
            data = doc.to_dict()
            old_avg = data.get('average_score', 0.0)
            count = data.get('count', 0)
            
            new_avg = ((old_avg * count) + new_score) / (count + 1)
            new_count = count + 1
        else:
            new_avg = new_score
            new_count = 1
            
        doc_ref.set({
            'average_score': new_avg,
            'count': new_count,
            'last_updated': firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Fejl ved opdatering af historisk score: {e}")

def save_outfit_to_history(outfit_items, weather_data, location, style_score):
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
        "style_score": style_score,
        "outfit": outfit_summary
    }
    db.collection("history").add(doc_data)
    
    current_avg_temp = weather_data.get('avg_feels_like_10h')
    if current_avg_temp is not None:
        for item in outfit_items:
            update_item_stats(item['id'], current_avg_temp)

# --- OUTFIT MEMORY, KAMP CACHE & AI OVERRIDES ---

def get_outfit_id(outfit_items):
    """Laver et unikt ID for en kombination af tøj."""
    ids = sorted([item['id'] for item in outfit_items])
    return "_".join(ids)

def save_approved_outfit(outfit_items, comment):
    try:
        oid = get_outfit_id(outfit_items)
        db.collection("approved_outfits").document(oid).set({
            "comment": comment,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Fejl ved gemning af godkendt outfit: {e}")

def save_rejected_outfit(outfit_items, comment):
    try:
        oid = get_outfit_id(outfit_items)
        db.collection("rejected_outfits").document(oid).set({
            "comment": comment,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Fejl ved gemning af afvist outfit: {e}")

@st.cache_data(ttl=600)
def load_outfit_feedback_cache():
    approved = {}
    rejected = {}
    approved_sets = []
    try:
        docs_app = db.collection("approved_outfits").stream()
        for doc in docs_app:
            approved[doc.id] = doc.to_dict().get('comment', '')
            if doc.id:
                ids = set(doc.id.split('_'))
                approved_sets.append(ids)
            
        docs_rej = db.collection("rejected_outfits").stream()
        for doc in docs_rej:
            rejected[doc.id] = doc.to_dict().get('comment', '')
    except:
        pass
    return approved, rejected, approved_sets

def save_ai_override(base_outfit_items, category, winner_id, new_score):
    """Gemmer den overskrevne score og beholder alle vindere."""
    try:
        base_id = get_outfit_id(base_outfit_items) if base_outfit_items else "empty"
        # BEMÆRK: ID'et indeholder nu winner_id, så vi ikke overskriver gamle vindere!
        doc_id = f"{base_id}_{category}_{winner_id}"
        db.collection("ai_score_overrides").document(doc_id).set({
            "base_outfit": base_id,
            "category": category,
            "winner_id": winner_id,
            "new_score": new_score,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Fejl ved gemning af AI override: {e}")

@st.cache_data(ttl=600)
def load_ai_overrides():
    """Henter alle vindere og grupperer dem efter base_outfit og kategori."""
    overrides = {}
    try:
        docs = db.collection("ai_score_overrides").stream()
        for doc in docs:
            data = doc.to_dict()
            base_id = data.get("base_outfit")
            cat = data.get("category")
            w_id = data.get("winner_id")
            n_score = data.get("new_score")
            
            if not base_id or not cat or not w_id:
                continue
                
            key = f"{base_id}_{cat}"
            if key not in overrides:
                overrides[key] = {}
            # Tilføj vinderen til listen for denne specifikke tøjkombination
            overrides[key][w_id] = n_score
    except:
        pass
    return overrides

def get_match_cache_id(base_outfit_items, category, cand_dicts):
    """Laver et unikt ID for en specifik kamp mellem kandidater."""
    base_id = get_outfit_id(base_outfit_items) if base_outfit_items else "empty"
    cand_ids = "_".join(sorted([c['id'] for c in cand_dicts]))
    return f"{base_id}_{category}_{cand_ids}"

def get_cached_match(match_id):
    """Henter resultatet af en tidligere udkæmpet kamp mellem specifikke kandidater."""
    try:
        doc = db.collection("ai_match_cache").document(match_id).get()
        if doc.exists:
            return doc.to_dict().get("raw_feedback")
    except:
        pass
    return None

def save_match_cache(match_id, raw_feedback):
    """Gemmer AI's dom af kampen, så vi slipper for at bruge et API-kald igen."""
    try:
        db.collection("ai_match_cache").document(match_id).set({
            "raw_feedback": raw_feedback,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        print(f"Fejl ved gemning af match cache: {e}")

# --- SMART SCORE LOGIK ---

def calculate_match_score(target_color, allowed_list):
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

def calculate_shade_bonus(outfit_items):
    shade_values = {"Lys": 1, "Mellem": 2, "Mørk": 3}
    shades = {}
    
    for item in outfit_items:
        cat = item['analysis'].get('category')
        shade_str = item['analysis'].get('shade', 'Mellem')
        shades[cat] = shade_values.get(shade_str, 2)
        
    bonus = 0
    if 'Top' in shades and 'Bund' in shades:
        bonus += abs(shades['Top'] - shades['Bund'])
    if 'Top' in shades and 'Overtøj' in shades:
        bonus += abs(shades['Top'] - shades['Overtøj'])
        
    return bonus

def calculate_outfit_style_score(outfit_items):
    if len(outfit_items) < 2:
        return 0.0
    
    _, _, approved_sets = load_outfit_feedback_cache()
    outfit_ids = set([item['id'] for item in outfit_items])
    is_outfit_approved = False
    for a_set in approved_sets:
        if outfit_ids.issubset(a_set):
            is_outfit_approved = True
            break
    
    total_score = 0
    pair_count = 0
    items_list = list(outfit_items)
    
    for i in range(len(items_list)):
        for j in range(i + 1, len(items_list)):
            item1 = items_list[i]
            item2 = items_list[j]
            
            data1 = item1['analysis']
            data2 = item2['analysis']
            
            allowed1 = data1['compatibility'].get(data2['category'], [])
            allowed2 = data2['compatibility'].get(data1['category'], [])
            
            score1, _ = calculate_match_score(data2['primary_color'], allowed1)
            score2, _ = calculate_match_score(data1['primary_color'], allowed2)
            
            if score1 is not None and score2 is not None:
                total_score += (score1 + score2)
            else:
                if is_outfit_approved:
                    total_score += 3
                else:
                    total_score += 10
            
            pair_count += 1
                
    if pair_count == 0:
        return 0.0
        
    base_avg = total_score / pair_count
    shade_bonus = calculate_shade_bonus(outfit_items)
    
    return round(base_avg - shade_bonus, 1)

def calculate_smart_score(item, color_score, weather_data):
    weather_penalty = 0
    item_avg = item.get('avg_temp')
    current_avg = weather_data.get('avg_feels_like_10h')
    
    if item_avg is not None and current_avg is not None:
        diff = abs(current_avg - item_avg)
        weather_penalty = diff * TEMP_PENALTY_FACTOR
    
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
    .style-score-box { background-color: #fff9c4; padding: 15px; border-radius: 10px; margin: 20px 0; border-left: 5px solid #fbc02d; color: #444; }
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

    st.markdown("---")
    with st.expander("📖 Ikoner", expanded=False):
        st.markdown("""
        <small>
        👑 : Forsvarende Mester (Bedste AI-score)<br>
        ⭐ : Perfekt<br>
        1️⃣ : Godt<br>
        2️⃣ : Fint<br>
        3️⃣ : Acceptabelt<br>
        🚫 : Inkompatibel farve<br>
        ⚠️ : Blindgyde<br>
        ❗️ : Synonym farve<br>
        ✅ : Godkendt af Stylist<br>
        ❌ : Afvist af Stylist
        </small>
        """, unsafe_allow_html=True)

st.title("Dagens Outfit")

# Håndter og vis AI-beskeder fra forrige "Spørg Stylist" / "Bedøm Outfit" handling
if "ai_msg" in st.session_state:
    msg = st.session_state.ai_msg
    if msg["type"] == "success":
        st.success(f"**👔 Stylisten siger:**\n\n{msg['text']}")
    elif msg["type"] == "error":
        st.error(f"**⚠️ Stylisten siger:**\n\n{msg['text']}")
    elif msg["type"] == "warning":
        st.warning(f"**Stylisten siger:**\n\n{msg['text']}")
    else:
        st.info(f"**Stylisten siger:**\n\n{msg['text']}")
    del st.session_state.ai_msg

wardrobe = load_wardrobe()
if not wardrobe:
    st.info("Databasen er tom. Tilføj tøj via admin.py.")
    st.stop()

if 'outfit' not in st.session_state:
    st.session_state.outfit = {} 

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
    st.success("🎉 Dit outfit er komplet!")

# --- STYLE SCORE & KNAPPER ---
if st.session_state.outfit:
    approved_cache, rejected_cache, _ = load_outfit_feedback_cache()
    current_outfit_id = get_outfit_id(st.session_state.outfit.values())
    
    is_approved_before = current_outfit_id in approved_cache
    is_rejected_before = current_outfit_id in rejected_cache
    
    style_score = calculate_outfit_style_score(st.session_state.outfit.values())
    
    hist_score = get_global_style_stats()
    hist_text = f"Historisk Stil Score: {hist_score:.1f}" if hist_score is not None else "Historisk Stil Score: --"
    
    score_display = f"<b>Dagens Stil Score: {style_score}</b>"
    if is_approved_before:
        score_display += " ✅"
    elif is_rejected_before:
        score_display += " ❌"
    
    score_display += f" &nbsp;&nbsp;|&nbsp;&nbsp; {hist_text}"
    
    st.markdown(f"""
    <div class="style-score-box">
        {score_display}
    </div>
    """, unsafe_allow_html=True)

    if is_approved_before:
        saved_comment = approved_cache[current_outfit_id]
        st.success(f"**Tidligere Bedømmelse (Godkendt):**\n\n{saved_comment}")
    elif is_rejected_before:
        saved_comment = rejected_cache[current_outfit_id]
        st.warning(f"**Tidligere Bedømmelse (Ikke Godkendt🤔):**\n\n{saved_comment}")

    btn_col1, btn_col2 = st.columns(2)
    
    with btn_col1:
        if st.button("🔮 Bedøm Outfit", type="secondary", use_container_width=True):
            
            # Find ud af, om brugeren har sat flueben ved nogen kandidater
            cand_dicts = []
            cand_cat = None
            for item in wardrobe:
                cat = item['analysis']['category']
                if st.session_state.get(f"cand_{cat}_{item['id']}", False):
                    cand_dicts.append(item)
                    cand_cat = cat
                    
            base_outfit_items = list(st.session_state.outfit.values())
            
            if len(cand_dicts) > 0:
                # --- KANDIDAT-TILSTAND ---
                match_id = get_match_cache_id(base_outfit_items, cand_cat, cand_dicts)
                raw_feedback = get_cached_match(match_id)
                
                if raw_feedback:
                    st.toast("Genbruger tidligere AI-vurdering for præcis denne kamp!", icon="⚡")
                else:
                    with st.spinner(f"Stylisten vurderer dit fundament og de {len(cand_dicts)} kandidater..."):
                        raw_feedback = get_ai_feedback(base_outfit_items, cand_dicts)
                        # Gem resultatet, hvis det ikke var en fejl
                        if "AI Fejl:" not in raw_feedback and "⚠️" not in raw_feedback:
                            save_match_cache(match_id, raw_feedback)

                if "❌ FUNDAMENT AFVIST" in raw_feedback.upper():
                    display_feedback = raw_feedback
                    for cand in cand_dicts:
                        c_name = cand['analysis'].get('display_name', 'Ukendt')
                        display_feedback = display_feedback.replace(cand['id'], c_name)
                    # Gemmer KUN base_outfit_items
                    save_rejected_outfit(base_outfit_items, display_feedback)
                    load_outfit_feedback_cache.clear()
                    st.session_state.ai_msg = {"type": "error", "text": display_feedback}
                    
                elif "❌ INGEN VINDER" in raw_feedback.upper():
                    display_feedback = raw_feedback
                    for cand in cand_dicts:
                        c_name = cand['analysis'].get('display_name', 'Ukendt')
                        display_feedback = display_feedback.replace(cand['id'], c_name)
                    save_approved_outfit(base_outfit_items, "Godkendt base, men ingen kandidater passede.")
                    load_outfit_feedback_cache.clear()
                    st.session_state.ai_msg = {"type": "warning", "text": display_feedback}
                    
                elif "✅ VINDER:" in raw_feedback.upper() or "✅" in raw_feedback:
                    winner_id = None
                    winner_item = None
                    
                    match = re.search(r'✅\s*VINDER:\s*([A-Za-z0-9_-]+)', raw_feedback, re.IGNORECASE)
                    if match:
                        extracted_id = match.group(1).strip()
                        for cand in cand_dicts:
                            if cand['id'] == extracted_id:
                                winner_id = cand['id']
                                winner_item = cand
                                break
                    
                    # Fallback (hvis regex fejler)
                    if not winner_item:
                        for cand in cand_dicts:
                            if f"VINDER: {cand['id']}" in raw_feedback:
                                winner_id = cand['id']
                                winner_item = cand
                                break
                    
                    if winner_item:
                        # 1. Træk begrundelse og den samlede dom ud
                        begrundelse_valg = ""
                        outfit_bedommelse = ""
                        
                        match_begrundelse = re.search(r'BEGRUNDELSE_VALG:\s*(.*?)(?=OUTFIT_BEDØMMELSE:|$)', raw_feedback, re.DOTALL | re.IGNORECASE)
                        if match_begrundelse:
                            begrundelse_valg = match_begrundelse.group(1).strip()
                            
                        match_bedommelse = re.search(r'OUTFIT_BEDØMMELSE:\s*(.*)', raw_feedback, re.DOTALL | re.IGNORECASE)
                        if match_bedommelse:
                            outfit_bedommelse = match_bedommelse.group(1).strip()
                        
                        if not outfit_bedommelse: # Fallback hvis AI laver kludder
                            outfit_bedommelse = raw_feedback
                        
                        # 2. Udskift IDs med rigtige navne i teksterne
                        for cand in cand_dicts:
                            c_name = cand['analysis'].get('display_name', 'Ukendt')
                            c_shade = cand['analysis'].get('shade', '')
                            c_color = cand['analysis'].get('primary_color', '')
                            full_name = f"{c_name} ({c_shade} {c_color})"
                            
                            begrundelse_valg = begrundelse_valg.replace(cand['id'], full_name)
                            outfit_bedommelse = outfit_bedommelse.replace(cand['id'], full_name)

                        # 3. Anvend den smartere -1 point override regel (Løsning 2)
                        lowest_score = float('inf')
                        winner_current_score = 0
                        
                        # Indlæs overskrevne scores for at lade vinderen arve ud fra NUVÆRENDE point
                        ai_overrides = load_ai_overrides()
                        base_outfit_id = get_outfit_id(base_outfit_items) if base_outfit_items else "empty"
                        override_key = f"{base_outfit_id}_{cand_cat}"
                        cat_overrides = ai_overrides.get(override_key, {})
                        
                        for cand in cand_dicts:
                            # Udregn den rene score først
                            pure_score = calculate_outfit_style_score(base_outfit_items + [cand])
                            
                            # Tjek om kandidaten allerede har en override-score for dette base-outfit
                            cand_current_score = cat_overrides.get(cand['id'], pure_score)
                            
                            if cand['id'] == winner_id:
                                winner_current_score = cand_current_score
                                
                            if cand_current_score < lowest_score:
                                lowest_score = cand_current_score
                        
                        # Hvis vinderen har en DÅRLIGERE (højere) nuværende score end den bedste taber,
                        # så arver vinderen taberens score minus 1 point!
                        if winner_current_score > lowest_score:
                            new_score = lowest_score - 1
                            save_ai_override(base_outfit_items, cand_cat, winner_id, new_score)
                            load_ai_overrides.clear()
                        
                        # 4. Tilføj vinderen til UI
                        st.session_state.outfit[cand_cat] = winner_item
                        
                        # 5. GEM KUN DEN RENE BEDØMMELSE I DATABASEN (Samlet Outfit)
                        combined_outfit = base_outfit_items + [winner_item]
                        save_approved_outfit(combined_outfit, outfit_bedommelse)
                        load_outfit_feedback_cache.clear()
                        
                        # 6. Lav pæn besked til brugeren med begge dele
                        display_msg = f"**Hvorfor den vandt:** {begrundelse_valg}\n\n**Samlet bedømmelse:** {outfit_bedommelse}"
                        st.session_state.ai_msg = {"type": "success", "text": display_msg}
                        
                    else:
                        st.session_state.ai_msg = {"type": "warning", "text": f"Kunne ikke finde vinder-ID'et i svaret:\n\n{raw_feedback}"}
                
                # Fjern flueben, så de ikke hænger fast til næste gang
                for cand in cand_dicts:
                    st.session_state[f"cand_{cand_cat}_{cand['id']}"] = False
                
                st.rerun()
            else:
                # --- STANDARD-TILSTAND (Ingen kandidater valgt) ---
                with st.spinner("Stylisten kigger på dit tøj..."):
                    feedback = get_ai_feedback(base_outfit_items)
                    
                    if "✅" in feedback:
                        save_approved_outfit(base_outfit_items, feedback)
                        st.session_state.ai_msg = {"type": "success", "text": feedback}
                    else:
                        save_rejected_outfit(base_outfit_items, feedback)
                        st.session_state.ai_msg = {"type": "info", "text": feedback}
                    
                    load_outfit_feedback_cache.clear()
                    st.rerun()

    with btn_col2:
        if st.button("✅ Gem & Bær", type="primary", use_container_width=True):
            if weather_data:
                with st.spinner("Gemmer og opdaterer tøj-statistik..."):
                    save_outfit_to_history(list(st.session_state.outfit.values()), weather_data, city, style_score)
                    update_global_style_stats(style_score)
                    load_wardrobe.clear()
                    
                st.toast(f"Gemt! Din score på {style_score} er nu en del af historikken.", icon="📈")
                st.rerun()
            else:
                st.error("Kan ikke gemme uden vejrdata. Prøv at indtaste din by igen i sidebaren.")

if missing_cats:
    st.subheader("Vælg næste del:")
    tabs = st.tabs([CATEGORY_LABELS[c] for c in missing_cats])
    
    approved_cache, rejected_cache, approved_sets = load_outfit_feedback_cache()
    ai_overrides = load_ai_overrides()
    
    for i, cat in enumerate(missing_cats):
        with tabs[i]:
            all_items = get_items_by_category(wardrobe, cat)
            valid_items_with_score = []
            current_selection_list = list(st.session_state.outfit.values())
            
            current_ids = [item['id'] for item in current_selection_list]
            base_outfit_id = get_outfit_id(current_selection_list) if current_selection_list else "empty"
            
            # Find alle overrides og udnævn den forsvarende mester
            override_key = f"{base_outfit_id}_{cat}"
            cat_overrides = ai_overrides.get(override_key, {})
            
            champion_id = None
            if cat_overrides:
                # Mesteren er den med det absolut laveste pointtal (værdi) for denne base/kategori
                champion_id = min(cat_overrides, key=cat_overrides.get)

            # 1. Beregninger
            for item in all_items:
                is_valid, color_score, is_synonym = check_compatibility_basic(item, current_selection_list)
                _, weather_penalty = calculate_smart_score(item, color_score, weather_data)
                
                temp_outfit = current_selection_list + [item]
                
                # Den oprindelige viste score (baseret rent på stil)
                projected_style_score = calculate_outfit_style_score(temp_outfit)
                
                candidate_set = set(current_ids + [item['id']])
                
                is_part_of_success = False
                for a_set in approved_sets:
                    if candidate_set.issubset(a_set):
                        is_part_of_success = True
                        break
                
                cand_id_list = sorted(list(candidate_set))
                cand_id_str = "_".join(cand_id_list)
                is_rejected_exact = cand_id_str in rejected_cache

                is_dead_end = False
                if st.session_state.outfit:
                    is_dead_end = check_dead_end(item, current_selection_list, wardrobe)
                
                # Hvis genstanden er en af de gemte vindere for dette outfit, overskriv dens score!
                if item['id'] in cat_overrides:
                    projected_style_score = float(cat_overrides[item['id']])
                
                # Tjek om vi er the reigning champion
                is_champion = False
                if champion_id and item['id'] == champion_id:
                    is_champion = True
                
                # Sortering er nu defineret som: Synlig Pointscore + Vejrpoint
                smart_score = projected_style_score + weather_penalty
                
                # Tilføj andre bonus/straf til den endelige sorteringsscore
                is_strict_incompatible = False
                if not is_valid:
                    if is_part_of_success:
                        smart_score += 3 
                    else:
                        smart_score += 1000
                        is_strict_incompatible = True
                        
                if is_part_of_success:
                    smart_score -= SUCCESS_BONUS
                
                if is_rejected_exact:
                    smart_score += REJECTION_PENALTY
                    
                valid_items_with_score.append((smart_score, item, color_score, weather_penalty, is_synonym, is_part_of_success, is_rejected_exact, is_dead_end, projected_style_score, is_strict_incompatible, is_champion))
            
            valid_items_with_score.sort(key=lambda x: x[0])
            
            if not valid_items_with_score:
                st.error(f"Ingen {CATEGORY_LABELS[cat].lower()} tilgængelig!")
            else:
                for idx, (smart_score, item, color_score, penalty, is_synonym, is_part_of_success, is_rejected_exact, is_dead_end, projected_style_score, is_strict_incompatible, is_champion) in enumerate(valid_items_with_score):
                    if idx % 3 == 0:
                        img_cols = st.columns(3)
                    
                    with img_cols[idx % 3]:
                        st.image(item['image_path'], use_container_width=True)
                        data = item['analysis']
                        name = data['display_name']
                        shade_str = f"({data.get('shade', 'Mellem')} {data.get('primary_color', '')})"
                        
                        label_text = f"{name}"
                        if is_synonym:
                            label_text += " ❗️"
                        
                        num_existing = len(current_selection_list)
                        if num_existing > 0:
                            label_text += f"\n{shade_str} {projected_style_score:.1f}"
                        else:
                            label_text += f"\n{shade_str} 0.0"
                        
                        # --- IKON LOGIK ---
                        icon_prefix = ""
                        if is_champion: icon_prefix += "👑 "
                        if is_strict_incompatible: icon_prefix += "🚫 "
                        if is_dead_end: icon_prefix += "⚠️ "
                        
                        if is_part_of_success:
                            icon_prefix += "✅ "
                        elif is_rejected_exact:
                            icon_prefix += "❌ "
                        
                        if not is_strict_incompatible and not is_champion:
                            if color_score == 0: icon_prefix += "⭐ "      
                            elif color_score == 1: icon_prefix += "1️⃣ "     
                            elif 2 <= color_score <= 3: icon_prefix += "2️⃣ "     
                            elif 4 <= color_score <= 5: icon_prefix += "3️⃣ "     
                        
                        label_text = icon_prefix + label_text
                        
                        if st.button(label_text, key=f"add_{item['id']}"):
                            if is_strict_incompatible:
                                st.toast("Advarsel: Inkompatibel farve valgt!", icon="🚫")
                            if is_dead_end:
                                st.toast(f"Blindgyde advarsel!", icon="⚠️")
                            st.session_state.outfit[cat] = item
                            st.rerun()
                            
                        # Afkrydsningsboks til "Kandidat" systemet (Vises kun hvis der er en base at bygge på)
                        if len(current_selection_list) > 0:
                            st.checkbox("Vælg som kandidat", key=f"cand_{cat}_{item['id']}")
            
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
                        color_scores = []
                        for color in potential_colors:
                            total_score = 0
                            for outfit_item in current_items:
                                allowed_list = outfit_item['analysis']['compatibility'].get(cat, [])
                                if color in allowed_list:
                                    total_score += allowed_list.index(color)
                            color_scores.append((color, total_score))
                        
                        color_scores.sort(key=lambda x: x[1])
                        
                        st.write("Disse farver passer:")
                        st.markdown(" ".join([f"`{color} ({score})`" for color, score in color_scores]))
                    else:
                        st.warning("Ingen farve passer!")