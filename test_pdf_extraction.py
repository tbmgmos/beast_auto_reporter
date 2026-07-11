#!/usr/bin/env python3
"""
Test script to verify PDF extraction is working
"""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.pdf_extractor import PDFExtractor

def test_pdf_extraction(pdf_path):
    """Test PDF extraction on a single file"""
    print(f"\n{'='*60}")
    print(f"Testing PDF extraction: {pdf_path}")
    print(f"{'='*60}")
    
    extractor = PDFExtractor()
    
    # Extract all text first
    text = extractor.extract_text_from_pdf(pdf_path)
    print(f"\nExtracted text length: {len(text)} chars")
    print(f"\nFirst 1000 chars:\n{text[:1000]}")
    print(f"\nLast 1000 chars:\n{text[-1000:]}")
    
    # Extract technical info
    info = extractor.extract_technical_info(pdf_path)
    print(f"\n{'='*60}")
    print("Technical info extracted:")
    print(f"{'='*60}")
    for key, value in info.items():
        print(f"  {key}: {value}")
    
    return info

if __name__ == "__main__":
    import glob
    
    # Find PDF files in common locations
    pdf_files = []
    
    # Check current directory and subdirectories
    for pattern in ["*.pdf", "**/*.pdf"]:
        pdf_files.extend(glob.glob(pattern, recursive=True))
    
    if not pdf_files:
        print("No PDF files found in current directory")
        print("Usage: python test_pdf_extraction.py /path/to/pdf/file.pdf")
    else:
        print(f"Found {len(pdf_files)} PDF files:")
        for f in pdf_files[:5]:  # Limit to 5 files
            print(f"  - {f}")
        
        # Test each PDF file
        for pdf_file in pdf_files[:5]:
            try:
                info = test_pdf_extraction(pdf_file)
                print(f"\n✅ Extracted: LUFS={info.get('lufs')}, Peak={info.get('true_peak')}, LRA={info.get('lra')}")
            except Exception as e:
                print(f"\n❌ Error: {e}")
                import traceback
                traceback.print_exc()
