from bs4 import BeautifulSoup
import re
import requests
import platform
import tqdm
import glob
import ctypes
import sys
import argparse
import os
import subprocess
from shutil import which

jlink_url = r'https://www.segger.com/downloads/jlink/'

parser = argparse.ArgumentParser(description='Download and install the latest SEGGER JLink software package.')
parser.add_argument('--install', action=argparse.BooleanOptionalAction, default=True, help="Attempt to install using the system's package manager")
parser.add_argument('--version', type=str, default='latest', help="Which version of JLink to install. Takes a version string such as \"v8.10g\". Default: Latest")
args = parser.parse_args()


def get_current_installed_version_number(system):
	dll_paths = []
	if system == 'Linux':
		dll_paths = ['/opt/SEGGER/JLink*/libjlink*']
	elif system == 'Windows':
		dll_paths = [r'C:\Program Files\SEGGER\JLink*\JLink*.dll', r'C:\Program Files (x86)\SEGGER\JLink*\JLink*.dll']
	elif system == 'MacOSX':
		dll_paths = ['/Applications/SEGGER/JLink*/libjlink*']

	for path in dll_paths:
		dll_files = glob.glob(path)
		for dllFile in dll_files:
			try:
				dll = ctypes.CDLL(dllFile)
				dll_version = dll.JLINK_GetDLLVersion()
				return dll_version
			except:
				pass
	return None


def version_number_to_string(version_number):
	version_number_str = str(version_number)
	major, minor, patch = int(version_number_str[0]), int(version_number_str[1:3]), int(version_number_str[3:])
	patch = '' if patch == 0 else chr(ord('a') + patch - 1)
	return f'V{major}.{minor}{patch}'


def version_string_to_number(version_str):
	version_regex = '[vV]([0-9]*).([0-9]*)([a-z])?'
	match = re.match(version_regex, version_str)
	major, minor, patch = int(match[1]), int(match[2]), match[3]
	patch = 0 if patch is None else ord(patch) - ord('a') + 1
	version_number_str = f'{major}{minor:02d}{patch:02d}'
	return int(version_number_str)


def detect_package_format_and_package_install_command():
    # Check for common package managers
    package_managers = {
        'deb': ['apt', 'apt-get', 'dpkg'],
        'rpm': ['yum', 'dnf', 'rpm', 'zypper']
    }

    package_manager_commands = {
        'apt': 'apt install',
        'apt-get': 'apt-get install',
        'dpkg': 'dpkg -i',
        'rpm': 'rpm -Uh',
        'dnf': 'dnf install',
        'zypper': 'zypper install',
        'yum': 'yum install',
    }

    for format, managers in package_managers.items():
        for manager in managers:
            if which(manager):
                command = package_manager_commands[manager]
                return format, command
    
    # Default to tgz if we can't determine the package format
    return 'tgz'



def get_jlink_package(packages, version, preferred_format=None):
    """
    Determine the correct JLink package for the current system.
    
    Args:
        version (str): JLink version index to use (default: "0")
    
    Returns:
        dict: Package information with 'name' and 'path' keys, or None if no matching package is found
    """
    
    # Get system information
    system = platform.system()
    machine = platform.machine().lower()
    is_64bit = platform.architecture()[0] == '64bit'

    print(f"System: {system}")
    print(f"Architecture: {machine}")
    
    # Determine OS category and available packages
    if version not in packages:
        return None
    
    if system == 'Linux':
        # Check if ARM architecture
        if 'arm' in machine or 'aarch' in machine:
            os_key = 'Linux ARM'
        else:
            os_key = 'Linux'
            
        if os_key not in packages[version]:
            return None
            
        available_packages = packages[version][os_key]
        
        # Filter by architecture
        arch_packages = [pkg for pkg in available_packages 
                        if ('64-bit' in pkg['name']) == is_64bit]
        
        # Try preferred format first
        if preferred_format in ['deb', 'rpm']:
            format_packages = [pkg for pkg in arch_packages 
                             if pkg['path'].endswith(f'.{preferred_format}')]
            if format_packages:
                return format_packages[0]
        
        # Fallback to tgz
        tgz_packages = [pkg for pkg in arch_packages if pkg['path'].endswith('.tgz')]
        if tgz_packages:
            return tgz_packages[0]
            
    elif system == 'Windows':
        # Check if ARM architecture
        if 'arm' in machine:
            os_key = 'Windows ARM'
        else:
            os_key = 'Windows'
            
        if os_key not in packages[version]:
            return None
            
        available_packages = packages[version][os_key]
        
        # For Windows, simply select based on architecture
        for pkg in available_packages:
            if ('64-bit' in pkg['name']) == is_64bit:
                return pkg
                
    elif system == 'Darwin':  # macOS
        if 'macOS' not in packages[version]:
            return None
            
        available_packages = packages[version]['macOS']
        
        # Handle universal package preference
        universal_packages = [pkg for pkg in available_packages 
                            if 'Universal' in pkg['name']]
        if universal_packages:
            return universal_packages[0]
        
        # Select based on architecture
        if 'arm' in machine:
            # Apple Silicon
            arm_packages = [pkg for pkg in available_packages if 'Apple Silicon' in pkg['name']]
            if arm_packages:
                return arm_packages[0]
        else:
            # Intel
            intel_packages = [pkg for pkg in available_packages if 'Intel Silicon' in pkg['name']]
            if intel_packages:
                return intel_packages[0]
    
    return None


