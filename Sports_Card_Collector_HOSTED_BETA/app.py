import base64
import io
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st
from openai import OpenAI
from PIL import Image

st.set_page_config(page_title="AI Sports Card Collector Beta", page_icon="🏆", layout="wide")

# ---------- Configuration ----------

def secret(name, default=""):
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)

OPENAI_API_KEY = secret("OPENAI_API_KEY")
OPENAI_MODEL = secret("OPENAI_MODEL", "gpt-5.6-terra")
SUPABASE_URL = secret("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = secret("SUPABASE_ANON_KEY")
PHOTO_BUCKET = secret("PHOTO_BUCKET", "card-photos")

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "sport": {"type": "string"},
        "player": {"type": "string"},
        "year": {"type": "string"},
        "manufacturer": {"type": "string"},
        "set": {"type": "string"},
        "card_number": {"type": "string"},
        "rookie": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
        "parallel_variation": {"type": "string"},
        "serial_number": {"type": "string"},
        "autograph": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
        "relic": {"type": "string", "enum": ["Yes", "No", "Unknown"]},
        "grading_company": {"type": "string"},
        "grade": {"type": "string"},
        "condition": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string"},
    },
    "required": [
        "sport", "player", "year", "manufacturer", "set", "card_number",
        "rookie", "parallel_variation", "serial_number", "autograph", "relic",
        "grading_company", "grade", "condition", "confidence", "notes"
    ],
    "additionalProperties": False,
}

CARD_NUMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "confirmed_card_number": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "string"},
        "ambiguous": {"type": "boolean"},
    },
    "required": ["confirmed_card_number", "confidence", "evidence", "ambiguous"],
    "additionalProperties": False,
}

CARD_CHECKLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "exact_identity_confirmed": {"type": "boolean"},
        "confirmed_card_number": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["exact_identity_confirmed", "confirmed_card_number", "confidence", "reason", "sources"],
    "additionalProperties": False,
}

VALUE_SCHEMA = {
    "type": "object",
    "properties": {
        "exact_match": {"type": "boolean"},
        "matched_player": {"type": "string"},
        "matched_year": {"type": "string"},
        "matched_set": {"type": "string"},
        "matched_card_number": {"type": "string"},
        "matched_parallel_variation": {"type": "string"},
        "estimated_value": {"type": "number", "minimum": 0},
        "last_sold_comp": {"type": "number", "minimum": 0},
        "comp_date": {"type": "string"},
        "low_range": {"type": "number", "minimum": 0},
        "high_range": {"type": "number", "minimum": 0},
        "valuation_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_summary": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
                "required": ["title", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "exact_match", "matched_player", "matched_year", "matched_set", "matched_card_number",
        "matched_parallel_variation", "estimated_value", "last_sold_comp", "comp_date",
        "low_range", "high_range", "valuation_confidence", "evidence_summary", "sources"
    ],
    "additionalProperties": False,
}

# ---------- Helpers ----------

def configured():
    return bool(OPENAI_API_KEY and SUPABASE_URL and SUPABASE_ANON_KEY)

def normalize_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

def normalize_card_number(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower().replace("#", ""))

def clean_year(value):
    m = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return m.group(0) if m else str(value or "").strip()

def safe_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0

def image_to_jpeg_bytes(uploaded_file):
    raw = uploaded_file.getvalue()
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()

def image_to_data_url(uploaded_file):
    data = image_to_jpeg_bytes(uploaded_file)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("utf-8")

# ---------- Supabase auth / data ----------

def sb_headers(token=None, json_type=True):
    headers = {"apikey": SUPABASE_ANON_KEY}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_type:
        headers["Content-Type"] = "application/json"
    return headers

def auth_signup(email, password):
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers=sb_headers(),
        json={"email": email, "password": password},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.json().get("msg") or r.text)
    return r.json()

def auth_login(email, password):
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers=sb_headers(),
        json={"email": email, "password": password},
        timeout=30,
    )
    if not r.ok:
        try:
            msg = r.json().get("error_description") or r.json().get("msg") or r.text
        except Exception:
            msg = r.text
        raise RuntimeError(msg)
    return r.json()

def auth_user(token):
    r = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers=sb_headers(token),
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError("Session expired. Please sign in again.")
    return r.json()

