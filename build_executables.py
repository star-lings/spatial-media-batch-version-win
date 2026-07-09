#!/usr/bin/env python3
import os
import sys
import time
import platform
import subprocess
import shutil

def get_platform_name():
    """Get standardized platform name"""
    if sys.platform.startswith('win'):
        return 'windows'
    elif sys.platform.startswith('darwin'):
        return 'macos'
    elif sys.platform.startswith('linux'):
        return 'linux'
    return sys.platform

def retry_rmtree(directory_name, max_retries=3, delay=1):
    """Retry removing directory tree with multiple attempts"""
    for attempt in range(max_retries):
        try:
            if os.path.exists(directory_name):
                shutil.rmtree(directory_name)
            return True
        except PermissionError as error:
            if attempt == max_retries - 1:
                print(f"Warning: Could not remove {directory_name}: {error}")
                return False
            print(f"Retrying removal of {directory_name} in {delay} seconds...")
            time.sleep(delay)
    return False

def clean_build_directories():
    """Clean up build directories"""
    directories_to_clean = ['build', 'dist']
    for directory_name in directories_to_clean:
        if not retry_rmtree(directory_name):
            print(f"Warning: Proceeding without cleaning {directory_name}")

TARGETS = {
    'single': ('spatial_media_metadata_injector.spec', 'Spatial Media Metadata Injector'),
    'batch': ('spatial_media_batch_injector.spec', 'Spatial Media Batch Injector'),
}


def get_executable_name(base_name):
    """Get platform-specific executable name for a given base app name"""
    platform_name = get_platform_name()
    if platform_name == 'windows':
        return f'{base_name}.exe'
    elif platform_name == 'macos':
        return f'{base_name}.app'
    else:
        return base_name

def build_executable(targets=None):
    """Build the executable(s) for the current platform.

    targets: iterable of keys from TARGETS, defaults to all of them.
    """
    # Clean previous builds
    try:
        clean_build_directories()
    except Exception as error:
        print(f"Warning: Error during cleanup: {error}")
        print("Attempting to continue with build...")

    for key in (targets or TARGETS.keys()):
        spec_name, base_name = TARGETS[key]
        specification_file = os.path.join('spatialmedia', spec_name)
        command = ['pyinstaller', '--clean', specification_file]

        try:
            subprocess.check_call(command)
            platform_name = get_platform_name()
            exe_name = get_executable_name(base_name)
            print(f"Successfully built '{key}' executable for {platform_name}")
            print(f"Output: ./dist/{exe_name}")

            # Set executable permissions for Unix-like systems
            if platform_name in ('linux', 'macos'):
                output_path = os.path.join('dist', exe_name)
                if os.path.exists(output_path):
                    os.chmod(output_path, 0o755)

        except subprocess.CalledProcessError as error:
            print(f"Error building '{key}' executable: {error}")
            sys.exit(1)

if __name__ == "__main__":
    requested = sys.argv[1:] or None
    if requested:
        unknown = set(requested) - set(TARGETS)
        if unknown:
            print(f"Unknown target(s): {', '.join(unknown)}. Valid: {', '.join(TARGETS)}")
            sys.exit(1)
    build_executable(requested)