def parse_jlink_packages(soup, version_index=None):
    versions = {}

    # If version_index is provided, only process that specific version
    if version_index is not None:
        # Find the corresponding div with class "links v{version_num}"
        links_div = soup.select(f'div.links.v{version_index}')
        if not links_div:
            return {}
        links_div = links_div[0]

        packages = {}
        current_os = None

        # Find all OS headers and their associated packages
        for element in links_div.find_all(['p', 'div'], {'class': ['os-name', 'linkbox-link']}):
            if 'os-name' in element.get('class', []):
                current_os = element.text.strip()
                packages[current_os] = []
            elif 'linkbox-link' in element.get('class', []) and current_os:
                # Extract package info
                links = element.find_all('a')
                if len(links) >= 2:  # We expect at least 2 links - icon and text
                    package_name = links[1].text.strip()
                    package_path = links[1]['href']
                    if package_name and package_path:
                        packages[current_os].append({
                            'name': package_name,
                            'path': package_path
                        })

        versions[version_index] = packages
        return versions

    version_dict = get_jlink_versions(soup)
    for version_index, version_name in version_dict:
        versions.update(parse_jlink_packages(soup, version_index=version_index))

    return versions


def get_jlink_versions(soup):
    versions = {}
    select = soup.select_one('select.version')
    if not select:
        return versions

    for option in select.find_all('option'):
        version_num = option['value']
        version_name = option.text
        versions[version_num] = version_name
    return versions


def download_file(file_url, file_name):
    with requests.post(file_url, data={'accept_license_agreement': 'accepted'}, stream=True) as r:
        print("Downloading {}...".format(file_name))
        if r.headers['content-type'] != 'application/octet-stream':
            raise Exception("File not found on server")
        if r.status_code != 200:
            raise Exception(f"Got status code {r.status_code} while requesting file from server")
        file_size = int(r.headers.get('content-length', 0))
        block_size = 1024
        progress_bar = tqdm.tqdm(total=file_size, unit='iB', unit_scale=True)
        with open(file_name, 'wb') as f_out:
            for data in r.iter_content(block_size):
                progress_bar.update(len(data))
                f_out.write(data)
        progress_bar.close()


def find_jlink_version_index(versions, version_name):
    for version_idx in versions:
        if versions[version_idx].lower() == version_name.lower():
            return version_idx
        if version_string_to_number(versions[version_idx]) == version_name:
            return version_idx

    return None


with requests.get(jlink_url) as r:
    soup = BeautifulSoup(r.text, 'lxml')

    if args.version == 'latest':
        version_index = '0'
    else:
        versions = get_jlink_versions(soup)
        version_index = find_jlink_version_index(versions, args.version)
        if version_index is None:
            print(f"Could not find JLink version: {args.version}")
            sys.exit(2)
        else:
            print(f"Found JLink version: {args.version} at {version_index}")

    package_info = parse_jlink_packages(soup, version_index)

jlink_versions = get_jlink_versions(soup)
latest_version_str = jlink_versions[version_index]

preferred_format, package_install_cmd = detect_package_format_and_package_install_command()

best_package_info = get_jlink_package(package_info, version_index, preferred_format)
if best_package_info is None:
    print("No package found for this system.")
    sys.exit(1)

print(f"Package Type: {preferred_format}")
print(f"Package Install Command: {package_install_cmd}")

new_version_number = version_string_to_number(latest_version_str)
if args.version == 'latest':
    print(f"Latest Version: {latest_version_str} ({new_version_number})")
else:
    print(f"Replacing with Version: {latest_version_str} ({new_version_number})")

current_version_number = get_current_installed_version_number(platform.system())
if current_version_number is None:
    print("Installed version: None")
else:
    print(f"Installed version: {version_number_to_string(current_version_number)} ({current_version_number})")
    if args.version == 'latest' and current_version_number >= new_version_number:
        print("Already on latest version.")
        sys.exit(0)

file_name = best_package_info['path'].split('/')[-1]
file_url = jlink_url + file_name

download_file(file_url, file_name)

if args.install:
    package_install_cmd = package_install_cmd.split(' ')
    package_install_cmd = ['sudo'] + package_install_cmd + [os.path.realpath(file_name)]
    print(f"Executing {' '.join(package_install_cmd)}")
    p = subprocess.run(package_install_cmd)
    if p.returncode != 0:
        print("Failed")
        sys.exit(p.returncode)

print('Success')
sys.exit(0)