#!/usr/bin/env python3
"""
Test script for pyloudnorm analysis
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.parameter_exporter import ParameterExporter
from src.audio_analyzer import AudioAnalyzer
import logging

# Enable logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_audio_analyzer():
    """Test the AudioAnalyzer class"""
    logger.info("=== Testing AudioAnalyzer ===")
    
    config = {
        'audio': {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak': -2.0,
            'lra_max': 18.0,
            'sample_rate': 48000
        }
    }
    
    analyzer = AudioAnalyzer(config)
    logger.info("AudioAnalyzer initialized successfully")
    
    return analyzer

def test_parameter_exporter():
    """Test the ParameterExporter class"""
    logger.info("=== Testing ParameterExporter ===")
    
    config = {
        'audio': {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak': -2.0,
            'lra_max': 18.0,
            'sample_rate': 48000
        },
        'export': {
            'csv': {
                'delimiter': ',',
                'encoding': 'utf-8'
            },
            'html': {
                'include_css': True,
                'include_timestamp': True
            }
        }
    }
    
    exporter = ParameterExporter(config)
    logger.info("ParameterExporter initialized successfully")
    
    return exporter

def create_test_audio_file():
    """Create a simple test audio file"""
    try:
        import numpy as np
        import soundfile as sf
        
        # Create a simple 1-second stereo audio file at 48kHz
        sample_rate = 48000
        duration = 1.0
        t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
        
        # Create a simple sine wave
        audio_data = np.sin(2 * np.pi * 440 * t) * 0.5  # 440 Hz sine wave at -6 dB
        audio_data = np.column_stack([audio_data, audio_data])  # Stereo
        
        test_file = "/tmp/test_audio.wav"
        sf.write(test_file, audio_data, sample_rate)
        logger.info(f"Created test audio file: {test_file}")
        
        return test_file
    except Exception as e:
        logger.error(f"Error creating test audio file: {e}")
        return None

def test_analysis_with_file(audio_file):
    """Test analysis with a specific audio file"""
    logger.info(f"=== Testing analysis with file: {audio_file} ===")
    
    config = {
        'audio': {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak': -2.0,
            'lra_max': 18.0,
            'sample_rate': 48000
        }
    }
    
    # Test AudioAnalyzer directly
    analyzer = AudioAnalyzer(config)
    try:
        result = analyzer.analyze_file(audio_file)
        logger.info(f"Analysis result: {result}")
        return result
    except Exception as e:
        logger.error(f"Error during analysis: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_export_with_file(audio_file, output_dir="/tmp"):
    """Test export with a specific audio file"""
    logger.info(f"=== Testing export with file: {audio_file} ===")
    
    config = {
        'audio': {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak': -2.0,
            'lra_max': 18.0,
            'sample_rate': 48000
        },
        'export': {
            'csv': {
                'delimiter': ',',
                'encoding': 'utf-8'
            },
            'html': {
                'include_css': True,
                'include_timestamp': True
            }
        }
    }
    
    exporter = ParameterExporter(config)
    
    try:
        results = exporter.analyze_and_export(
            audio_files=[audio_file],
            output_dir=output_dir,
            formats=['csv', 'html', 'txt', 'json'],
            report_name="test_audio_analysis"
        )
        logger.info(f"Export results: {results}")
        return results
    except Exception as e:
        logger.error(f"Error during export: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("PyLoudNorm Test Script")
    logger.info("=" * 60)
    
    # Test basic initialization
    test_audio_analyzer()
    test_parameter_exporter()
    
    # Create test audio file
    test_file = create_test_audio_file()
    
    if test_file and os.path.exists(test_file):
        # Test analysis
        test_analysis_with_file(test_file)
        
        # Test export
        test_export_with_file(test_file)
    else:
        logger.error("Could not create test audio file")
    
    logger.info("=" * 60)
    logger.info("Test completed")
    logger.info("=" * 60)
