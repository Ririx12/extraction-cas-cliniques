# FastAPI minimal pour extraction de sections FR depuis PDF ou texte
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
import pdfplumber, io, re

app = FastAPI(title="Extraction CR Radiologie")

SECTION_PATTERNS = [
    r"^\s*(indication|motif|contexte)\s*[:\-]?\s*(.+)",
    r"^\s*(technique|protocole)\s*[:\-]?\s*(.+)",
    r"^\s*(description|examen|constatations|résultats?)\s*[:\-]?\s*(.+)",
    r"^\s*(conclusion|impression|avis)\s*[:\-]?\s*(.+)",
    r"^\s*(signataires?|rédacteur|chef|médecin)\s*[:\-]?\s*(.+)",
]
# même ordre que keys ci-dessous
SECTION_KEYS = ["indication", "technique", "description", "conclusion", "signataires"]

def extract_text_from_pdf(file_bytes: bytes) -> str:
    text = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)

def split_sections(text: str) -> dict:
    """
    Essayez d’attraper les en-têtes usuels FR, insensibles à la casse.
    Fonctionne sur CR “normaux”. Pour les cas très libres on met tout en description.
    """
    lines = [l.strip() for l in text.splitlines()]
    sections = {k: "" for k in SECTION_KEYS}
    current = None

    # regex compilées
    regs = [re.compile(p, re.IGNORECASE) for p in SECTION_PATTERNS]

    for line in lines:
        matched = False
        for idx, rgx in enumerate(regs):
            m = rgx.match(line)
            if m:
                current = SECTION_KEYS[idx]
                # si la ligne est "Titre: contenu", on capture le contenu à la suite
                sections[current] = (sections[current] + "\n" + (m.group(2) if m.lastindex and m.lastindex >= 2 else "")).strip()
                matched = True
                break
        if not matched:
            # pas d’en-tête reconnu → accumulate dans la section en cours ou description
            tgt = current or "description"
            sections[tgt] = (sections[tgt] + ("\n" if sections[tgt] else "") + line).strip()

    # nettoyage simple
    for k, v in sections.items():
        sections[k] = re.sub(r"\n{3,}", "\n\n", v).strip()
    return sections

@app.get("/")
def root():
    return {"ok": True, "msg": "API extraction CR prête."}

@app.post("/extract-from-text")
def extract_from_text(text: str = Form(...)):
    sections = split_sections(text)
    return JSONResponse({"sections": sections})

@app.post("/extract-from-pdf")
async def extract_from_pdf(file: UploadFile = File(...)):
    content = await file.read()
    txt = extract_text_from_pdf(content)
    sections = split_sections(txt)
    return JSONResponse({"sections": sections, "raw_text_preview": txt[:1000]})
