import streamlit as st
import json
import os
import io
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

# --- HJÆLPEFUNKTIONER ---
def standardize_image(image, target_size=(800, 800), bg_color=(255, 255, 255)):
    """Skalerer og padder billedet til et standard kvadrat og returnerer WebP bytes."""
    # Konverter til RGB for at fjerne evt. gennemsigtighed
    if image.mode in ("RGBA", "P"):
        img = image.convert("RGB")
    else:
        img = image.copy()
    
    # Bevar proportioner og skaler ned
    img.thumbnail(target_size, Image.Resampling.LANCZOS)
    
    # Opret det nye firkantede lærred med baggrundsfarven
    new_img = Image.new("RGB", target_size, bg_color)
    
    # Udregn positionen, så billedet centreres
    paste_pos = (
        (target_size[0] - img.width) // 2,
        (target_size[1] - img.height) // 2
    )
    new_img.paste(img, paste_pos)
    
    # Gem som WebP bytes
    img_byte_arr = io.BytesIO()
    new_img.save(img_byte_arr, format='WEBP', quality=85)
    return img_byte_arr.getvalue()

# --- FIREBASE SETUP ---
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(KEY_FILE)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Kunne ikke forbinde til Firebase. Fejl: {e}")
        st.stop()

db = firestore.client()

# --- JSON SCHEMAS TIL API'ET ---
# Dette tvinger AI'en til at levere præcis denne struktur hver gang (sparer tokens på prompt-eksempler)
base_schema = {
    "type": "OBJECT",
    "properties": {
        "category": {"type": "STRING"},
        "type": {"type": "STRING"},
        "display_name": {"type": "STRING"},
        "primary_color": {"type": "STRING"},
        "shade": {"type": "STRING"},
        "secondary_color": {"type": "STRING"},
        "pattern": {"type": "STRING"},
        "compatibility": {
            "type": "OBJECT",
            "properties": {
                "Top": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Bund": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Sko": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Strømper": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Overtøj": {"type": "ARRAY", "items": {"type": "STRING"}}
            }
        }
    },
    "required": ["category", "type", "display_name", "primary_color", "shade", "secondary_color", "pattern", "compatibility"]
}

additions_schema = {
    "type": "OBJECT",
    "properties": {
        "compatibility_additions": {
            "type": "OBJECT",
            "properties": {
                "Top": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Bund": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Sko": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Strømper": {"type": "ARRAY", "items": {"type": "STRING"}},
                "Overtøj": {"type": "ARRAY", "items": {"type": "STRING"}}
            }
        }
    },
    "required": ["compatibility_additions"]
}

# --- AI PROMPT (Base / Junior Stylist) ---
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


Du skal analysere det vedhæftede billede af et stykke herretøj.
Du må IKKE opfinde dine egne værdier til de faste felter - du SKAL vælge fra listerne herunder.

1. IDENTIFIKATION:
- Hovedkategori: [Top, Bund, Sko, Strømper, Overtøj]
  * VIGTIGT: Hvis genstanden er en 'Overshirt', 'Cardigan', 'Zip-up' eller en kraftig skjorte beregnet til at have åben over en t-shirt (lag-på-lag), SKAL den kategoriseres som 'Overtøj', ikke 'Top'.
- Type: Vælg den mest præcise fra listen: [T-shirt, Polo, Skjorte, Strik, Sweatshirt, Vest, Jeans, Chinos, Habitbukser, Sweatpants, Shorts, Sneakers, Støvler, Pæne Sko, Loafers, Jakke, Frakke, Blazer, Cardigan, Overshirt, Dress, Sport, Uld].
- Display Navn: Generer et kort, beskrivende navn på dansk på max 4 ord (F.eks. "Olivengrøn Strik", "Mørkeblå Chinos").
- Primær Farve: Vælg den tætteste fra [Sort, Hvid, Creme, Grå, Navy, Blå, Beige, Brun, Grøn, Oliven, Rød, Bordeaux, Accent]
- Intensitet (Shade): [Lys, Mellem, Mørk]
- Sekundær Farve: Hvis ingen tydelig, skriv "Ingen". Ellers vælg fra samme liste.
- Mønster: [Solid, Struktur, Mønster]

