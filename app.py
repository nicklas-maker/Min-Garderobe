import streamlit as st
import os
import firebase_admin
from firebase_admin import credentials, firestore

# --- KONFIGURATION ---
# Billeder ligger stadig på GitHub (lokalt i forhold til koden)
IMAGE_FOLDER = "img"

CATEGORIES = ["Top", "Bund", "Strømper", "Sko", "Overtøj"]
CATEGORY_LABELS = {
    "Overtøj": "Overtøj",
    "Top": "Trøje",   
    "Bund": "Bukser", 
    "Strømper": "Strømper",
    "Sko": "Sko"
}

# --- FIREBASE INIT ---
# Denne logik sikrer at appen virker både på din PC og i Skyen
if not firebase_admin._apps:
    # 1. Prøv lokal fil (PC)
    if os.path.exists("firestore_key.json"):
        cred = credentials.Certificate("firestore_key.json")
        firebase_admin.initialize_app(cred)
    # 2. Prøv Streamlit Secrets (Cloud)
    elif "firebase" in st.secrets:
        # Hent data fra secrets og lav dem om til et dictionary
        key_dict = dict(st.secrets["firebase"])
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    else:
        st.error("Mangler Firebase nøgle! (firestore_key.json eller Secrets)")
        st.stop()

db = firestore.client()

# --- FUNKTIONER ---

@st.cache_data(ttl=600) # Gem data i 10 minutter for at spare læsninger
def load_wardrobe():
    """Henter alt tøj fra Firestore databasen."""
    items = []
    try:
        # Hent dokumenter fra samlingen 'wardrobe'
        docs = db.collection("wardrobe").stream()
        for doc in docs:
            item = doc.to_dict()
            item['id'] = doc.id # Gem dokumentets ID til knapperne
            items.append(item)
    except Exception as e:
        st.error(f"Fejl ved hentning af data: {e}")
    return items

def get_items_by_category(items, category):
    return [i for i in items if i['analysis']['category'] == category]

def check_compatibility(candidate, current_outfit):
    """
    Tjekker om 'candidate' passer med alt i 'current_outfit'.
    Returnerer en score (lav=godt) og en bool (gyldigt match).
    """
    if not current_outfit:
        return True, 0

    total_score = 0
    is_valid = True

    for selected_item in current_outfit:
        cand_data = candidate['analysis']
        sel_data = selected_item['analysis']
        
        cand_cat = cand_data['category']
        sel_cat = selected_item['analysis']['category']
        
        cand_color = cand_data['primary_color']
        sel_color = sel_data['primary_color']

        # Tjek hvad den valgte ting siger om kandidaten
        allowed_by_selected = sel_data['compatibility'].get(cand_cat, [])
        # Tjek hvad kandidaten siger om den valgte ting
        allowed_by_candidate = cand_data['compatibility'].get(sel_cat, [])

        # Begge skal være enige (Intersection)
        if cand_color in allowed_by_selected and sel_color in allowed_by_candidate:
            score1 = allowed_by_selected.index(cand_color)
            score2 = allowed_by_candidate.index(sel_color)
            total_score += (score1 + score2)
        else:
            is_valid = False
            break 
    
    return is_valid, total_score

def check_dead_end(candidate, current_outfit, wardrobe):
    """
    FREMTIDS-RADAR:
    Tjekker om valget af 'candidate' vil gøre det umuligt at fylde
    de resterende pladser i outfittet.
    """
    temp_outfit = current_outfit + [candidate]
    filled_cats = {item['analysis']['category'] for item in temp_outfit}
    missing_cats = [c for c in CATEGORIES if c not in filled_cats]
    
    for missing_cat in missing_cats:
        potential_items = get_items_by_category(wardrobe, missing_cat)
        if not potential_items:
            continue
            
        found_match = False
        for potential_item in potential_items:
            is_valid, _ = check_compatibility(potential_item, temp_outfit)
            if is_valid:
                found_match = True
                break 
        
        if not found_match:
            return True # Blindgyde fundet!
            
    return False

# --- UI SETUP ---
st.set_page_config(page_title="Garderoben", page_icon="👔", layout="wide")