def list_cards(token):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/cards",
        headers=sb_headers(token),
        params={"select": "*", "order": "created_at.desc"},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text)
    return r.json()

def insert_card(token, row):
    headers = sb_headers(token)
    headers["Prefer"] = "return=representation"
    r = requests.post(f"{SUPABASE_URL}/rest/v1/cards", headers=headers, json=row, timeout=30)
    if not r.ok:
        raise RuntimeError(r.text)
    return r.json()[0]

def update_card(token, card_id, changes):
    headers = sb_headers(token)
    headers["Prefer"] = "return=representation"
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/cards",
        headers=headers,
        params={"id": f"eq.{card_id}"},
        json=changes,
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text)
    data = r.json()
    return data[0] if data else None

def delete_card(token, card_id):
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/cards",
        headers=sb_headers(token),
        params={"id": f"eq.{card_id}"},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text)

def upload_photo(token, user_id, uploaded_file, side):
    if uploaded_file is None:
        return ""
    data = image_to_jpeg_bytes(uploaded_file)
    name = f"{user_id}/{uuid.uuid4().hex}_{side}.jpg"
    headers = sb_headers(token, json_type=False)
    headers["Content-Type"] = "image/jpeg"
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{PHOTO_BUCKET}/{name}",
        headers=headers,
        data=data,
        timeout=45,
    )
    if not r.ok:
        raise RuntimeError(f"Photo upload failed: {r.text}")
    return name

def delete_photo(token, path):
    if not path:
        return
    r = requests.delete(
        f"{SUPABASE_URL}/storage/v1/object/{PHOTO_BUCKET}",
        headers=sb_headers(token),
        json={"prefixes": [path]},
        timeout=30,
    )
    # Storage delete semantics can differ; ignore missing-photo failures during beta.
    return r.ok

def signed_photo_url(token, path, expires=3600):
    if not path:
        return None
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/sign/{PHOTO_BUCKET}/{path}",
        headers=sb_headers(token),
        json={"expiresIn": expires},
        timeout=30,
    )
    if not r.ok:
        return None
    signed = r.json().get("signedURL")
    if not signed:
        return None
    if signed.startswith("http"):
        return signed
    return f"{SUPABASE_URL}/storage/v1{signed}"

# ---------- OpenAI card intelligence ----------

def openai_client():
    if not OPENAI_API_KEY:
        raise RuntimeError("Server API key is not configured.")
    return OpenAI(api_key=OPENAI_API_KEY)

def analyze_card(front, back=None):
    content = [{
        "type": "input_text",
        "text": (
            "Identify this sports trading card accurately. Use both front and back when provided. "
            "Do not invent unreadable details. CARD NUMBER means the catalog/checklist number for this exact card. "
            "Never use stats, jersey numbers, years, set size, copyright numbers, print codes, or incidental numbers. "
            "If uncertain, return card_number as an empty string. Condition is only a cautious visual description."
        ),
    }]
    content.append({"type": "input_image", "image_url": image_to_data_url(front), "detail": "high"})
    if back is not None:
        content.append({"type": "input_image", "image_url": image_to_data_url(back), "detail": "high"})
    r = openai_client().responses.create(
        model=OPENAI_MODEL,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_schema", "name": "card_identity", "strict": True, "schema": CARD_SCHEMA}},
    )
    return json.loads(r.output_text)

def verify_card_number(front, back, identification):
    content = [{
        "type": "input_text",
        "text": (
            "Verify ONLY the exact catalog/checklist card number. Use the BACK as primary visual evidence. "
            "Ignore stats, years, set size, jersey numbers, print codes, season totals and all incidental numbers. "
            f"First-pass identity: player={identification.get('player','')}, year={identification.get('year','')}, "
            f"manufacturer={identification.get('manufacturer','')}, set={identification.get('set','')}, "
            f"candidate={identification.get('card_number','')}. If uncertain, return blank and ambiguous=true."
        ),
    }]
    content.append({"type": "input_image", "image_url": image_to_data_url(front), "detail": "high"})
    if back is not None:
        content.append({"type": "input_image", "image_url": image_to_data_url(back), "detail": "high"})
    r = openai_client().responses.create(
        model=OPENAI_MODEL,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_schema", "name": "card_number_check", "strict": True, "schema": CARD_NUMBER_SCHEMA}},
    )
    return json.loads(r.output_text)

