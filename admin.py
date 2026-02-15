import streamlit as st
import json
import os
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from github import Github
import google.generativeai as genai
from PIL import Image

# --- KONFIGURATION ---
KEY_FILE = "firestore_key.json"

# --- SETUP AF HEMMELIGHEDER (Secrets) ---
try:
    # 1. GitHub Setup
    GITHUB_TOKEN = st.secrets["github_token"]
    GITHUB_REPO_NAME = st.secrets["github_repo"]
    
    # 2. Google Gemini Setup
    GOOGLE_API_KEY = st.secrets["google_api_key"]
    genai.configure(api_key=GOOGLE_API_KEY)
    
except FileNotFoundError:
    st.error("⚠️ Mangler 'secrets.toml'! Husk at tilføje både GitHub og Google API Keys.")
    st.stop()
except KeyError as e:
    st.error(f"⚠️ Din secrets.toml mangler nøglen: {e}")
    st.stop()

# --- FIREBASE SETUP ---
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(KEY_FILE)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Kunne ikke forbinde til Firebase. Fejl: {e}")
        st.stop()

db = firestore.client()

# --- AI PROMPT (Opdateret med nye farver og regler) ---
AI_PROMPT = """ANALYSE INSTRUKTION:

[VALGFRIT: Skriv evt. "Dette er overtøj" eller "Dette er en top" her for at hjælpe mig, hvis det er tvetydigt]

Du skal analysere det vedhæftede billede af et stykke herretøj.
Din opgave er at returnere struktureret JSON data. Du må IKKE opfinde dine egne værdier til de faste felter - du SKAL vælge fra listerne herunder.

1. IDENTIFIKATION:
- Hovedkategori: [Top, Bund, Sko, Strømper, Overtøj]
  * VIGTIGT: Hvis genstanden er en 'Overshirt', 'Cardigan', 'Zip-up' eller en kraftig skjorte beregnet til at have åben over en t-shirt (lag-på-lag), SKAL den kategoriseres som 'Overtøj', ikke 'Top'.
- Type: Vælg den mest præcise fra listen: [T-shirt, Polo, Skjorte, Strik, Sweatshirt, Vest, Jeans, Chinos, Habitbukser, Sweatpants, Shorts, Sneakers, Støvler, Pæne Sko, Loafers, Jakke, Frakke, Blazer, Cardigan, Overshirt, Dress, Sport, Uld].
- Display Navn: Generer et kort, beskrivende navn på dansk på max 4 ord (F.eks. "Olivengrøn Strik", "Mørkeblå Chinos").
- Primær Farve: Vælg den tætteste fra [Sort, Hvid, Creme, Grå, Navy, Blå, Beige, Brun, Grøn, Oliven, Rød, Bordeaux, Accent]
- Intensitet (Shade): [Lys, Mellem, Mørk]
- Sekundær Farve: Hvis ingen tydelig, skriv "Ingen". Ellers vælg fra samme liste.
- Mønster: [Solid, Struktur, Mønster]
- Materiale: Vælg det primære materiale: [Bomuld, Uld, Hør, Silke, Læder, Ruskind, Denim, Syntetisk, Canvas]
- Sæson: Vurder tøjets tykkelse/varme: [Sommer, Vinter, Helårs, Overgang]

2. MATCHING REGLER (Kompatibilitet):
Baseret på din viden om 'Heritage / Classic Menswear', lav lister over hvilke farver der passer til dette item. Inkludér både de sikre neutrale valg og karakteristiske accentfarver som Rød, så længe de overholder den tidløse æstetik.
- VIGTIGT: Sorter listerne! De absolut bedste/sikreste matches skal stå FØRST. Men inkludér både klassiske neutrale farver og dybe accentfarver (som f.eks. Rød/Bordeaux), der komplementerer stilen.
- Familie-regel: Hvis en farvefamilie generelt passer (f.eks. blå nuancer), så skriv BÅDE 'Blå' og 'Navy' på listen over matches, medmindre det er et specifikt clash.
- Tone-i-Tone: Husk også at inkludere 'tone-i-tone' matches, men sørg for at anbefale kontrast i intensitet (f.eks. Mørk Top til Lyse Bukser).
- Brug KUN farvenavnene fra listen ovenfor.

3. OUTPUT FORMAT (JSON):
{
  "category": "String",
  "type": "String",
  "display_name": "String",
  "primary_color": "String",
  "shade": "String",
  "secondary_color": "String",
  "pattern": "String",
  "material": "String",
  "season": "String",
  "compatibility": {
    "Top": ["Farve1", "Farve2"...],      // (Hvis item er Bund/Sko/Strømper/Overtøj)
    "Bund": ["Farve1", "Farve2"...],     // (Hvis item er Top/Sko/Strømper/Overtøj)
    "Sko": ["Farve1", "Farve2"...],      // (Hvis item er Top/Bund/Strømper/Overtøj)
    "Strømper": ["Farve1", "Farve2"...], // (Hvis item er Top/Bund/Sko/Overtøj)
    "Overtøj": ["Farve1", "Farve2"...]   // (Hvis item er Top/Bund/Sko/Strømper)
  }
}"""