st.markdown("""
<style>
    .stButton>button { width: 100%; border-radius: 12px; height: auto; min-height: 3em; }
    img { border-radius: 10px; }
    div[data-testid="stExpander"] { border: none; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
    .data-badge { 
        font-size: 0.8em; 
        color: #666; 
        background-color: #f0f2f6; 
        padding: 2px 6px; 
        border-radius: 4px; 
        margin-top: 4px;
        display: inline-block;
    }
    
    /* Gør billeder lidt mindre på mobil for bedre overblik */
    @media (max-width: 768px) {
        div[data-testid="stImage"] img {
            width: 75% !important;
            margin-left: auto;
            margin-right: auto;
            display: block;
        }
    }
</style>
""", unsafe_allow_html=True)

st.title("Dagens Outfit")

# Hent garderobe fra Cloud
wardrobe = load_wardrobe()
if not wardrobe:
    st.info("Databasen er tom. Tilføj tøj via admin.py på din PC.")
    st.stop()

# Session State (Hukommelse mens appen kører)
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
    st.success("🎉 Dit outfit er komplet! Du ser skarp ud.")
    # (Her kommer "Gem Outfit" knappen senere)
else:
    st.subheader("Vælg næste del:")
    tabs = st.tabs([CATEGORY_LABELS[c] for c in missing_cats])
    
    for i, cat in enumerate(missing_cats):
        with tabs[i]:
            all_items = get_items_by_category(wardrobe, cat)
            valid_items = []
            current_selection_list = list(st.session_state.outfit.values())
            
            # Beregn scores og filtrer
            for item in all_items:
                is_valid, score = check_compatibility(item, current_selection_list)
                if is_valid:
                    valid_items.append((score, item))
            
            # Sorter: Laveste score først
            valid_items.sort(key=lambda x: x[0])
            
            if not valid_items:
                st.error(f"Ingen {CATEGORY_LABELS[cat].lower()} matcher dit nuværende valg!")
                st.caption("Tip: Tjek farve-info på dit valgte tøj ovenfor.")
            else:
                img_cols = st.columns(3)
                for idx, (score, item) in enumerate(valid_items):
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
                        # 1. Blindgyde?
                        if is_dead_end:
                            icon_prefix += "⚠️ "
                        # 2. Score niveau? (Kun hvis vi matcher mod noget)
                        elif st.session_state.outfit:
                            if score == 0: icon_prefix += "⭐ "
                            elif score == 1: icon_prefix += "1️⃣ "
                            elif 2 <= score <= 3: icon_prefix += "2️⃣ "
                            elif 4 <= score <= 5: icon_prefix += "3️⃣ "
                        
                        label_text = icon_prefix + label_text
                        
                        if st.button(label_text, key=f"add_{item['id']}"):
                            if is_dead_end:
                                st.toast(f"Advarsel: Blindgyde!", icon="⚠️")
                            st.session_state.outfit[cat] = item
                            st.rerun()
            
            # --- SHOPPING INSPIRATION ---
            if st.session_state.outfit:
                st.markdown("")
                with st.expander(f"💡 Inspiration: Hvilken farve {CATEGORY_LABELS[cat].lower()} passer her?"):
                    current_items = list(st.session_state.outfit.values())
                    first_item = current_items[0]
                    # Start med farver tilladt af første item
                    potential_colors = set(first_item['analysis']['compatibility'].get(cat, []))
                    
                    # Indsnævr med resten (Intersection)
                    for outfit_item in current_items[1:]:
                        allowed = set(outfit_item['analysis']['compatibility'].get(cat, []))
                        potential_colors = potential_colors.intersection(allowed)
                    
                    if potential_colors:
                        st.write("Disse farver passer:")
                        sorted_colors = sorted(list(potential_colors))
                        st.markdown(" ".join([f"`{c}`" for c in sorted_colors]))
                    else:
                        st.warning("Ingen farve passer til hele outfittet!")
            else:
                with st.expander(f"💡 Inspiration: Hvilken farve passer?"):
                    st.info("Vælg mindst ét stykke tøj for at få farve-forslag.")