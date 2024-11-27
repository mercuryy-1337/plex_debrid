#import modules
from base import *
from ui.ui_print import *
import releases
import json
from concurrent.futures import ThreadPoolExecutor
import asyncio

# (required) Name of the Debrid service
name = "Real Debrid"
short = "RD"
media_file_extensions = ['.mkv', '.mp4', '.avi', '.mov']
# (required) Authentification of the Debrid service, can be oauth aswell. Create a setting for the required variables in the ui.settings_list. For an oauth example check the trakt authentification.
api_key = ""
# Define Variables
session = requests.Session()
errors = [
    [202," action already done"],
    [400," bad Request (see error message)"],
    [403," permission denied (infringing torrent or account locked or not premium)"],
    [503," service unavailable (see error message)"],
    [404," wrong parameter (invalid file id(s)) / unknown ressource (invalid id)"],
    ]
def setup(cls, new=False):
    from debrid.services import setup
    setup(cls,new)

# Error Log
def logerror(response):
    if not response.status_code in [200,201,204]:
        desc = ""
        for error in errors:
            if response.status_code == error[0]:
                desc = error[1]
        ui_print("[realdebrid] error: (" + str(response.status_code) + desc + ") " + str(response.content), debug=ui_settings.debug)
    if response.status_code == 401:
        ui_print("[realdebrid] error: (401 unauthorized): realdebrid api key does not seem to work. check your realdebrid settings.")
    if response.status_code == 403:
        ui_print("[realdebrid] error: (403 unauthorized): You may have attempted to add an infringing torrent or your realdebrid account is locked or you dont have premium.")

