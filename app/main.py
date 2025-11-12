#!/usr/bin/env python3
# main.py - FastAPI avec extraction avancée des CR radiologie + OCR

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import pdfplumber, io, re, tempfile, os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import sqlite3
import logging
import base64

# Configuration
SPECIALTIES = [
    "Breast", "Cardiac", "Gastrointestinal", "Genitourinary", "IA",
    "Interventional", "MSK", "Neuroradiology Brain", "Neuroradiology ORL", 
    "Nuclearetmolecular", "Obstetrical", "Pediatrics", "Physics",
    "Spine", "Thoracic", "Vascular"
]

EXAM_TO_SPECIALTY = {
    "tdm cérébrale": "Neuroradiology Brain",
    "tdm cerebrale": "Neuroradiology Brain", 
    "irm cérébrale": "Neuroradiology Brain",
    "irm cerebrale": "Neuroradiology Brain",
    "ct cérébrale": "Neuroradiology Brain",
    "perfusion": "Neuroradiology Brain",
    "carotides": "Neuroradiology ORL",
    "polygone de willis": "Neuroradiology Brain",
    "rachis": "Spine",
    "thorax": "Thoracic",
    "abdomen": "Gastrointestinal",
    "pelvis": "Genitourinary"
}

app = FastAPI(title="Extraction CR Radiologie Avancée")

