import html
import json
import requests
import textwrap
import yaml


headers = {
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
    'User-Agent': 'gazelle-origin',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip,deflate,sdch',
    'Accept-Language': 'en-US,en;q=0.8',
    'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.3'}


class GazelleAPIError(Exception):
    def __init__(self, code, message):
        super().__init__()
        self.code = code
        self.message = message

    def __str__(self):
        return self.message


# GazelleAPI code is based off of REDbetter (https://github.com/Mechazawa/REDBetter-crawler).
class GazelleAPI:
    def __init__(self, api_key):
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.session.headers.update({'Authorization': api_key})

    def request(self, action, **kwargs):
        ajaxpage = 'https://redacted.ch/ajax.php'
        params = {'action': action}
        params.update(kwargs)

        r = self.session.get(ajaxpage, params=params, allow_redirects=False, timeout=30)
        if r.status_code == 401 or r.status_code == 403:
            raise GazelleAPIError('unauthorized', 'Authentication error: ' + r.json()['error'])
        if r.status_code != 200:
            raise GazelleAPIError('request',
                'Could not retrieve origin data. Try again later. (status {0})'.format(r.status_code))

        parsed = json.loads(r.content)
        if parsed['status'] != 'success':
            raise GazelleAPIError('request-json', 'Could not retrieve origin data. Check the torrent ID/hash or try again later.')

        return parsed['response']

    def _make_table(self, dict):
        k_width = max(len(html.unescape(k)) for k in dict.keys()) + 2
        result = ''
        for k,v in dict.items():
            if v == "''":
                v = '~'
            result += "".join((html.unescape((k + ':').ljust(k_width)), v)) + '\n'
        return result

    def get_torrent_info(self, hash=None, id=None):
        info = self.request('torrent', hash=hash, id=id)
        group = info['group']
        torrent = info['torrent']

        if group['categoryName'] != 'Music':
            raise GazelleAPIError('music', 'Not a music torrent')

        musicInfo = group['musicInfo']

        # build artist name
        if len(musicInfo['artists']) <= 2:
            artists = ' & '.join([artist['name'] for artist in musicInfo['artists']])
        else:
            artists = 'Various Artists'

        delimited_artists = {category: ', '.join([artist['name'] for artist in artist_list])
         for category, artist_list in musicInfo.items()}

        # Maps release type numbers to their string values
        release_codes = {
            1: "Album",
            3: "Soundtrack",
            5: "EP",
            6: "Anthology",
            7: "Compilation",
            9: "Single",
            11: "Live album",
            13: "Remix",
            14: "Bootleg",
            15: "Interview",
            16: "Mixtape",
            17: "Demo",
            18: "Concert Recording",
            19: "DJ Mix",
            21: "Unknown"
        }
        releaseTypes = release_codes.get(group['releaseType'], "none")

        file_list = [m.groupdict() for m in
                     re.finditer(r"(?P<Name>.*?){{{(?P<Size>\d+)}}}\|\|\|", torrent['fileList'])]

        # If the api can return empty tags
        group['tags'] = group.get('tags', '')

        info_dict = {k:html.unescape(v) if isinstance(v, str) else v for k,v in {
            'Artist':                  artists,
            'Name':                    group['name'],
            'Release type':            releaseTypes,
            'Record label':            torrent['remasterRecordLabel'],
            'Catalog number':          torrent['remasterCatalogueNumber'],
            'Edition year':            torrent['remasterYear'] or '',
            'Edition':                 torrent['remasterTitle'],
            'Tags':                    str(', '.join(str(tag) for tag in group['tags'])),
            'Main artists':            delimited_artists['artists'],
            'Featured artists':        delimited_artists['with'],
            'Producers':               delimited_artists['producer'],
            'Remix artists':           delimited_artists['remixedBy'],
            'DJs':                     delimited_artists['dj'],
            'Composers':               delimited_artists['composers'],
            'Conductors':              delimited_artists['conductor'],
            'Original year':           group['year'] or '',
            'Original release label':  group['recordLabel'] or '',
            'Original catalog number': group['catalogueNumber'] or '',
            'Media':                   torrent['media'],
            'Log':                     '{0}%'.format(torrent['logScore']) if torrent['hasLog'] else '',
            'Format':                  torrent['format'],
            'Encoding':                torrent['encoding'],
            'Directory':               torrent['filePath'],
            'Size':                    torrent['size'],
            'File count':              torrent['fileCount'],
            'Info hash':               torrent.get("infoHash", hash or "Unknown"), # OPS fallback
            'Uploaded':                torrent['time'],
            'Permalink':               'https://redacted.ch/torrents.php?torrentid={0}'.format(torrent['id']),      
            'Cover':                   group['wikiImage']
        }.items()}

        dump = yaml.dump(info_dict, width=float('inf'), sort_keys=False, allow_unicode=True)

        out = {}
        for line in dump.strip().split('\n'):
            key, value = line.split(':', 1)
            if key == 'Uploaded' or key == 'Encoding':
                value = value.replace("'", '')
            out[key] = value.strip()

        result = self._make_table(out) + '\n'

        comment = html.unescape(torrent['description']).strip('\r\n')
        if comment:
            comment = textwrap.indent(comment, '  ', lambda line: True)
            result += 'Comment: |-\n{0}\n\n'.format(comment)

        result += yaml.dump({'Files': file_list}, width=float('inf'), allow_unicode=True)

        groupDescription = html.unescape(group['bbBody']).strip('\r\n')
        if groupDescription:
            groupDescription = textwrap.indent(groupDescription, '  ', lambda line: True)
            result += '\n\nDescription: |-\n{0}\n\n'.format(groupDescription)

        return result