def checklist_crosscheck(identity, visual):
    prompt = (
        "Cross-check the exact catalog/checklist number for a sports trading card using reliable web references. "
        f"Player={identity.get('player','')}; Year={identity.get('year','')}; "
        f"Manufacturer={identity.get('manufacturer','')}; Set={identity.get('set','')}; "
        f"visual candidate={visual.get('confirmed_card_number','')}. "
        "Prefer manufacturer checklists, TCDB-like checklist references, PSA/Beckett/catalog references, COMC catalog pages, "
        "or similarly reputable sources. Ignore stats, set size, print codes, jersey numbers and serial numbering. "
        "Only confirm when player, year, set and card number all align."
    )
    r = openai_client().responses.create(
        model=OPENAI_MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
        text={"format": {"type": "json_schema", "name": "checklist_crosscheck", "strict": True, "schema": CARD_CHECKLIST_SCHEMA}},
    )
    return json.loads(r.output_text)

def find_value(card):
    prompt = (
        "Estimate the current raw-card market value using recent sold/completed sale evidence, not active asking prices. "
        f"Exact card required: player={card.get('player','')}; year={card.get('year','')}; "
        f"manufacturer={card.get('manufacturer','')}; set={card.get('set','')}; "
        f"card number={card.get('card_number','')}; parallel/variation={card.get('parallel_variation','')}. "
        "Do not use a comp unless the player, year, set and card number match. Exclude different parallels/variations. "
        "If exact evidence is insufficient, exact_match=false and set estimated_value=0."
    )
    r = openai_client().responses.create(
        model=OPENAI_MODEL,
        tools=[{"type": "web_search"}],
        input=prompt,
        text={"format": {"type": "json_schema", "name": "card_value", "strict": True, "schema": VALUE_SCHEMA}},
    )
    return json.loads(r.output_text)

def valuation_matches(card, val):
    if not val.get("exact_match"):
        return False
    checks = [
        (normalize_text(card.get("player")), normalize_text(val.get("matched_player"))),
        (normalize_text(card.get("year")), normalize_text(val.get("matched_year"))),
        (normalize_text(card.get("set")), normalize_text(val.get("matched_set"))),
        (normalize_card_number(card.get("card_number")), normalize_card_number(val.get("matched_card_number"))),
    ]
    if any(a != b or not a for a, b in checks):
        return False
    requested_parallel = normalize_text(card.get("parallel_variation"))
    matched_parallel = normalize_text(val.get("matched_parallel_variation"))
    if requested_parallel and requested_parallel != matched_parallel:
        return False
    return True

def duplicate_identity(card):
    return (
        normalize_text(card.get("player")),
        normalize_text(card.get("year")),
        normalize_text(card.get("manufacturer")),
        normalize_text(card.get("set")),
        normalize_card_number(card.get("card_number")),
        normalize_text(card.get("parallel_variation")),
        normalize_text(card.get("serial_number")),
        normalize_text(card.get("grading_company")),
        normalize_text(card.get("grade")),
    )

def reset_scan():
    st.session_state.pop("scan_result", None)
    st.session_state.pop("valuation", None)
    st.session_state.pop("duplicate", None)
    st.session_state["scan_nonce"] = st.session_state.get("scan_nonce", 0) + 1

# ---------- Auth UI ----------

st.markdown("""
<style>
@media (max-width: 900px) {
  .block-container {padding-top:.8rem!important;padding-left:.7rem!important;padding-right:.7rem!important}
  div.stButton > button, div.stDownloadButton > button {min-height:3.2rem!important;font-size:1.05rem!important}
  input, textarea, select {font-size:16px!important}
}
</style>
""", unsafe_allow_html=True)

st.title("🏆 AI Sports Card Collector — Private Beta")

if not configured():
    st.error("This hosted beta has not been configured by the owner yet.")
    st.stop()

