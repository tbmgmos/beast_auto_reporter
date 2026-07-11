#!/usr/bin/env python3
"""
Test script to verify PDF → JSON → Table workflow
"""

import sys
import os
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.pdf_extractor import PDFExtractor

def test_pdf_to_json(pdf_path):
    """Test PDF extraction and JSON save/load"""
    print(f"\n{'='*60}")
    print(f"Testing PDF → JSON workflow: {pdf_path}")
    print(f"{'='*60}")
    
    extractor = PDFExtractor()
    
    # Extract technical info
    print(f"\n1. Extracting technical info from PDF...")
    info = extractor.extract_technical_info(pdf_path)
    print(f"   Extracted: {info}")
    
    if not info or all(v is None for v in info.values()):
        print("   ⚠️ Warning: Extraction returned empty or incomplete data!")
    
    # Save to JSON
    json_path = Path(pdf_path).stem + "_data.json"
    print(f"\n2. Saving to JSON: {json_path}")
    extractor.save_tech_info_to_json({Path(pdf_path).stem: info}, json_path)
    
    # Load from JSON
    print(f"\n3. Loading from JSON...")
    loaded = extractor.load_tech_info_from_json(json_path)
    print(f"   Loaded keys: {list(loaded.keys())}")
    
    # Verify data
    print(f"\n4. Verifying data...")
    if loaded:
        for key, value in loaded.items():
            print(f"   {key}: {value}")
            if isinstance(value, dict):
                lufs = value.get('lufs')
                peak = value.get('true_peak')
                lra = value.get('lra')
                print(f"      -> LUFS: {lufs}, Peak: {peak}, LRA: {lra}")
    
    # Clean up
    if os.path.exists(json_path):
        os.remove(json_path)
        print(f"\n5. Cleaned up JSON file: {json_path}")
    
    return loaded

def test_multiple_pdfs():
    """Test with multiple PDF files"""
    import glob
    
    pdf_files = glob.glob("*.pdf") + glob.glob("**/*.pdf", recursive=True)
    
    if not pdf_files:
        print("\nNo PDF files found in current directory")
        print("Usage: python test_pdf_json.py /path/to/pdf/file.pdf")
        return
    
    print(f"\nFound {len(pdf_files)} PDF files:")
    for f in pdf_files[:10]:
        print(f"  - {f}")
    
    # Test each PDF
    for pdf_file in pdf_files[:10]:
        try:
            result = test_pdf_to_json(pdf_file)
            if result:
                print("\n✅ Test PASSED")
            else:
                print("\n❌ Test FAILED - empty result")
        except Exception as e:
            print(f"\n❌ Test ERROR: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test PDF → JSON workflow")
    parser.add_argument("pdf_path", nargs="?", help="Path to PDF file")
    args = parser.parse_args()
    
    if args.pdf_path:
        test_pdf_to_json(args.pdf_path)
    else:
        test_multiple_pdfs()