# Get Function
def get(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36','authorization': 'Bearer ' + api_key}
    response = None
    try:
        response = session.get(url, headers=headers)
        logerror(response)
        response = json.loads(response.content, object_hook=lambda d: SimpleNamespace(**d))
    except Exception as e:
        ui_print("[realdebrid] error: (json exception): " + str(e), debug=ui_settings.debug)
        response = None
    return response

# Post Function
def post(url, data):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36','authorization': 'Bearer ' + api_key}
    response = None
    try:
        response = session.post(url, headers=headers, data=data)
        response = json.loads(response.content, object_hook=lambda d: SimpleNamespace(**d))
    except Exception as e:
        if hasattr(response,"status_code"):
            if response.status_code >= 300:
                ui_print("[realdebrid] error: (json exception): " + str(e), debug=ui_settings.debug)
        else:
            ui_print("[realdebrid] error: (json exception): " + str(e), debug=ui_settings.debug)
        response = None
    return response

# Delete Function
def delete(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36','authorization': 'Bearer ' + api_key}
    try:
        response = requests.delete(url, headers=headers)
        print(f"Delete response: {response.json()}")
        # time.sleep(1)
    except Exception as e:
        ui_print("[realdebrid] error: (delete exception): " + str(e), debug=ui_settings.debug)
        print("[realdebrid] error: (delete exception): " + str(e))
        None
    return None

# Object classes
class file:
    def __init__(self, id, name, size, wanted_list, unwanted_list):
        self.id = id
        self.name = name
        self.size = size / 1000000000
        self.match = ''
        wanted = False
        unwanted = False
        for key, wanted_pattern in wanted_list:
            if wanted_pattern.search(self.name):
                wanted = True
                self.match = key
                break

        if not wanted:
            for key, unwanted_pattern in unwanted_list:
                if unwanted_pattern.search(self.name) or self.name.endswith('.exe') or self.name.endswith('.txt'):
                    unwanted = True
                    break

        self.wanted = wanted
        self.unwanted = unwanted

    def __eq__(self, other):
        return self.id == other.id

class version:
    def __init__(self, files):
        self.files = files
        self.needed = 0
        self.wanted = 0
        self.unwanted = 0
        self.size = 0
        for file in self.files:
            self.size += file.size
            if file.wanted:
                self.wanted += 1
            if file.unwanted:
                self.unwanted += 1

# (required) Download Function.
def download(element, stream=True, query='', force=False):
    cached = element.Releases
    if query == '':
        query = element.deviation()
    wanted = [query]
    if not isinstance(element, releases.release):
        wanted = element.files()
    for release in cached[:]:
        # if release matches query
        if regex.match(query, release.title,regex.I) or force:
            if stream:
                release.size = 0
                for version in release.files:
                    if hasattr(version, 'files'):
                        if len(version.files) > 0 and version.wanted > len(wanted) / 2 or force:
                            cached_ids = []
                            for file in version.files:
                                cached_ids += [file.id]
                            # post magnet to real debrid
                            try:
                                response = post('https://api.real-debrid.com/rest/1.0/torrents/addMagnet',{'magnet': str(release.download[0])})
                                torrent_id = str(response.id)
                            except:
                                ui_print('[realdebrid] error: could not add magnet for release: ' + release.title, ui_settings.debug)
                                continue
                            response = post('https://api.real-debrid.com/rest/1.0/torrents/selectFiles/' + torrent_id,{'files': str(','.join(cached_ids))})
                            response = get('https://api.real-debrid.com/rest/1.0/torrents/info/' + torrent_id)
                            actual_title = ""
                            if len(response.links) == len(cached_ids):
                                actual_title = response.filename
                                release.download = response.links
                            else:
                                if response.status in ["queued","magnet_conversion","downloading","uploading"]:
                                    if hasattr(element,"version"):
                                        debrid_uncached = True
                                        for i,rule in enumerate(element.version.rules):
                                            if (rule[0] == "cache status") and (rule[1] == 'requirement' or rule[1] == 'preference') and (rule[2] == "cached"):
                                                debrid_uncached = False
                                        if debrid_uncached:
                                            import debrid as db
                                            release.files = version.files
                                            db.downloading += [element.query() + ' [' + element.version.name + ']']
                                            ui_print('[realdebrid] adding uncached release: ' + release.title)
                                            return True
                                else:
                                    ui_print('[realdebrid] error: selecting this cached file combination returned a .rar archive - trying a different file combination.', ui_settings.debug)
                                    delete_torrent(torrent_id)
                                    continue
                            if len(release.download) > 0:
                                for link in release.download:
                                    try:
                                        response = post('https://api.real-debrid.com/rest/1.0/unrestrict/link',{'link': link})
                                    except:
                                        break
                                release.files = version.files
                                ui_print('[realdebrid] adding cached release: ' + release.title)
                                if not actual_title == "":
                                    release.title = actual_title
                                return True
                ui_print('[realdebrid] error: no streamable version could be selected for release: ' + release.title)
                return False
            else:
                try:
                    response = post('https://api.real-debrid.com/rest/1.0/torrents/addMagnet',{'magnet': release.download[0]})
                    time.sleep(0.1)
                    post('https://api.real-debrid.com/rest/1.0/torrents/selectFiles/' + str(response.id),{'files': 'all'})
                    ui_print('[realdebrid] adding uncached release: ' + release.title)
                    return True
                except:
                    continue
        else:
            ui_print('[realdebrid] error: rejecting release: "' + release.title + '" because it doesnt match the allowed deviation', ui_settings.debug)
    return False

def check_if_cached(magnet): 
    try:
        magnet_url = "https://api.real-debrid.com/rest/1.0/torrents/addMagnet"
        add_response = post(magnet_url, data={'magnet': magnet})
        torrent_id = add_response.id
        if not torrent_id:
            raise Exception("Failed to retrieve torrent ID.")
        info_url = f"https://api.real-debrid.com/rest/1.0/torrents/info/{torrent_id}"
        info_response = get(info_url)
        if info_response.filename == "Invalid Magnet":
            delete_torrent(torrent_id)
            return None
        files = info_response.files

        media_file_ids = [
            str(file.id) for file in files 
            if file.path.endswith(tuple(media_file_extensions))
        ]
        if not media_file_ids:
            raise ui_print(f"[realdebrid] No media files found in torrent {torrent_id}.", ui_settings.debug)
        select_url = f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{torrent_id}"
        post(select_url, data={'files': ','.join(media_file_ids)})
        info_response = get(info_url)
        if info_response.status == 'downloaded':
            delete_torrent(torrent_id)
            return info_response
        else:
            delete_torrent(torrent_id)
    except Exception as e:
        ui_print(f"[realdebrid] Error(check_if_cached): {e}", ui_settings.debug)

def delete_torrent(torrent_id):
    headers = {'Authorization': f'Bearer {api_key}'}
    delete_url = f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}"
    response = requests.delete(delete_url, headers=headers)
    if response.status_code != 204:
        print(f"Error deleting torrent {torrent_id}, status: {response.json()}")
        time.sleep(1)
        delete_torrent(torrent_id)

async def fetch_with_executor(executor, func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, func, *args, **kwargs)

async def check_async(element, force=False):
    if force:
        wanted = ['.*']
    else:
        wanted = element.files()
    unwanted = releases.sort.unwanted
    wanted_patterns = list(zip(wanted, [regex.compile(r'(' + key + ')', regex.IGNORECASE) for key in wanted]))
    unwanted_patterns = list(zip(unwanted, [regex.compile(r'(' + key + ')', regex.IGNORECASE) for key in unwanted]))

    downloads = []
    for release in element.Releases[:]:
        if release.download:
            downloads.append(release.download)
        else:
            ui_print(f"[realdebrid] error: missing download URL for release '{release.title}'", ui_settings.debug)
            element.Releases.remove(release)
    
    if len(downloads) > 0:
        ui_print("[realdebrid] checking and sorting all release files ...", ui_settings.debug)
        
        executor = ThreadPoolExecutor(max_workers=2)
        tasks = [fetch_with_executor(executor, check_if_cached, download) for download in downloads]
        responses = await asyncio.gather(*tasks)
        responses = [response for response in responses if response is not None]
        for release in element.Releases:
            release.files = []
            release_hash = release.hash.lower()
            for response in responses:
                if response:
                    if response.hash.lower() == release_hash:
                        if hasattr(response, 'files'):
                            rd_attr = response.files
                            if len(rd_attr) > 0:
                                for cached_version in rd_attr:
                                    version_files = []
                                    debrid_file = file(str(cached_version.id), cached_version.path.lstrip('/'), cached_version.bytes, wanted_patterns, unwanted_patterns)
                                    version_files.append(debrid_file)
                                    release.files.append(version(version_files))
                                release.files.sort(key=lambda x: len(x.files), reverse=True)
                                release.files.sort(key=lambda x: x.wanted, reverse=True)
                                release.files.sort(key=lambda x: x.unwanted, reverse=False)
                                release.wanted = release.files[0].wanted
                                release.unwanted = release.files[0].unwanted
                                release.size = release.files[0].size
                                release.cached.append('RD')
                            continue
        ui_print("done", ui_settings.debug)


# (required) Check Function
def check(element, force=False):
    asyncio.run(check_async(element, force))
