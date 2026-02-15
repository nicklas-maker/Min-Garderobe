import streamlit as st
import json
import os
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from github import Github

# --- KONFIGURATION ---
KEY_FILE = "firestore_key.json"

# --- GITHUB SETUP (Sikker Version) ---
# Vi henter nu token fra .streamlit/secrets.toml via st.secrets
try:
    # Denne kommando kigger i din lokale secrets.toml fil
    GITHUB_TOKEN = st.secrets["github_token"]
    GITHUB_REPO_NAME = st.secrets["github_repo"]
except FileNotFoundError:
    st.error("⚠️ Mangler 'secrets.toml'! Du har glemt at oprette den hemmelige fil.")
    st.stop()
except KeyError:
    st.error("⚠️ Din secrets.toml fil er tom eller mangler 'github_token'/'github_repo'.")
    st.stop()

# 1. Forbind til Firebase (Kun database)
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(KEY_FILE)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Kunne ikke forbinde til Firebase. Har du husket 'firestore_key.json'? Fejl: {e}")
        st.stop()

db = firestore.client()

# --- AI PROMPT ---
AI_PROMPT = """ANALYSE INSTRUKTION:


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

st.set_page_config(page_title="Garderobe Admin (Sikker)", page_icon="🔒", layout="centered")

if 'form_key' not in st.session_state:
    st.session_state.form_key = 0

st.title("☁️ Garderobe Admin")
st.caption("Uploader billeder til GitHub (Sikkert) & data til Firestore")

if 'last_added' in st.session_state:
    st.toast(st.session_state.last_added, icon="✅")
    del st.session_state.last_added

# 0. HENT PROMPT
st.subheader("0. Hent AI Prompt")
st.markdown("Kopier teksten herunder ved at trykke på det lille **kopier-ikon** øverst til højre i boksen 👇")
st.code(AI_PROMPT, language="text")
st.markdown("🔗 **Genvej:** [Klik her for at åbne din Gemini AI Chat](https://gemini.google.com/gem/dfe5b48d941f)")
st.divider()

# 1. UPLOAD
st.subheader("1. Vælg Billede")
uploaded_file = st.file_uploader("Upload billede", type=["jpg", "png", "jpeg", "webp"], key=f"uploader_{st.session_state.form_key}")

if uploaded_file is not None:
    st.image(uploaded_file, caption="Preview", width=300)
    
    # 2. JSON
    st.subheader("2. Indsæt JSON fra AI")
    json_input = st.text_area(
        "JSON Data", 
        height=350, 
        placeholder='{\n  "category": "Top",\n  ...\n}',
        key=f"json_{st.session_state.form_key}"
    )

    # 3. GEM (GITHUB + FIRESTORE)
    if st.button("🚀 Gem i Skyen", type="primary"):
        if not json_input.strip():
            st.error("⚠️ Mangler JSON data!")
        else:
            try:
                # A. Valider JSON
                data = json.loads(json_input)
                
                with st.spinner("Uploader billede til GitHub..."):
                    # B. Upload billede til GITHUB (Bruger token fra secrets)
                    g = Github(GITHUB_TOKEN)
                    repo = g.get_repo(GITHUB_REPO_NAME)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    original_ext = uploaded_file.name.split(".")[-1]
                    filename = f"img_{timestamp}.{original_ext}"
                    path_in_repo = f"img/{filename}"
                    
                    # Opret filen på GitHub
                    commit_message = f"Tilføjet {data.get('display_name', 'nyt tøj')} via Admin"
                    repo.create_file(path_in_repo, commit_message, uploaded_file.getvalue())
                    
                    # C. Konstruer RAW URL (Direkte link til billedet)
                    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO_NAME}/main/{path_in_repo}"
                
                # D. Gem data i FIRESTORE (Med linket)
                doc_ref = db.collection("wardrobe").document()
                
                item_entry = {
                    "filename": filename,
                    "image_path": raw_url, 
                    "analysis": data,
                    "created_at": firestore.SERVER_TIMESTAMP
                }
                
                doc_ref.set(item_entry)
                
                # E. Reset
                st.session_state.last_added = f"Gemt sikkert! {data.get('display_name', 'Tøjet')}"
                st.session_state.form_key += 1 
                st.rerun()
                
            except json.JSONDecodeError as e:
                st.error(f"Fejl i JSON: {e}")
            except Exception as e:
                st.error(f"Fejl: {str(e)}")

# --- DATABASE STATUS ---
st.divider()
try:
    docs = db.collection("wardrobe").stream()
    count = sum(1 for _ in docs)
    st.info(f"Antal stykker tøj i Cloud Database: **{count}**")
except:
    pass