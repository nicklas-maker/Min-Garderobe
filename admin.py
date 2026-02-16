import streamlit as st
import json
import os
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from github import Github
from google import genai
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

# --- AI PROMPT (Nu som System Message med din nye Persona) ---
AI_PROMPT = """ROLLE & PERSONA:
Du er en ekspert i 'Modern Heritage' og klassisk herremode (ofte kaldet 'Grandpa Core' eller 'Ivy Style'). Du elsker tekstur, lag-på-lag, og jordfarver. Din stil er tidløs og hyggelig, men altid velklædt. Du foretrækker harmoni frem for vilde kontraster. Du er bosat i Danmark, men inspireres af steder som Wall Street og Norditalien, særligt i perioden imellem 1950'erne og 1980'erne.

ANALYSE INSTRUKTION:

FOKUS PÅ HOVEDGENSTANDEN:
Billedet viser ofte en model, der bærer flere stykker tøj (f.eks. bukser sammen med sko og trøje).
Din opgave er at identificere og analysere KUN DEN PRIMÆRE GENSTAND.
- Identificer fokus: Hvilken genstand er central, fylder mest eller er tydeligst belyst?
- Ignorer kontekst: Hvis billedet fokuserer på bukser, skal du fuldstændig ignorere skoene og overdelen modellen har på.
- Ignorer krop: Se bort fra modellens hud, hår og positur.
- Hvis du er i tvivl, vælg den genstand der udgør den største del af billedet.

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
Baseret på din viden om 'Modern Heritage', lav lister over hvilke farver der passer til dette item. Inkludér både de sikre neutrale valg og karakteristiske accentfarver som Rød, så længe de overholder den tidløse æstetik.
- VIGTIGT: Sorter listerne! De absolut bedste matches skal stå FØRST. Men inkludér både klassiske neutrale farver og dybe accentfarver (som f.eks. Rød/Bordeaux), der komplementerer stilen samt sikre matches.
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
st.subheader("1. Vælg Billeder")
uploaded_files = st.file_uploader(
    "Upload billeder (Du kan vælge op til 2 - kun det første gemmes)", 
    type=["jpg", "png", "jpeg", "webp"], 
    key=f"uploader_{st.session_state.form_key}",
    accept_multiple_files=True
)

if uploaded_files:
    # Begræns til 2 billeder
    files_to_process = uploaded_files[:2]
    
    # Hent og vis previews
    cols = st.columns(len(files_to_process))
    pil_images = []
    
    for i, file in enumerate(files_to_process):
        image = Image.open(file)
        pil_images.append(image)
        with cols[i]:
            caption = "Hovedbillede (Gemmes)" if i == 0 else "Ekstra (Kun til analyse)"
            st.image(image, caption=caption, use_container_width=True)
    
    # 2. AI ANALYSE KNAP
    st.subheader("2. Analyser med AI")
    
    if st.button("✨ Analyser (2x Ensemble)", type="secondary"):
        with st.spinner("Kører dobbelt-analyse for at fange alle matches..."):
            # Opsætning af klienten
            client = genai.Client(api_key=GOOGLE_API_KEY)
            
            try:
                # --- KØRSEL 1: Den strenge (Base) ---
                # Temp 0 for maksimal præcision
                response1 = client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=pil_images, # User Message: Kun billederne
                    config={
                        "temperature": 0,
                        "response_mime_type": "application/json",
                        "system_instruction": AI_PROMPT
                    }
                )
                data1 = json.loads(response1.text)

                # --- KØRSEL 2: Den kreative (Supplement) ---
                # Temp 0.4 for at finde alternativer vi måske missede
                response2 = client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=pil_images,
                    config={
                        "temperature": 0.2,
                        "response_mime_type": "application/json",
                        "system_instruction": AI_PROMPT
                    }
                )
                data2 = json.loads(response2.text)

                # --- FLETNING (Ensemble Logic) ---
                # Vi starter med data1 som fundament
                merged_data = data1.copy()
                comp1 = merged_data.get("compatibility", {})
                comp2 = data2.get("compatibility", {})

                # Gennemgå alle kategorier og flet listerne
                for category in ["Top", "Bund", "Sko", "Strømper", "Overtøj"]:
                    list1 = comp1.get(category, [])
                    list2 = comp2.get(category, [])
                    
                    # Bevar rækkefølgen fra list1, men tilføj NYE ting fra list2 i bunden
                    existing_items = set(list1)
                    for item in list2:
                        if item not in existing_items:
                            list1.append(item) # Tilføj til sidst (lavere rank)
                            existing_items.add(item)
                    
                    comp1[category] = list1
                
                merged_data["compatibility"] = comp1
                
                # Konverter tilbage til tekst for visning
                final_json_text = json.dumps(merged_data, indent=2, ensure_ascii=False)

                # Opdater UI
                text_area_key = f"json_{st.session_state.form_key}"
                st.session_state[text_area_key] = final_json_text
                st.session_state.ai_result = final_json_text
                
                st.rerun()
                
            except Exception as e:
                st.error(f"AI Fejl: {str(e)}")
                # Debugging info hvis det går galt
                try:
                    models_iter = client.models.list()
                    model_names = [m.name for m in models_iter if "gemini" in m.name]
                    # st.code("\n".join(model_names)) # Udkommenteret for ikke at støje
                except:
                    pass

    # 3. JSON RESULTAT (Kan redigeres)
    st.caption("Verificer data før du gemmer:")
    
    # --- RETTELSE: Undgå 'widget created with default value' advarsel ---
    # Vi tjekker om nøglen findes i session state. Hvis ikke, sætter vi den til vores 'ai_result' (eller tom).
    # Derefter fjerner vi 'value=' parameteren fra selve widgeten.
    widget_key = f"json_{st.session_state.form_key}"
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state.ai_result

    json_input = st.text_area(
        "JSON Data", 
        height=400, 
        key=widget_key
    )

    # 4. GEM (GITHUB + FIRESTORE)
    if st.button("🚀 Gem i Skyen", type="primary"):
        if not json_input.strip():
            st.error("⚠️ Mangler data! Tryk på 'Analyser' først.")
        else:
            try:
                # A. Valider JSON
                data = json.loads(json_input)
                
                # Hent hovedbilledet (det første)
                main_file = files_to_process[0]
                
                with st.spinner("Uploader til skyen..."):
                    # B. Upload billede til GITHUB
                    g = Github(GITHUB_TOKEN)
                    repo = g.get_repo(GITHUB_REPO_NAME)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    original_ext = main_file.name.split(".")[-1]
                    filename = f"img_{timestamp}.{original_ext}"
                    path_in_repo = f"img/{filename}"
                    
                    commit_message = f"Tilføjet {data.get('display_name', 'nyt tøj')}"
                    # PyGithub kræver bytes eller string, getvalue() giver bytes
                    repo.create_file(path_in_repo, commit_message, main_file.getvalue())
                    
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
                st.session_state.ai_result = "" 
                st.rerun()
                
            except json.JSONDecodeError as e:
                st.error(f"Fejl i JSON formatet: {e}")
            except Exception as e:
                st.error(f"System fejl: {str(e)}")

# --- DATABASE STATUS & DOWNLOAD ---
st.divider()
try:
    docs = db.collection("wardrobe").stream()
    all_items = []
    for doc in docs:
        item = doc.to_dict()
        item['firestore_id'] = doc.id 
        all_items.append(item)
    
    count = len(all_items)
    st.info(f"Antal stykker tøj i Cloud Database: **{count}**")
    
    if count > 0:
        json_string = json.dumps(all_items, indent=2, ensure_ascii=False)
        st.download_button(
            label="📥 Download hele databasen (JSON)",
            data=json_string,
            file_name="wardrobe_backup.json",
            mime="application/json"
        )
except:
    pass