if "access_token" not in st.session_state:
    login_tab, signup_tab = st.tabs(["Sign in", "Create account"])
    with login_tab:
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            try:
                result = auth_login(email.strip(), password)
                st.session_state["access_token"] = result["access_token"]
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    with signup_tab:
        st.caption("Each beta tester gets a private collection.")
        with st.form("signup"):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password (8+ characters)", type="password", key="signup_password")
            submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)
        if submitted:
            if len(password) < 8:
                st.error("Use a password with at least 8 characters.")
            else:
                try:
                    result = auth_signup(email.strip(), password)
                    if result.get("access_token"):
                        st.session_state["access_token"] = result["access_token"]
                        st.rerun()
                    else:
                        st.success("Account created. Check your email to confirm it, then return here and sign in.")
                except Exception as exc:
                    st.error(str(exc))
    st.stop()

token = st.session_state["access_token"]
try:
    user = auth_user(token)
except Exception:
    st.session_state.pop("access_token", None)
    st.rerun()

user_id = user["id"]
user_email = user.get("email", "")

with st.sidebar:
    st.success(f"Signed in as {user_email}")
    st.caption("Your collection is private to this account.")
    if st.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()
    st.markdown("**Photo tip:** fill the frame with one card, avoid glare, and photograph the back.")

scan_tab, collection_tab = st.tabs(["📷 Scan Card", "📚 Collection"])

# ---------- Scan tab ----------

