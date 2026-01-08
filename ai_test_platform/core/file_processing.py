import io
import pypdf
import pandas as pd
from fastapi import UploadFile
import base64
from core.ai_client import ai_client
from typing import Optional

async def parse_file_content(file: UploadFile, image_prompt: str = "OCR: Extract all text from this image.") -> str:
    filename = file.filename.lower()
    content_bytes = await file.read()
    text_content = ""

    try:
        if filename.endswith(".pdf"):
            # Handle PDF
            pdf_file = io.BytesIO(content_bytes)
            reader = pypdf.PdfReader(pdf_file)
            for page in reader.pages:
                text_content += page.extract_text() + "\n"
        
        elif filename.endswith(".csv"):
            # Handle CSV
            csv_file = io.BytesIO(content_bytes)
            try:
                df = pd.read_csv(csv_file)
                text_content = df.to_csv(index=False)
            except Exception as e:
                 # Fallback
                 try:
                    text_content = content_bytes.decode("utf-8")
                 except:
                    text_content = f"[Error reading CSV: {str(e)}]"
        
        elif filename.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')):
             # Handle Image (OCR)
             try:
                 encoded_image = base64.b64encode(content_bytes).decode('utf-8')
                 # Placeholder for OCR
                 text_content = f"[Image Content: {filename}]"
             except Exception as e:
                 text_content = f"[Error processing image: {str(e)}]"
        
        else:
            # Try text
            try:
                text_content = content_bytes.decode("utf-8")
            except:
                text_content = f"[Unsupported file type: {filename}]"

    except Exception as e:
        text_content = f"[Error parsing file: {str(e)}]"

    return text_content