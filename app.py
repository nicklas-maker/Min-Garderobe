import streamlit as st
import json
import os

# --- KONFIGURATION ---
IMAGE_FOLDER = "img"
DATABASE_FILE = "wardrobe.json"

# Interne nøgler (skal matche det der står i database/JSON)
CATEGORIES = ["Top", "Bund", "Strømper", "Sko", "Overtøj"]

# Visningsnavne (Det brugeren ser i appen)
CATEGORY_LABELS = {
    "Overtøj": "Overtøj",
    "Top": "Trøje",   
    "Bund": "Bukser", 
    "Strømper": "Strømper",
    "Sko": "Sko"
}

# --- FUNKTIONER ---

@st.cache_data
def load_wardrobe():
    """Indlæser databasen."""
    if not os.path.exists(DATABASE_FILE):
        return []
    with open(DATABASE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        
        # --- FIX: Windows vs Linux Stier ---
        # Retter baglæns skråstreger (\) til almindelige skråstreger (/)
        # så billederne kan findes på mobilen/skyen.
        for item in data:
            if 'image_path' in item:
                item['image_path'] = item['image_path'].replace('\\', '/')
        
        return data

def get_items_by_category(items, category):
    return [i for i in items if i['analysis']['category'] == category]

def check_compatibility(candidate, current_outfit):
    """
    Tjekker om 'candidate' passer med alt i 'current_outfit'.
    Returnerer en score og en bool.
    """
    if not current_outfit:
        return True, 0

    total_score = 0
    is_valid = True

    for selected_item in current_outfit:
        # 1. Hent data
        cand_data = candidate['analysis']
        sel_data = selected_item['analysis']
        
        cand_cat = cand_data['category']
        sel_cat = selected_item['analysis']['category']
        
        cand_color = cand_data['primary_color']
        sel_color = sel_data['primary_color']

        # 2. Tjek to-vejs kompatibilitet
        allowed_by_selected = sel_data['compatibility'].get(cand_cat, [])
        allowed_by_candidate = cand_data['compatibility'].get(sel_cat, [])

        if cand_color in allowed_by_selected and sel_color in allowed_by_candidate:
            score1 = allowed_by_selected.index(cand_color)
            score2 = allowed_by_candidate.index(sel_color)
            total_score += (score1 + score2)
        else:
            is_valid = False
            break 
    
    return is_valid, total_score

# --- UI SETUP ---
st.set_page_config(page_title="Garderoben", page_icon="👔", layout="wide")

st.markdown("""
<style>
    .stButton>button { width: 100%; border-radius: 12px; height: auto; min-height: 3em; }
    img { border-radius: 10px; }
    div[data-testid="stExpander"] { border: none; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
</style>
""", unsafe_allow_html=True)

st.title("Dagens Outfit")

# Hent garderobe
wardrobe = load_wardrobe()
if not wardrobe:
    st.warning("Din garderobe er tom! Brug 'admin.py' til at tilføje tøj.")
    st.stop()

# Session State
if 'outfit' not in st.session_state:
    st.session_state.outfit = {} 

# Nulstil knap
if st.sidebar.button("🗑️ Nulstil Outfit"):
    st.session_state.outfit = {}
    st.rerun()

# --- VISNING AF OUTFIT GRID (Opdateret: Kun valgte items) ---
# Find de kategorier, der faktisk er valgt (i den rigtige rækkefølge)
selected_cats = [cat for cat in CATEGORIES if cat in st.session_state.outfit]

if selected_cats:
    # Lav kun kolonner til det antal ting vi har valgt
    cols = st.columns(len(selected_cats))
    
    for i, cat in enumerate(selected_cats):
        item = st.session_state.outfit[cat]
        data = item['analysis']
        
        with cols[i]:
            # Sætter en fast bredde (150px) for at gøre billederne mindre og mere kompakte
            st.image(item['image_path'], width=300)
            
            # Kombinerer nu navn og farve-info på én linje
            shade_info = f"({data.get('shade', 'Mellem')} {data.get('primary_color', '')})"
            st.caption(f"✅ {data['display_name']} {shade_info}")
            
            if st.button("Fjern", key=f"del_{cat}"):
                del st.session_state.outfit[cat]
                st.rerun()
else:
    # Hvis intet er valgt endnu, vis en venlig start-besked
    st.info("Start med at vælge en del af dit outfit nedenfor 👇")

st.divider()

# --- VÆLGER-SEKTION ---
missing_cats = [c for c in CATEGORIES if c not in st.session_state.outfit]

if not missing_cats:
    st.balloons()
    st.success("🎉 Dit outfit er komplet! Du ser skarp ud.")
else:
    st.subheader("Vælg næste del:")
    
    tabs = st.tabs([CATEGORY_LABELS[c] for c in missing_cats])
    
    for i, cat in enumerate(missing_cats):
        with tabs[i]:
            all_items = get_items_by_category(wardrobe, cat)
            
            # Filtrer og sorter
            valid_items = []
            current_selection_list = list(st.session_state.outfit.values())
            
            for item in all_items:
                is_valid, score = check_compatibility(item, current_selection_list)
                if is_valid:
                    valid_items.append((score, item))
            
            valid_items.sort(key=lambda x: x[0])
            
            # Vis mulighederne
            if not valid_items:
                st.error(f"Ingen {CATEGORY_LABELS[cat].lower()} matcher dit nuværende valg!")
                st.caption("Tip: Tjek farve-info på dit valgte tøj ovenfor. Måske er dine sko for 'kritiske' over for farven?")
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
                        if score < 2 and st.session_state.outfit:
                            label_text = "⭐ " + label_text
                        
                        if st.button(label_text, key=f"add_{item['id']}"):
                            st.session_state.outfit[cat] = item
                            st.rerun()
            
            # --- SHOPPING INSPIRATION ---
            if st.session_state.outfit:
                st.markdown("")
                with st.expander(f"💡 Inspiration: Hvilken farve {CATEGORY_LABELS[cat].lower()} passer her?"):
                    current_items = list(st.session_state.outfit.values())
                    first_item = current_items[0]
                    potential_colors = set(first_item['analysis']['compatibility'].get(cat, []))
                    
                    for outfit_item in current_items[1:]:
                        allowed = set(outfit_item['analysis']['compatibility'].get(cat, []))
                        potential_colors = potential_colors.intersection(allowed)
                    
                    if potential_colors:
                        st.write("Dine valgte ting er enige om, at disse farver vil passe:")
                        sorted_colors = sorted(list(potential_colors))
                        st.markdown(" ".join([f"`{c}`" for c in sorted_colors]))
                        st.caption("*Bemærk: Dette er hvad OUTFITTET ønsker. Dine fysiske ting kan være skjult, hvis de ikke ønsker outfittet tilbage.*")
                    else:
                        st.warning("Ingen farve passer til hele outfittet!")
            else:
                with st.expander(f"💡 Inspiration: Hvilken farve passer?"):
                    st.info("Vælg mindst ét stykke tøj for at få farve-forslag.")