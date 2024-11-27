# Import modules
from base import *
from ui.ui_print import *
import releases

name = "TorBox"
short = "TB"
api_key = ""

def check(element, force=False):
    hashes = []
    for release in element.Releases[:]:
        if len(release.hash) == 40:
            hashes.append(release.hash.lower())
        else:
            element.Releases.remove(release)
            
    if not hashes:
        return

    # Check in smaller batches to avoid API limits
    batch_size = 25
    for i in range(0, len(hashes), batch_size):
        batch = hashes[i:i + batch_size]
        
        headers = {'Authorization': f'Bearer {api_key}'}
        params = {
            'hash': ','.join(batch),
            'list_files': 'true'
        }

        try:
            response = requests.get(
                'https://api.torbox.app/v1/api/torrents/checkcached',
                headers=headers,
                params=params,
                timeout=10
            )
            
            if response.status_code != 200:
                ui_print(f"[torbox] Cache check failed: {response.text}", debug=True)
                continue
                
            data = response.json()
            if not data.get('data'):
                continue
                
            cached_results = data['data']
            
            for release in element.Releases:
                release_hash = release.hash.lower()
                if release_hash in cached_results and 'files' in cached_results[release_hash]:
                    files_data = cached_results[release_hash]['files']
                    
                    # Process files
                    release_files = []
                    for file_info in files_data:
                        file_obj = SimpleNamespace()
                        file_obj.id = file_info.get('id', 0)
                        file_obj.name = file_info.get('name', '')
                        file_obj.size = float(file_info.get('size', 0)) / 1e9  # Convert to GB
                        release_files.append(file_obj)
                    
                    # Create version
                    if release_files:
                        ver = SimpleNamespace()
                        ver.files = release_files
                        ver.size = sum(f.size for f in release_files)
                        release.files = [ver]
                        release.size = ver.size
                        release.cached += ['TB']
                        
        except Exception as e:
            ui_print(f"[torbox] Cache check error: {str(e)}", debug=True)

def download(element, stream=True, query='', force=False):
    cached = element.Releases
    if query == '':
        query = element.deviation()
    for release in cached[:]:
        if regex.match(r'(' + query + ')', release.title, regex.I) or force:
            if stream:
                # Verify cache status first
                if 'TB' not in release.cached:
                    continue
                    
                # Cached download
                data = {
                    'magnet': str(release.download[0]),
                    'seed': 1
                }
                headers = {'Authorization': f'Bearer {api_key}'}
                try:
                    response = requests.post(
                        'https://api.torbox.app/v1/api/torrents/createtorrent',
                        headers=headers,
                        data=data,
                        timeout=30
                    )
                    if response.status_code == 200 and response.json().get('success'):
                        ui_print('[torbox] adding cached release: ' + release.title)
                        return True
                except:
                    continue
            else:
                ui_print(f"[torbox] uncached: {release.title}", debug=True)
                # Uncached download
                #TODO Not working
                data = {
                    'magnet': str(release.download[0]),
                    'seed': 1
                }
                headers = {'Authorization': f'Bearer {api_key}'}
                response = requests.post(
                    'https://api.torbox.app/v1/api/torrents/createtorrent',
                    headers=headers, 
                    data=data,
                    timeout=30
                )
                if response.status_code == 200 and response.json().get('success'):
                    ui_print('[torbox] adding uncached release: ' + release.title)
                    return True
        else:
            ui_print(f"[torbox] error: rejected release: {release.title} because it doesnt match the allowed deviation", debug=True)
    return False

def setup(cls, new=False):
    from debrid.services import setup
    setup(cls, new)