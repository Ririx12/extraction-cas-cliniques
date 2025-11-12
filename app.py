import io, re, csv, os, base64
import streamlit as st
import pdfplumber
from pypdf import PdfReader

# --- extraction texte robuste ---
def extract_text_pdf(file_bytes: bytes) -> str:
    text = ""
    # 1) essayer pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
    except Exception:
        pass
    # 2) fallback pypdf si vide
    if not text.strip():
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            for page in reader.pages:
                text += (page.extract_text() or "") + "\n"
        except Exception:
            pass
    return text.strip()

# --- parsing des sections par heuristiques ---
SECTION_PATTERNS = [
    ("indication", r"(?i)\b(indication)\s*[:\-–]\s*(.+?)(?=\n\s*(technique|description|conclusion|signa|signature|signataires)\b|$)"),
    ("technique",  r"(?i)\b(technique)\s*[:\-–]\s*(.+?)(?=\n\s*(indication|description|conclusion|signa|signature|signataires)\b|$)"),
    ("description",r"(?i)\b(description|compte\s*rendu)\s*[:\-–]?\s*(.+?)(?=\n\s*(indication|technique|conclusion|signa|signature|signataires)\b|$)"),
    ("conclusion", r"(?i)\b(conclusion|impression|résumé)\s*[:\-–]?\s*(.+?)(?=\n\s*(indication|technique|description|signa|signature|signataires)\b|$)"),
]

SIGN_PATTERN = r"(?i)(dr\.?\s+[A-ZÉÈÊÀÂÎÔÛÇ][\w\-\s']+|[A-ZÉÈÊÀÂÎÔÛÇ][a-zéèêàâîôûç]+(?:\s+[A-ZÉÈÊÀÂÎÔÛÇ][a-zéèêàâîôûç]+)+)\s*(?:MD|PhD|FMH|Radiologue)?"

def parse_sections(text: str):
    clean = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    out = {"indication":"", "technique":"", "description":"", "conclusion":"", "signataires":""}

    for key, pat in SECTION_PATTERNS:
        m = re.search(pat, clean, flags=re.DOTALL)
        if m:
            out[key] = m.group(2).strip()

    # signataires (heuristique simple)
    # cherche dans les 30 dernières lignes
    tail = "\n".join(clean.splitlines()[-30:])
    signatures = set(re.findall(SIGN_PATTERN, tail))
    out["signataires"] = "; ".join(sorted(signatures))

    return out

def to_csv(rows, path):
    headers = ["filename","indication","technique","description","conclusion","signataires"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h,"") for h in headers})

def make_download_link(content: str, filename: str, mime="text/csv"):
    b64 = base64.b64encode(content.encode()).decode()
    return f'<a download="{filename}" href="data:{mime};base64,{b64}">Télécharger {filename}</a>'

st.set_page_config(page_title="Extraction CR PDF", page_icon="🩻", layout="centered")
st.title("🩻 Extraction de comptes rendus PDF (local)")

tab1, tab2 = st.tabs(["📄 Fichiers PDF", "📁 Dossier (ZIP)"])

results = []

with tab1:
    files = st.file_uploader("Choisis un ou plusieurs PDF", type=["pdf"], accept_multiple_files=True)
    if files and st.button("Extraire"):
        for f in files:
            data = f.read()
            text = extract_text_pdf(data)
            if not text:
                st.warning(f"{f.name} : texte introuvable (probablement scanné → OCR nécessaire).")
                continue
            sections = parse_sections(text)
            row = {"filename": f.name, **sections}
            results.append(row)

with tab2:
    st.info("Astuce : compresse un dossier de PDF en ZIP puis charge-le ici.")
    zipfile = st.file_uploader("Charge un .zip de PDF", type=["zip"])
    if zipfile and st.button("Extraire le ZIP"):
        import zipfile as zf
        z = zf.ZipFile(io.BytesIO(zipfile.read()))
        for name in z.namelist():
            if not name.lower().endswith(".pdf"):
                continue
            data = z.read(name)
            text = extract_text_pdf(data)
            if not text:
                st.warning(f"{name} : texte introuvable (probablement scanné → OCR nécessaire).")
                continue
            sections = parse_sections(text)
            results.append({"filename": os.path.basename(name), **sections})

if results:
    st.success(f"{len(results)} fichier(s) extrait(s).")
    st.dataframe(results, use_container_width=True)

    # Export CSV
    import pandas as pd
    df = pd.DataFrame(results)
    csv_str = df.to_csv(index=False)
    st.download_button("💾 Télécharger CSV", csv_str, "extraction.csv", "text/csv")