with scan_tab:
    nonce = st.session_state.get("scan_nonce", 0)
    source = st.radio("Photo source", ["Camera", "Photo Library / Upload"], horizontal=True)

    if source == "Camera":
        c1, c2 = st.columns(2)
        with c1:
            front = st.camera_input("Front", key=f"front_cam_{nonce}")
        with c2:
            back = st.camera_input("Back (recommended)", key=f"back_cam_{nonce}")
    else:
        c1, c2 = st.columns(2)
        with c1:
            front = st.file_uploader("Choose front photo", type=["jpg","jpeg","png","webp"], key=f"front_up_{nonce}")
        with c2:
            back = st.file_uploader("Choose back photo", type=["jpg","jpeg","png","webp"], key=f"back_up_{nonce}")

    if st.button("🔎 Identify Card", type="primary", disabled=front is None, use_container_width=True):
        try:
            with st.spinner("Identifying and cross-checking the card number..."):
                identity = analyze_card(front, back)
                identity["year"] = clean_year(identity.get("year"))
                visual = verify_card_number(front, back, identity)
                checklist = checklist_crosscheck(identity, visual)

                vnum = str(visual.get("confirmed_card_number") or "").strip()
                cnum = str(checklist.get("confirmed_card_number") or "").strip()
                agree = normalize_card_number(vnum) and normalize_card_number(vnum) == normalize_card_number(cnum)
                strong_checklist = checklist.get("exact_identity_confirmed") and float(checklist.get("confidence") or 0) >= .92

                if agree and float(visual.get("confidence") or 0) >= .80 and float(checklist.get("confidence") or 0) >= .80:
                    identity["card_number"] = cnum
                    identity["_card_number_status"] = "image + checklist"
                elif strong_checklist and (not vnum or visual.get("ambiguous") or float(visual.get("confidence") or 0) < .70):
                    identity["card_number"] = cnum
                    identity["_card_number_status"] = "checklist"
                else:
                    identity["card_number"] = ""
                    identity["_card_number_status"] = "unresolved"
                    identity["confidence"] = min(float(identity.get("confidence") or 0), .79)

                identity["_visual_number"] = vnum
                identity["_checklist_number"] = cnum
                identity["_checklist_reason"] = checklist.get("reason", "")
                identity["_checklist_sources"] = checklist.get("sources", [])
                st.session_state["scan_result"] = identity
                st.session_state["scan_front"] = front
                st.session_state["scan_back"] = back
                st.session_state.pop("valuation", None)
                st.session_state.pop("duplicate", None)
        except Exception as exc:
            st.error(f"Identification failed: {exc}")

    card = st.session_state.get("scan_result")
    if card:
        confidence = round(float(card.get("confidence") or 0) * 100)
        st.info(f"AI identification confidence: {confidence}%")

        if card.get("card_number"):
            st.success(f"✅ Card # verified by {card.get('_card_number_status')}: {card.get('card_number')}")
        else:
            st.warning(
                f"⚠️ Card number was not auto-confirmed. Image candidate: {card.get('_visual_number') or 'none'}; "
                f"checklist candidate: {card.get('_checklist_number') or 'none'}."
            )

        with st.expander("Card-number verification details"):
            st.write(card.get("_checklist_reason") or "No additional detail.")
            for s in card.get("_checklist_sources") or []:
                if s.get("url"):
                    st.markdown(f"- [{s.get('title') or 'Reference'}]({s['url']})")

        st.subheader("Confirm before adding")
        a,b,c = st.columns(3)
        with a:
            sport = st.text_input("Sport", value=card.get("sport",""))
            player = st.text_input("Player", value=card.get("player",""))
            year = st.text_input("Year", value=card.get("year",""))
            manufacturer = st.text_input("Manufacturer", value=card.get("manufacturer",""))
        with b:
            set_name = st.text_input("Set", value=card.get("set",""))
            card_number = st.text_input("Card #", value=card.get("card_number",""))
            rookie = st.selectbox("Rookie?", ["Yes","No","Unknown"], index=["Yes","No","Unknown"].index(card.get("rookie","Unknown")))
            parallel = st.text_input("Parallel / Variation", value=card.get("parallel_variation",""))
        with c:
            serial = st.text_input("Serial #", value=card.get("serial_number",""))
            autograph = st.selectbox("Autograph?", ["Yes","No","Unknown"], index=["Yes","No","Unknown"].index(card.get("autograph","Unknown")))
            relic = st.selectbox("Relic?", ["Yes","No","Unknown"], index=["Yes","No","Unknown"].index(card.get("relic","Unknown")))
            condition = st.text_input("Condition", value=card.get("condition",""))

        grading_company = st.text_input("Grading company", value=card.get("grading_company",""))
        grade = st.text_input("Grade", value=card.get("grade",""))
        qty = st.number_input("Quantity", min_value=1, value=1, step=1)
        notes = st.text_area("Notes", value=card.get("notes",""))

        current_card = {
            "sport": sport, "player": player, "year": year, "manufacturer": manufacturer,
            "set": set_name, "card_number": card_number, "rookie": rookie,
            "parallel_variation": parallel, "serial_number": serial, "autograph": autograph,
            "relic": relic, "grading_company": grading_company, "grade": grade,
            "condition": condition, "notes": notes,
        }

        if st.button("💰 Find Value", disabled=not card_number.strip(), use_container_width=True):
            try:
                with st.spinner("Searching recent sold-card evidence..."):
                    val = find_value(current_card)
                    if valuation_matches(current_card, val):
                        st.session_state["valuation"] = val
                    else:
                        st.session_state["valuation"] = {"exact_match": False}
                        st.warning("Exact card match not confirmed — value was not applied.")
            except Exception as exc:
                st.error(f"Value lookup failed: {exc}")

        val = st.session_state.get("valuation") or {}
        estimated_value = safe_float(val.get("estimated_value")) if val.get("exact_match") else 0
        last_comp = safe_float(val.get("last_sold_comp")) if val.get("exact_match") else 0

        if val.get("exact_match"):
            st.success("✅ Exact card identity confirmed for pricing.")
            v1,v2,v3 = st.columns(3)
            v1.metric("Estimated value", f"${estimated_value:,.2f}")
            v2.metric("Last sold comp", f"${last_comp:,.2f}")
            v3.metric("Confidence", f"{round(safe_float(val.get('valuation_confidence'))*100)}%")
            st.write(val.get("evidence_summary",""))
            with st.expander("Pricing sources"):
                for s in val.get("sources") or []:
                    if s.get("url"):
                        st.markdown(f"- [{s.get('title') or 'Source'}]({s['url']})")

        if st.button("✅ Add to Collection", type="primary", use_container_width=True):
            try:
                existing = list_cards(token)
                candidate = {
                    "sport": sport, "player": player, "year": year, "manufacturer": manufacturer,
                    "set_name": set_name, "card_number": card_number, "rookie": rookie,
                    "parallel_variation": parallel, "serial_number": serial, "autograph": autograph,
                    "relic": relic, "grading_company": grading_company, "grade": grade,
                    "condition": condition, "quantity": int(qty), "estimated_value": estimated_value,
                    "last_sold_comp": last_comp, "comp_date": val.get("comp_date") if val.get("exact_match") else None,
                    "confidence": float(card.get("confidence") or 0), "notes": notes,
                }

                dup = None
                cand_identity = duplicate_identity(current_card)
                for e in existing:
                    e_identity = duplicate_identity({
                        "player": e.get("player"), "year": e.get("year"), "manufacturer": e.get("manufacturer"),
                        "set": e.get("set_name"), "card_number": e.get("card_number"),
                        "parallel_variation": e.get("parallel_variation"), "serial_number": e.get("serial_number"),
                        "grading_company": e.get("grading_company"), "grade": e.get("grade"),
                    })
                    if cand_identity == e_identity:
                        dup = e
                        break

                if dup:
                    st.session_state["duplicate"] = {"existing": dup, "candidate": candidate}
                else:
                    front_path = upload_photo(token, user_id, st.session_state.get("scan_front"), "front")
                    back_path = upload_photo(token, user_id, st.session_state.get("scan_back"), "back") if st.session_state.get("scan_back") else ""
                    candidate["front_photo_path"] = front_path
                    candidate["back_photo_path"] = back_path
                    insert_card(token, candidate)
                    st.success(f"Added {player} to your collection.")
                    if st.button("📸 Scan Next Card", key="scan_next_after_add", type="primary", use_container_width=True):
                        reset_scan()
                        st.rerun()
            except Exception as exc:
                st.error(f"Could not save card: {exc}")

        dup_state = st.session_state.get("duplicate")
        if dup_state:
            e = dup_state["existing"]
            st.warning(f"⚠️ Already in collection. Current quantity: {e.get('quantity',1)}.")
            d1,d2,d3 = st.columns(3)
            with d1:
                if st.button("➕ Increase quantity", type="primary", use_container_width=True):
                    update_card(token, e["id"], {"quantity": int(e.get("quantity") or 1) + int(qty)})
                    st.session_state.pop("duplicate", None)
                    st.success("Quantity updated.")
                    reset_scan()
                    st.rerun()
            with d2:
                if st.button("🗂️ Save separate copy", use_container_width=True):
                    candidate = dict(dup_state["candidate"])
                    candidate["front_photo_path"] = upload_photo(token, user_id, st.session_state.get("scan_front"), "front")
                    candidate["back_photo_path"] = upload_photo(token, user_id, st.session_state.get("scan_back"), "back") if st.session_state.get("scan_back") else ""
                    insert_card(token, candidate)
                    st.session_state.pop("duplicate", None)
                    reset_scan()
                    st.rerun()
            with d3:
                if st.button("Cancel", use_container_width=True):
                    st.session_state.pop("duplicate", None)
                    st.rerun()

