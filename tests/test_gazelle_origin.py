import unittest
import os
from dotenv import dotenv_values
from gazelleorigin.__main__ import main, GazelleOrigin
import yaml
from contextlib import redirect_stdout
import io
import re


class TestCore(unittest.TestCase):

    def setUp(self):

        self.env_file = "keys.env"
        self.envs = dotenv_values(self.env_file, verbose=True)

        self.test_dicts = dict.fromkeys(["ops.yaml", "red.yaml"])
        for file in self.test_dicts:
            with open(file, 'r', encoding='UTF-8') as f:
                self.test_dicts[file] = yaml.safe_load(f)
                self.test_dicts[file].pop("Tags") # pop these in case they change
                self.test_dicts[file].pop("Description")

    def test_parser(self):
        g = GazelleOrigin(['--tracker', 'ops', '--env', self.env_file, 'https://orpheus.network/torrents.php?torrentid=1225441'])

        self.assertEqual({'id': '1225441'},
                         g.parse_torrent_input(torrent="1225441"))

        self.assertEqual({'hash': '4562B9F4F3A7559BBD4D5ACC477C39D2B6F777B4'},
                         g.parse_torrent_input(torrent="4562B9F4F3A7559BBD4D5ACC477C39D2B6F777B4"))

        self.assertEqual({'id': '1888808'},
                         g.parse_torrent_input(torrent="https://redacted.ch/torrents.php?id=875854&torrentid=1888808#torrent1888808"))



    def test_ops(self):
        self.run_main(['--tracker', 'ops', '--env', self.env_file, 'https://orpheus.network/torrents.php?torrentid=1225441'],
                       expected=self.test_dicts['ops.yaml'])

    def test_red(self):

        self.run_main(['--tracker', 'red', '--env', self.env_file, 'https://redacted.ch/torrents.php?torrentid=1684059'],
                       expected=self.test_dicts['red.yaml'])

    def test_red_with_env(self):
        os.environ["RED_API_KEY"] = self.envs["RED_API_KEY"]
        self.run_main(['--tracker', 'red', 'https://redacted.ch/torrents.php?torrentid=1684059'],
                       expected=self.test_dicts['red.yaml'])

    def run_main(self, args, expected):
        # Run the main function and capture the output, make sure the output yaml parses, and compare it to the expected output
        r = io.StringIO()
        with redirect_stdout(r):
            main(args)
        response = re.split("^Handling.*\n", r.getvalue())
        parsed = yaml.safe_load(response[1])
        self.assertEqual(parsed, parsed | expected)
        self.assertGreater(len(parsed['Tags'].split(', ')), 3)
        self.assertGreater(len(parsed['Description'].split('\n')), 3)
