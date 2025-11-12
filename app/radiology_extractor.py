#!/usr/bin/env python3
# radiology_extractor_enhanced.py

import re
import os
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Dict, List, Tuple, Any
import PyPDF2

# Configuration améliorée
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

class RadiologyReportExtractor:
    def __init__(self, db_path: str = "radiology_reports.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_specialty ON reports(specialty)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_exam_date ON reports(exam_date)")
        conn.commit()
        conn.close()

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extrait le texte d'un PDF avec PyPDF2 + OCR optionnel"""
        text_chunks = []
        try:
            with open(pdf_path, "rb") as fh:
                reader = PyPDF2.PdfReader(fh)
                for page in reader.pages:
                    text = page.extract_text() or ""
                    if text.strip():
                        text_chunks.append(text)
        except Exception as e:
            logging.error(f"Erreur lecture PDF {pdf_path}: {e}")
            return ""
        
        text = self.clean_text("\n".join(text_chunks))
        
        # Fallback OCR si texte insuffisant
        if len(text) < 200:  # Si texte trop court
            text = self._extract_with_ocr(pdf_path) or text
            
        return text

    def _extract_with_ocr(self, pdf_path: str) -> Optional[str]:
        """Extraction OCR optionnelle si les packages sont installés"""
        try:
            from pdf2image import convert_from_path
            import pytesseract
            
            logging.info(f"Tentative OCR pour {pdf_path}")
            images = convert_from_path(pdf_path)
            ocr_text = []
            
            for img in images:
                page_text = pytesseract.image_to_string(img, lang='fra+eng')
                ocr_text.append(page_text)
                
            return self.clean_text("\n".join(ocr_text))
            
        except ImportError:
            logging.warning("OCR non disponible: installer pdf2image et pytesseract")
        except Exception as e:
            logging.error(f"Erreur OCR {pdf_path}: {e}")
            
        return None

    def clean_text(self, text: str) -> str:
        """Nettoie le texte extrait"""
        if not text:
            return ""
        # Supprime les caractères non imprimables
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', ' ', text)
        # Normalise les espaces et nouvelles lignes
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def extract_patient_info(self, text: str) -> Dict[str, Any]:
        """Extrait les informations patient avec patterns robustes"""
        info = {}
        
        # Pattern principal amélioré pour gérer les variantes
        patterns = [
            # Format: NOM, DD.MM.YYYY (age ans) / ID / ...
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

    def extract_date(self, text: str) -> Optional[datetime]:
        """Extrait la date de l'examen avec patterns flexibles"""
        date_patterns = [
            r'Examen\(s\)\s+du\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})',
            r'le\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})',
            r'Neuchâtel,\s*le\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})',
            r'(\d{1,2}[./-]\d{1,2}[./-]\d{4})',
        ]
        
        for pattern in date_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                date_str = match.group(1)
                # Normalise le format
                date_str = re.sub(r'[./-]', '/', date_str)
                parts = date_str.split('/')
                if len(parts) == 3:
                    day, month, year = parts
                    # Assure le format YYYY-MM-DD
                    if len(year) == 4:
                        try:
                            if len(day) == 4:  # Cas YYYY/MM/DD
                                year, month, day = day, month, year
                            return datetime(int(year), int(month), int(day))
                        except ValueError:
                            continue
        return None

    def extract_sections(self, text: str) -> Dict[str, str]:
        """Extrait les sections principales avec regex flexibles et robustes"""
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
                    # Nettoie le contenu
                    content = re.sub(r'^\s*[-*•]\s*', '', content)  # Supprime puces
                    content = re.sub(r'\s+', ' ', content)  # Normalise espaces
                    sections[section] = content
                    break
        
        # Extraction des signatures - patterns plus flexibles
        signature_patterns = [
            r'Valid[ée]\s+(?:électroniquement\s+)?par\s+([^,\n]+)',
            r'Docteur\s+([A-Z][a-z]+\s+[A-Z][a-z]+)(?:\s*,\s*[^,\n]+)?',
            r'([A-Z][a-z]+\s+[A-Z][a-z]+)\s*/\s*([A-Z][a-z]+\s+[A-Z][a-z]+)'
        ]
        
        signatures = []
        for pattern in signature_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                if isinstance(match, tuple):
                    signatures.extend([m.strip() for m in match if m.strip()])
                else:
                    signatures.append(match.strip())
        
        # Nettoie et déduplique les signatures
        cleaned_signatures = []
        for sig in set(signatures):
            # Supprime les titres répétitifs
            sig = re.sub(r'^\s*Docteur\s+', '', sig, flags=re.IGNORECASE)
            sig = re.sub(r'\s*Médecin.*$', '', sig)
            if sig and len(sig) > 5:  # Filtre les signatures trop courtes
                cleaned_signatures.append(sig)
        
        sections['validated_by'] = '; '.join(cleaned_signatures) if cleaned_signatures else ""
        
        return sections

    def determine_specialty(self, exam_text: str, technique_text: str) -> str:
        """Détermine la spécialité basée sur l'examen et la technique"""
        combined_text = (exam_text + " " + technique_text).lower()
        
        # Recherche directe dans le mapping
        for keyword, specialty in EXAM_TO_SPECIALTY.items():
            if keyword in combined_text:
                return specialty
        
        # Recherche par mots-clés étendue
        brain_keywords = ['cérébr', 'cerveau', 'encéphale', 'crâne', 'crânien', 'brain', 'cerebral']
        orl_keywords = ['carotide', 'sinus', 'oro', 'facial', 'neck']
        spine_keywords = ['rachis', 'vertébr', 'spine', 'cervical', 'lombaire']
        thoracic_keywords = ['thorax', 'poumon', 'pulmon', 'thoracique', 'pulmonary']
        
        if any(keyword in combined_text for keyword in brain_keywords):
            return "Neuroradiology Brain"
        elif any(keyword in combined_text for keyword in orl_keywords):
            return "Neuroradiology ORL"
        elif any(keyword in combined_text for keyword in spine_keywords):
            return "Spine"
        elif any(keyword in combined_text for keyword in thoracic_keywords):
            return "Thoracic"
        
        return "IA"  # Default

    def validate_exam_technique_coherence(self, exam: str, technique: str) -> Tuple[bool, str]:
        """Valide la cohérence entre examen et technique"""
        if not exam and not technique:
            return True, "Aucune information"
        if not exam:
            return False, "Examen manquant"
        if not technique:
            return True, "Technique manquante mais acceptable"
        
        exam_lower = exam.lower()
        technique_lower = technique.lower()
        
        # Vérifications de cohérence
        checks = [
            ("tdm", ["tomodensitométrie", "acquisition", "reconstruction", "spiralée"]),
            ("irm", ["imagerie par résonance", "résonance", "irm"]),
            ("perfusion", ["perfusion", "injection"]),
            ("angio", ["angio", "vasculaire", "artériel"]),
            ("carotide", ["carotide", "vasculaire", "artériel"]),
        ]
        
        for exam_term, tech_terms in checks:
            if exam_term in exam_lower:
                if not any(tech_term in technique_lower for tech_term in tech_terms):
                    return False, f"'{exam_term}' dans examen mais pas cohérent avec technique"
        
        return True, "OK"

    def generate_report_id(self, exam_date: Optional[datetime], patient_id: str, pdf_path: str) -> str:
        """Génère un ID unique pour le rapport"""
        date_part = exam_date.strftime("%Y%m%d") if exam_date else "unknown_date"
        patient_part = patient_id if patient_id else "unknown_patient"
        file_part = Path(pdf_path).stem
        
        return f"{date_part}_{patient_part}_{file_part}"

    def extract_report(self, pdf_path: str) -> Optional[Dict]:
        """Extrait toutes les informations d'un rapport"""
        try:
            text = self.extract_text_from_pdf(pdf_path)
            if not text or len(text) < 100:
                logging.warning(f"Texte trop court ou vide pour {pdf_path}")
                return None
            
            # Extraction des informations
            patient_info = self.extract_patient_info(text)
            exam_date = self.extract_date(text)
            sections = self.extract_sections(text)
            
            # Détermination spécialité
            specialty = self.determine_specialty(
                sections['exam_type'], 
                sections['technique']
            )
            
            # Validation cohérence
            is_coherent, coherence_msg = self.validate_exam_technique_coherence(
                sections['exam_type'],
                sections['technique']
            )
            
            if not is_coherent:
                logging.warning(f"Incohérence détectée dans {pdf_path}: {coherence_msg}")
            
            # Construction du rapport
            report = {
                'id': self.generate_report_id(
                    exam_date, 
                    patient_info.get('patient_identifier', ''),
                    pdf_path
                ),
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
            logging.error(f"Erreur lors de l'extraction de {pdf_path}: {e}")
            return None

    def save_report(self, report: Dict) -> bool:
        """Sauvegarde le rapport en base"""
        try:
            conn = sqlite3.connect(self.db_path)
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
            logging.error(f"Erreur sauvegarde rapport {report.get('id')}: {e}")
            return False

    def process_directory(self, directory: str) -> Dict:
        """Traite tous les PDFs d'un répertoire"""
        stats = {"total": 0, "success": 0, "failed": 0, "details": []}
        
        for pdf_file in Path(directory).glob("*.pdf"):
            stats["total"] += 1
            logging.info(f"Traitement de {pdf_file.name}")
            
            report = self.extract_report(str(pdf_file))
            if report:
                if self.save_report(report):
                    stats["success"] += 1
                    stats["details"].append({
                        "file": pdf_file.name,
                        "id": report["id"],
                        "status": "success"
                    })
                    logging.info(f"✓ Rapport {report['id']} sauvegardé")
                else:
                    stats["failed"] += 1
                    stats["details"].append({
                        "file": pdf_file.name, 
                        "status": "save_failed"
                    })
                    logging.error(f"✗ Échec sauvegarde {pdf_file.name}")
            else:
                stats["failed"] += 1
                stats["details"].append({
                    "file": pdf_file.name,
                    "status": "extraction_failed"
                })
                logging.error(f"✗ Échec extraction {pdf_file.name}")
        
        return stats

# Test avec les documents fournis
def test_extraction():
    """Teste l'extraction sur les documents fournis"""
    extractor = RadiologyReportExtractor("test_reports.db")
    
    test_files = [
        "PP (1).pdf", "QQ (1).pdf", "OP (1).pdf", "QS (1).pdf"
    ]
    
    for test_file in test_files:
        if Path(test_file).exists():
            print(f"\n{'='*50}")
            print(f"Test extraction: {test_file}")
            print(f"{'='*50}")
            
            report = extractor.extract_report(test_file)
            if report:
                print(f"✓ Succès: {report['id']}")
                print(f"  Patient: {report['patient_name']}")
                print(f"  Examen: {report['exam_type'][:50]}...")
                print(f"  Spécialité: {report['specialty']}")
                print(f"  Signatures: {report['validated_by']}")
            else:
                print(f"✗ Échec extraction")
        else:
            print(f"Fichier non trouvé: {test_file}")

# Utilisation
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    # Test d'extraction
    test_extraction()
    
    # Traitement complet
    extractor = RadiologyReportExtractor("radiology_reports.db")
    stats = extractor.process_directory("./radiology_reports")
    
    print(f"\n{'='*50}")
    print(f"Résumé: {stats['success']}/{stats['total']} rapports traités avec succès")
    print(f"Échecs: {stats['failed']}")