# ---------- Collection tab ----------

with collection_tab:
    try:
        cards = list_cards(token)
    except Exception as exc:
        st.error(f"Could not load collection: {exc}")
        cards = []

    if not cards:
        st.info("No cards saved yet.")
    else:
        st.subheader("Search & Filters")
        q = st.text_input("Search", placeholder="Player, set, card #, notes...").strip().lower()
        sports = sorted({c.get("sport") for c in cards if c.get("sport")})
        years = sorted({str(c.get("year")) for c in cards if c.get("year")}, reverse=True)
        f1,f2,f3 = st.columns(3)
        sport_filter = f1.selectbox("Sport", ["All sports"] + sports)
        year_filter = f2.selectbox("Year", ["All years"] + years)
        sort = f3.selectbox("Sort by", ["Newest added","Highest value","Lowest value","Player A–Z","Year newest"])

        filtered = []
        for c in cards:
            if sport_filter != "All sports" and c.get("sport") != sport_filter:
                continue
            if year_filter != "All years" and str(c.get("year")) != year_filter:
                continue
            hay = " ".join(str(c.get(k) or "") for k in [
                "player","year","manufacturer","set_name","card_number","sport",
                "parallel_variation","serial_number","notes"
            ]).lower()
            if q and q not in hay:
                continue
            filtered.append(c)

        if sort == "Highest value":
            filtered.sort(key=lambda c: safe_float(c.get("estimated_value")), reverse=True)
        elif sort == "Lowest value":
            filtered.sort(key=lambda c: safe_float(c.get("estimated_value")))
        elif sort == "Player A–Z":
            filtered.sort(key=lambda c: str(c.get("player") or "").lower())
        elif sort == "Year newest":
            filtered.sort(key=lambda c: int(c.get("year") or 0), reverse=True)

        total_qty = sum(int(c.get("quantity") or 0) for c in filtered)
        total_val = sum(safe_float(c.get("estimated_value")) * int(c.get("quantity") or 0) for c in filtered)
        m1,m2,m3 = st.columns(3)
        m1.metric("Matching entries", len(filtered))
        m2.metric("Total quantity", total_qty)
        m3.metric("Estimated value", f"${total_val:,.2f}")

        for c in filtered:
            with st.container(border=True):
                left, middle, right = st.columns([1.5,5,2])
                photo_url = signed_photo_url(token, c.get("front_photo_path"))
                with left:
                    if photo_url:
                        st.image(photo_url, use_container_width=True)
                with middle:
                    st.markdown(f"**{c.get('player') or 'Unknown player'}**")
                    st.caption(f"{c.get('year') or ''} {c.get('set_name') or ''} #{c.get('card_number') or ''}")
                    st.write(f"{c.get('sport') or ''} • Qty {c.get('quantity') or 1}")
                with right:
                    st.metric("Est. value", f"${safe_float(c.get('estimated_value')):,.2f}")
                    if st.button("👁️ View", key=f"view_{c['id']}", use_container_width=True):
                        st.session_state["view_card_id"] = None if st.session_state.get("view_card_id") == c["id"] else c["id"]
                        st.rerun()
                    if st.button("🗑️ Delete", key=f"del_{c['id']}", use_container_width=True):
                        st.session_state["delete_card_id"] = c["id"]
                        st.rerun()

                if st.session_state.get("view_card_id") == c["id"]:
                    p1,p2 = st.columns(2)
                    front_url = signed_photo_url(token, c.get("front_photo_path"))
                    back_url = signed_photo_url(token, c.get("back_photo_path"))
                    if front_url: p1.image(front_url, caption="Front", use_container_width=True)
                    if back_url: p2.image(back_url, caption="Back", use_container_width=True)
                    st.json({
                        "Sport": c.get("sport"), "Player": c.get("player"), "Year": c.get("year"),
                        "Manufacturer": c.get("manufacturer"), "Set": c.get("set_name"),
                        "Card #": c.get("card_number"), "Parallel": c.get("parallel_variation"),
                        "Serial #": c.get("serial_number"), "Grade": c.get("grade"),
                        "Condition": c.get("condition"), "Quantity": c.get("quantity"),
                        "Estimated Value": c.get("estimated_value"), "Last Sold Comp": c.get("last_sold_comp"),
                        "Notes": c.get("notes"),
                    })

                if st.session_state.get("delete_card_id") == c["id"]:
                    st.warning("Permanently delete this card from your private collection?")
                    x,y = st.columns(2)
                    if x.button("Yes, delete permanently", key=f"confirm_{c['id']}", type="primary", use_container_width=True):
                        delete_card(token, c["id"])
                        delete_photo(token, c.get("front_photo_path"))
                        delete_photo(token, c.get("back_photo_path"))
                        st.session_state.pop("delete_card_id", None)
                        st.rerun()
                    if y.button("Cancel", key=f"cancel_{c['id']}", use_container_width=True):
                        st.session_state.pop("delete_card_id", None)
                        st.rerun()

        # Private CSV export for this signed-in user.
        if filtered:
            import csv
            buf = io.StringIO()
            fields = [
                "sport","player","year","manufacturer","set_name","card_number","rookie",
                "parallel_variation","serial_number","autograph","relic","grading_company",
                "grade","condition","quantity","estimated_value","last_sold_comp","comp_date","notes"
            ]
            writer = csv.DictWriter(buf, fieldnames=fields)
            writer.writeheader()
            for c in cards:
                writer.writerow({k: c.get(k,"") for k in fields})
            st.download_button(
                "⬇️ Download My Collection (CSV)",
                data=buf.getvalue().encode("utf-8"),
                file_name="my_sports_card_collection.csv",
                mime="text/csv",
                use_container_width=True,
            )

st.caption("Private beta. AI identification and market estimates can be wrong. Verify important details before buying, selling, grading or insuring cards.")