st.set_page_config(page_title="Garderobe Admin (AI & Cloud)", page_icon="🤖", layout="centered")

if 'form_key' not in st.session_state:
    st.session_state.form_key = 0
if 'ai_result' not in st.session_state:
    st.session_state.ai_result = ""

st.title("🤖 Garderobe Admin")
st.caption("AI-indeksering med Gemini Pro • Billeder på GitHub • Data i Firestore")

if 'last_added' in st.session_state:
    st.toast(st.session_state.last_added, icon="✅")
    del st.session_state.last_added

# 1. UPLOAD
st.subheader("1. Vælg Billede")
uploaded_file = st.file_uploader("Upload billede", type=["jpg", "png", "jpeg", "webp"], key=f"uploader_{st.session_state.form_key}")

if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption="Preview", width=300)
    
    # 2. AI ANALYSE KNAP
    st.subheader("2. Analyser med AI")
    
    if st.button("✨ Analyser Billede (Gemini Pro)", type="secondary"):
        with st.spinner("Spørger stylisten..."):
            try:
                # Opsætning af modellen
                model = genai.GenerativeModel(
                    model_name="gemini-1.5-pro",
                    generation_config={
                        "temperature": 0,
                        "response_mime_type": "application/json"
                    }
                )
                
                # Send billede og prompt
                response = model.generate_content([AI_PROMPT, image])
                
                # Gem resultatet i session state så det vises i tekstfeltet
                st.session_state.ai_result = response.text
                st.rerun() # Genindlæs for at vise teksten
                
            except Exception as e:
                st.error(f"AI Fejl: {str(e)}")

    # 3. JSON RESULTAT (Kan redigeres)
    st.caption("Verificer data før du gemmer:")
    json_input = st.text_area(
        "JSON Data", 
        value=st.session_state.ai_result,
        height=400, 
        key=f"json_{st.session_state.form_key}"
    )

    # 4. GEM (GITHUB + FIRESTORE)
    if st.button("🚀 Gem i Skyen", type="primary"):
        if not json_input.strip():
            st.error("⚠️ Mangler data! Tryk på 'Analyser' først.")
        else:
            try:
                # A. Valider JSON
                data = json.loads(json_input)
                
                with st.spinner("Uploader til skyen..."):
                    # B. Upload billede til GITHUB
                    g = Github(GITHUB_TOKEN)
                    repo = g.get_repo(GITHUB_REPO_NAME)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    original_ext = uploaded_file.name.split(".")[-1]
                    filename = f"img_{timestamp}.{original_ext}"
                    path_in_repo = f"img/{filename}"
                    
                    commit_message = f"Tilføjet {data.get('display_name', 'nyt tøj')}"
                    # PyGithub kræver bytes eller string, getvalue() giver bytes
                    repo.create_file(path_in_repo, commit_message, uploaded_file.getvalue())
                    
                    # C. Konstruer RAW URL
                    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO_NAME}/main/{path_in_repo}"
                
                # D. Gem data i FIRESTORE
                doc_ref = db.collection("wardrobe").document()
                
                item_entry = {
                    "filename": filename,
                    "image_path": raw_url, 
                    "analysis": data,
                    "created_at": firestore.SERVER_TIMESTAMP
                }
                
                doc_ref.set(item_entry)
                
                # E. Reset
                st.session_state.last_added = f"Gemt! {data.get('display_name', 'Tøjet')}"
                st.session_state.form_key += 1 
                st.session_state.ai_result = "" # Nulstil AI tekst
                st.rerun()
                
            except json.JSONDecodeError as e:
                st.error(f"Fejl i JSON formatet: {e}")
            except Exception as e:
                st.error(f"System fejl: {str(e)}")

# --- DATABASE STATUS ---
st.divider()
try:
    docs = db.collection("wardrobe").stream()
    count = sum(1 for _ in docs)
    st.info(f"Antal stykker tøj i Cloud Database: **{count}**")
except:
    pass