2. MATCHING REGLER (Kompatibilitet):
Baseret på din viden om 'Modern Heritage', lav lister over hvilke farver der passer til dette item. Inkludér både de sikre neutrale valg og karakteristiske accentfarver som Rød, så længe de overholder den tidløse æstetik.
- VIGTIGT: Sorter listerne! De absolut bedste matches skal stå FØRST. Men inkludér både klassiske neutrale farver og dybe accentfarver (som f.eks. Rød/Bordeaux), der komplementerer stilen samt sikre matches.
- EGEN KATEGORI: Du må IKKE bedømme farver for tøjets egen kategori. Hvis det analyserede tøj f.eks. er i hovedkategorien 'Overtøj', skal listen for 'Overtøj' forblive helt tom [].
- Familie-regel: Hvis en farvefamilie generelt passer (f.eks. blå nuancer), så skriv BÅDE 'Blå' og 'Navy' på listen over matches, medmindre det er et specifikt clash.
- Tone-i-Tone: Husk også at inkludere 'tone-i-tone' matches, men sørg for at anbefale kontrast i intensitet (f.eks. Mørk Top til Lyse Bukser).
- Brug KUN farvenavnene fra listen ovenfor."""

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
    
    if st.button("✨ Analyser (Junior, Senior & Master)", type="secondary"):
        with st.spinner("Analyserer billedet over 3 omgange..."):
            client = genai.Client(api_key=GOOGLE_API_KEY)
            
            try:
                # --- KØRSEL 1: Junior (Base Analyse) ---
                response1 = client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=pil_images, 
                    config={
                        "temperature": 0,
                        "response_mime_type": "application/json",
                        "response_schema": base_schema,
                        "system_instruction": AI_PROMPT
                    }
                )
                data1 = json.loads(response1.text)
                json_str_1 = json.dumps(data1, ensure_ascii=False, indent=2)

                # --- KØRSEL 2: Senior (Korrektur & Supplement) ---
                review_prompt = f"""
                ANALYSE INSTRUKTION:

                FOKUS PÅ HOVEDGENSTANDEN:
                Billedet viser ofte en model, der bærer flere stykker tøj (f.eks. bukser sammen med sko og trøje).
                Din opgave er at identificere og analysere KUN DEN PRIMÆRE GENSTAND.
                - Identificer fokus: Hvilken genstand er central, fylder mest eller er tydeligst belyst?
                - Ignorer kontekst: Hvis billedet fokuserer på bukser, skal du fuldstændig ignorere skoene og overdelen modellen har på.
                - Ignorer krop: Se bort fra modellens hud, hår og positur.
                - Hvis du er i tvivl, vælg den genstand der udgør den største del af billedet.

                ROLLE:
                Du agerer nu som 'Senior Stylist', der læser korrektur på en analyse lavet af en kollega. Du er en ekspert i 'Modern Heritage' og klassisk herremode (ofte kaldet 'Grandpa Core' eller 'Ivy Style'). Du elsker tekstur, lag-på-lag, og jordfarver. Din stil er tidløs og hyggelig, men altid velklædt. Du foretrækker harmoni frem for vilde kontraster. Du er bosat i Danmark, men inspireres af steder som Wall Street og Norditalien, særligt i perioden imellem 1950'erne og 1980'erne.
                
                Din opgave er primært at gennemgå 'compatibility' listerne i nedenstående JSON data.
                Du skal IKKE ændre på identifikation (Display Navn, Type, Farve, Intensitet, Mønster) medmindre det er åbenlyst forkert.
                
                INPUT DATA (Fra kollega):
                {json_str_1}
                
                INSTRUKTION:
                1. Kig på farverne i 'compatibility' sektionen for hver kategori.
                2. Er der klassiske 'Modern Heritage' farver, der mangler? Vælg kun ud fra listen [Sort, Hvid, Creme, Grå, Navy, Blå, Beige, Brun, Grøn, Oliven, Rød, Bordeaux, Accent]
                3. Tilføj dem KUN hvis det er et sikkert stil-match.
                4. Nye farver skal tilføjes i bunden af listerne.
                5. EGEN KATEGORI: Du må ikke tilføje farver til tøjets egen kategori (den skal forblive helt tom).
                """
                
                response2 = client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=pil_images,
                    config={
                        "temperature": 0.2,
                        "response_mime_type": "application/json",
                        "response_schema": base_schema,
                        "system_instruction": review_prompt
                    }
                )
                data2 = json.loads(response2.text)

                # Fletning 1 & 2
                merged_data = data1.copy()
                item_category = merged_data.get("category")
                comp1 = merged_data.get("compatibility", {})
                comp2 = data2.get("compatibility", {})

                for category in ["Top", "Bund", "Sko", "Strømper", "Overtøj"]:
                    # Sikkerhedsnet: Spring tøjets egen kategori over og gør den tom
                    if category == item_category:
                        comp1[category] = []
                        continue
                        
                    list1 = comp1.get(category, [])
                    list2 = comp2.get(category, [])
                    
                    final_list = list(list1)
                    existing = set(list1)
                    
                    for item in list2:
                        if item not in existing:
                            final_list.append(item)
                            existing.add(item)
                            
                    comp1[category] = final_list
                
                merged_data["compatibility"] = comp1
                json_str_2 = json.dumps(merged_data, ensure_ascii=False, indent=2)

                # --- FORBEREDELSE TIL KØRSEL 3 ---
                # 1. Udregn hvilke farver der IKKE er valgt endnu
                allowed_colors = ["Sort", "Hvid", "Creme", "Grå", "Navy", "Blå", "Beige", "Brun", "Grøn", "Oliven", "Rød", "Bordeaux", "Accent"]
                remaining_colors = {}
                for category in ["Top", "Bund", "Sko", "Strømper", "Overtøj"]:
                    # Tøjets egen kategori skal slet ikke med i Kørsel 3
                    if category == item_category:
                        continue 
                        
                    existing_colors = merged_data.get("compatibility", {}).get(category, [])
                    remaining_colors[category] = [c for c in allowed_colors if c not in existing_colors]
                
                remaining_json_str = json.dumps(remaining_colors, ensure_ascii=False, indent=2)
                
                # 2. Udtræk kun basis-info om tøjet (så prompten bliver kortere)
                item_info = {
                    "type": merged_data.get("type"),
                    "display_name": merged_data.get("display_name"),
                    "primary_color": merged_data.get("primary_color"),
                    "shade": merged_data.get("shade"),
                    "secondary_color": merged_data.get("secondary_color"),
                    "pattern": merged_data.get("pattern")
                }
                item_info_str = json.dumps(item_info, ensure_ascii=False, indent=2)

                # --- KØRSEL 3: Master Stylist (Smart-Casual & Minimalisme) ---
                master_prompt = f"""
                ROLLE:
                Du agerer nu som 'Master Stylist'. Din personlige stil er centreret omkring "Maskulin smart-casual" og "Tidløs minimalisme".
                Du kigger på et stykke tøj med et stilrent, råt og skarpt blik.

                OPGAVE:
                Du skal vurdere tøjet og udvælge MAKSIMALT 1 ekstra farve pr. kategori fra en bruttoliste af farver, som vil passe til tøjet.

                TØJET DU VURDERER:
                {item_info_str}

                RESTERENDE FARVER (Du må KUN vælge herfra):
                {remaining_json_str}
                
                INSTRUKTION:
                1. For de kategorier, der er angivet i 'RESTERENDE FARVER', vurder de oplyste farver op mod tøjet og din minimalistiske stil.
                2. VIGTIGT: Du må MAKSIMALT vælge 1 farve pr. kategori.
                3. Hvis ingen af de resterende farver passer godt ind, SKAL du efterlade listen tom.
                """

                response3 = client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=pil_images,
                    config={
                        "temperature": 0.2,
                        "response_mime_type": "application/json",
                        "response_schema": additions_schema,
                        "system_instruction": master_prompt
                    }
                )
                data3 = json.loads(response3.text)

                # --- FLETNING 3 (Tilføj resterende valg nederst) ---
                comp_final = merged_data.get("compatibility", {})
                additions = data3.get("compatibility_additions", {})

                for category in ["Top", "Bund", "Sko", "Strømper", "Overtøj"]:
                    if category == item_category:
                        comp_final[category] = []
                        continue
                        
                    existing_list = comp_final.get(category, [])
                    new_suggestions = additions.get(category, [])
                    
                    added_count = 0
                    for item in new_suggestions:
                        # Tjekker om farven reelt var på rest-listen og tvinger max 1
                        if item in remaining_colors.get(category, []) and added_count < 1:
                            existing_list.append(item)
                            added_count += 1
                            
                    comp_final[category] = existing_list

                merged_data["compatibility"] = comp_final
                final_json_text = json.dumps(merged_data, indent=2, ensure_ascii=False)

                # Opdater UI
                text_area_key = f"json_{st.session_state.form_key}"
                st.session_state[text_area_key] = final_json_text
                st.session_state.ai_result = final_json_text
                
                st.rerun()
                
            except Exception as e:
                st.error(f"AI Fejl: {str(e)}")
                try:
                    models_iter = client.models.list()
                except:
                    pass

    # 3. JSON RESULTAT (Kan redigeres)
    st.caption("Verificer data før du gemmer:")
    
    # --- RETTELSE: Undgå 'widget created with default value' advarsel ---
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
                    filename = f"img_{timestamp}.webp"
                    path_in_repo = f"img/{filename}"
                    
                    commit_message = f"Tilføjet {data.get('display_name', 'nyt tøj')}"
                    
                    # Standardiser billedet før upload (800x800, hvid baggrund, WebP)
                    processed_image_bytes = standardize_image(pil_images[0])
                    
                    # Upload til GitHub
                    repo.create_file(path_in_repo, commit_message, processed_image_bytes)
                    
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
        # RETTELSE: Vi bruger default=str til at håndtere Datetime objekter
        json_string = json.dumps(all_items, indent=2, ensure_ascii=False, default=str)
        st.download_button(
            label="📥 Download hele databasen (JSON)",
            data=json_string,
            file_name="wardrobe_backup.json",
            mime="application/json"
        )
except:
    pass