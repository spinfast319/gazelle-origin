#!/usr/bin/env python3
from dataclasses import dataclass
import argparse
import io
import os
import re
import subprocess
import sys
from dotenv import dotenv_values
from typing import Dict

try:
    import bencoder
    has_bencoder = True
except ModuleNotFoundError:
    has_bencoder = False

import yaml
from hashlib import sha1
from . import GazelleAPI, GazelleAPIError


EXIT_CODES = {
    'hash': 3,
    'music': 4,
    'unauthorized': 5,
    'request': 6,
    'request-json': 7,
    'api-key': 8,
    'tracker': 9,
    'input-error': 10
}

@dataclass
class TrackerData:
    base_url: str
    api_key_env: str # name of the api key environmental variable
    aliases: list[str]
    api_key: str = None


TRACKERS = [
    TrackerData(base_url="https://redacted.sh",
            api_key_env="RED_API_KEY",
            aliases=["red", "flacsfor.me"]),
    TrackerData(base_url="https://orpheus.network",
            api_key_env="OPS_API_KEY",
            aliases=["ops", "opsfet.ch"])
]

class GazelleOrigin:
    def __init__(self, argv=None):
        self.args = None
        self.api = None
        self.fetched = {}

        parser = argparse.ArgumentParser(
            description='Fetches torrent origin information from Gazelle-based music trackers',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='Either ORIGIN_TRACKER or --tracker must be set to a supported tracker:\n'
                   '  redacted.sh: "RED", or any string containing "flacsfor.me"\n'
                   '  orpheus.network: "OPS", or any string containing "opsfet.ch"'
        )
        parser.add_argument('torrent', nargs='+', help='torrent identifier, which can be either its info hash, torrent ID, permalink, or path to torrent file(s) whose name or computed info hash should be used')
        parser.add_argument('--out', '-o', help='Path to write origin data (default: print to stdout).', metavar='file')
        parser.add_argument('--ORIGIN_TRACKER','--tracker', '-t', metavar='tracker',default=os.environ.get("ORIGIN_TRACKER"), help='Tracker to use. Optional if the ORIGIN_TRACKER environment variable is set.')
        parser.add_argument('--api-key', metavar='key', help='API key. Optional if the <TRACKER>_API_KEY (e.g., RED_API_KEY) environment variable is set.')
        parser.add_argument('--env', '-e', nargs=1, metavar='file', help='file to load environment variables from')
        parser.add_argument('--post', '-p', nargs='+', metavar='file', default=[], help='script(s) to run after each output is written.\n'
                            'These scripts have access to environment variables with info about the item including OUT, ARTIST, NAME, DIRECTORY, EDITION, YEAR, FORMAT, ENCODING')
        parser.add_argument('--recursive', '-r', action='store_true', help='recursively search directories for files')
        parser.add_argument('--no-hash', '-n', action='store_true', help='don\'t compute hash from torrent files')
        parser.add_argument(
            "--ignore-invalid",
            "-i",
            default="ask",
            const="ask",
            nargs="?",
            choices=["stop", "ask", "continue"],
            help="Stop, ask, or continue when encountering an error (default: %(default)s)")
        parser.add_argument('--deduplicate', '-d', action='store_true', help='if specified, only one torrent with any given id/hash will be fetched')

        for tracker in TRACKERS:
            parser.add_argument('--' + tracker.api_key_env, help=argparse.SUPPRESS, default=os.environ.get(tracker.api_key_env))

        # First, check if "--env" is provided. If it is, set the default arguments to the values in the file, then parse arguments again.
        # This ensures that the command line options override the env file, which overrides environmental variables.
        args = parser.parse_args(argv)
        if args.env:
            if os.path.isfile(args.env[0]):
                envs = dotenv_values(args.env[0], verbose=True)
                parser.set_defaults(**envs)
            else:
                print('Unable to open file ' + args.env[0])
                sys.exit(EXIT_CODES['input-error'])
        args = parser.parse_args(argv)

        for script in args.post:
            if not os.path.isfile(script):
                print('Invalid post script: ' + script)
                sys.exit(EXIT_CODES['input-error'])

        if not args.ORIGIN_TRACKER:
            print(
                'Tracker must be provided using either --tracker or setting the ORIGIN_TRACKER environment variable.',
                file=sys.stderr)
            sys.exit(EXIT_CODES['tracker'])

        # Search for the tracker with an alias matching the input
        tracker = next((x for x in TRACKERS if args.ORIGIN_TRACKER.lower() in x.aliases), None)
        if not tracker:
            print('Invalid tracker: {0}'.format(args.ORIGIN_TRACKER), file=sys.stderr)
            sys.exit(EXIT_CODES['tracker'])

        tracker.api_key = args.api_key or getattr(args, tracker.api_key_env)
        if not tracker.api_key:  # Avoid KeyError
            print(
                f'API key must be provided using either --api-key or setting the {", ".join(x.api_key_env for x in TRACKERS)} environment variables.',
                file=sys.stderr)
            sys.exit(EXIT_CODES['api-key'])

        try:
            self.api = GazelleAPI(tracker)
        except GazelleAPIError as e:
            print('Error initializing Gazelle API client')
            if self.handle_invalid() == "stop":
                sys.exit(EXIT_CODES[e.code])

        self.args = args


    def ask_invalid(self):
        """Prompt the user for the next action after encountering an error."""
        do_this = ""
        options_ask = ["c", "s"]
        while do_this not in options_ask:
            print("Error. (s)top the entire program or (c)ontinue from this error?")
            print("Options (lowercase) = {}".format(options_ask))
            do_this = input("Your choice: ")
        if do_this == 'c':
            do_this = "continue"
        elif do_this == 's':
            do_this = 'stop'
        return do_this


    def handle_invalid(self):
        """Handle an invalid torrent error."""
        if self.args.ignore_invalid == "continue":
            return "continue"
        elif self.args.ignore_invalid == "ask":
            result = self.ask_invalid()
            return result
        else:
            return 'stop'


    def run(self):
        for torrent in self.args.torrent:
            self.handle_input_torrent(torrent)

    def parse_torrent_input(self, torrent, walk=True):
        """
        Parse hash or id of torrent
        torrent can be an id, hash, url, or path
        """
        # torrent is literal infohash
        if re.match(r'^[\da-fA-F]{40}$', torrent):
            return {'hash': torrent}
        # torrent is literal id
        if re.match(r'^\d+$', torrent):
            return {'id': torrent}
        # torrent is valid path
        if os.path.exists(torrent):
            if walk and os.path.isdir(torrent):
                for path in map(lambda x: os.path.join(torrent, x), os.listdir(torrent)):
                    self.handle_input_torrent(path, walk=self.args.recursive)
                return 'walked'
            # If file/dir name is info hash use that
            filename = os.path.split(torrent)[-1].split('.')[0]
            if re.match(r'^[\da-fA-F]{40}$', filename):
                return {'hash': filename}
            # If torrent file compute the info hash
            if not self.args.no_hash and os.path.isfile(torrent) and os.path.split(torrent)[-1].endswith('.torrent'):
                if has_bencoder:
                    with open(torrent, 'rb') as torrent:
                        try:
                            decoded = bencoder.decode(torrent.read())
                            info_hash = sha1(bencoder.encode(decoded[b'info'])).hexdigest()
                        except:
                            return None
                        return {'hash': info_hash}
                else:
                    print('Found torrent file ' + torrent + ' but unable to load bencoder module to compute hash')
                    print('Install bencoder (pip install bencoder) then try again or pass --no-hash to not compute the hash')
                    if self.handle_invalid() != "stop":
                        return None
                    else:
                        sys.exit(EXIT_CODES['input-error'])
        # torrent is a URL
        url_match = re.match(r'.*torrentid=(\d+).*', torrent)
        if not url_match or url_match.lastindex < 1:
            return None
        return {'id': url_match[1]}

    def handle_input_torrent(self, torrent, walk=True):
        """
        Get torrent's info from GazelleAPI
        torrent can be an id, hash, url, or path
        """
        print("Handling {}".format(torrent))
        parsed = self.parse_torrent_input(torrent, walk)
        if parsed == 'walked':
            return
        if not parsed:
            print('Invalid torrent ID, hash, file, or URL: ' + torrent, file=sys.stderr)
            if self.handle_invalid() != "stop":
                return
            else:
                sys.exit(EXIT_CODES["hash"])

        if self.args.deduplicate:
            if 'id' in parsed:
                if parsed['id'] in self.fetched:
                    return
                self.fetched[parsed['id']] = True
            if 'hash' in parsed:
                if parsed['hash'] in self.fetched:
                    return
                self.fetched[parsed['hash']] = True

        # Actually get the info from the API
        try:
            info = self.api.get_torrent_info(**parsed)
        except GazelleAPIError as e:
            if self.handle_invalid() == "stop":
                skip = False
            elif e.code == 'request':
                # If server returned 500 series error then stop because server might be having trouble
                skip = int(str(e).split('(status ')[-1][:-1]) < 500
            else:
                skip = e.code == 'request-json' or e.code == 'music'
            if skip:
                print('Got %s retrieving %s, skipping' % (str(e), torrent))
                return
            else:
                print(e, file=sys.stderr)
                sys.exit(EXIT_CODES[e.code])

        if self.args.out:
            with io.open(self.args.out, 'a' if os.path.exists(self.args.out) else 'w', encoding='utf-8') as f:
                f.write(info)
        else:
            print(info, end='')

        if self.args.post:
            fetched_info = yaml.load(info, Loader=yaml.SafeLoader)
            for script in self.args.post:
                subprocess.run(script, shell=True, env={k.upper(): str(v) for k, v in {**vars(self.args), **fetched_info}.items()})


def main(argv=None):
    gazelle_origin = GazelleOrigin(argv)
    gazelle_origin.run()

if __name__ == '__main__':
    main(sys.argv[1:])