# Initialisation de la base de données
def init_db():
    conn = sqlite3.connect("radiology_reports.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            exam_date TEXT,
            patient_name TEXT,
            patient_dob TEXT,
            patient_age INTEGER,
            patient_identifier TEXT,
            exam_type TEXT,
            specialty TEXT,
            indication TEXT,
            technique TEXT,
            description TEXT,
            conclusion TEXT,
            validated_by TEXT,
            raw_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def clean_text(text: str) -> str:
    """Nettoie le texte extrait"""
    if not text:
        return ""
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_text_with_ocr(file_bytes: bytes) -> str:
    """Extrait le texte d'un PDF scanné avec OCR"""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        
        logging.info("Tentative d'extraction OCR...")
        
        # Convertir PDF en images
        images = convert_from_bytes(file_bytes, dpi=300)
        logging.info(f"PDF converti en {len(images)} images")
        
        # Extraire le texte de chaque image avec OCR
        extracted_text = []
        for i, image in enumerate(images):
            text = pytesseract.image_to_string(image, lang='fra+eng')
            extracted_text.append(f"--- Page {i+1} ---\n{text}")
            logging.info(f"Page {i+1} traitée: {len(text)} caractères")
        
        result = "\n".join(extracted_text)
        logging.info(f"OCR terminé: {len(result)} caractères extraits")
        return clean_text(result)
        
    except ImportError as e:
        logging.error(f"OCR non disponible: {e}")
        return ""
    except Exception as e:
        logging.error(f"Erreur OCR: {e}")
        return ""

def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extrait le texte d'un PDF avec fallback OCR"""
    text = ""
    
    # Essai 1: Extraction texte standard
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text_chunks = []
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_chunks.append(page_text)
                logging.info(f"Page {i+1}: {len(page_text)} caractères (pdfplumber)")
            
            text = clean_text("\n".join(text_chunks))
            logging.info(f"Extraction pdfplumber: {len(text)} caractères")
    except Exception as e:
        logging.error(f"Erreur pdfplumber: {e}")
    
    # Si texte insuffisant, essai OCR
    if not text or len(text) < 200:
        logging.info("Texte insuffisant, passage à l'OCR...")
        ocr_text = extract_text_with_ocr(file_bytes)
        if ocr_text and len(ocr_text) > len(text):
            text = ocr_text
            logging.info(f"OCR a fourni {len(text)} caractères")
    
    return text

# [Le reste de votre code reste inchangé : extract_patient_info, extract_date, extract_sections, etc.]
# ... Gardez toutes les autres fonctions telles quelles ...

def extract_patient_info(text: str) -> Dict[str, Any]:
    """Extrait les informations patient"""
    info = {}
    
    patterns = [
        r'([A-Z][A-Z\s\-\']+),\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})\s*\((\d+)\s*ans\)\s*/\s*(\d+)\s*/\s*([A-Z]\d+)',
        r'([A-Z][A-Z\s\-\']+),\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})\s*\((\d+)\s*ans\)\s*/\s*(\d+)',
        r'([A-Z][A-Z\s\-\']+)\s*,\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})\s*\((\d+)\s*ans\)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            info['patient_name'] = match.group(1).strip()
            info['patient_dob'] = match.group(2)
            info['patient_age'] = int(match.group(3))
            if len(match.groups()) >= 4:
                info['patient_identifier'] = match.group(4)
            if len(match.groups()) >= 5:
                info['exam_identifier'] = match.group(5)
            break
    
    # Fallback pour identifiant patient
    if 'patient_identifier' not in info:
        id_patterns = [
            r'No de patient\s*[:]?\s*(\d+)',
            r'IPP\s*(\d+)',
            r'/\s*(\d{6,})\s*/\s*[A-Z]?\d+',
        ]
        for pattern in id_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info['patient_identifier'] = match.group(1)
                break
    
    return info

def extract_date(text: str) -> Optional[datetime]:
    """Extrait la date de l'examen"""
    date_patterns = [
        r'Examen\(s\)\s+du\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})',
        r'le\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})',
        r'Neuchâtel,\s*le\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})',
    ]
    
    for pattern in date_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            date_str = match.group(1)
            date_str = re.sub(r'[./-]', '/', date_str)
            parts = date_str.split('/')
            if len(parts) == 3:
                day, month, year = parts
                if len(year) == 4:
                    try:
                        if len(day) == 4:
                            year, month, day = day, month, year
                        return datetime(int(year), int(month), int(day))
                    except ValueError:
                        continue
    return None

def extract_sections(text: str) -> Dict[str, str]:
    """Extrait les sections principales du CR"""
    sections = {
        'exam_type': '', 'indication': '', 'technique': '', 
        'description': '', 'conclusion': '', 'validated_by': ''
    }
    
    # Patterns flexibles pour chaque section
    section_patterns = {
        'exam_type': [
            r'Examen\(s\)[^:]*:(.*?)(?=Indication|Technique|Comparatif|Description|Conclusion|\n\s*[A-Z][a-z]|$)',
            r'Examen\(s\)[^:]*:(.*)'
        ],
        'indication': [
            r'Indication\s*:?\s*(.*?)(?=Technique|Comparatif|Description|Conclusion|Technique|\n\s*[A-Z][a-z]|$)',
            r'Indication\s*:?\s*(.*)'
        ],
        'technique': [
            r'Technique\s*:?\s*(.*?)(?=Comparatif|Description|Conclusion|\n\s*[A-Z][a-z]|$)',
            r'Technique\s*:?\s*(.*)'
        ],
        'description': [
            r'Description\s*:?\s*(.*?)(?=Conclusion|Validé|Docteur|\n\s*[A-Z][a-z]{2,}|$)',
            r'Description\s*:?\s*(.*)'
        ],
        'conclusion': [
            r'Conclusion\s*:?\s*(.*?)(?=Validé|Docteur|En restant|NB :|\n\s*[A-Z][a-z]{2,}|$)',
            r'Conclusion\s*:?\s*(.*)'
        ]
    }
    
    for section, patterns in section_patterns.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                content = match.group(1).strip()
                content = re.sub(r'^\s*[-*•]\s*', '', content)
                content = re.sub(r'\s+', ' ', content)
                sections[section] = content
                break
    
    # Extraction des signatures
    signature_patterns = [
        r'Valid[ée]\s+(?:électroniquement\s+)?par\s+([^,\n]+)',
        r'Docteur\s+([A-Z][a-z]+\s+[A-Z][a-z]+)',
    ]
    
    signatures = []
    for pattern in signature_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            if isinstance(match, tuple):
                signatures.extend([m.strip() for m in match if m.strip()])
            else:
                signatures.append(match.strip())
    
    # Nettoyage signatures
    cleaned_signatures = []
    for sig in set(signatures):
        sig = re.sub(r'^\s*Docteur\s+', '', sig, flags=re.IGNORECASE)
        sig = re.sub(r'\s*Médecin.*$', '', sig)
        if sig and len(sig) > 5:
            cleaned_signatures.append(sig)
    
    sections['validated_by'] = '; '.join(cleaned_signatures) if cleaned_signatures else ""
    
    return sections

def determine_specialty(exam_text: str, technique_text: str) -> str:
    """Détermine la spécialité"""
    combined_text = (exam_text + " " + technique_text).lower()
    
    for keyword, specialty in EXAM_TO_SPECIALTY.items():
        if keyword in combined_text:
            return specialty
    
    # Fallback par mots-clés
    brain_keywords = ['cérébr', 'cerveau', 'encéphale', 'crâne', 'crânien']
    orl_keywords = ['carotide', 'sinus', 'oro', 'facial']
    spine_keywords = ['rachis', 'vertébr', 'cervical', 'lombaire']
    thoracic_keywords = ['thorax', 'poumon', 'pulmon', 'thoracique']
    
    if any(keyword in combined_text for keyword in brain_keywords):
        return "Neuroradiology Brain"
    elif any(keyword in combined_text for keyword in orl_keywords):
        return "Neuroradiology ORL"
    elif any(keyword in combined_text for keyword in spine_keywords):
        return "Spine"
    elif any(keyword in combined_text for keyword in thoracic_keywords):
        return "Thoracic"
    
    return "IA"

def save_report_to_db(report: Dict) -> bool:
    """Sauvegarde le rapport en base SQLite"""
    try:
        conn = sqlite3.connect("radiology_reports.db")
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO reports 
            (id, exam_date, patient_name, patient_dob, patient_age, patient_identifier,
             exam_type, specialty, indication, technique, description, conclusion, 
             validated_by, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report["id"], report["exam_date"], report["patient_name"],
            report["patient_dob"], report["patient_age"], report["patient_identifier"],
            report["exam_type"], report["specialty"], report["indication"],
            report["technique"], report["description"], report["conclusion"],
            report["validated_by"], report["raw_text"]
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Erreur sauvegarde rapport: {e}")
        return False

def extract_complete_report(text: str, filename: str = "upload") -> Dict:
    """Extrait un rapport complet depuis le texte"""
    try:
        if not text or len(text) < 100:
            return {"error": "Texte trop court ou vide"}
        
        # Extraction des informations
        patient_info = extract_patient_info(text)
        exam_date = extract_date(text)
        sections = extract_sections(text)
        
        # Détermination spécialité
        specialty = determine_specialty(
            sections['exam_type'], 
            sections['technique']
        )
        
        # Génération ID
        date_part = exam_date.strftime("%Y%m%d") if exam_date else "unknown_date"
        patient_part = patient_info.get('patient_identifier', 'unknown_patient')
        file_part = Path(filename).stem
        report_id = f"{date_part}_{patient_part}_{file_part}"
        
        # Construction du rapport
        report = {
            'id': report_id,
            'exam_date': exam_date.strftime("%Y-%m-%d") if exam_date else None,
            'patient_name': patient_info.get('patient_name', ''),
            'patient_dob': patient_info.get('patient_dob', ''),
            'patient_age': patient_info.get('patient_age'),
            'patient_identifier': patient_info.get('patient_identifier', ''),
            'exam_type': sections['exam_type'],
            'specialty': specialty,
            'indication': sections['indication'],
            'technique': sections['technique'],
            'description': sections['description'],
            'conclusion': sections['conclusion'],
            'validated_by': sections['validated_by'],
            'raw_text': text
        }
        
        return report
        
    except Exception as e:
        logging.error(f"Erreur extraction rapport: {e}")
        return {"error": f"Erreur lors de l'extraction: {str(e)}"}

@app.get("/")
def root():
    return {"ok": True, "msg": "API extraction CR radiologie avancée prête."}

@app.post("/extract-from-text")
def extract_from_text(text: str = Form(...)):
    """Extrait les sections depuis un texte brut"""
    try:
        report = extract_complete_report(text, "text_input")
        if "error" in report:
            return JSONResponse({"error": report["error"]}, status_code=400)
        
        # Sauvegarde en base
        save_success = save_report_to_db(report)
        report["saved_to_db"] = save_success
        
        return JSONResponse({"report": report})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/extract-from-pdf")
async def extract_from_pdf(file: UploadFile = File(...)):
    """Extrait les sections depuis un PDF"""
    try:
        if not file.filename.endswith('.pdf'):
            return JSONResponse({"error": "Le fichier doit être un PDF"}, status_code=400)
        
        content = await file.read()
        text = extract_text_from_pdf(content)
        
        if not text:
            return JSONResponse({"error": "Impossible d'extraire le texte du PDF"}, status_code=400)
        
        report = extract_complete_report(text, file.filename)
        if "error" in report:
            return JSONResponse({"error": report["error"]}, status_code=400)
        
        # Sauvegarde en base
        save_success = save_report_to_db(report)
        report["saved_to_db"] = save_success
        
        return JSONResponse({
            "report": report,
            "raw_text_preview": text[:1000] + "..." if len(text) > 1000 else text,
            "extraction_method": "OCR" if "--- Page" in text else "pdfplumber"
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/reports")
def get_reports(limit: int = 10, offset: int = 0):
    """Récupère les rapports sauvegardés"""
    try:
        conn = sqlite3.connect("radiology_reports.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) as total FROM reports")
        total = c.fetchone()["total"]
        
        c.execute("""
            SELECT id, exam_date, patient_name, patient_age, exam_type, specialty, validated_by
            FROM reports 
            ORDER BY exam_date DESC 
            LIMIT ? OFFSET ?
        """, (limit, offset))
        
        rows = [dict(row) for row in c.fetchall()]
        conn.close()
        
        return JSONResponse({
            "reports": rows,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset
            }
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
